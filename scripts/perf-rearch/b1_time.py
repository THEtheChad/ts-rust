#!/usr/bin/env python3
"""B1 wall timing: plain `tsc -p` with emit, sides interleaved run by run.

usage: b1_time.py --out FILE --runs N --side LABEL=BIN[,ENV=VAL...] [--side ...]
                  [--project NAME ...] [--max-load 1.5] [--repeat-load 3.0]
                  [--phases]

Each run starts only when the 1-minute load (/proc/loadavg) is under
--max-load. Sides run interleaved (one run of each side, then the next
round). The load is recorded before and after each run; a run that ends
above --repeat-load is repeated. Every run writes to a fresh scratch outDir
under ~/.cache/perf-rearch/scratch/b1/time-out (never into the project).
GOPORT_LAUNCH=0 is set for the rs sides so wait4 sees the work process.
Prints one line per run and, at the end, the median and min-max of wall,
user and sys per side and project. --phases adds a last run of each rs side
with GOPORT_EMIT_PHASE_TIMES=1 and prints its report (that needs a `diagnostics` feature build:
`scripts/run-cargo-capped.sh build --release -p ts_goport --features diagnostics --bin tsgo`).
"""

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
PROJECTS = HOME / ".cache/perf-rearch/projects"
PLAIN = ["--noEmit", "false", "--incremental", "false", "--composite", "false"]
CFG = {
    "umami": (HOME / ".cache/lspdiff/projects/umami", "tsconfig.json", []),
    "hono": (PROJECTS / "hono", "tsconfig.build.json", []),
    "query": (PROJECTS / "query", "packages/query-core/tsconfig.prod.json", []),
    "ts-pattern": (PROJECTS / "ts-pattern", "tsconfig.json", ["--rootDir", "src"]),
    "effect": (PROJECTS / "effect", "packages/effect/tsconfig.noeffect.json", []),
}
OUT = HOME / ".cache/perf-rearch/scratch/b1/time-out"


def load() -> float:
    return float(Path("/proc/loadavg").read_text().split()[0])


def wait_load(max_load: float) -> float:
    l = load()
    waited = 0
    while l >= max_load:
        if waited % 300 == 0:
            print(f"  waiting for load < {max_load} (now {l})", flush=True)
        time.sleep(5)
        waited += 5
        l = load()
    return l


def run(binary, env_extra, project, extra, rs):
    cwd, cfg, base = CFG[project]
    if OUT.exists():
        shutil.rmtree(OUT)
    argv = [binary, "-p", cfg, *PLAIN, "--outDir", str(OUT), *base, *extra]
    env = dict(os.environ)
    env.update(env_extra)
    if rs:
        env["GOPORT_LAUNCH"] = "0"
    start = time.perf_counter()
    p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    _, status, usage = os.wait4(p.pid, 0)
    wall = time.perf_counter() - start
    err = p.stderr.read().decode("utf-8", "replace")
    p.stderr.close()
    return wall, usage.ru_utime, usage.ru_stime, os.waitstatus_to_exitcode(status), err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--side", action="append", required=True)
    ap.add_argument("--project", action="append")
    ap.add_argument("--extra", default="", help="extra tsc args, space separated")
    ap.add_argument("--max-load", type=float, default=1.5)
    ap.add_argument("--repeat-load", type=float, default=3.0)
    ap.add_argument("--phases", action="store_true")
    a = ap.parse_args()
    sides = []
    for s in a.side:
        label, rest = s.split("=", 1)
        parts = rest.split(",")
        env = dict(kv.split("=", 1) for kv in parts[1:])
        sides.append((label, parts[0], env, label != "go"))
    projects = a.project or ["umami", "hono", "query"]
    extra = a.extra.split()
    log = open(a.out, "a")

    def emit(line):
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    import hashlib
    emit(f"# b1_time projects={projects} extra={extra} runs={a.runs} max_load={a.max_load}")
    for label, binary, env, _ in sides:
        digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()[:16]
        emit(f"# side {label}: {binary} sha256={digest} env={env}")
    results = {}
    for project in projects:
        # warm-up (file cache), not counted
        for label, binary, env, rs in sides:
            run(binary, env, project, extra, rs)
        for r in range(a.runs):
            for label, binary, env, rs in sides:
                while True:
                    l0 = wait_load(a.max_load)
                    wall, user, sys_, rc, _ = run(binary, env, project, extra, rs)
                    l1 = load()
                    ok = l1 <= a.repeat_load
                    emit(f"{project} {label} run{r} wall={wall:.3f} user={user:.3f} sys={sys_:.3f} rc={rc} load={l0:.2f}->{l1:.2f}{'' if ok else ' REPEAT'}")
                    if ok:
                        results.setdefault((project, label), []).append((wall, user, sys_, l0, l1))
                        break
    emit("# summary: median wall [min-max], median user, median sys, load range")
    for (project, label), rows in results.items():
        walls = [w for w, *_ in rows]
        users = [u for _, u, *_ in rows]
        syss = [s for _, _, s, *_ in rows]
        loads = [x for *_, l0, l1 in rows for x in (l0, l1)]
        emit(f"{project:<10} {label:<14} wall {statistics.median(walls):.3f}s [{min(walls):.3f}-{max(walls):.3f}] "
             f"user {statistics.median(users):.3f}s sys {statistics.median(syss):.3f}s n={len(rows)} "
             f"load {min(loads):.2f}-{max(loads):.2f}")
    if a.phases:
        for project in projects:
            for label, binary, env, rs in sides:
                if not rs:
                    continue
                wait_load(a.max_load)
                env2 = dict(env)
                env2["GOPORT_EMIT_PHASE_TIMES"] = "1"
                wall, *_rest, err = run(binary, env2, project, extra, rs)
                if "emit phase times (GOPORT_EMIT_PHASE_TIMES)" not in err:
                    sys.exit(f"b1_time.py: {binary} printed no phase report for {project} "
                             "(not a diagnostics feature build?)")
                emit(f"## phases {project} {label} wall={wall:.3f}\n{err.rstrip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
