#!/usr/bin/env python3
"""LSP tier of the parity gate: vendor/lspdiff sessions on tsc-rs against cached Go LSP sessions.

usage: parity_lsp.py --rs BIN --out DIR [--go BIN] [--tier quick|full] [--only REGEX] [--jobs 4]
                     [--js TSSERVER | --no-js] [--js-all]

Standard sessions (lspdiff's session.mjs; project, files, positions): 12 files and 6 positions per
file (lspdiff's defaults are 6 and 4) on umami and ts-pattern, and in full also on hono. Watch
scenarios (scenario.mjs; the client advertises dynamic didChangeWatchedFiles registration and
refresh support, and registrations, pushed diagnostics and every other server message are compared
at checkpoints): fsevents, fsevents-narrow and pkgjson-overlay (the watch-small fixture, written
fresh into ~/.cache/perf-rearch/parity/scratch/lsp-<session> before each server run),
watch-ts-pattern-f12 and unsaved-ts-pattern; full adds watch-umami-f12, watch-narrow-umami-f12 (the
workspace folder is umami/src), watch-hono-f12 and second-project-query. Up to --jobs sessions run
side by side. quick's
sessions are all full sessions, so a quick run compares with a full base.

Go's four sessions per item set (go.jsonl to go4.jsonl, GO_RUNS) are kept in ~/.cache/perf-rearch/parity/
go-cache/<go sha>/lsp/<session>-<project fingerprint>-<lspdiff sources sha>/ (parity_cli.fingerprint:
commit, node_modules mtimes; the sha of the lspdiff sources that script the session, SESSION_SOURCES)
and reused, so a run starts only tsc-rs. Standard sessions run with ATA off (vendor/lspdiff
client.mjs initializeParams). In full, tsc-rs runs three times: twice as is and once with
GOPORT_PARSE_THREADS=1. An item is judged in this order (docs/perf-rearch/parity.md, "JS tsserver as
the LSP referee"):
1. tsc-rs determinism: an answer on which a later tsc-rs run differs from the first is a DIFF with
   detail rs-nondet or rs-schedule, even when one run matches Go.
2. GO-BROKEN: Go's own sessions differ on the answer (Go's references order and scenario
   registrations race across its goroutines). This is the old GO-UNSTABLE: in full, where step 1
   ran, it is GO-BROKEN; in quick, where tsc-rs ran once, it stays GO-UNSTABLE (not compared).
3. JS-AGREES: a DIFF of a kind JS referees (referee.mjs REFEREED_KINDS), on which JS tsserver 6.0.3
   (tsserver.mjs; --js, default ~/.cache/perf-rearch/js-tsc) sides with tsc-rs on every point where
   tsc-rs differs from Go. JS replays the session only when the session has such a DIFF; its log is
   cached in ~/.cache/perf-rearch/parity/js-cache/<tsserver sha>/lsp/<session>-<project
   fingerprint>-<sources sha>/. With one tsc-rs run (quick), tsc-rs runs once more to confirm.
4. Otherwise DIFF (the item's js.reason says why JS did not decide, when it ran).
--js-all runs JS on every session: it records JS's view of every item that is not MATCH (also
GO-BROKEN ones, for the report) and the calibration (per kind: on how many answers where tsc-rs
equals Go JS's normalized answer equals Go's too) in results.json "jsCalibration".
Items: lsp/<session>/<index> <step> (index: the answer's place in the session). Status MATCH,
GO-BROKEN, JS-AGREES, DIFF, GO-UNSTABLE (quick); a session that fails is one ERROR item. Output:
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
from parity_cli import fingerprint  # noqa: E402

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
         scen('watch-ts-pattern-f12', 'watch', 'ts-pattern'), scen('unsaved-ts-pattern', 'unsaved', 'ts-pattern')]
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
# GO-UNSTABLE. Part of the cache key, so caches filled with fewer runs are not reused.
GO_RUNS = 4
RS_RUNS = {'quick': [], 'full': ['--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1']}
JS_TSSERVER = HOME / '.cache/perf-rearch/js-tsc/node_modules/typescript/lib/tsserver.js'
# The lspdiff sources that script a session, so Go's (and JS's) answers depend on them; the diff and
# referee code (lspdiff.mjs, referee.mjs) and the bench scripts do not change a cached answer.
SESSION_SOURCES = ('client.mjs', 'fixtures.mjs', 'project.mjs', 'scenario.mjs', 'session.mjs')


def scenario_sources_sha(extra=()):
    h = hashlib.sha256()
    for name in sorted(SESSION_SOURCES + tuple(extra)):
        f = HERE / 'vendor/lspdiff' / name
        h.update(f.name.encode() + b'\0' + f.read_bytes())
    return h.hexdigest()[:12]


def tsserver_sha(path):
    """tsserver's identity: tsserver.js loads _tsserver.js, which loads typescript.js."""
    h = hashlib.sha256()
    lib = Path(path).resolve().parent
    for name in (Path(path).name, '_tsserver.js', 'typescript.js'):
        if (lib / name).exists():
            h.update(name.encode() + b'\0' + (lib / name).read_bytes())
    return h.hexdigest()[:16]


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
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha = sha256_file(args.go)[:16]
    items, sessions = {}, {}
    t0 = time.monotonic()
    src_sha = scenario_sources_sha()
    js_src_sha = scenario_sources_sha(('tsserver.mjs',))
    js_sha = tsserver_sha(args.js) if use_js else None

    def run_one(sess):
        """One session: (its items, its summary, the line to print)."""
        name, project, scenario = sess['name'], sess['project'], sess.get('scenario')
        if project is FIXTURE:
            root = HOME / f'.cache/perf-rearch/parity/scratch/lsp-{name}'
            key = f'{name}-fixture-{src_sha}-g{GO_RUNS}'
            js_key = f'{name}-fixture-{js_src_sha}'
        else:
            root = next((Path(os.path.expanduser(d)) for d in PROJECTS[project] if Path(os.path.expanduser(d)).is_dir()), None)
            if root is None:
                return {f'lsp/{name}': {'status': 'SKIP', 'detail': 'project not prepared'}}, None, f'lsp {name}: SKIP'
            key = f'{name}-{fingerprint(root)}-{src_sha}-g{GO_RUNS}'
            js_key = f'{name}-{fingerprint(root)}-{js_src_sha}'
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
            if row['status'] == 'GO-UNSTABLE' and args.tier == 'full':
                # Step 2: Go's runs differ and tsc-rs's three runs agree (step 1 made rs-nondet and
                # rs-schedule DIFFs before this).
                item['status'] = 'GO-BROKEN'
                item['detail'] = 'Go runs differ'
            if row.get('detail'):
                item['detail'] = row['detail']
            if row.get('js'):
                item['referees'] = {'js': row['js']}
            got[f'lsp/{name}/{row["i"]:04d} {row["step"]}'] = item
        c = Counter(v['status'] for v in got.values())
        js_file = produced.parent / 'js.json'
        js_info = json.loads(js_file.read_text()) if js_file.exists() else {}
        runs = dict(re.findall(r'^(go\d*|rs[\w-]*)(?: \(.*\))?: \d+ answers in ([\d.]+) s', r.stdout, re.M))
        runs.update(re.findall(r'^(js|rs-confirm): \d+ answers in ([\d.]+) s', r.stdout, re.M))
        info = {'secs': secs, 'answers': len(rows), 'counts': dict(c), 'root': str(root),
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
    counts = Counter(v['status'] for v in items.values())
    summary = {'tier': args.tier, 'rs': args.rs, 'goSha': go_sha, 'counts': dict(counts), 'sessions': sessions,
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
