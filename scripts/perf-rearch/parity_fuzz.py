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
keyed by the Go binary, the case id and the case's content (case_key: every file, the options, the
extra arguments, the directory, oracle.ARGS, the timeout and the number of Go runs), so a changed
mutator, seed corpus or parameter makes new Go runs. The first time, Go runs twice (8 times for a
case with --traceResolution). Later runs only run tsc-rs (--rs-runs times; 2 also catches tsc-rs
nondeterminism).

Every tsc-rs attempt counts (the row's `attempts`, one per tsc-rs process: seconds and outcome). A
timeout or crash in any attempt makes the case a DIFF with `rsFailure` (parity_compare.py fails on
it whatever the base says, unless parity-grandfathered.json lists it). The retry policy is for
diagnosis only: after a timeout (not where Go timed out too) tsc-rs runs once more with twice the
time, so the row says whether it was load (the retry answered) or a hang; the case stays a DIFF
either way.

Per case: MATCH, GO-BROKEN, GO-PARTITION, JS-AGREES or DIFF (sig: difffuzz's signature, diag:,
emit:, stderr:, exit:, crash:, hang, or rs-nondet when two tsc-rs runs differ). A case that is not
MATCH is judged in the order of parity_referees.py:
  1. two tsc-rs runs that differ: DIFF (rs-nondet);
  2. Go's cached result is GO-FAIL (a Go timeout or crash in one of its runs) or GO-UNSTABLE (its
     runs differ): GO-BROKEN when tsc-rs neither crashes nor times out, DIFF when it does (unless
     it equals Go's crash byte for byte: MATCH). The Go cache keeps GO-FAIL and GO-UNSTABLE as
     they were; only the case's status changes;
  3. Go with --checkers N, N in parity_referees.CHECKERS, as many Go runs per N as above (cached as
     <case>.r<runs>.p1-c<N>.pkl; not when a run timed out): GO-PARTITION when Go's outputs differ
     across N and tsc-rs equals one; not for --showConfig cases (parity_referees.blocker);
  4. JS tsc (--js, once, timeout twice --timeout, cached in
     ~/.cache/perf-rearch/parity/js-cache/<js sha>/<fuzz|corpus>/<case>.pkl): JS-AGREES when JS
     sides with tsc-rs on every point where tsc-rs and Go differ;
  5. DIFF (detail: go-partition-miss and the JS check's reason, when they ran).
A GO-BROKEN, GO-PARTITION or JS-AGREES case with --rs-runs 1 runs tsc-rs once more first. Its row
has `detail`, `sig` (the difference from Go's default) and `referees`. --no-referees gives the old
statuses (GO-UNSTABLE and GO-FAIL not compared, DIFF). Output in --out: results.json ({"items":
{"fuzz/<S>:<i>": {"status", "sig"}}, "counts", "secs"}) and diffs/<S>-<i>/ (repro, go.out, rs.out,
diff) for each DIFF. Exit 0.
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
sys.path.insert(0, str(HERE))  # python3 -I does not put the script's dir on the path
import oracle  # noqa: E402
import parity_inputs as inputs  # noqa: E402
import parity_referees as referees  # noqa: E402
import seeds  # noqa: E402
from difffuzz import DEFAULT_UPSTREAM, make_case  # noqa: E402

WORK = Path('/tmp/parity-fuzz')
PARTITION_VERSION = 2  # bump when a change of this file changes the cached per-N Go results
JS_CACHE_VERSION = 2  # the same, for JS tsc's results
CACHE_VERSION = 2  # the same, for Go's default results


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def go_cached(args, d, inputs, extra, count, cfile):
    """Go's result of a case, run `count` times and cached in cfile: (first result, status None,
    GO-FAIL or GO-UNSTABLE, seconds spent running Go)."""
    if cfile.exists():
        go, status = pickle.loads(cfile.read_bytes())
        return go, status, 0.0
    gs = [oracle.run(args.go, d, inputs, args.timeout, extra) for _ in range(count)]
    secs = sum(g.secs for g in gs)
    go = gs[0]
    if any(g.code == 'timeout' or oracle.crashed(g) for g in gs):
        status = 'GO-FAIL'
    elif len({g.key() for g in gs}) > 1:
        status = 'GO-UNSTABLE'
    else:
        status = None
    go.secs = 0.0
    write_cache(cfile, (go, status))
    return go, status, secs


def write_cache(cfile, value):
    tmp = cfile.with_suffix(f'.tmp{os.getpid()}')
    tmp.write_bytes(pickle.dumps(value))
    tmp.replace(cfile)


def go_at_n(args, d, inputs, extra, count, cfile):
    """Go's output key at one --checkers N, run `count` times: (key, UNSTABLE when the runs differ or
    FAILED when one timed out; seconds spent running Go). Cached in cfile unless a run timed out (load).
    A Go crash that every run repeats is an output (tsc-rs reproduces some on purpose)."""
    if cfile.exists():
        return pickle.loads(cfile.read_bytes()), 0.0
    gs = [oracle.run(args.go, d, inputs, args.timeout, extra) for _ in range(count)]
    secs = sum(g.secs for g in gs)
    if any(g.code == 'timeout' for g in gs):
        return referees.FAILED, secs
    keys = {g.key() for g in gs}
    value = referees.UNSTABLE if len(keys) > 1 else gs[0].key()
    write_cache(cfile, value)
    return value, secs


def case_key(case, d, go_count, timeout):
    """What a case's results depend on: its content (files, options, arguments), where it runs and
    how (oracle.ARGS, the timeout, the number of Go runs). 16 hex digits."""
    return inputs.json_hash({'files': case['files'], 'options': case.get('options'), 'args': case.get('args', []),
                             'dir': str(d), 'argv': oracle.ARGS, 'timeout': timeout, 'goRuns': go_count})[:16]


class Case:
    """One case on disk, with the tier's runners for the referees. Every tsc-rs process it starts is
    in `attempts`."""

    def __init__(self, args, cache, js_cache, cid, d, inputs, extra, go_count, ckey):
        self.args, self.cache, self.js_cache, self.cid = args, cache, js_cache, cid
        self.d, self.inputs, self.extra, self.go_count, self.ckey = d, inputs, extra, go_count, ckey
        self.go_secs = 0.0
        self.attempts = []

    def run_rs(self, timeout, why):
        r = oracle.run(self.args.rs, self.d, self.inputs, timeout, self.extra)
        self.attempts.append({'why': why, 'secs': round(r.secs, 2), 'timeout': timeout,
                              'outcome': 'timeout' if r.code == 'timeout' else 'crash' if referees.crashed(r) else f'exit {r.code}'})
        return r

    def failure(self):
        """'timeout' or 'crash' when an attempt timed out or crashed, else None."""
        outcomes = [a['outcome'] for a in self.attempts]
        return 'timeout' if 'timeout' in outcomes else 'crash' if 'crash' in outcomes else None

    def go_at_n(self, n):
        key, secs = go_at_n(self.args, self.d, self.inputs, [*self.extra, *referees.go_args(n)], self.go_count,
                            self.cache / f'{self.cid}-{self.ckey}.r{self.go_count}.p{PARTITION_VERSION}-c{n}.pkl')
        self.go_secs += secs
        return key

    def js_side(self):
        """JS tsc's result (one run: it is single-threaded), cached unless it timed out."""
        if self.js_cache is None:  # no JS 6.0.3 (referees.js_identity): no JS-AGREES
            return None
        cfile = self.js_cache / f'{self.cid}-{self.ckey}.v{JS_CACHE_VERSION}.pkl'
        if cfile.exists():
            js = pickle.loads(cfile.read_bytes())
        else:
            js = oracle.run(self.args.js, self.d, self.inputs, self.args.timeout * 2, self.extra)
            if js.code != 'timeout':
                cfile.parent.mkdir(parents=True, exist_ok=True)
                write_cache(cfile, js)
        return [js]


def attempts_text(c):
    return ', '.join(f'{a["why"]} {a["outcome"]} ({a["secs"]} s of {a["timeout"]:g})' for a in c.attempts)


def judge(c, go, go_status, rs_runs, sig):
    """(status, detail) and the extra row fields of a case, in the order of parity_referees. A tsc-rs
    attempt that timed out or crashed is a DIFF (step 1) even where a later attempt answered, except an
    exact copy of Go's own crash output, which is MATCH (tsc-rs reproduces some Go crashes on purpose)."""
    rs = rs_runs[0]
    if sig is not None and sig.startswith('rs-nondet:'):
        return 'DIFF', None, {}
    if go_status == 'GO-FAIL' and go.code != 'timeout' and all(r.key() == go.key() for r in rs_runs) and c.args.referees:
        return 'MATCH', None, {}
    if go_status is None and sig is None and not c.failure():
        return 'MATCH', None, {}
    if not c.args.referees:
        return go_status or 'DIFF', None, {}
    if go_status:
        reason = referees.go_broken_reason([go], None) or ('Go runs differ' if go_status == 'GO-UNSTABLE'
                                                            else 'Go timed out or crashed on a repeated run')
        (status, detail), extra = referees.judge_broken(f'{go_status}: {reason}', rs_runs), {}
        if status == referees.GO_BROKEN and c.failure():
            return 'DIFF', f'go-broken ({go_status}: {reason}), tsc-rs {c.failure()} in an attempt: {attempts_text(c)}', extra
        if status == 'DIFF' and c.failure():
            detail = f'{detail}; attempts: {attempts_text(c)}'
    elif c.failure():
        return 'DIFF', f'tsc-rs {c.failure()} in an attempt: {attempts_text(c)}', {}
    else:
        status, detail, info = referees.referee(
            [*oracle.ARGS, *c.extra], c.go_at_n, c.js_side, [go], [rs], rs.key(), c.args.referee_all)
        extra = {'referees': info}
    if status in referees.REFEREED and len(rs_runs) < 2:  # determinism first: one more tsc-rs run
        again = c.run_rs(c.args.timeout, 'confirm')
        if again.key() != rs.key() or c.failure():
            return 'DIFF', f'rs-nondet: on the extra run of the referee check ({c.attempts[-1]["outcome"]})', extra
    return status, detail, extra


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
    ckey = case_key(case, d, go_count, args.timeout)
    c = Case(args, cache, args.js_cache, cid, d, inputs, extra, go_count, ckey)
    go, go_status, go_secs = go_cached(args, d, inputs, extra, go_count, cache / f'{cid}-{ckey}.v{CACHE_VERSION}.r{go_count}.pkl')
    rs_secs = 0.0
    if go_status and not args.referees:
        shutil.rmtree(d, ignore_errors=True)
        return i, {'status': go_status}, go_secs, rs_secs
    rs_runs = []
    # Where Go itself timed out, tsc-rs gets the same time once: a timeout there is a DIFF either way
    # (GO-BROKEN needs an answer), so the diagnostic retry and the second run would only cost time.
    go_hung = go_status == 'GO-FAIL' and go.code == 'timeout' and args.referees
    for k in range(args.rs_runs):
        rs = c.run_rs(args.timeout, f'run{k + 1}')
        rs_secs += rs.secs
        if rs.code == 'timeout' and go_hung:
            rs_runs.append(rs)
            break
        if rs.code == 'timeout':  # diagnosis only (load or a hang): the timeout stays in c.attempts,
            rs = c.run_rs(args.timeout * 2, f'run{k + 1} retry')  # and judge makes the case a DIFF
            rs_secs += rs.secs
        rs_runs.append(rs)
    sigs = [oracle.signature(go, rs, str(d.resolve()) + '/') for rs in rs_runs]
    if len({rs.key() for rs in rs_runs}) > 1:
        sig = 'rs-nondet:' + str(next(s for s in sigs if s))
    else:
        sig = sigs[0]
    status, detail, extra_row = judge(c, go, go_status, rs_runs, sig)
    row = {'status': status, **({'detail': detail} if detail else {}), **({'sig': sig} if sig else {}), **extra_row}
    if status == 'DIFF' and c.failure():
        row['rsFailure'] = c.failure()
    if len(c.attempts) > args.rs_runs or c.failure():
        row['attempts'] = c.attempts
    if status == 'DIFF':
        row.update(mutations=case.get('mutations', []), seedCase=case['name'])
        if detail and sig and not detail.startswith(('go-broken', 'rs-nondet', 'tsc-rs ')):
            row['detail'] = f'{detail}: {sig}'
        bad = next((rs for rs, s in zip(rs_runs, sigs) if s), rs_runs[0])
        oracle.write_finding(out / 'diffs' / cid, case, sig or detail, go, bad)
    shutil.rmtree(d, ignore_errors=True)
    return i, row, go_secs + c.go_secs, rs_secs


def diff_kind(row):
    """What a DIFF row is, for the summary: difffuzz's signature, or the referee's detail (a DIFF
    where Go was broken and tsc-rs crashed or hung too has no signature when both timed out)."""
    return row.get('sig') or row.get('detail') or '?'


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
    ap.add_argument('--js', default=str(referees.JS_TSC), help='JS tsc for JS-AGREES (an executable script)')
    ap.add_argument('--referees', action=argparse.BooleanOptionalAction, default=True,
                    help='GO-BROKEN, GO-PARTITION and JS-AGREES (else GO-UNSTABLE, GO-FAIL and DIFF as before)')
    ap.add_argument('--referee-all', action='store_true',
                    help='run the JS check even where GO-PARTITION decided (recorded in referees.js)')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha = sha256(args.go)[:16]
    kind = 'corpus' if args.corpus else 'fuzz'
    cache = Path(args.cache) if args.cache else Path.home() / f'.cache/perf-rearch/parity/go-cache/{go_sha}/{kind}'
    cache.mkdir(parents=True, exist_ok=True)
    args.js_cache, js_error = None, None
    if args.referees:
        try:
            args.js_cache = Path.home() / f'.cache/perf-rearch/parity/js-cache/{referees.js_sha(args.js)}/{kind}'
        except (OSError, ValueError) as e:  # no JS 6.0.3: every JS check fails, no JS-AGREES
            js_error = str(e)
            print(f'{kind}: JS referee unavailable: {js_error}', flush=True)
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
               'goSha': go_sha, 'corpus': len(corpus), 'counts': dict(counts), 'timeout': args.timeout,
               **({'jsError': js_error} if js_error else {}),
               'secs': {**{k: round(v, 1) for k, v in secs.items()}, 'wall': round(time.monotonic() - start, 1)},
               'items': dict(sorted(items.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'{kind}: {dict(counts)} in {summary["secs"]["wall"]}s; DIFF signatures: '
          f'{dict(Counter(diff_kind(r) for r in items.values() if r["status"] == "DIFF"))}')


if __name__ == '__main__':
    main()
