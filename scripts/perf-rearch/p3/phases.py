#!/usr/bin/env python3
"""Lane P3: when each process image of one tsgo run reaches thp_guard (GOPORT_THP_GUARD_DEBUG=1 prints one stderr
line there), relative to the spawn, and when the caller sees EOF and the exit. Min and median over N runs.
usage: phases.py <cwd> <runs> <bin> [ENV=V ...] -- <args...>"""
import os, sys, time, statistics, subprocess, selectors
cwd, runs, b = sys.argv[1], int(sys.argv[2]), sys.argv[3]
i = sys.argv.index('--'); extra, args = sys.argv[4:i], sys.argv[i+1:]
env = dict(os.environ, GOPORT_THP_GUARD_DEBUG='1')
for kv in extra:
    k, v = kv.split('=', 1); env[k] = v
rows = []
for r in range(runs):
    t0 = time.perf_counter()
    p = subprocess.Popen([b] + args, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    marks = []
    buf = b''
    while True:
        chunk = os.read(p.stderr.fileno(), 65536)
        now = time.perf_counter() - t0
        if not chunk:
            break
        buf += chunk
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            if b'thp_guard' in line:
                marks.append(now)
    eof = time.perf_counter() - t0
    p.wait()
    end = time.perf_counter() - t0
    rows.append(marks + [eof, end])
n = max(len(r) for r in rows)
names = [f"image{k + 1}" for k in range(n - 2)] + ["eof", "exit"]
for k, name in enumerate(names):
    vals = [r[k] * 1e3 for r in rows if len(r) == n]
    print(f"{name:8s} min {min(vals):6.2f} med {statistics.median(vals):6.2f} ms")
print("load", open('/proc/loadavg').read().split()[0])
