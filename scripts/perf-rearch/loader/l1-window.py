#!/usr/bin/env python3
"""Observation-to-watch window of a language server's first program load (loader v2 L1).

usage: python3 -I l1-window.py view|base <obs log> [--kinds read,stat,realpath,entries]
       python3 -I l1-window.py pair <view log> <base log> [<view log> <base log> ...] [--kinds ...]

The window of an observation is the time from when the load got the OS answer of a path to
the end of the watch registration that follows the load (every `registerCapability`
answered): a change to the path inside it is seen only when a later watch event reports it,
in Go as in tsc-rs (project/session.go updateWatch registers after the load).
- `view`: a log of a tsc-rs diagnostics build (GOPORT_LOAD_OBS_LOG): `load <t0> <t1>`, then
  `obs <kind> <ms> <worker|loader> <path>` for each entry of the load's view that the loader
  demanded (its observation time: the first OS call, by a worker or the loader), and
  `watch <ms>`.
- `base`: a log of the base serial loader with the window spike patch: `os <kind> <ms>
  <thread> <path>` for every OS call, `load <t0> <t1>` around each program load, `watch
  <ms>`. The loader's observations are its thread's calls inside the load (the thread that
  makes the load's first call), first call per kind and path.
Both sides take the time before the OS call, and write their logs after the watch
registration (so the logging is outside the window).
Prints JSON: the load span, the delay from the load's end to the watch registration, and per
kind the count and the window's min, p10, p50, p90, max and mean (ms), all kinds together too.

`pair`: for each (view log, base log) pair of the same project, the paths that both sides
observed, and per path the delta of the windows (view - base, ms); the deltas of all pairs
are pooled. Per kind and for all kinds: the count, p50, p90, p99, max, and the paths over
+50 ms and over +100 ms. The verdict: MATERIAL when the paired p90 is over +50 ms or more
than 5% of the paths are over +100 ms (criterion stated before the measurement), else NOT
MATERIAL.
"""
import json
import statistics
import sys


def quantiles(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    at = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]
    return {"n": len(values), "min": round(values[0], 1), "p10": round(at(0.1), 1), "p50": round(at(0.5), 1),
            "p90": round(at(0.9), 1), "max": round(values[-1], 1), "mean": round(statistics.fmean(values), 1)}


def windows(mode, path):
    """(load span, load end to watch, observations by worker, {(kind, path): window ms})."""
    lines = [line.rstrip("\n").split(" ", 4) for line in open(path, encoding="utf-8", errors="replace")]
    loads = [(float(l[1]), float(l[2])) for l in lines if l[0] == "load"]
    if not loads:
        sys.exit("no load in the log")
    t0, t1 = loads[0]
    watches = [float(l[1]) for l in lines if l[0] == "watch" and float(l[1]) >= t1]
    if not watches:
        sys.exit("no watch registration after the first load")
    t_watch = watches[0]
    obs = {}
    if mode == "view":
        # The obs lines that follow the first load line.
        first = next(i for i, l in enumerate(lines) if l[0] == "load")
        for l in lines[first + 1:]:
            if l[0] == "load":
                break
            if l[0] == "obs":
                _, kind, ms, who, p = l
                obs[(kind, p)] = (float(ms), who)
    else:
        calls = [l for l in lines if l[0] == "os" and t0 <= float(l[2]) <= t1]
        if not calls:
            sys.exit("no OS call in the first load")
        loader = calls[0][3]
        for _, kind, ms, thread, p in calls:
            if thread == loader:
                obs.setdefault((kind, p), (float(ms), "loader"))
    by_worker = sum(1 for _, who in obs.values() if who == "worker")
    return t1 - t0, t_watch - t1, by_worker, {key: t_watch - ms for key, (ms, _) in obs.items()}


def deltas_summary(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    at = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]
    over50 = sum(1 for v in values if v > 50)
    over100 = sum(1 for v in values if v > 100)
    return {"n": len(values), "p50": round(at(0.5), 1), "p90": round(at(0.9), 1), "p99": round(at(0.99), 1),
            "max": round(values[-1], 1), "over_50": over50, "over_100": over100,
            "over_100_pct": round(100 * over100 / len(values), 2)}


def main():
    args = sys.argv[1:]
    kinds = None
    if "--kinds" in args:
        i = args.index("--kinds")
        kinds = set(args[i + 1].split(","))
        del args[i:i + 2]
    mode = args.pop(0)
    if mode == "pair":
        per_kind = {}
        for view_log, base_log in zip(args[0::2], args[1::2]):
            v = windows("view", view_log)[3]
            b = windows("base", base_log)[3]
            for key in v.keys() & b.keys():
                if kinds and key[0] not in kinds:
                    continue
                per_kind.setdefault(key[0], []).append(v[key] - b[key])
        all_deltas = [d for values in per_kind.values() for d in values]
        summary = deltas_summary(all_deltas)
        reads = deltas_summary(per_kind.get("read", []))
        material = lambda s: s.get("n", 0) > 0 and (s["p90"] > 50 or s["over_100_pct"] > 5)
        print(json.dumps({
            "pairs": len(args) // 2,
            "kinds": {kind: deltas_summary(values) for kind, values in sorted(per_kind.items())},
            "all": summary,
            "verdict_all": "MATERIAL" if material(summary) else "NOT MATERIAL",
            "verdict_reads": "MATERIAL" if material(reads) else "NOT MATERIAL",
        }))
        return
    (path,) = args
    span, to_watch, by_worker, w = windows(mode, path)
    per_kind = {}
    for (kind, _), window in w.items():
        if kinds and kind not in kinds:
            continue
        per_kind.setdefault(kind, []).append(window)
    out = {
        "mode": mode,
        "load_ms": round(span, 1),
        "load_end_to_watch_ms": round(to_watch, 1),
        "by_worker": by_worker,
        "kinds": {kind: quantiles(values) for kind, values in sorted(per_kind.items())},
        "all": quantiles([v for values in per_kind.values() for v in values]),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
