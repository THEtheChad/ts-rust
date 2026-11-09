#!/usr/bin/env python3
"""Lane P3: wall of many short runs of one command per variant, interleaved, waiting for load < LIMIT.
usage: startup.py <cwd> <runs> <name>=<env k:v;...|->=<bin> ... -- <args...>
Prints min, median, max wall (ms) per variant, and rusage CPU via wait4. Output is discarded."""
import os, sys, time, statistics, subprocess
def load(): return float(open('/proc/loadavg').read().split()[0])
cwd, runs = sys.argv[1], int(sys.argv[2])
i = sys.argv.index('--'); specs, args = sys.argv[3:i], sys.argv[i+1:]
limit = float(os.environ.get('LIMIT', '1.5'))
vs = []
for s in specs:
    name, envs, b = s.split('=', 2) if s.count('=') >= 2 else (s.split('=')[0], '-', s.split('=', 1)[1])
    env = dict(os.environ)
    if envs != '-':
        for kv in envs.split(';'):
            k, v = kv.split(':', 1); env[k] = v
    vs.append((name, b, env, [], []))
devnull = open(os.devnull, 'wb')
for r in range(runs):
    order = vs[r % len(vs):] + vs[:r % len(vs)]
    for name, b, env, walls, cpus in order:
        while load() > limit: time.sleep(2)
        t = time.perf_counter()
        p = subprocess.Popen([b] + args, cwd=cwd, env=env, stdout=devnull, stderr=devnull)
        _, st, ru = os.wait4(p.pid, 0)
        w = time.perf_counter() - t
        if os.waitstatus_to_exitcode(st) not in (0, 1, 2): print(name, 'rc', os.waitstatus_to_exitcode(st)); sys.exit(1)
        walls.append(w * 1e3); cpus.append((ru.ru_utime + ru.ru_stime) * 1e3)
for name, b, env, walls, cpus in vs:
    print(f"{name:14s} n={len(walls)} wall min {min(walls):.2f} med {statistics.median(walls):.2f} max {max(walls):.2f} ms"
          f"  cpu med {statistics.median(cpus):.2f} ms")
print(f"load now {load()}")
