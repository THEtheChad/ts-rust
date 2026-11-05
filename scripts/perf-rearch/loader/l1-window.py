#!/usr/bin/env python3
"""Observation-to-watch window of a language server's first program load (loader v2 L1).

usage: python3 -I l1-window.py view|base <obs log> [--kinds read,stat,realpath,entries]

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
Prints JSON: the load span, the delay from the load's end to the watch registration, and per
kind the count and the window's min, p10, p50, p90, max and mean (ms), all kinds together too.
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


def main():
    args = sys.argv[1:]
    kinds = None
    if "--kinds" in args:
        i = args.index("--kinds")
        kinds = set(args[i + 1].split(","))
        del args[i:i + 2]
    mode, path = args
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
    per_kind = {}
    for (kind, _), (ms, _) in obs.items():
        if kinds and kind not in kinds:
            continue
        per_kind.setdefault(kind, []).append(t_watch - ms)
    out = {
        "mode": mode,
        "load_ms": round(t1 - t0, 1),
        "load_end_to_watch_ms": round(t_watch - t1, 1),
        "by_worker": sum(1 for _, who in obs.values() if who == "worker"),
        "kinds": {kind: quantiles(values) for kind, values in sorted(per_kind.items())},
        "all": quantiles([v for values in per_kind.values() for v in values]),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
