#!/usr/bin/env python3
"""L1 ttfd A/B (from s1a-ttfd.py): idle-gated, interleaved ttfd runs (ttfd-logs.mjs) of several
LSP servers on one project, with the servers' own phase times, and a sample manifest. It also
keeps, per run, the loader v2 L0 lines of a tsc-rs server with GOPORT_LOAD_TIMES=1: the phases
(wall, utime, stime, minflt of the loading thread for setup, run_queue, finish, collect, publish,
bind) and the load view's counts; summary.md has their medians.

usage: python3 -I l1-ttfd.py --out DIR --runs N [--project DIR] [--file REL] LABEL=CMD [LABEL=CMD ...]
  CMD is a server binary, or ENV=VAL,...:BIN to run BIN with those variables.
Method (docs/perf-rearch/brief.md): each run starts when the 1-minute load is under 1.5; the
order of the labels rotates each round; the load is recorded before and after each run; a run
whose load after is above 3 is excluded and repeated (at most twice). Every run, kept or not,
goes into DIR/manifest.jsonl with its load and, for an excluded run, the reason. DIR/summary.md
has per label the median and range of ttfd, program update (the server's log line), didOpen,
the diagnostic request's round trip (it waits behind didOpen), process CPU, and for tsc-rs with
GOPORT_LOAD_TIMES the resolve-ahead counts of the load.
"""
import json, os, re, statistics, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
def opt(name, default=None):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
    return default
out = Path(opt("--out")); runs = int(opt("--runs", "5"))
project = opt("--project", str(Path.home() / ".cache/lspdiff/projects/umami"))
file_rel = opt("--file")
max_load = float(os.environ.get("MAX_LOAD", "1.5")); repeat_load = float(os.environ.get("REPEAT_LOAD", "3"))
sides = [a.split("=", 1) for a in args]
out.mkdir(parents=True, exist_ok=True)
manifest = out / "manifest.jsonl"

def load1():
    return float(Path("/proc/loadavg").read_text().split()[0])

def wait_quiet():
    while (l := load1()) >= max_load:
        time.sleep(5)
    return l

def ms(rx, txt):
    m = re.search(rx, txt)
    if not m: return None
    v, u = float(m.group(1)), m.group(2)
    return v / 1000 if u in ("µs", "us") else v * 1000 if u == "s" else v

def run_one(label, cmd):
    env = dict(os.environ)
    if ":" in cmd and "=" in cmd.split(":", 1)[0]:
        sets, cmd = cmd.split(":", 1)
        for kv in sets.split(","):
            k, v = kv.split("=", 1); env[k] = v
    argv = ["node", str(HERE / "ttfd-logs.mjs"), cmd, "--", project] + (["--file", file_rel] if file_rel else [])
    p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=300)
    d = json.loads(p.stdout)
    txt = "\n".join(l for _, l in d["logs"])
    lt = re.search(r"goport load-times: .*", d.get("stderr", ""))
    return {
        "ttfd_ms": d["ttfd_ms"], "diag_ms": d["diag_ms"], "cpu_ms": d["cpu_ms"], "items": d["items"],
        "program_ms": ms(r"Program update for \S+ completed in ([\d.]+)(ms|µs|s)", txt),
        "didopen_ms": ms(r"textDocument/didOpen. in ([\d.]+)(ms|µs|s)", txt),
        "load_times": lt.group(0) if lt else None,
        **l0(d.get("stderr", "")),
        **{k: int(v) for k, v in re.findall(r"\b(new_keys|taken|rejected|missing|scopes_taken|scopes_missing): (\d+)", lt.group(0) if lt else "")},
    }

lines_extra = []
def l0(err):
    """The L0 numbers of the first load: phase walls and thread CPU, view counts."""
    r = {}
    m = re.search(r"goport load-phases: (.*)", err)
    phases = dict(re.findall(r"(\w+)=\[([^\]]*)\]", m.group(1))) if m else {}
    for name in ("publish", "bind"):
        p = re.search(r"goport load-phase: " + name + r"=\[([^\]]*)\]", err)
        if p: phases[name] = p.group(1)
    for name, body in phases.items():
        kv = dict(re.findall(r"(\w+)=([-\d.]+)", body))
        for k in ("wall", "utime", "stime", "minflt"):
            if k in kv: r[f"{name}_{k}"] = float(kv[k])
    if phases:
        r["loader_wall"] = sum(r.get(f"{n}_wall", 0) for n in ("setup", "run_queue", "finish", "collect"))
        r["loader_utime"] = sum(r.get(f"{n}_utime", 0) for n in ("setup", "run_queue", "finish", "collect"))
        r["loader_stime"] = sum(r.get(f"{n}_stime", 0) for n in ("setup", "run_queue", "finish", "collect"))
    v = re.search(r"goport load-view: (.*)", err)
    if v:
        kv = dict(re.findall(r"(\w+)=([-\d.]+)", v.group(1)))
        for k in ("hit_rate", "demands", "hits", "worker_hits", "misses", "late", "bypass", "races"):
            if k in kv: r[f"view_{k}"] = float(kv[k])
    return r

done = {}
if manifest.exists():
    for line in manifest.read_text().splitlines():
        r = json.loads(line)
        if r["kept"]: done[(r["label"], r["round"])] = r
for rnd in range(runs):
    order = sides[rnd % len(sides):] + sides[:rnd % len(sides)]
    for label, cmd in order:
        if (label, rnd) in done: continue
        for attempt in range(3):
            before = wait_quiet()
            t = time.time()
            try:
                r = run_one(label, cmd); err = None
            except Exception as e:  # noqa: BLE001
                r, err = {}, repr(e)[:300]
            after = load1()
            kept = err is None and (after <= repeat_load or attempt == 2)
            reason = None if kept else (f"error {err}" if err else f"load after {after} > {repeat_load}")
            if kept and after > repeat_load: reason = f"kept after 2 repeats; load after {after}"
            rec = {"label": label, "round": rnd, "attempt": attempt, "start": t, "load_before": before,
                   "load_after": after, "kept": kept, "reason": reason, **r}
            with manifest.open("a") as f: f.write(json.dumps(rec) + "\n")
            print(f"{label} r{rnd} a{attempt} load {before:.2f}->{after:.2f} ttfd {r.get('ttfd_ms', 0):.1f} "
                  f"program {r.get('program_ms')} {'' if kept else 'EXCLUDED ' + reason}", flush=True)
            if kept: break

rows = [json.loads(l) for l in manifest.read_text().splitlines()]
lines = ["| label | n | ttfd median [range] | program update | didOpen | diagnostic request round trip | process CPU | keys taken / missing / rejected (median) |", "|---|---|---|---|---|---|---|---|"]
def cell(vals):
    vals = [v for v in vals if v is not None]
    return f"{statistics.median(vals):.1f} [{min(vals):.1f}–{max(vals):.1f}]" if vals else "-"
for label, _ in sides:
    kept = [r for r in rows if r["label"] == label and r["kept"]]
    lines.append(f"| {label} | {len(kept)} | {cell([r['ttfd_ms'] for r in kept])} | {cell([r['program_ms'] for r in kept])} | "
                 f"{cell([r['didopen_ms'] for r in kept])} | {cell([r['diag_ms'] for r in kept])} | {cell([r['cpu_ms'] for r in kept])} | "
                 + ("/".join(str(statistics.median([r[k] for r in kept if k in r])) for k in ("taken", "missing", "rejected")) if any("taken" in r for r in kept) else "-") + " |")
lines.append("")
cols = ["loader_wall", "loader_utime", "loader_stime", "setup_wall", "run_queue_wall", "run_queue_utime", "run_queue_stime",
        "run_queue_minflt", "collect_wall", "publish_wall", "bind_wall", "view_hit_rate", "view_worker_hits", "view_misses",
        "view_late", "view_bypass"]
lines.append("| label | " + " | ".join(cols) + " |")
lines.append("|---|" + "---|" * len(cols))
for label, _ in sides:
    kept = [r for r in rows if r["label"] == label and r["kept"]]
    def med(k):
        vals = [r[k] for r in kept if r.get(k) is not None]
        return f"{statistics.median(vals):.4g}" if vals else "-"
    lines.append(f"| {label} | " + " | ".join(med(k) for k in cols) + " |")
excl = [r for r in rows if not r["kept"]]
lines.append(f"\n{len(rows)} runs recorded, {len(excl)} excluded" + "".join(f"\n- {r['label']} r{r['round']} a{r['attempt']}: {r['reason']}" for r in excl))
(out / "summary.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
