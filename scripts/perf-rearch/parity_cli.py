#!/usr/bin/env python3
"""CLI tier of the parity gate: real project configs on tsc-rs and Go, compared byte for byte.

usage: parity_cli.py --rs CMD --out DIR [--go BIN] [--tier quick|full] [--jobs N] [--rs-runs N]
                     [--only REGEX] [--projects FILE] [--cache DIR] [--label-rs NAME] [--list]

--rs is the binary under test, or a command split at spaces (`node .../tsc` for the js mode).
Projects and configs come from --projects (default parity-projects.json next to this file); a
project's first existing "dirs" entry is used, and a project with none is SKIP. A synthetic project
names a "fixture" of parity-fixtures.json instead: it is written once to a read-only
content-addressed dir (parity_inputs.fixture_tree, under the snapshot store's cli-fixtures/), which
is then its project dir. Each config runs the variants below (VARIANTS); quick runs the items the
quick manifest names (parity-quick-manifest.json "cli", parity_modes.py):

  noemit      -p CFG --noEmit --pretty false
  emit        -p CFG --noEmit false --pretty false   (every written file compared)
  decl        emit + --declaration --declarationMap --sourceMap
  incr        emit + --incremental, run twice (cold, then no-op)
  listfiles   --noEmit --listFiles
  extdiag     --noEmit --extendedDiagnostics; the Memory values are masked, the time lines are
              dropped (a time line shows only when its duration is not zero), and the padding
              of the statistics (it follows the widest value) is one space
  pretty      --noEmit --pretty true (colors and code frames)
  showconfig  --showConfig
  build       -b CFG --verbose --pretty false, run twice (cold, then up to date); only configs
              with "build": true

Opt-in variants run only on the configs whose "variants" list names them (the synthetic fixtures;
on the real projects their output would be megabytes):

  build-edit     -b CFG --verbose --explainFiles --traceResolution --pretty false, run twice: cold,
                 then after the config's "edit" ({"file": path from the project dir, "append":
                 text}: the file with the text appended, written into the overlay's upper dir)
  explain-trace  -p CFG --noEmit --explainFiles --traceResolution --pretty false

Every side of an item runs on a fresh overlay of the project's snapshot (lane H4: parity_inputs.Inputs,
a read-only copy of the tree made once per content) mounted at the project's own path (unshare -rm,
then overlayfs with the snapshot as the lower dir; the upper dir is
~/.cache/perf-rearch/parity/scratch/<item>/upper), so the configured outDir, declarationDir and
tsBuildInfoFile are used as they are, paths in output are the project's real paths, neither the
project nor its snapshot ever changes, another process writing into the project changes nothing a
run sees, and the written files are exactly the upper dir (sha256 each; a file the second run of
incr or build rewrites or touches counts again). Every snapshot is made before the first process and
watched with inotify while the tier runs (parity_watch.py); a change to one makes its project's items
ERROR and discards the tier's staged cache entries.

The second run of build (and incr) compares file mtimes that the first run wrote, and mtimes come
from the wall clock. When the wall clock steps during a side (here systemd-timesyncd stepped it back
about 1.14 s every 32 s; seen as a change of CLOCK_REALTIME - CLOCK_MONOTONIC), a later output can
be older than an earlier one, and Go and tsc-rs alike then rebuild ("is older than input"). Such a
side runs again, up to CLOCK_ATTEMPTS times; when every attempt saw a step, the item is an ERROR.

Go's result is cached in --cache (default ~/.cache/perf-rearch/parity/go-cache/<go sha>/cli), keyed
by the Go binary, the project path, the content of the snapshot (parity_inputs.tree_hash: sources,
configs, node_modules, every file but .git), config, arguments and the run's parameters (timeout,
overlay command). Each entry holds that key material and is read only when it matches. New Go and
JS entries are staged (parity_inputs.Stage) and published only when the tier's inputs verified clean
at its end. When the cache is made Go runs twice. tsc-rs runs --rs-runs times (default 1 for quick,
2 for full). Every tsc-rs process counts, also the attempts of a side that ran again because the
wall clock stepped (`attempts`): a crash or timeout in any of them sets rsFailure.

Compared per item: exit code, stdout, stderr and the written file tree (sha256 of each file).
Status: MATCH, GO-BROKEN, GO-PARTITION, JS-AGREES, DIFF, ERROR, SKIP. An item that is not MATCH
is judged in the order of parity_referees.py:
  1. two tsc-rs runs that differ: DIFF (rs-nondet), even when one matches Go;
  2. Go's two runs differ, or Go crashed or timed out: GO-BROKEN when tsc-rs neither crashes nor
     times out (else DIFF);
  3. Go with --checkers N, N in parity_referees.CHECKERS (twice per N, cached as <key>.p1-c<N>.json
     next to the default result): GO-PARTITION when Go's outputs differ across N and tsc-rs equals
     one (detail: the N); not for -b and showconfig items (parity_referees.blocker);
  4. JS tsc (--js, `node <js>`, once, cached in ~/.cache/perf-rearch/parity/js-cache/<js sha>/cli):
     JS-AGREES when JS sides with tsc-rs on every point where tsc-rs and Go differ;
  5. DIFF (detail: go-partition-miss and the JS check's reason when they ran, then the difference).
A GO-BROKEN, GO-PARTITION or JS-AGREES item with --rs-runs 1 runs tsc-rs once more first. Each such
item's row has `referees` (what the checks found) and `defaultDiff`. --no-referees (the js mode)
gives the old statuses: GO-UNSTABLE where Go's runs differ, DIFF otherwise. Output: DIR/results.json
({"items": {"cli/<project>/<config>/<variant>": {...}}}) and DIR/items/<item>/ (rs output and a diff
against Go) for each DIFF.
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
sys.path.insert(0, str(HERE))  # python3 -I does not put the script's dir on the path
import parity_inputs as inputs  # noqa: E402
import parity_modes as modes  # noqa: E402
import parity_referees as referees  # noqa: E402

HOME = Path.home()
SCRATCH = HOME / '.cache/perf-rearch/parity/scratch'
ENV = {k: v for k, v in os.environ.items() if not k.startswith(('GOPORT_', 'TSGO_', 'TS_'))}
ENV.pop('NODE_OPTIONS', None)
TIMEOUT = 1800
# Runs a command on an overlay at the project's path (lower: the project's snapshot, upper: what the
# run writes), in a user and mount namespace, so neither the project nor the snapshot changes and the
# written files are the upper dir. $1 lower, $2 upper, $3 work, $4 mount point, $5 cwd.
OVERLAY = ['unshare', '-rm', '--', 'sh', '-c',
           'mount -t overlay overlay -o "lowerdir=$1,upperdir=$2,workdir=$3" "$4" && cd "$5" && shift 5 && exec "$@"',
           'overlay']
CACHE_VERSION = 6  # bump when a change of this file changes Go's cached results (6: lane H4 snapshots, staged)
PARTITION_VERSION = 3  # the same, for the per-N Go results of the partition check
JS_CACHE_VERSION = 3  # the same, for JS tsc's results
# A side whose wall clock stepped by more than CLOCK_STEP_NS runs again (see the top comment).
CLOCK_STEP_NS = 5_000_000
CLOCK_ATTEMPTS = 6


def clock_offset():
    """CLOCK_REALTIME - CLOCK_MONOTONIC: constant except where the wall clock is stepped."""
    return time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(time.CLOCK_MONOTONIC)


class ClockStepped(Exception):
    pass

# name: (kind, extra args).
VARIANTS = {
    'noemit': ('plain', ['--noEmit', '--pretty', 'false']),
    'emit': ('emit', ['--pretty', 'false']),
    'decl': ('emit', ['--declaration', '--declarationMap', '--sourceMap', '--pretty', 'false']),
    'incr': ('incr', ['--incremental', '--pretty', 'false']),
    'listfiles': ('plain', ['--noEmit', '--listFiles', '--pretty', 'false']),
    'extdiag': ('plain', ['--noEmit', '--extendedDiagnostics', '--pretty', 'false']),
    'pretty': ('plain', ['--noEmit', '--pretty', 'true']),
    'showconfig': ('plain', ['--showConfig']),
    'build': ('build', ['--verbose', '--pretty', 'false']),
    'build-edit': ('build-edit', ['--verbose', '--explainFiles', '--traceResolution', '--pretty', 'false']),
    'explain-trace': ('plain', ['--noEmit', '--explainFiles', '--traceResolution', '--pretty', 'false']),
}
# Run only on the configs whose "variants" list names them (see the top comment).
OPT_IN = {'build-edit', 'explain-trace'}
# The kinds that run twice on one overlay, and the -b kinds.
TWO_RUNS = ('incr', 'build', 'build-edit')
BUILD_KINDS = ('build', 'build-edit')
FIXTURES = HERE / 'parity-fixtures.json'
FIXTURE_DIR_MODE, FIXTURE_FILE_MODE = 0o555, 0o444
FIXTURE_MTIME = 978307200  # 2001-01-01: older than anything a build writes
# -b --verbose starts each message with the wall clock: "06:12:55 PM - " (--pretty false) or
# "[6:12:55 PM] " (pretty).
CLOCK_RE = re.compile(rb'^(?:\[\d{1,2}:\d{2}:\d{2}(?: [AP]M)?\] |\d{1,2}:\d{2}:\d{2}(?: [AP]M)? - )', re.M)
MASK_RE = re.compile(rb'^((?:Memory used|Memory allocs|[A-Za-z /]* time)):[ \t]*\S+[ \t]*$', re.M)
# The statistics are padded to the widest value, which can be a masked one: one space after the colon.
STAT_RE = re.compile(rb'^([A-Za-z][A-Za-z /()]*):[ \t]+(\S+)[ \t]*$', re.M)


# A time line is printed only when its duration is not zero (Go's statistics.go, and tsc-rs's), so
# whether "Emit time" or "Bind time" shows depends on the clock: the time lines are dropped.
TIME_LINE_RE = re.compile(rb'^[A-Za-z /]* time:[^\n]*\n', re.M)


def mask_stats(out):
    return STAT_RE.sub(rb'\1: \2', TIME_LINE_RE.sub(b'', MASK_RE.sub(rb'\1: <masked>', out)))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def expand(p):
    return Path(os.path.expanduser(p))


def fixture_root(name):
    """The project dir of the synthetic project `name` of parity-fixtures.json (made when there is
    none; inputs.SnapshotError when the one there is not exactly the fixture)."""
    spec = json.loads(FIXTURES.read_text())
    if set(spec) != {'about', 'fixtures'} or name not in spec['fixtures']:
        raise ValueError(f'{FIXTURES}: no fixture {name!r} (the top level is exactly {{about, fixtures}})')
    tree, _ = inputs.fixture_tree(spec['fixtures'][name], inputs.SNAPSHOTS / 'cli-fixtures', FIXTURE_DIR_MODE,
                                  FIXTURE_FILE_MODE, FIXTURE_MTIME, name)
    return tree


def load_projects(path):
    spec = json.loads(Path(path).read_text())
    out = []
    for p in spec['projects']:
        if 'fixture' in p:
            root = fixture_root(p['fixture'])
        else:
            root = next((expand(d) for d in p['dirs'] if (expand(d) / p.get('marker', 'package.json')).exists()), None)
        out.append({**p, 'root': root})
    return out


def rs_failure(runs):
    """'timeout' or 'crash' when a tsc-rs run timed out or crashed, else None."""
    if any(r.code == 'timeout' for r in runs):
        return 'timeout'
    return 'crash' if any(referees.crashed(r) for r in runs) else None


def outcome(r):
    return 'timeout' if r.code == 'timeout' else 'crash' if referees.crashed(r) else f'exit {r.code}'



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
        self.clock_retries = 0
        self.inputs = None  # the tier's parity_inputs.Inputs
        self.attempts = []  # every process of a side, as (side label, attempt, run): [(cmd, Run)]

    def args(self):
        kind, extra = VARIANTS[self.variant]
        cfg = self.config['config']
        if kind in BUILD_KINDS:
            return ['-b', cfg, *extra]
        if kind in ('emit', 'incr'):
            return ['-p', cfg, '--noEmit', 'false', *extra]
        return ['-p', cfg, *extra]

    def run_side(self, cmd):
        """One side on a fresh overlay: one or two runs (incr, build), each a Run with the files it wrote.
        A two-run side during which the wall clock stepped runs again (see the top comment)."""
        kind = VARIANTS[self.variant][0]
        if kind not in TWO_RUNS:
            return self.run_side_once(cmd, kind)
        for _ in range(CLOCK_ATTEMPTS):
            before = clock_offset()
            runs = self.run_side_once(cmd, kind)
            step = clock_offset() - before
            if abs(step) <= CLOCK_STEP_NS:
                return runs
            self.clock_retries += 1
        raise ClockStepped(f'the wall clock stepped during each of {CLOCK_ATTEMPTS} attempts '
                           f'(last {step / 1e6:+.0f} ms)')

    def run_side_once(self, cmd, kind):
        clear(self.scratch)
        upper, work = self.scratch / 'upper', self.scratch / 'work'
        upper.mkdir(parents=True)
        work.mkdir()
        root = self.project['root']
        snap = self.inputs.of(root)
        cwd = Path(snap.label) / self.config.get('cwd', '.')
        before = {}
        runs = []
        for k in range(2 if kind in TWO_RUNS else 1):
            if k:
                time.sleep(0.02)  # mtime granularity between the cold and the second run
            if k and kind == 'build-edit':
                self.edit(snap, upper, work)
            code, out, err, secs = execute([*OVERLAY, str(snap.path), str(upper), str(work), snap.label, str(cwd),
                                            *cmd, *self.args()], self.scratch)
            changed = self.inputs.check(root)
            if changed:
                raise inputs.SnapshotError(changed)
            if self.variant == 'extdiag':
                out = mask_stats(out)
            if kind in BUILD_KINDS:
                out = CLOCK_RE.sub(b'<clock> - ', out)
            now = tree_of(upper)
            runs.append(Run(code, out, err, {f: sha256_file(upper / f) if v[0] >= 0 else 'deleted'
                                             for f, v in now.items() if before.get(f) != v}, secs))
            before = now
        self.attempts.append((cmd, runs))
        return runs

    def edit(self, snap, upper, work):
        """The config's edit between the two runs of build-edit: the text appended to the file (a path
        from the project dir) through the overlay, as a user's editor would write it."""
        edit = self.config['edit']
        rel = Path(edit['file'])
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError(f'edit {rel}: not a path inside the project')
        code, _, err, _ = execute([*OVERLAY, str(snap.path), str(upper), str(work), snap.label, snap.label,
                                   'sh', '-c', 'printf %s "$1" >> "$2"', 'edit', edit['append'], str(rel)],
                                  self.scratch, timeout=60)
        if code != 0:
            raise RuntimeError(f'edit {rel}: exit {code}: {err[-300:]!r}')


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


def run_key(runs):
    """A hashable form of a side (one or two runs): equal exactly when compare() finds no difference."""
    return json.dumps([r.key() for r in runs])


def cached_side(it, stage, cfile, material, cmd, count=2, cache_timeouts=True):
    """A side of an item run `count` times (the first one kept), staged for cfile with its key
    material: (runs, why the runs differ or None)."""
    gc = stage.load(cfile, material)
    if gc is None:
        sides = [it.run_side(cmd) for _ in range(count)]
        unstable = next((compare(sides[0], o) for o in sides[1:] if compare(sides[0], o)), None)
        gc = {'item': it.name, 'cmd': cmd, 'runs': [r.to_json() for r in sides[0]], 'unstable': unstable}
        if cache_timeouts or not any(r.code == 'timeout' for side in sides for r in side):
            stage.save(cfile, material, gc)
    runs = [Run.from_json(r) for r in gc['runs']]
    if it.variant == 'extdiag':  # caches written before the padding rule (idempotent)
        for r in runs:
            r.stdout = mask_stats(r.stdout)
    return runs, gc['unstable']


class Tier:
    """Runs and judges the items of one CLI tier run (see the top comment for the order of checks)."""

    def __init__(self, args, rs_cmd, rs_runs, go_sha, cache, out):
        self.args, self.rs_cmd, self.rs_runs, self.go_sha, self.cache, self.out = args, rs_cmd, rs_runs, go_sha, cache, out
        self.inputs = inputs.Inputs()
        self.stage = inputs.Stage('cli')
        self.js_sha, self.js_error = None, None
        if args.referees:
            try:
                self.js_sha = referees.js_sha(args.js)
            except (OSError, ValueError) as e:  # no JS 6.0.3: no JS-AGREES (every JS check fails)
                self.js_error = str(e)
        self.js_cache = HOME / f'.cache/perf-rearch/parity/js-cache/{self.js_sha}/cli'

    def material(self, it, *extra):
        """The key material of an item's evidence: what the run reads (the snapshot's content and the
        path it is mounted at) and how it runs (arguments, config, timeout, the overlay command), with
        the producer in `extra`."""
        root = it.project['root']
        snap = self.inputs.of(root)
        return [*extra, str(root), snap.label, snap.hash, it.config, it.variant, it.args(), TIMEOUT, OVERLAY]

    def key(self, it, *extra):
        return hashlib.sha256(json.dumps(self.material(it, *extra)).encode()).hexdigest()[:24]

    def run_item(self, it):
        it.inputs = self.inputs
        key = self.key(it, CACHE_VERSION, self.go_sha)
        go_runs, go_unstable = cached_side(it, self.stage, self.cache / f'{key}.json',
                                           self.material(it, CACHE_VERSION, self.go_sha), [self.args.go])
        it.attempts = []  # from here: tsc-rs's processes only
        rs_all = [it.run_side(self.rs_cmd) for _ in range(self.rs_runs)]
        clear(it.scratch)
        row = {'goSecs': round(sum(r.secs for r in go_runs), 1),
               'rsSecs': [round(sum(r.secs for r in runs), 1) for runs in rs_all]}
        diffs = [compare(go_runs, runs) for runs in rs_all]
        nondet = next((compare(rs_all[0], runs) for runs in rs_all[1:] if compare(rs_all[0], runs)), None)
        status, detail, extra = self.judge(it, key, go_runs, go_unstable, rs_all, diffs[0], nondet)
        clear(it.scratch)
        # Every tsc-rs process counts: the side's runs, a side run again after a clock step, and a
        # confirming run. A crash or timeout in any of them is a failure, whatever the final runs say.
        rs_procs = [r for cmd, runs in it.attempts if cmd == self.rs_cmd for r in runs]
        failure = rs_failure(rs_procs)
        if failure and status != 'DIFF':
            status, detail = 'DIFF', f'tsc-rs {failure} in an attempt ({", ".join(map(outcome, rs_procs))})'
        row.update(status=status, **({'detail': detail} if detail else {}), **extra)
        if failure:
            row['rsFailure'] = failure
        if status != 'MATCH' or len(rs_procs) > sum(map(len, rs_all)):
            row['attempts'] = [{'outcome': outcome(r), 'code': r.code, 'secs': round(r.secs, 2)} for r in rs_procs]
        if it.clock_retries:
            row['clockRetries'] = it.clock_retries
        if status == 'DIFF':
            self.write_diff(it, go_runs, rs_all, diffs, nondet)
        return row

    def judge(self, it, key, go_runs, go_unstable, rs_all, diff, nondet):
        """(status, detail, extra row fields) in the order of parity_referees."""
        label = self.args.label_rs
        if nondet:
            return 'DIFF', f'rs-nondet: {nondet}', {}
        if not go_unstable and not diff:
            return 'MATCH', None, {}
        if not self.args.referees:
            if go_unstable:
                return 'GO-UNSTABLE', f'Go runs differ: {go_unstable}; {label} {"equals" if not diff else "differs from"} Go run 1', {}
            return 'DIFF', diff, {}
        reason = referees.go_broken_reason(go_runs, go_unstable)
        if reason:
            (status, detail), extra = referees.judge_broken(reason, rs_all[0]), {}
        else:
            status, detail, info = referees.referee(
                it.args(), lambda n: self.go_at_n(it, key, n), lambda: self.js_side(it), go_runs, rs_all[0],
                run_key(rs_all[0]), self.args.referee_all)
            extra = {'referees': info, 'defaultDiff': diff}
            if status == 'DIFF':
                detail = f'{detail}: {diff}' if detail else diff
        if status in referees.REFEREED and len(rs_all) < 2:  # determinism first: one more tsc-rs run
            again = compare(rs_all[0], it.run_side(self.rs_cmd))
            if again:
                return 'DIFF', f'rs-nondet: {again}', extra
        return status, detail, extra

    def go_at_n(self, it, key, n):
        runs, unstable = cached_side(it, self.stage, self.cache / f'{key}.p{PARTITION_VERSION}-c{n}.json',
                                     self.material(it, CACHE_VERSION, self.go_sha, 'p', PARTITION_VERSION, n),
                                     [self.args.go, *referees.go_args(n)], cache_timeouts=False)
        if any(r.code == 'timeout' for r in runs):
            return referees.FAILED
        return referees.UNSTABLE if unstable else run_key(runs)

    def js_side(self, it):
        """JS tsc's side (one run: it is single-threaded), cached unless it timed out; None when the
        JS referee is not typescript 6.0.3 (referees.js_identity)."""
        if self.js_error:
            return None
        cfile = self.js_cache / f'{self.key(it, JS_CACHE_VERSION, self.js_sha)}.json'
        runs, _ = cached_side(it, self.stage, cfile, self.material(it, JS_CACHE_VERSION, self.js_sha),
                              ['node', self.args.js], count=1, cache_timeouts=False)
        return runs

    def write_diff(self, it, go_runs, rs_all, diffs, nondet):
        label, rs_cmd = self.args.label_rs, self.rs_cmd
        d = self.out / 'items' / it.name.replace('/', '__')
        d.mkdir(parents=True, exist_ok=True)
        bad = next(runs for runs, df in zip(rs_all, diffs + [nondet]) if df) if any(diffs) else rs_all[-1]
        (d / 'rs.json').write_text(json.dumps([r.to_json() for r in bad], indent=1))
        (d / 'cmd.txt').write_text(f'cd {it.project["root"] / it.config.get("cwd", ".")} && {shlex.join([*rs_cmd, *it.args()])}'
                                   f'   # writes into the project: run it on a copy or an overlay '
                                   f'(the gate ran it on the snapshot {self.inputs.of(it.project["root"]).path})\n')
        text = ''
        for k, (g, r) in enumerate(zip(go_runs, bad)):
            text += text_diff(g.stdout, r.stdout, f'go run{k + 1} stdout', f'{label} stdout')
            text += text_diff(g.stderr, r.stderr, f'go run{k + 1} stderr', f'{label} stderr')
            for n in sorted(set(g.tree) | set(r.tree)):
                if g.tree.get(n) != r.tree.get(n):
                    text += f'file {n}: go {g.tree.get(n, "-")[:12]} {label} {r.tree.get(n, "-")[:12]}\n'
        (d / 'diff.txt').write_text(text)


def _snapshot_error(inp, root):
    try:
        inp.of(root)
    except inputs.SnapshotError as e:
        return str(e)
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
    ap.add_argument('--js', default=str(referees.JS_TSC), help='JS tsc for JS-AGREES (run with node)')
    ap.add_argument('--referees', action=argparse.BooleanOptionalAction, default=True,
                    help='GO-BROKEN, GO-PARTITION and JS-AGREES (off for the js mode: GO-UNSTABLE and DIFF as before)')
    ap.add_argument('--referee-all', action='store_true',
                    help='run the JS check even where GO-PARTITION decided (recorded in referees.js)')
    ap.add_argument('--snapshots', help=f'the snapshot store (default {inputs.SNAPSHOTS}; the self-tests use their own)')
    args = ap.parse_args()
    if args.snapshots:
        inputs.SNAPSHOTS = Path(args.snapshots)
    rs_cmd = shlex.split(args.rs)
    rs_runs = args.rs_runs or (1 if args.tier == 'quick' else 2)
    go_sha = sha256_file(args.go)[:16]
    cache = Path(args.cache) if args.cache else HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}/cli'
    out = Path(args.out)
    projects = load_projects(args.projects)
    quick_items = modes.working_manifest().cli if args.tier == 'quick' else None
    items, skipped = [], {}
    for p in projects:
        for c in p['configs']:
            for v, (kind, _) in VARIANTS.items():
                if v == 'build' and not c.get('build'):
                    continue
                if v in OPT_IN and v not in c.get('variants', []):
                    continue
                if v in c.get('skipVariants', []):
                    continue
                if quick_items is not None and f'cli/{p["name"]}/{c["label"]}/{v}' not in quick_items:
                    continue
                name = f'cli/{p["name"]}/{c["label"]}/{v}'
                if not re.search(args.only, name):
                    continue
                if p['root'] is None:
                    skipped[name] = {'status': 'SKIP', 'detail': 'project not prepared'}
                elif not (p['root'] / c.get('cwd', '.') / c['config']).exists():
                    skipped[name] = {'status': 'SKIP', 'detail': 'config not in this project dir'}
                else:
                    items.append(Item(p, c, v))
    if args.list:
        for it in items:
            print(it.name, shlex.join(it.args()), f'(cwd {it.config.get("cwd", ".")})')
        for n in skipped:
            print(n, 'SKIP')
        return
    out.mkdir(parents=True, exist_ok=True)
    tier = Tier(args, rs_cmd, rs_runs, go_sha, cache, out)
    results = dict(skipped)
    lock = threading.Lock()
    counts = Counter(r['status'] for r in skipped.values())
    start = time.monotonic()
    snap_start = time.monotonic()
    roots = sorted({str(it.project['root']) for it in items})
    with ThreadPoolExecutor(4) as pool:  # every snapshot before the first process, then the watch
        for root, err in zip(roots, pool.map(lambda r: _snapshot_error(tier.inputs, r), roots)):
            if err:
                print(f'cli: no snapshot of {root}: {err}', flush=True)
    tier.inputs.watch()
    snap_secs = round(time.monotonic() - snap_start, 1)
    print(f'cli: {len(tier.inputs.snaps)} snapshots in {snap_secs}s; '
          f'{tier.inputs.watch_error or "watched with inotify"}', flush=True)

    def one(it):
        try:
            row = tier.run_item(it)
        except Exception as e:  # a harness error, never a match
            row = {'status': 'ERROR', 'detail': repr(e)[:400]}
        with lock:
            results[it.name] = row
            counts[row['status']] += 1
            print(f'{row["status"]:<11} {it.name} {row.get("detail", "")} '
                  f'(go {row.get("goSecs", "-")}s, rs {row.get("rsSecs", "-")}s)', flush=True)

    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(one, items))
    verify_start = time.monotonic()
    changed = tier.inputs.verify()
    verify_secs = round(time.monotonic() - verify_start, 1)
    for it in items:
        why = changed.get(str(it.project['root']))
        if why and results[it.name]['status'] != 'ERROR':
            counts[results[it.name]['status']] -= 1
            results[it.name] = {'status': 'ERROR', 'detail': why, 'was': results[it.name]}
            counts['ERROR'] += 1
            print(f'ERROR       {it.name} {why}', flush=True)
    counts = +counts
    summary = {'tier': args.tier, 'rs': args.rs, 'rsRuns': rs_runs, 'goSha': go_sha, 'counts': dict(counts),
               'inputs': tier.inputs.hashes, 'snapshots': {r: s.as_json() for r, s in tier.inputs.snaps.items()},
               'watch': tier.inputs.watch_error or 'inotify', 'changed': changed,
               'secs': {'snapshots': snap_secs, 'verify': verify_secs},
               'jsSha': tier.js_sha, **({'jsError': tier.js_error} if tier.js_error else {}),
               'wallSecs': round(time.monotonic() - start, 1), 'items': dict(sorted(results.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    if changed:  # an invalidated run: none of its new cache entries is evidence
        print(f'cli: inputs changed ({len(changed)} roots): {tier.stage.discard()} staged cache entries discarded')
    else:
        print(f'cli: {tier.stage.publish()} cache entries published')
    print(f'cli: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
