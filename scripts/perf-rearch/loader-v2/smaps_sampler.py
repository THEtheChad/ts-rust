import os, sys, time
# usage: python3 -I smaps_sampler.py PID OUT  (last smaps_rollup Rss/Anonymous/AnonHugePages before PID exits; see sm-wrap.sh)
pid=int(sys.argv[1]); out=sys.argv[2]; last=""; t0=time.monotonic()
while os.path.exists(f"/proc/{pid}"):
    try:
        s=open(f"/proc/{pid}/smaps_rollup").read()
        last=f"t={1000*(time.monotonic()-t0):.0f}ms\n"+"".join(l for l in s.splitlines(True) if l.split(":")[0] in ("Rss","Anonymous","AnonHugePages","Private_Dirty"))
    except OSError: break
    time.sleep(0.05)
open(out,"w").write(last)
