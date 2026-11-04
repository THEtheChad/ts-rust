#!/usr/bin/env python3
"""Differential fuzzer: mutated TypeScript test cases through Go tsc (the oracle) and tsc-rs.

usage: scripts/difffuzz/difffuzz.py fuzz --out DIR [--workers N] [--minutes M] [--seed S] [--reduce-minutes M]
       scripts/difffuzz/difffuzz.py replay --seed S --case I [--out DIR]
       scripts/difffuzz/difffuzz.py check CASE.json
       scripts/difffuzz/difffuzz.py reduce CASE.json --out DIR

Common flags: --go BIN (default ~/.local/bin/tsgo-oracle), --rs BIN (default target/release/tsgo),
--upstream DIR (the tsc/ dir of the pinned microsoft/TypeScript checkout), --timeout SECS,
--weights NAME=W,... (text mutator weights; pass the same to `replay`).

Case I of seed S is a pure function of (S, I, the seed corpus), so `replay` makes it again.
Any difference in exit code, stdout, stderr or written files is a finding. A difference is
confirmed with two more Go runs (cases where Go's own output changes are dropped) and one more
tsc-rs run. Findings are deduplicated by signature (oracle.signature); after the time budget the
smallest example of each signature is reduced (reduce.py) and written to DIR/findings/<sig>/:
repro/ (files and tsconfig.json), go.out, rs.out, diff, info.json, case.json.
"""
import argparse
import difflib
import json
import os
import random
import re
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mutators  # noqa: E402
import oracle  # noqa: E402
import seeds  # noqa: E402
from reduce import Reducer  # noqa: E402

REPO = HERE.parents[1]
DEFAULT_UPSTREAM = Path.home() / '.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc'


def slug(sig):
    return re.sub(r'[^\w.~>+-]+', '_', sig)[:90]


def make_case(seed, i, corpus):
    rng = random.Random(f'{seed}:{i}')
    case = rng.choice(corpus)
    if rng.random() < 0.03:
        case = {**case, 'mutations': []}
    else:
        case = mutators.mutate(rng, case, corpus)
    if 'noEmit' not in case['options'] and rng.random() < 0.4:
        case = {**case, 'options': {**case['options'], 'noEmit': True}}
    if rng.random() < 0.3:
        case = {**case, 'args': rng.choice(mutators.ARG_VARIANTS)}
    return {**case, 'id': f'{seed}:{i}'}


def case_size(case):
    return sum(len(t) for t in case['files'].values())


class Campaign:
    def __init__(self, args, corpus):
        self.args, self.corpus = args, corpus
        self.oracle = oracle.Oracle(args.go, args.rs, args.timeout)
        self.out = Path(args.out)
        self.tmp = Path(args.tmp) / f'difffuzz-{os.getpid()}'
        self.lock = threading.Lock()
        self.next_id = 0
        self.stats = Counter()
        self.sigs = Counter()
        self.examples = {}  # sig -> [case]
        self.secs = {'go': 0.0, 'rs': 0.0}

    def take_id(self):
        with self.lock:
            i = self.next_id
            self.next_id += 1
            return i

    def worker(self, k, deadline):
        d = self.tmp / f'w{k}'
        while time.monotonic() < deadline:
            case = make_case(self.args.seed, self.take_id(), self.corpus)
            try:
                sig, go, rs, note = self.oracle.check(case, d)
            except Exception as e:  # a harness problem, not a finding; keep going
                with self.lock:
                    self.stats['harness-error'] += 1
                print(f'harness error on {case["id"]}: {e!r}', file=sys.stderr)
                continue
            with self.lock:
                self.stats['cases'] += 1
                self.secs['go'] += go.secs
                self.secs['rs'] += rs.secs
                if note:
                    self.stats[note] += 1
                    self.log('notes.txt', f'{case["id"]} {note} {sig} {case.get("mutations")}')
                if max(go.secs, rs.secs) > 5:
                    self.stats['slow'] += 1
                    self.log('slow.txt', f'{case["id"]} go {go.secs:.1f}s ({go.code}) rs {rs.secs:.1f}s ({rs.code}) '
                                         f'{case.get("mutations")}')
                if sig is None:
                    continue
                self.stats['diffs'] += 1
                new = sig not in self.sigs
                self.sigs[sig] += 1
                keep = self.examples.setdefault(sig, [])
                if len(keep) < self.args.keep:
                    keep.append(case)
                    raw = self.out / 'raw' / slug(sig) / case['id'].replace(':', '-')
                    oracle.write_finding(raw, case, sig, go, rs, {'note': note})
                if new:
                    print(f'[{time.strftime("%H:%M:%S")}] new signature {sig} ({case["id"]}, {case.get("mutations")})',
                          flush=True)

    def log(self, name, line):
        with open(self.out / name, 'a') as f:
            f.write(line + '\n')

    def report(self, start):
        el = time.monotonic() - start
        s = self.stats
        line = (f'[{time.strftime("%H:%M:%S")}] {el / 60:.1f} min, {s["cases"]} cases ({s["cases"] / max(el, 1):.1f}/s), '
                f'{s["diffs"]} diffs, {len([x for x in self.sigs if not x.startswith("oracle:")])} signatures, '
                f'go-nondet {s["go-nondet"]}, rs-flaky {s["rs-flaky"]}, rs-nondet {s["rs-nondet"]}, slow {s["slow"]}, oracle {sum(v for k, v in self.sigs.items() if k.startswith("oracle:"))}')
        print(line, flush=True)
        with open(self.out / 'log.txt', 'a') as f:
            f.write(line + '\n')

    def fuzz(self):
        self.out.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        deadline = start + self.args.minutes * 60
        threads = [threading.Thread(target=self.worker, args=(k, deadline), daemon=True) for k in range(self.args.workers)]
        for t in threads:
            t.start()
        last = start
        while any(t.is_alive() for t in threads):
            time.sleep(1)
            if time.monotonic() - last >= 60:
                last = time.monotonic()
                self.report(start)
        self.report(start)
        self.write_stats(start)
        self.reduce_all()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_stats(self, start):
        stats = {'seed': self.args.seed, 'weights': self.args.weights, 'workers': self.args.workers, 'minutes': (time.monotonic() - start) / 60,
                 'seeds': len(self.corpus), **self.stats, 'secs': self.secs, 'signatures': dict(self.sigs.most_common())}
        (self.out / 'stats.json').write_text(json.dumps(stats, indent=2) + '\n')

    def reduce_one(self, sig, k):
        case = min(self.examples[sig], key=case_size)
        d = self.tmp / f'r{k}'
        reducer = Reducer(self.oracle, sig, d / 'work', self.args.reduce_minutes * 60)
        small = reducer.run(case) or case
        found, go, rs, note = self.oracle.check(small, d / 'confirm')
        if found != sig:
            small, (found, go, rs, note) = case, self.oracle.check(case, d / 'confirm')
        target = self.out / 'findings' / slug(sig)
        shutil.rmtree(target, ignore_errors=True)
        oracle.write_finding(target, small, sig, go, rs, {
            'hits': self.sigs[sig], 'example': case['id'], 'reduced': small is not case, 'reduce_tests': reducer.tests,
            'confirmed_signature': found, 'note': note})
        print(f'reduced {sig}: {case_size(case)} -> {case_size(small)} chars in {reducer.tests} runs', flush=True)

    def reduce_all(self):
        sigs = [s for s in self.examples if not s.startswith('oracle:')]
        with ThreadPoolExecutor(self.args.workers) as pool:
            list(pool.map(self.reduce_one, sigs, range(len(sigs))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['fuzz', 'replay', 'check', 'reduce'])
    ap.add_argument('case_json', nargs='?')
    ap.add_argument('--out', default='/tmp/difffuzz-out')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument('--minutes', type=float, default=10)
    ap.add_argument('--reduce-minutes', type=float, default=10)
    ap.add_argument('--seed', default='1')
    ap.add_argument('--case', type=int)
    ap.add_argument('--keep', type=int, default=5, help='raw examples kept per signature')
    ap.add_argument('--timeout', type=float, default=60)
    ap.add_argument('--go', default=str(Path.home() / '.local/bin/tsgo-oracle'))
    ap.add_argument('--rs', default=str(REPO / 'target/release/tsgo'))
    ap.add_argument('--upstream', default=str(DEFAULT_UPSTREAM))
    ap.add_argument('--tmp', default='/tmp')
    ap.add_argument('--weights', default='', help='text mutator weights, e.g. jsdoc_host=12,deep=0')
    args = ap.parse_args()
    for item in filter(None, args.weights.split(',')):
        name, w = item.split('=')
        i = [m.__name__ for m, _ in mutators.TEXT_MUTATORS].index('m_' + name)
        mutators.TEXT_MUTATORS[i] = (mutators.TEXT_MUTATORS[i][0], float(w))
    orc = oracle.Oracle(args.go, args.rs, args.timeout)
    work = Path(args.tmp) / f'difffuzz-{os.getpid()}'
    if args.cmd == 'fuzz':
        corpus, _ = seeds.load(args.upstream)
        print(f'{len(corpus)} seed cases', flush=True)
        Campaign(args, corpus).fuzz()
        return
    if args.cmd == 'replay':
        corpus, _ = seeds.load(args.upstream)
        case = make_case(args.seed, args.case, corpus)
    else:
        case = oracle.read_case(args.case_json)
    sig, go, rs, note = orc.check(case, work / 'check')
    print(f'signature: {sig} note: {note} mutations: {case.get("mutations")}')
    if args.cmd == 'reduce' and sig and not sig.startswith('oracle:'):
        reducer = Reducer(orc, sig, work / 'reduce', args.reduce_minutes * 60)
        case = reducer.run(case) or case
        sig, go, rs, note = orc.check(case, work / 'check')
        print(f'reduced in {reducer.tests} runs to {case_size(case)} chars: {sig}')
    if sig and args.cmd != 'check':
        oracle.write_finding(Path(args.out), case, sig, go, rs, {'note': note})
        print(f'wrote {args.out}')
    elif sig:
        sys.stdout.write(''.join(difflib.unified_diff(
            go.text().splitlines(True), rs.text().splitlines(True), 'go', 'tsc-rs')))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
