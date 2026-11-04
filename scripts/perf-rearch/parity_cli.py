#!/usr/bin/env python3
"""CLI tier of the parity gate: real project configs on tsc-rs and Go, compared byte for byte.

usage: parity_cli.py --rs CMD --out DIR [--go BIN] [--tier quick|full] [--jobs N] [--rs-runs N]
                     [--only REGEX] [--projects FILE] [--cache DIR] [--label-rs NAME] [--list]

--rs is the binary under test, or a command split at spaces (`node .../tsc` for the js mode).
Projects and configs come from --projects (default parity-projects.json next to this file); a
project's first existing "dirs" entry is used, and a project with none is SKIP. Each config runs
the variants of the tier (VARIANTS below; quick runs the variants marked quick, on the configs
marked quick):

  noemit      -p CFG --noEmit --pretty false
  emit        -p CFG --noEmit false --pretty false   (every written file compared)
  decl        emit + --declaration --declarationMap --sourceMap
  incr        emit + --incremental, run twice (cold, then no-op)
  listfiles   --noEmit --listFiles
  extdiag     --noEmit --extendedDiagnostics; the Memory and time values are masked and the
              padding of the statistics (it follows the widest value) is one space
  pretty      --noEmit --pretty true (colors and code frames)
  showconfig  --showConfig
  build       -b CFG --verbose --pretty false, run twice (cold, then up to date); only configs
              with "build": true

Every side of an item runs on a fresh overlay of the project dir (unshare -rm, then an overlayfs
mount on the project's own path; the upper dir is ~/.cache/perf-rearch/parity/scratch/<item>/upper),
so the configured outDir, declarationDir and tsBuildInfoFile are used as they are, paths in output
are the project's real paths, the project never changes, and the written files are exactly the
upper dir (sha256 each; a file the second run of incr or build rewrites or touches counts again).

Go's result is cached in --cache (default ~/.cache/perf-rearch/parity/go-cache/<go sha>/cli), keyed
by the Go binary, project path, config and arguments. When the cache is made Go runs twice; an item
where Go's two runs differ is GO-UNSTABLE (not compared). tsc-rs runs --rs-runs times (default 1 for
quick, 2 for full); two tsc-rs runs that differ are a DIFF (rs-nondet) even when one matches Go.

Compared per item: exit code, stdout, stderr and the written file tree (sha256 of each file).
Status: MATCH, DIFF, GO-UNSTABLE, ERROR, SKIP. Output: DIR/results.json ({"items": {"cli/<project>/
<config>/<variant>": {...}}}) and DIR/items/<item>/ (rs output and a diff against Go) for each DIFF.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
SCRATCH = HOME / '.cache/perf-rearch/parity/scratch'
ENV = {k: v for k, v in os.environ.items() if not k.startswith(('GOPORT_', 'TSGO_', 'TS_'))}
ENV.pop('NODE_OPTIONS', None)
TIMEOUT = 1800
# Runs a command on an overlay of the project dir (lower: the project, upper: what the run writes), in
# a user and mount namespace, so the project never changes and the written files are the upper dir.
OVERLAY = ['unshare', '-rm', '--', 'sh', '-c',
           'mount -t overlay overlay -o "lowerdir=$1,upperdir=$2,workdir=$3" "$1" && cd "$4" && shift 4 && exec "$@"',
           'overlay']
CACHE_VERSION = 4  # bump when a change of this file changes Go's cached results

# name: (quick, kind, extra args). {S} is the scratch dir.
VARIANTS = {
    'noemit': (True, 'plain', ['--noEmit', '--pretty', 'false']),
    'emit': (True, 'emit', ['--pretty', 'false']),
    'decl': (False, 'emit', ['--declaration', '--declarationMap', '--sourceMap', '--pretty', 'false']),
    'incr': (False, 'incr', ['--incremental', '--pretty', 'false']),
    'listfiles': (False, 'plain', ['--noEmit', '--listFiles', '--pretty', 'false']),
    'extdiag': (True, 'plain', ['--noEmit', '--extendedDiagnostics', '--pretty', 'false']),
    'pretty': (False, 'plain', ['--noEmit', '--pretty', 'true']),
    'showconfig': (False, 'plain', ['--showConfig']),
    'build': (False, 'build', ['--verbose', '--pretty', 'false']),
}
# -b --verbose starts each message with the wall clock: "06:12:55 PM - " (--pretty false) or
# "[6:12:55 PM] " (pretty).
CLOCK_RE = re.compile(rb'^(?:\[\d{1,2}:\d{2}:\d{2}(?: [AP]M)?\] |\d{1,2}:\d{2}:\d{2}(?: [AP]M)? - )', re.M)
MASK_RE = re.compile(rb'^((?:Memory used|Memory allocs|[A-Za-z /]* time)):[ \t]*\S+[ \t]*$', re.M)
# The statistics are padded to the widest value, which can be a masked one: one space after the colon.
STAT_RE = re.compile(rb'^([A-Za-z][A-Za-z /()]*):[ \t]+(\S+)[ \t]*$', re.M)


def mask_stats(out):
    return STAT_RE.sub(rb'\1: \2', MASK_RE.sub(rb'\1: <masked>', out))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def expand(p):
    return Path(os.path.expanduser(p))


def load_projects(path):
    spec = json.loads(Path(path).read_text())
    out = []
    for p in spec['projects']:
        root = next((expand(d) for d in p['dirs'] if (expand(d) / p.get('marker', 'package.json')).exists()), None)
        out.append({**p, 'root': root})
    return out


class Run:
    def __init__(self, code, stdout, stderr, tree, secs):
        self.code, self.stdout, self.stderr, self.tree, self.secs = code, stdout, stderr, tree, secs

    def key(self):
        return [self.code, hashlib.sha256(self.stdout).hexdigest(), hashlib.sha256(self.stderr).hexdigest(),
                sorted(self.tree.items())]

    def to_json(self):
        return {'code': self.code, 'stdout': self.stdout.decode('utf-8', 'surrogateescape'),
                'stderr': self.stderr.decode('utf-8', 'surrogateescape'), 'tree': self.tree, 'secs': round(self.secs, 2)}

    @staticmethod
    def from_json(d):
        return Run(d['code'], d['stdout'].encode('utf-8', 'surrogateescape'),
                   d['stderr'].encode('utf-8', 'surrogateescape'), d['tree'], d['secs'])


def tree_of(upper):
    """{relative path: (size, mtime_ns)} of the files in an overlay upper dir: what the runs wrote. An
    overlay whiteout (a deleted file) is size -1."""
    tree = {}
    for top, dirs, files in os.walk(upper):
        for f in files:
            p = os.path.join(top, f)
            st = os.lstat(p)
            if stat.S_ISCHR(st.st_mode):
                tree[os.path.relpath(p, upper)] = (-1, 0)
            elif stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                tree[os.path.relpath(p, upper)] = (st.st_size, st.st_mtime_ns)
    return tree


def execute(cmd, cwd, timeout=TIMEOUT):
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=cwd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        code = 'timeout'
    return code, out, err, time.monotonic() - start


class Item:
    def __init__(self, project, config, variant):
        self.project, self.config, self.variant = project, config, variant
        self.name = f'cli/{project["name"]}/{config["label"]}/{variant}'
        self.scratch = SCRATCH / self.name.replace('/', '__')
        self.cwd = project['root'] / config.get('cwd', '.')

    def args(self):
        quick, kind, extra = VARIANTS[self.variant]
        cfg = self.config['config']
        if kind == 'build':
            return ['-b', cfg, *extra]
        if kind in ('emit', 'incr'):
            return ['-p', cfg, '--noEmit', 'false', *extra]
        return ['-p', cfg, *extra]

    def run_side(self, cmd):
        """One side on a fresh overlay: one or two runs (incr, build), each a Run with the files it wrote."""
        kind = VARIANTS[self.variant][1]
        clear(self.scratch)
        upper, work = self.scratch / 'upper', self.scratch / 'work'
        upper.mkdir(parents=True)
        work.mkdir()
        root = self.project['root']
        cwd = root / self.config.get('cwd', '.')
        before = {}
        runs = []
        for k in range(2 if kind in ('incr', 'build') else 1):
            if k:
                time.sleep(0.02)  # mtime granularity between the cold and the second run
            code, out, err, secs = execute([*OVERLAY, str(root), str(upper), str(work), str(cwd), *cmd, *self.args()], cwd)
            if self.variant == 'extdiag':
                out = mask_stats(out)
            if kind == 'build':
                out = CLOCK_RE.sub(b'<clock> - ', out)
            now = tree_of(upper)
            runs.append(Run(code, out, err, {f: sha256_file(upper / f) if v[0] >= 0 else 'deleted'
                                             for f, v in now.items() if before.get(f) != v}, secs))
            before = now
        return runs


def clear(d):
    """Removes a scratch dir; overlayfs leaves work/work with mode 000."""
    if d.exists():
        subprocess.run(['chmod', '-R', 'u+rwx', str(d)], check=False)
        shutil.rmtree(d)


def text_diff(a, b, na, nb):
    lines = list(difflib.unified_diff(a.decode('utf-8', 'replace').splitlines(True),
                                      b.decode('utf-8', 'replace').splitlines(True), na, nb, n=2))
    return ''.join(lines[:400])


def compare(go_runs, rs_runs):
    """None when equal, else a short description of the first difference."""
    if len(go_runs) != len(rs_runs):
        return 'run count'
    for k, (g, r) in enumerate(zip(go_runs, rs_runs)):
        tag = '' if len(go_runs) == 1 else f'run{k + 1} '
        if g.code != r.code:
            return f'{tag}exit {g.code}>{r.code}'
        if g.stdout != r.stdout:
            gl, rl = g.stdout.splitlines(), r.stdout.splitlines()
            first = next((i for i, (x, y) in enumerate(zip(gl, rl)) if x != y), min(len(gl), len(rl)))
            return f'{tag}stdout ({len(gl)} vs {len(rl)} lines, first at line {first + 1})'
        if g.stderr != r.stderr:
            return f'{tag}stderr'
        if g.tree != r.tree:
            names = sorted(set(g.tree) | set(r.tree))
            bad = [n for n in names if g.tree.get(n) != r.tree.get(n)]
            kinds = Counter('missing' if n not in r.tree else 'extra' if n not in g.tree else 'content' for n in bad)
            return f'{tag}files {dict(kinds)} e.g. {bad[0]}'
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rs', required=True)
    ap.add_argument('--go', default=str(HOME / '.local/bin/tsgo-oracle-3e9236361fa9'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--tier', default='full', choices=('quick', 'full'))
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--rs-runs', type=int)
    ap.add_argument('--only', default='')
    ap.add_argument('--projects', default=str(HERE / 'parity-projects.json'))
    ap.add_argument('--cache')
    ap.add_argument('--label-rs', default='tsc-rs')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()
    rs_cmd = shlex.split(args.rs)
    rs_runs = args.rs_runs or (1 if args.tier == 'quick' else 2)
    go_sha = sha256_file(args.go)[:16]
    cache = Path(args.cache) if args.cache else HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}/cli'
    out = Path(args.out)
    projects = load_projects(args.projects)
    items, skipped = [], {}
    for p in projects:
        for c in p['configs']:
            for v, (quick, kind, _) in VARIANTS.items():
                if kind == 'build' and not c.get('build'):
                    continue
                if v in c.get('skipVariants', []):
                    continue
                if args.tier == 'quick' and not (quick and c.get('quick')):
                    continue
                name = f'cli/{p["name"]}/{c["label"]}/{v}'
                if not re.search(args.only, name):
                    continue
                if p['root'] is None:
                    skipped[name] = {'status': 'SKIP', 'detail': 'project not prepared'}
                else:
                    items.append(Item(p, c, v))
    if args.list:
        for it in items:
            print(it.name, shlex.join(it.args()), f'(cwd {it.config.get("cwd", ".")})')
        for n in skipped:
            print(n, 'SKIP')
        return
    out.mkdir(parents=True, exist_ok=True)
    results = dict(skipped)
    lock = threading.Lock()
    counts = Counter(r['status'] for r in skipped.values())
    start = time.monotonic()

    def one(it):
        try:
            row = run_item(it)
        except Exception as e:  # a harness error, never a match
            row = {'status': 'ERROR', 'detail': repr(e)[:400]}
        with lock:
            results[it.name] = row
            counts[row['status']] += 1
            print(f'{row["status"]:<11} {it.name} {row.get("detail", "")} '
                  f'(go {row.get("goSecs", "-")}s, rs {row.get("rsSecs", "-")}s)', flush=True)

    def run_item(it):
        key = hashlib.sha256(json.dumps([CACHE_VERSION, go_sha, str(it.project['root']), it.config, it.variant,
                                         it.args()]).encode()).hexdigest()[:24]
        cfile = cache / f'{key}.json'
        if cfile.exists():
            gc = json.loads(cfile.read_text())
        else:
            g1 = it.run_side([args.go])
            g2 = it.run_side([args.go])
            gc = {'item': it.name, 'runs': [r.to_json() for r in g1], 'unstable': compare(g1, g2)}
            cfile.parent.mkdir(parents=True, exist_ok=True)
            tmp = cfile.with_suffix(f'.tmp{os.getpid()}')
            tmp.write_text(json.dumps(gc))
            tmp.replace(cfile)
        go_runs = [Run.from_json(r) for r in gc['runs']]
        if it.variant == 'extdiag':  # caches written before the padding rule (idempotent)
            for r in go_runs:
                r.stdout = mask_stats(r.stdout)
        go_secs = round(sum(r.secs for r in go_runs), 1)
        rs_all = [it.run_side(rs_cmd) for _ in range(rs_runs)]
        clear(it.scratch)
        rs_secs = [round(sum(r.secs for r in runs), 1) for runs in rs_all]
        row = {'goSecs': go_secs, 'rsSecs': rs_secs}
        diffs = [compare(go_runs, runs) for runs in rs_all]
        nondet = next((compare(rs_all[0], runs) for runs in rs_all[1:] if compare(rs_all[0], runs)), None)
        if gc['unstable']:
            row.update(status='GO-UNSTABLE', detail=f'Go runs differ: {gc["unstable"]}; '
                                                      f'{args.label_rs} {"equals" if not diffs[0] else "differs from"} Go run 1')
        elif nondet:
            row.update(status='DIFF', detail=f'rs-nondet: {nondet}')
        elif diffs[0]:
            row.update(status='DIFF', detail=diffs[0])
        else:
            row.update(status='MATCH')
        if row['status'] == 'DIFF':
            d = out / 'items' / it.name.replace('/', '__')
            d.mkdir(parents=True, exist_ok=True)
            bad = next(runs for runs, df in zip(rs_all, diffs + [nondet]) if df) if any(diffs) else rs_all[-1]
            (d / 'rs.json').write_text(json.dumps([r.to_json() for r in bad], indent=1))
            (d / 'cmd.txt').write_text(f'cd {it.project["root"] / it.config.get("cwd", ".")} && {shlex.join([*rs_cmd, *it.args()])}'
                                       f'   # writes into the project: run it on a copy or an overlay\n')
            text = ''
            for k, (g, r) in enumerate(zip(go_runs, bad)):
                text += text_diff(g.stdout, r.stdout, f'go run{k + 1} stdout', f'{args.label_rs} stdout')
                text += text_diff(g.stderr, r.stderr, f'go run{k + 1} stderr', f'{args.label_rs} stderr')
                for n in sorted(set(g.tree) | set(r.tree)):
                    if g.tree.get(n) != r.tree.get(n):
                        text += f'file {n}: go {g.tree.get(n, "-")[:12]} {args.label_rs} {r.tree.get(n, "-")[:12]}\n'
            (d / 'diff.txt').write_text(text)
        return row

    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(one, items))
    summary = {'tier': args.tier, 'rs': args.rs, 'rsRuns': rs_runs, 'goSha': go_sha, 'counts': dict(counts),
               'wallSecs': round(time.monotonic() - start, 1), 'items': dict(sorted(results.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'cli: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
