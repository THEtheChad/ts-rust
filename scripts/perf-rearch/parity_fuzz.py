#!/usr/bin/env python3
"""Fuzz tier of the parity gate: a fixed batch of difffuzz cases, tsc-rs against cached Go results.

usage: parity_fuzz.py --rs BIN --out DIR [--go BIN] [--seed S] [--start N] [--count N] [--jobs N]
                      [--rs-runs 1|2] [--cache DIR] [--upstream DIR] [--timeout SECS] [--corpus]

--corpus runs the seed cases themselves, unmutated (every compiler and conformance case of the pin
that difffuzz can turn into a tsconfig project, about 12,000; --count 0 means all), with the case's
own options: items corpus/<case path>, Go cache in .../corpus. Without it, a mutated batch.

Case i of seed S is difffuzz's make_case(S, i, corpus) (vendor/difffuzz, a copy of feat/difffuzz
033e55442), so a batch is the same set of cases on every run. Each case runs in its own fixed
directory, /tmp/parity-fuzz/<S>-<i>, so absolute paths in the output (--listFiles, --explainFiles)
are the same for Go and tsc-rs and from run to run.

Go's result of a case is cached in --cache (default ~/.cache/perf-rearch/parity/go-cache/<go sha>/fuzz),
keyed by the Go binary, seed and case id. The first time, Go runs twice: a case on which Go's two runs
differ (8 runs for a case with --traceResolution) is GO-UNSTABLE, a Go timeout or panic is GO-FAIL; neither is compared. Later runs only run
tsc-rs (--rs-runs times; 2 also catches tsc-rs nondeterminism). A tsc-rs timeout gets one more try
with twice the time.

Per case: MATCH, DIFF (difffuzz's signature: diag:, emit:, stderr:, exit:, crash:, hang, or
rs-nondet when two tsc-rs runs differ), GO-UNSTABLE or GO-FAIL. Output in --out: results.json
({"items": {"fuzz/<S>:<i>": {"status", "sig"}}, "counts", "secs"}) and diffs/<S>-<i>/ (repro, go.out,
rs.out, diff) for each DIFF. Exit 0.
"""
import argparse
import hashlib
import json
import os
import pickle
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'vendor/difffuzz'))
import oracle  # noqa: E402
import seeds  # noqa: E402
from difffuzz import DEFAULT_UPSTREAM, make_case  # noqa: E402

WORK = Path('/tmp/parity-fuzz')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run_case(args, corpus, cache, out, i):
    if args.corpus:
        case = {**corpus[i], 'id': f'corpus:{i}'}
        cid = f'corpus-{i}'
    else:
        case = make_case(args.seed, i, corpus)
        cid = f'{args.seed}-{i}'
    d = WORK / cid
    inputs = oracle.write_project(case, d)
    extra = case.get('args', [])
    # --traceResolution output follows Go's resolver threads: 8 Go runs, as difffuzz confirms it.
    go_count = 8 if '--traceResolution' in extra else 2
    cfile = cache / f'{cid}.r{go_count}.pkl'
    go_secs = rs_secs = 0.0
    if cfile.exists():
        go, go_status = pickle.loads(cfile.read_bytes())
    else:
        gs = [oracle.run(args.go, d, inputs, args.timeout, extra) for _ in range(go_count)]
        go_secs = sum(g.secs for g in gs)
        go = gs[0]
        if any(g.code == 'timeout' or oracle.crashed(g) for g in gs):
            go_status = 'GO-FAIL'
        elif len({g.key() for g in gs}) > 1:
            go_status = 'GO-UNSTABLE'
        else:
            go_status = None
        go.secs = 0.0
        tmp = cfile.with_suffix(f'.tmp{os.getpid()}')
        tmp.write_bytes(pickle.dumps((go, go_status)))
        tmp.replace(cfile)
    if go_status:
        shutil.rmtree(d, ignore_errors=True)
        return i, {'status': go_status}, go_secs, rs_secs
    rs_runs = []
    for _ in range(args.rs_runs):
        rs = oracle.run(args.rs, d, inputs, args.timeout, extra)
        if rs.code == 'timeout':
            rs = oracle.run(args.rs, d, inputs, args.timeout * 2, extra)
        rs_secs += rs.secs
        rs_runs.append(rs)
    sigs = [oracle.signature(go, rs, str(d.resolve()) + '/') for rs in rs_runs]
    if len({rs.key() for rs in rs_runs}) > 1:
        sig = 'rs-nondet:' + str(next(s for s in sigs if s))
    else:
        sig = sigs[0]
    if sig is None:
        row = {'status': 'MATCH'}
    else:
        row = {'status': 'DIFF', 'sig': sig, 'mutations': case.get('mutations', []), 'seedCase': case['name']}
        bad = next(rs for rs, s in zip(rs_runs, sigs) if s)
        oracle.write_finding(out / 'diffs' / cid, case, sig, go, bad)
    shutil.rmtree(d, ignore_errors=True)
    return i, row, go_secs, rs_secs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rs', required=True)
    ap.add_argument('--go', default=str(Path.home() / '.local/bin/tsgo-oracle-3e9236361fa9'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--seed', default='1')
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--count', type=int, help='default: 2000 cases, or every case with --corpus')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--rs-runs', type=int, default=1, choices=(1, 2))
    ap.add_argument('--timeout', type=float, default=60)
    ap.add_argument('--cache')
    ap.add_argument('--upstream', default=str(DEFAULT_UPSTREAM))
    ap.add_argument('--corpus', action='store_true')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha = sha256(args.go)[:16]
    kind = 'corpus' if args.corpus else 'fuzz'
    cache = Path(args.cache) if args.cache else Path.home() / f'.cache/perf-rearch/parity/go-cache/{go_sha}/{kind}'
    cache.mkdir(parents=True, exist_ok=True)
    corpus, _ = seeds.load(args.upstream)
    if args.count is None:
        args.count = 2000 if not args.corpus else 0
    if args.corpus and not args.count:
        args.count = len(corpus) - args.start
    start = time.monotonic()
    items, counts, secs = {}, Counter(), Counter()
    lock = threading.Lock()
    done = 0

    def one(i):
        nonlocal done
        try:
            i, row, g, r = run_case(args, corpus, cache, out, i)
        except Exception as e:  # a harness error is reported as such, never as a match
            row, g, r = {'status': 'ERROR', 'sig': repr(e)[:300]}, 0.0, 0.0
        with lock:
            items[f'corpus/{corpus[i]["name"]}' if args.corpus else f'fuzz/{args.seed}:{i}'] = row
            counts[row['status']] += 1
            secs['go'] += g
            secs['rs'] += r
            done += 1
            if done % 1000 == 0:
                print(f'{kind} {done}/{args.count} {dict(counts)} {time.monotonic() - start:.0f}s', flush=True)

    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(one, range(args.start, args.start + args.count)))
    summary = {'seed': args.seed, 'start': args.start, 'count': args.count, 'rsRuns': args.rs_runs,
               'goSha': go_sha, 'corpus': len(corpus), 'counts': dict(counts),
               'secs': {**{k: round(v, 1) for k, v in secs.items()}, 'wall': round(time.monotonic() - start, 1)},
               'items': dict(sorted(items.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'{kind}: {dict(counts)} in {summary["secs"]["wall"]}s; DIFF signatures: '
          f'{dict(Counter(r["sig"] for r in items.values() if r["status"] == "DIFF"))}')


if __name__ == '__main__':
    main()
