#!/usr/bin/env python3
"""LSP tier of the parity gate: vendor/lspdiff sessions on tsc-rs against cached Go LSP sessions.

usage: parity_lsp.py --rs BIN --out DIR [--go BIN] [--tier quick|full] [--only REGEX] [--jobs 4]
                     [--js TSSERVER | --no-js] [--js-all]

Standard sessions (lspdiff's session.mjs; project, files, positions): 12 files and 6 positions per
file (lspdiff's defaults are 6 and 4) on umami and ts-pattern, and in full also on hono. Watch
scenarios (scenario.mjs; the client advertises dynamic didChangeWatchedFiles registration and
refresh support, and registrations, pushed diagnostics and every other server message are compared
at checkpoints): fsevents, fsevents-narrow and pkgjson-overlay (the watch-small fixture, written
fresh into ~/.cache/perf-rearch/parity/scratch/lsp-<session> before each server run), ata-overlay
(the ata-small fixture with ATA on against a private typings cache and the stand-in npm
vendor/lspdiff/ata-npm), watch-ts-pattern-f12 and unsaved-ts-pattern; full adds watch-umami-f12, watch-narrow-umami-f12 (the
workspace folder is umami/src), watch-hono-f12 and second-project-query. Up to --jobs sessions run
side by side. quick's
sessions are all full sessions, so a quick run compares with a full base.

Go's four sessions per item set (go.jsonl to go4.jsonl, GO_RUNS) are kept in ~/.cache/perf-rearch/parity/
go-cache/<go sha>/lsp/<session>-<key>/ and reused, so a run starts only tsc-rs. The key (session_key)
is the sha of what the session reads and how it runs: the session's definition (project, files,
positions, scenario), the resolved project dir (its realpath) and the content of its whole tree
(parity_inputs.tree_hash), for scenarios also the typings cache their ATA reads
(~/.cache/typescript/7.1), the lspdiff sources that script the session (SESSION_SOURCES), the
harness settings that change answers (LSPDIFF_SETTLE_MS, LSPDIFF_REQUEST_TIMEOUT_MS) and GO_RUNS. The
trees are hashed when the tier starts and again when it ends; a session whose inputs changed while
the tier ran is an ERROR. Standard sessions run with ATA off (vendor/lspdiff
client.mjs initializeParams). In full, tsc-rs runs three times: twice as is and once with
GOPORT_PARSE_THREADS=1. An item is judged in this order (docs/perf-rearch/parity.md, "JS tsserver as
the LSP referee"):
1. tsc-rs determinism: a tsc-rs run that crashed or timed out, or an answer on which a later tsc-rs
   run differs from the first, is a DIFF (detail "tsc-rs: ...", rs-nondet or rs-schedule), even
   when one run matches Go.
2. GO-BROKEN: a Go run crashed or timed out before the answer (vendor/lspdiff client.mjs: no answer
   in LSPDIFF_REQUEST_TIMEOUT_MS, default 300 s), or Go's own runs differ on it (Go's references
   order and scenario registrations race across its goroutines). The last case is the old
   GO-UNSTABLE status, which the lsp tier no longer reports. A Go crash is cached, a timeout is not.
3. JS-AGREES: a DIFF of a kind JS referees (referee.mjs REFEREED_KINDS), on which JS tsserver 6.0.3
   (tsserver.mjs; --js, default ~/.cache/perf-rearch/js-tsc) sides with tsc-rs on every point where
   tsc-rs differs from Go. JS replays the session only when the session has such a DIFF; its log is
   cached in ~/.cache/perf-rearch/parity/js-cache/<tsserver identity>/lsp/<session>-<key>/ (the same
   key, with tsserver.mjs among the sources; parity_referees.js_identity: the whole typescript
   6.0.3 package).
   With one tsc-rs run (quick), an answer that would be GO-BROKEN or JS-AGREES needs one more tsc-rs
   run (rs-confirm) that gives it again; otherwise it is a DIFF rs-nondet.
4. Otherwise DIFF (the item's js.reason says why JS did not decide, when it ran).
--js-all runs JS on every session: it records JS's view of every item that is not MATCH (also
GO-BROKEN ones, for the report) and the calibration (per kind: on how many answers where tsc-rs
equals Go JS's normalized answer equals Go's too) in results.json "jsCalibration".
Items: lsp/<session>/<index> <step> (index: the answer's place in the session). Status MATCH,
GO-BROKEN, JS-AGREES, DIFF; a session that fails is one ERROR item. Output:
DIR/results.json and DIR/<session>/ (lspdiff's files: mismatches.json, summary.json, rs.jsonl,
js.json, js.jsonl when JS ran).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
import parity_inputs as inputs  # noqa: E402
import parity_referees as referees  # noqa: E402

HERE = Path(__file__).resolve().parent
HOME = Path.home()
PROJECTS = {
    'umami': ['~/.cache/perf-rearch/projects/umami', '~/.cache/lspdiff/projects/umami'],
    'ts-pattern': ['~/.cache/perf-rearch/parity-projects/repos/gvergnaud__ts-pattern'],
    'hono': ['~/.cache/perf-rearch/parity-projects/repos/honojs__hono'],
    'query': ['~/.cache/perf-rearch/projects/query', '~/.cache/perf-rearch/parity-projects/repos/TanStack__query'],
}
FIXTURE = None  # a scenario on a fixture: its project dir is the scratch dir


def std(project):
    return {'name': f'{project}-f12-p6', 'project': project, 'files': 12, 'positions': 6}


def scen(name, scenario, project=FIXTURE, files=12):
    return {'name': name, 'project': project, 'files': files, 'scenario': scenario}


QUICK = [std('umami'), std('ts-pattern'),
         scen('fsevents', 'fsevents'), scen('fsevents-narrow', 'fsevents-narrow'), scen('pkgjson-overlay', 'pkgjson-overlay'),
         scen('watch-ts-pattern-f12', 'watch', 'ts-pattern'), scen('unsaved-ts-pattern', 'unsaved', 'ts-pattern'),
         scen('ata-overlay', 'ata-overlay')]
SESSIONS = {
    'quick': QUICK,
    'full': QUICK[:2] + [std('hono')] + QUICK[2:] + [
        scen('watch-umami-f12', 'watch', 'umami'), scen('watch-narrow-umami-f12', 'watch-narrow', 'umami'),
        scen('watch-hono-f12', 'watch', 'hono'),
        scen('second-project-query', 'second-project', 'query')],
}
# The tsc-rs runs per session: lspdiff options. full: two plain runs (rs-nondet) and one with a
# single parse worker (rs-schedule).
# Go sessions per item set. Go's own answers vary between runs on some items (hono `references`
# order: ls/crossproject.go handleCrossProject searches projects in goroutines; scenario watch
# registrations), so every session runs Go four times and an answer any two runs disagree on is
# GO-BROKEN. Part of the cache key, so caches filled with fewer runs are not reused.
GO_RUNS = 4
RS_RUNS = {'quick': [], 'full': ['--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1']}
JS_TSSERVER = HOME / '.cache/perf-rearch/js-tsc/node_modules/typescript/lib/tsserver.js'
# The lspdiff sources that script a session, so Go's (and JS's) answers depend on them; the diff and
# referee code (lspdiff.mjs, referee.mjs) and the bench scripts do not change a cached answer.
SESSION_SOURCES = ('client.mjs', 'fixtures.mjs', 'project.mjs', 'scenario.mjs', 'session.mjs', 'ata-npm/npm', 'ata-npm/fake-npm.mjs')


def scenario_sources_sha(extra=()):
    h = hashlib.sha256()
    for name in sorted(SESSION_SOURCES + tuple(extra)):
        f = HERE / 'vendor/lspdiff' / name
        h.update(name.encode() + b'\0' + f.read_bytes())
    return h.hexdigest()[:12]


TYPINGS = HOME / '.cache/typescript/7.1'  # the ATA typings cache of a server with the default XDG_CACHE_HOME
HARNESS_ENV = ('LSPDIFF_SETTLE_MS', 'LSPDIFF_REQUEST_TIMEOUT_MS')


def session_key(sess, root, snapshot, sources_sha, extra=()):
    """The cache key of a session's Go (or JS) logs: 24 hex digits over everything its answers depend
    on (see the top comment). A fixture session's root is its scratch dir, rewritten from
    fixtures.mjs (in the sources) before every server run, so its tree is not hashed."""
    material = {'session': sess, 'root': str(root), 'real': os.path.realpath(root), 'sources': sources_sha,
                'env': {k: os.environ.get(k) for k in HARNESS_ENV}, 'extra': list(extra)}
    if sess['project'] is not FIXTURE:
        material['tree'] = snapshot.of(root)
    if sess.get('scenario') and TYPINGS.is_dir():
        material['typings'] = snapshot.of(TYPINGS)
    return inputs.json_hash(material)[:24]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rs', required=True)
    ap.add_argument('--go', default=str(HOME / '.local/bin/tsgo-lsp-oracle-3e9236361fa9'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--tier', default='full', choices=('quick', 'full'))
    ap.add_argument('--only', default='')
    ap.add_argument('--jobs', type=int, default=4, help='sessions run side by side (default 4)')
    ap.add_argument('--js', default=str(JS_TSSERVER), help='JS tsserver.js, the JS-AGREES referee')
    ap.add_argument('--no-js', action='store_true', help='no JS referee (statuses MATCH, GO-BROKEN, DIFF)')
    ap.add_argument('--js-all', action='store_true', help='run JS on every session: calibration and the report')
    args = ap.parse_args()
    use_js = not args.no_js and Path(args.js).exists()
    js_error = None
    if use_js:
        try:
            js_sha = referees.js_identity(args.js)
        except (OSError, ValueError) as e:  # no JS 6.0.3: no JS referee
            use_js, js_error = False, str(e)
            print(f'lsp: JS referee unavailable: {js_error}', flush=True)
    snapshot = inputs.Snapshot()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha = sha256_file(args.go)[:16]
    items, sessions = {}, {}
    t0 = time.monotonic()
    src_sha = scenario_sources_sha()
    js_src_sha = scenario_sources_sha(('tsserver.mjs',))
    js_sha = js_sha if use_js else None

    def run_one(sess):
        """One session: (its items, its summary, the line to print)."""
        name, project, scenario = sess['name'], sess['project'], sess.get('scenario')
        if project is FIXTURE:
            root = HOME / f'.cache/perf-rearch/parity/scratch/lsp-{name}'
        else:
            root = next((Path(os.path.expanduser(d)) for d in PROJECTS[project] if Path(os.path.expanduser(d)).is_dir()), None)
            if root is None:
                return {f'lsp/{name}': {'status': 'SKIP', 'detail': 'project not prepared'}}, None, f'lsp {name}: SKIP'
        key = f'{name}-{session_key(sess, root, snapshot, src_sha, [f"g{GO_RUNS}"])}'
        js_key = f'{name}-{session_key(sess, root, snapshot, js_src_sha)}'
        cache = HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}/lsp/{key}'
        sout = out / name
        # A heap above Node's default: umami's 7 logs are 340 MB each, and JS adds one.
        cmd = ['node', '--max-old-space-size=12288', str(HERE / 'vendor/lspdiff/lspdiff.mjs'), 'diff', '--project', str(root),
               '--files', str(sess['files']), '--go', args.go, '--rs', args.rs, '--out', str(sout),
               '--go-cache', str(cache), *RS_RUNS[args.tier]]
        cmd += ['--go-runs', str(GO_RUNS)]
        if use_js:
            cmd += ['--js', args.js, '--js-cache', str(HOME / f'.cache/perf-rearch/parity/js-cache/{js_sha}/lsp/{js_key}')]
            cmd += ['--js-all'] if args.js_all else []
        cmd += ['--scenario', scenario] if scenario else ['--positions', str(sess['positions'])]
        start = time.monotonic()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        secs = round(time.monotonic() - start, 1)
        (out / f'{name}.log').write_text(r.stdout + r.stderr)
        produced = sout / f'{root.name}-diff{"-" + scenario if scenario else ""}' / 'items.json'
        if r.returncode or not produced.exists():
            return ({f'lsp/{name}': {'status': 'ERROR', 'detail': f'lspdiff exit {r.returncode}: {r.stderr[-300:]}'}},
                    {'secs': secs, 'error': True}, f'lsp {name}: ERROR (exit {r.returncode}) {secs}s')
        rows = json.loads(produced.read_text())
        got = {}
        for row in rows:
            item = {'status': row['status'], 'method': row['method']}
            if row.get('detail'):
                item['detail'] = row['detail']
            if row.get('rsFailure'):
                item['rsFailure'] = row['rsFailure']
            if row.get('js'):
                item['referees'] = {'js': row['js']}
            got[f'lsp/{name}/{row["i"]:04d} {row["step"]}'] = item
        c = Counter(v['status'] for v in got.values())
        js_file = produced.parent / 'js.json'
        js_info = json.loads(js_file.read_text()) if js_file.exists() else {}
        runs = dict(re.findall(r'^(go\d*|rs[\w-]*)(?: \(.*\))?: \d+ answers in ([\d.]+) s', r.stdout, re.M))
        runs.update(re.findall(r'^(js|rs-confirm): \d+ answers in ([\d.]+) s', r.stdout, re.M))
        info = {'secs': secs, 'answers': len(rows), 'counts': dict(c), 'root': str(root), 'key': key,
                'runSecs': {k: float(v) for k, v in runs.items()}}
        if js_info.get('ran'):
            info['js'] = {'calibration': js_info.get('calibration', {}), 'decided': js_info.get('decided', 0),
                          'cached': bool(re.search(r'^js: \d+ answers from ', r.stdout, re.M))}
        return got, info, f'lsp {name}: {dict(c)} {secs}s'

    chosen = [sess for sess in SESSIONS[args.tier] if re.search(args.only, sess['name'])]
    # Sessions run side by side (each server run is its own process; fixture sessions have their own
    # scratch dirs). Most of a scenario's time is the settle wait at its checkpoints.
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, sess): sess['name'] for sess in chosen}
        for f in as_completed(futures):
            got, info, line = f.result()
            items.update(got)
            if info is not None:
                sessions[futures[f]] = info
            print(line, flush=True)
    changed = snapshot.verify()
    for sess in chosen:
        info = sessions.get(sess['name'], {})
        why = next((w for r, w in changed.items() if r == info.get('root') or (sess.get('scenario') and r == str(TYPINGS))), None)
        if why and info.get('root'):
            for k in [k for k in items if k.startswith(f'lsp/{sess["name"]}/')]:
                items[k] = {'status': 'ERROR', 'detail': why, 'was': items[k]}
            print(f'lsp {sess["name"]}: ERROR {why}', flush=True)
    counts = Counter(v['status'] for v in items.values())
    summary = {'tier': args.tier, 'rs': args.rs, 'goSha': go_sha, 'counts': dict(counts), 'sessions': sessions,
               'inputs': snapshot.hashes, **({'jsError': js_error} if js_error else {}),
               'wallSecs': round(time.monotonic() - t0, 1)}
    if use_js:
        summary['jsSha'] = js_sha
        calib = {}
        for info in sessions.values():
            for method, c in info.get('js', {}).get('calibration', {}).items():
                acc = calib.setdefault(method, {'goEqual': 0, 'jsEqual': 0, 'noJs': 0})
                for k in acc:
                    acc[k] += c.get(k, 0)
        if calib:
            summary['jsCalibration'] = calib
            for method, c in sorted(calib.items()):
                print(f'js calibration {method}: {c["jsEqual"]}/{c["goEqual"]} '
                      f'({100 * c["jsEqual"] / max(c["goEqual"], 1):.1f}%){" no JS answer " + str(c["noJs"]) if c["noJs"] else ""}')
    for k, v in sorted(items.items()):
        if v['status'] == 'JS-AGREES' or (args.js_all and v.get('referees', {}).get('js') and v['status'] != 'MATCH'):
            js = v['referees']['js']
            if 'equalsRs' in js:
                said = (f'normalized, JS equals tsc-rs: {js["equalsRs"]}; JS equals Go runs: {",".join(js["equalsGoRuns"]) or "none"}; '
                        f'tsc-rs equals Go runs: {",".join(js["rsEqualsGoRuns"]) or "none"}')
            else:
                said = 'agrees' if js.get('agrees') else js.get('reason', '')
            print(f'js {v["status"]:<11} {k}: {said}'[:400])
    summary['items'] = dict(sorted(items.items()))
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'lsp: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
