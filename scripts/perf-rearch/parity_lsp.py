#!/usr/bin/env python3
"""LSP tier of the parity gate: vendor/lspdiff sessions on tsc-rs against cached Go LSP sessions.

usage: parity_lsp.py --rs BIN --out DIR [--go BIN] [--tier quick|full] [--only REGEX]

Sessions (project, files, positions): quick runs umami and ts-pattern with lspdiff's defaults (6
files, 4 positions per file); full runs them with 12 files and 6 positions, and hono's too. Go's
two sessions per item set (go.jsonl, go2.jsonl) are kept in ~/.cache/perf-rearch/parity/go-cache/
<go sha>/lsp/<session>/ and reused, so a run starts only tsc-rs. An answer on which Go's two
sessions differ is GO-UNSTABLE. Items: lsp/<session>/<index> <step> (index: the answer's place in the
session). Status MATCH, DIFF, GO-UNSTABLE; a session that fails is one ERROR item. Output:
DIR/results.json and DIR/<session>/ (lspdiff's files: mismatches.json, summary.json, rs.jsonl).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
PROJECTS = {
    'umami': ['~/.cache/perf-rearch/projects/umami', '~/.cache/lspdiff/projects/umami'],
    'ts-pattern': ['~/.cache/perf-rearch/parity-projects/repos/gvergnaud__ts-pattern'],
    'hono': ['~/.cache/perf-rearch/parity-projects/repos/honojs__hono'],
}
SESSIONS = {
    'quick': [('umami', 6, 4), ('ts-pattern', 6, 4)],
    'full': [('umami', 12, 6), ('ts-pattern', 12, 6), ('hono', 12, 6)],
}


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
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha = sha256_file(args.go)[:16]
    items, sessions = {}, {}
    t0 = time.monotonic()
    for project, files, positions in SESSIONS[args.tier]:
        name = f'{project}-f{files}-p{positions}'
        if not re.search(args.only, name):
            continue
        root = next((Path(os.path.expanduser(d)) for d in PROJECTS[project] if Path(os.path.expanduser(d)).is_dir()), None)
        if root is None:
            items[f'lsp/{name}'] = {'status': 'SKIP', 'detail': 'project not prepared'}
            continue
        cache = HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}/lsp/{name}'
        sout = out / name
        start = time.monotonic()
        r = subprocess.run(['node', str(HERE / 'vendor/lspdiff/lspdiff.mjs'), 'diff', '--project', str(root),
                            '--files', str(files), '--positions', str(positions), '--go', args.go, '--rs', args.rs,
                            '--out', str(sout), '--go-cache', str(cache)],
                           capture_output=True, text=True, timeout=3600)
        secs = round(time.monotonic() - start, 1)
        (out / f'{name}.log').write_text(r.stdout + r.stderr)
        produced = sout / f'{root.name}-diff' / 'items.json'
        if r.returncode or not produced.exists():
            items[f'lsp/{name}'] = {'status': 'ERROR', 'detail': f'lspdiff exit {r.returncode}: {r.stderr[-300:]}'}
            sessions[name] = {'secs': secs, 'error': True}
            print(f'lsp {name}: ERROR (exit {r.returncode}) {secs}s', flush=True)
            continue
        rows = json.loads(produced.read_text())
        for row in rows:
            items[f'lsp/{name}/{row["i"]:04d} {row["step"]}'] = {'status': row['status'], 'method': row['method']}
        c = Counter(row['status'] for row in rows)
        sessions[name] = {'secs': secs, 'answers': len(rows), 'counts': dict(c), 'root': str(root)}
        print(f'lsp {name}: {dict(c)} {secs}s', flush=True)
    counts = Counter(v['status'] for v in items.values())
    summary = {'tier': args.tier, 'rs': args.rs, 'goSha': go_sha, 'counts': dict(counts), 'sessions': sessions,
               'wallSecs': round(time.monotonic() - t0, 1), 'items': dict(sorted(items.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'lsp: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
