# Sample /proc/PID/task/*/stat every 2 ms until PID exits.
# Writes per-thread totals (tid name utime_ms stime_ms minflt) and, for the busiest thread, a timeline.
# usage: python3 -I tt_sampler.py PID OUT  (see tt-wrap.sh)
import os, sys, time
pid = int(sys.argv[1]); out = sys.argv[2]; best = {}; series = {}
tick = 1000 / os.sysconf("SC_CLK_TCK"); t0 = time.monotonic()
while os.path.exists(f"/proc/{pid}"):
    try: tids = os.listdir(f"/proc/{pid}/task")
    except FileNotFoundError: break
    now = (time.monotonic() - t0) * 1000
    for tid in tids:
        try: s = open(f"/proc/{pid}/task/{tid}/stat").read()
        except OSError: continue
        name = s[s.index("(")+1:s.rindex(")")]; f = s[s.rindex(")")+2:].split()
        u, k, mf = int(f[11]) * tick, int(f[12]) * tick, int(f[7])
        b = best.get(tid)
        if not b or u + k >= b[1] + b[2]: best[tid] = (name, u, k, mf)
        sr = series.setdefault(tid, [])
        if not sr or sr[-1][1:] != (u, k, mf): sr.append((round(now, 1), u, k, mf))
    time.sleep(0.002)
with open(out, "w") as fh:
    top = sorted(best.items(), key=lambda x: -(x[1][1] + x[1][2]))
    for tid, (n, u, k, mf) in top:
        if u + k: fh.write(f"{tid}\t{n}\t{u:.0f}\t{k:.0f}\t{mf}\n")
    fh.write("# timeline of busiest thread: t_ms utime stime minflt\n")
    for row in series[top[0][0]]: fh.write("# " + " ".join(str(x) for x in row) + "\n")
