#!/usr/bin/env python3
"""Repeats the parity gate's `-b --verbose` pair (cold run, then a second run that should be up to
date) on fresh overlays of a project, many pairs side by side, optionally under CPU load, and counts
the pairs whose second run rebuilds a project ("is out of date because output ... is older than
input") or updates the timestamps of one that is up to date. For each such pair it keeps both
runs' stdout and stderr, and the mtimes (ns) of the files that the first run wrote.

usage: b-race-repro.py --bin <tsgo> --item hono|sync-persister [--pairs N] [--jobs J] [--burn K]
                       [--out DIR]
  --burn K   K busy-loop processes for the duration (CPU load beside the pairs)
  --env K=V  extra environment for the binary (repeatable)
  --step-back FILE:MS  after the first run, set the mtime of FILE (relative to the project root) MS
             ms back: what a wall clock step back between its write and the writes before it does
             (deterministic; every pair then shows the effect)
"""
import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
ITEMS = {
    'hono': (HOME / '.cache/perf-rearch/projects/hono', '.', 'tsconfig.json'),
    'sync-persister': (HOME / '.cache/perf-rearch/projects/query',
                       'packages/query-sync-storage-persister', 'tsconfig.json'),
}
OVERLAY = ['unshare', '-rm', '--', 'sh', '-c',
           'mount -t overlay overlay -o "lowerdir=$1,upperdir=$2,workdir=$3" "$1" && cd "$4" && shift 4 && exec "$@"',
           'overlay']
# The second-run messages of a mtime inversion: a rebuild, or a timestamp update of an up-to-date project.
BAD = (b'is older than input', b'needs to update timestamps of output files that are older than input files')


def burn():
    while True:
        pass


def tree(upper):
    out = {}
    for top, _, files in os.walk(upper):
        for f in files:
            p = os.path.join(top, f)
            st = os.lstat(p)
            out[os.path.relpath(p, upper)] = st.st_mtime_ns
    return out


def clear(d):
    if d.exists():
        subprocess.run(['chmod', '-R', 'u+rwx', str(d)], check=False)
        shutil.rmtree(d)


def pair(args):
    i, opts = args
    root, sub, cfg = ITEMS[opts['item']]
    scratch = Path(opts['out']) / 'scratch' / str(i)
    clear(scratch)
    upper, work = scratch / 'upper', scratch / 'work'
    upper.mkdir(parents=True)
    work.mkdir()
    env = dict(os.environ)
    env.update(opts['env'])
    cmd = [*OVERLAY, str(root), str(upper), str(work), str(root / sub), opts['bin'], '-b', cfg,
           '--verbose', '--pretty', 'false']
    outs = []
    trees = []
    offset = lambda: time.clock_gettime_ns(time.CLOCK_REALTIME) - time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    start = offset()
    for k in range(2):
        if k:
            time.sleep(0.02)
            for spec in opts['step_back']:
                f, ms = spec.rsplit(':', 1)
                st = os.stat(upper / f)
                t = st.st_mtime_ns - int(float(ms) * 1e6)
                os.utime(upper / f, ns=(st.st_atime_ns, t))
        p = subprocess.run(cmd, cwd=root / sub, env=env, capture_output=True)
        outs.append((p.returncode, p.stdout, p.stderr))
        trees.append(tree(upper))
    # The wall clock step during the pair (CLOCK_REALTIME - CLOCK_MONOTONIC), as parity_cli.py sees it.
    step = (offset() - start) / 1e6
    bad = any(m in outs[1][1] for m in BAD)
    if bad:
        d = Path(opts['out']) / 'bad' / str(i)
        d.mkdir(parents=True, exist_ok=True)
        (d / 'run1.out').write_bytes(outs[0][1])
        (d / 'run2.out').write_bytes(outs[1][1])
        (d / 'run1.err').write_bytes(outs[0][2])
        (d / 'run2.err').write_bytes(outs[1][2])
        (d / 'mtimes.json').write_text(json.dumps({'run1': trees[0], 'run2': trees[1]}, indent=0, sort_keys=True))
    clear(scratch)
    return i, bad, abs(step) > 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True)
    ap.add_argument('--item', default='hono', choices=sorted(ITEMS))
    ap.add_argument('--pairs', type=int, default=20)
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--burn', type=int, default=0)
    ap.add_argument('--env', action='append', default=[])
    ap.add_argument('--step-back', action='append', default=[])
    ap.add_argument('--out', default=str(HOME / '.cache/perf-rearch/b-race'))
    a = ap.parse_args()
    out = Path(a.out)
    clear(out)
    out.mkdir(parents=True)
    env = dict(kv.split('=', 1) for kv in a.env)
    burners = [multiprocessing.Process(target=burn, daemon=True) for _ in range(a.burn)]
    for b in burners:
        b.start()
    opts = {'item': a.item, 'bin': a.bin, 'out': a.out, 'env': env, 'step_back': a.step_back}
    start = time.monotonic()
    try:
        with multiprocessing.Pool(a.jobs) as pool:
            res = pool.map(pair, [(i, opts) for i in range(a.pairs)])
    finally:
        for b in burners:
            b.terminate()
    nbad = sum(1 for _, bad, _ in res if bad)
    print(f'{a.item}: {nbad}/{a.pairs} pairs rebuilt in the second run '
          f'(jobs {a.jobs}, burn {a.burn}, {time.monotonic() - start:.0f}s, load {open("/proc/loadavg").read().split()[0]})')
    print(f'wall clock stepped during {sum(1 for *_, s in res if s)} pairs; bad pairs with a step '
          f'{sum(1 for _, b, s in res if b and s)}, without {sum(1 for _, b, s in res if b and not s)}')
    print('bad pairs:', sorted(i for i, bad, _ in res if bad))


if __name__ == '__main__':
    sys.exit(main())
