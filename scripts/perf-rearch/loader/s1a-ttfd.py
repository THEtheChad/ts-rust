#!/usr/bin/env python3
"""S1a ttfd A/B: idle-gated, interleaved ttfd runs (ttfd-logs.mjs) of several LSP servers on one
project, with the servers' own phase times, and a sample manifest.

usage: python3 -I s1a-ttfd.py --out DIR --runs N [--project DIR] [--file REL] LABEL=CMD [LABEL=CMD ...]
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
        **{k: int(v) for k, v in re.findall(r"\b(new_keys|taken|rejected|missing|scopes_taken|scopes_missing): (\d+)", lt.group(0) if lt else "")},
    }

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
excl = [r for r in rows if not r["kept"]]
lines.append(f"\n{len(rows)} runs recorded, {len(excl)} excluded" + "".join(f"\n- {r['label']} r{r['round']} a{r['attempt']}: {r['reason']}" for r in excl))
(out / "summary.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
