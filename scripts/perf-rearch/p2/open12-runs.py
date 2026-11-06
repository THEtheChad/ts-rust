#!/usr/bin/env python3
"""P2: load-gated, interleaved open12 timelines. Each round runs every side once (the order rotates per round);
each run waits for the 1-minute load < MAX_LOAD (1.5), records the load before and after and the CPU that other
processes used during the run (/proc/stat busy minus the run's own CPU), and repeats a run whose load after ends
above 3 (at most twice). Each run writes the timeline JSON of open12-timeline.mjs.
  scripts/perf-rearch/p2/open12-runs.py --out DIR --rounds N [--env side:K=V] side=BIN [side=BIN ...]
The last line of the log is DONE or FAIL rc=N."""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HONO = Path.home() / ".cache/perf-rearch/parity-projects/repos/honojs__hono"


def load1():
    return float(open("/proc/loadavg").read().split()[0])


def busy_s():
    f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    return (sum(f[:8]) - f[3] - f[4]) / os.sysconf("SC_CLK_TCK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=7)
    ap.add_argument("--project", default=str(HONO))
    ap.add_argument("--max-load", type=float, default=1.5)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("sides", nargs="+")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sides = [s.split("=", 1) for s in a.sides]
    envs = {}
    for e in a.env:
        side, kv = e.split(":", 1)
        k, v = kv.split("=", 1)
        envs.setdefault(side, {})[k] = v
    log = open(out / "runs.jsonl", "a")
    for rnd in range(a.rounds):
        order = sides[rnd % len(sides):] + sides[:rnd % len(sides)]
        for name, binary in order:
            dest = out / f"{name}-r{rnd + 1}.json"
            if dest.exists():
                continue
            for attempt in range(3):
                while (l0 := load1()) >= a.max_load:
                    time.sleep(5)
                b0, t0 = busy_s(), time.perf_counter()
                env = dict(os.environ, **envs.get(name, {}))
                p = subprocess.run(["node", str(HERE / "open12-timeline.mjs"), binary, a.project, "--out", str(dest)],
                                   capture_output=True, text=True, env=env, timeout=120)
                wall = time.perf_counter() - t0
                b1, l1 = busy_s(), load1()
                res = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {}
                other = max(0.0, (b1 - b0) - res.get("cpu_ms", 0) / 1000) / wall
                rec = dict(side=name, round=rnd + 1, attempt=attempt, load_before=l0, load_after=l1,
                           other_cores=round(other, 2), rc=p.returncode, **res)
                log.write(json.dumps(rec) + "\n")
                log.flush()
                print(json.dumps(rec), flush=True)
                if p.returncode != 0:
                    print(p.stderr[-2000:], file=sys.stderr)
                    return 1
                if l1 <= 3:
                    break
                dest.unlink()
    return 0


if __name__ == "__main__":
    rc = main()
    print("DONE" if rc == 0 else f"FAIL rc={rc}", flush=True)
    sys.exit(rc)
