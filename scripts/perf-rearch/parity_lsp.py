#!/usr/bin/env python3
"""LSP tier of the parity gate: vendor/lspdiff sessions on tsc-rs against cached Go LSP sessions.

usage: parity_lsp.py --rs BIN --out DIR [--go BIN] [--tier quick|full] [--only REGEX] [--jobs 4]

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

Go's two sessions per item set (go.jsonl, go2.jsonl) are kept in ~/.cache/perf-rearch/parity/
go-cache/<go sha>/lsp/<session>-<project fingerprint>-<lspdiff sources sha>/ (parity_cli.fingerprint:
commit, node_modules mtimes; the sha of the vendored lspdiff sources, since they script the session)
and reused, so a run starts only tsc-rs. Standard sessions run with ATA off (vendor/lspdiff
client.mjs initializeParams). An answer on which Go's two sessions differ is GO-UNSTABLE (scenarios: four
Go sessions, go.jsonl to go4.jsonl, since Go's registrations race across its background goroutines). In full, tsc-rs
runs three times: twice as is and once with GOPORT_PARSE_THREADS=1; an answer on which a later run
differs from the first is a DIFF with detail rs-nondet or rs-schedule, even when one run matches
Go. Items: lsp/<session>/<index> <step> (index: the answer's place in the session). Status MATCH,
DIFF, GO-UNSTABLE; a session that fails is one ERROR item. Output: DIR/results.json and
DIR/<session>/ (lspdiff's files: mismatches.json, summary.json, rs.jsonl).
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
RS_RUNS = {'quick': [], 'full': ['--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1']}



def scenario_sources_sha():
    h = hashlib.sha256()
    for f in sorted((HERE / 'vendor/lspdiff').glob('*.mjs')):
        h.update(f.name.encode() + b'\0' + f.read_bytes())
    return h.hexdigest()[:12]


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
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha = sha256_file(args.go)[:16]
    items, sessions = {}, {}
    t0 = time.monotonic()
    src_sha = scenario_sources_sha()

    def run_one(sess):
        """One session: (its items, its summary, the line to print)."""
        name, project, scenario = sess['name'], sess['project'], sess.get('scenario')
        if project is FIXTURE:
            root = HOME / f'.cache/perf-rearch/parity/scratch/lsp-{name}'
            key = f'{name}-fixture-{src_sha}'
        else:
            root = next((Path(os.path.expanduser(d)) for d in PROJECTS[project] if Path(os.path.expanduser(d)).is_dir()), None)
            if root is None:
                return {f'lsp/{name}': {'status': 'SKIP', 'detail': 'project not prepared'}}, None, f'lsp {name}: SKIP'
            key = f'{name}-{fingerprint(root)}-{src_sha}'
        cache = HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}/lsp/{key}'
        sout = out / name
        cmd = ['node', str(HERE / 'vendor/lspdiff/lspdiff.mjs'), 'diff', '--project', str(root),
               '--files', str(sess['files']), '--go', args.go, '--rs', args.rs, '--out', str(sout),
               '--go-cache', str(cache), *RS_RUNS[args.tier]]
        cmd += ['--scenario', scenario, '--go-runs', '4'] if scenario else ['--positions', str(sess['positions'])]
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
            got[f'lsp/{name}/{row["i"]:04d} {row["step"]}'] = item
        c = Counter(row['status'] for row in rows)
        runs = dict(re.findall(r'^(go\d*|rs[\w-]*)(?: \(.*\))?: \d+ answers in ([\d.]+) s', r.stdout, re.M))
        info = {'secs': secs, 'answers': len(rows), 'counts': dict(c), 'root': str(root),
                'runSecs': {k: float(v) for k, v in runs.items()}}
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
               'wallSecs': round(time.monotonic() - t0, 1), 'items': dict(sorted(items.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'lsp: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
