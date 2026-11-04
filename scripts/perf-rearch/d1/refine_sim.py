#!/usr/bin/env python3
"""D1: estimate the balance of a refinement of Go's 4-way split from checker call logs.

  refine_sim.py C4_LOG C16_LOG [K]      (METRIC=types: use types created instead of instantiations)

The logs come from run-calllog.sh (same directory).

C4_LOG / C16_LOG: GOPORT_CHECKER_CALL_LOG files of the default run (Go's 4-way split) and of
--checkers 16 (Go's 16-way split). Each `file` line holds the checker's cumulative instantiation count, so
the delta is the file's cost in that checker's context.

Reports, by instantiations (a CPU proxy, not wall):
  - per-group totals at c4 and their max/mean;
  - the c16 split's per-checker totals (real duplication) and max/mean;
  - a refinement estimate: each c4 group split into K sub-checkers (K=4 by default), files assigned
    longest-first to the least-loaded sub-checker of their group, using each file's c16 cost (its cost in
    a 16-checker context, i.e. with 16-way duplication). Max over the 16 sub-checkers vs the c16 max.
"""
import os
import re
import sys
from collections import defaultdict

METRIC = os.environ.get("METRIC", "inst")  # inst (default) or types
LINE = re.compile(r'^c(\d+) \d+ file (.+?) diagnostics=\d+ types=(\d+) symbols=\d+ instantiations=(\d+)')


def read(path):
    """file -> (checker, cost), checker -> total instantiations, in log order."""
    last = defaultdict(int)
    files = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        m = LINE.match(line)
        if not m:
            continue
        c, name, types, inst = int(m[1]), m[2], int(m[3]), int(m[4])
        if METRIC == "types":
            inst = types
        files[name] = (c, inst - last[c])
        last[c] = inst
    return files, dict(last)


def stats(vals):
    vals = list(vals)
    mean = sum(vals) / len(vals)
    return max(vals), mean, max(vals) / mean


def main():
    c4, t4 = read(sys.argv[1])
    c16, t16 = read(sys.argv[2])
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    mx4, mean4, r4 = stats(t4.values())
    mx16, mean16, r16 = stats(t16.values())
    print(f'c4 groups: total {sum(t4.values())/1e6:.1f}M max {mx4/1e6:.1f}M max/mean {r4:.3f}')
    print(f'c16 split: total {sum(t16.values())/1e6:.1f}M max {mx16/1e6:.1f}M max/mean {r16:.3f}')
    groups = defaultdict(list)
    for name, (g, _) in c4.items():
        cost = c16.get(name, (0, 0))[1]
        groups[g].append((cost, name))
    loads = []
    for g, items in sorted(groups.items()):
        sub = [0] * k
        for cost, _ in sorted(items, reverse=True):
            i = min(range(k), key=lambda j: sub[j])
            sub[i] += cost
        loads.extend(sub)
    # A file's c16 cost already includes the duplication of its c16 checker; the refined sub-checkers' sum is
    # comparable to the c16 total only approximately.
    mxr, meanr, rr = stats(loads)
    print(f'refine {k}x4 (c16 file costs, LPT within groups): total {sum(loads)/1e6:.1f}M '
          f'max {mxr/1e6:.1f}M max/mean {rr:.3f}  max vs c16 max {mxr/mx16:.3f}')
    biggest = sorted(((c, n) for n, (_, c) in c16.items()), reverse=True)[:3]
    print('largest c16 file costs:', ', '.join(f'{n.rsplit("/", 1)[-1]} {c/1e6:.2f}M' for c, n in biggest))


if __name__ == '__main__':
    main()
