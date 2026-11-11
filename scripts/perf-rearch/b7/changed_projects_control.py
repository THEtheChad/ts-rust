#!/usr/bin/env python3
"""Lane B7 control: Go's updateSnapshot answer for two changed projects has more than one byte form,
and the api tier's comparison (parity_api.same, with sort_changed_projects) takes them as one.

usage: changed_projects_control.py <go bin> [runs] [scenario]   (default 8 runs of build-symlink-monorepo)
Prints each run's raw order of result.changes.changedProjects, then whether the runs compare equal with
and without the normalisation. Every session runs in the tier's sandbox (parity_api_session.run_session).
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import parity_api as api  # noqa: E402
import parity_api_session as session  # noqa: E402


def main():
    go = sys.argv[1]
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    sc = api.find_scenario(sys.argv[3] if len(sys.argv) > 3 else 'build-symlink-monorepo')
    fdir = api.fixture_snapshot(sc)[0] if sc.get('disk') is not None else None
    ts = [session.run_session(go, sc, session.SCRATCH / sc['name'] / f'control-{os.getpid()}-{k}', fdir) for k in range(runs)]
    orders = []
    for t in ts:
        raw = next(s['answer'] for s in t['steps'] if isinstance(s.get('answer'), str) and '"changedProjects"' in s['answer'])
        keys = re.findall(r'"([^"]+)":\{"changedFiles"', raw)
        orders.append(tuple(k.rsplit('/', 1)[-1] for k in keys))
        print('run', len(orders), orders[-1], 'failure', t['failure'])
    raw_equal = len({api.canon({'steps': [s.get('answer') for s in t['steps']]}) for t in ts}) == 1
    same = all(api.same(ts[0], t) for t in ts[1:])
    print(f'byte forms of the answer: {len(set(orders))}; raw answers all equal: {raw_equal}; '
          f'tier comparison (normalised) all equal: {same}')
    return 0 if same else 1


if __name__ == '__main__':
    sys.exit(main())
