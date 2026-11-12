#!/usr/bin/env python3
"""CPU of an LSP server per request kind, from a samply profile and the matching `lspdiff.mjs bench --trace` run.

usage: python3 -I scripts/lspdiff/profile-windows.py --pair TRACE.json PROFILE.json.gz [--pair ...] [options]
  --kind REGEX     only request kinds matching (kinds as trace-split.mjs names them: "completion c", "hover", ...)
  --top N          functions per table (default 20)
  --alone          only requests with no other request in flight from send to answer (clean attribution)
  --window W       send (default: send..first byte of the answer), handler (the server's handled time, ending at
                   the answer's first byte or the handled line, whichever came first) or wait (send..handler start)
  --idle           also the CPU of each thread between requests (no request in flight), per round gap
  --under SUBSTR   only samples whose stack has a frame containing SUBSTR, and the inclusive table only counts
                   frames below the outermost such frame (what that function spends its time in)

The profile comes from scripts/lspdiff/lspdiff.mjs bench --trace with the server wrapped in samply-user
(record --save-only --unstable-presymbolicate). Trace times are performance.now() ms; the trace's clock pair maps
them to CLOCK_MONOTONIC, samply's sample clock on Linux. For each request kind it sums on-CPU ms per thread name and
the top functions by inclusive and self CPU per thread over the kind's windows, divided by the number of requests
(ms per request).
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "perf-rearch"))
from samply_top import GENERIC, Symbolizer, load, strip_hash  # noqa: E402

TYPED = "\nconsole.log(Math.max(1, 2).toFixed(1));\n"


def kinds_of(trace):
    """Names completions the way trace-split.mjs does: by what was typed, the session's first on its own."""
    keys = trace["keys"]
    first = True
    out = {}
    for s in sorted(trace["samples"], key=lambda s: s["start"]):
        kind = s["kind"]
        if kind == "completion":
            if first:
                kind = "completion first"
            else:
                k = None
                for key in keys:
                    if key["at"] <= s["start"] + 0.01:
                        k = key
                line = "".join(x["ch"] for x in keys if x["round"] == k["round"] and x["key"] <= k["key"]).split("\n")[-1]
                m = re.search(r"[\w$)]*\.?$", line)
                kind = "completion " + (m.group(0) or line[-1:])
            first = False
        out[s.get("id")] = kind
    # The edited file's diagnostic pull is the first (lowest id) of each key's pulls, as in trace-split.mjs.
    for k in keys:
        pulls = [s for s in trace["samples"] if s["kind"] == "diagnostic" and s.get("id") is not None
                 and k["at"] - 0.01 <= s["start"] <= k["at"] + 5]
        if pulls:
            edited = min(s["id"] for s in pulls)
            for s in pulls:
                out[s["id"]] = "diagnostic edited" if s["id"] == edited else "diagnostic other"
    return out


def handled_lines(trace):
    out = {}
    for l in trace["logs"]:
        m = re.match(r"handled method '([^']+)' \((\d+)\) in (\S+)", l["message"])
        if m:
            out[int(m.group(2))] = (parse_duration(m.group(3)), l["at"])
    return out


def parse_duration(text):
    unit = {"ns": 1e-6, "µs": 1e-3, "us": 1e-3, "ms": 1.0, "s": 1000.0}
    return sum(float(v) * unit[u] for v, u in re.findall(r"([\d.]+)(ns|µs|us|ms|s)", text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", nargs=2, action="append", required=True, metavar=("TRACE", "PROFILE"))
    ap.add_argument("--kind")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--alone", action="store_true")
    ap.add_argument("--window", default="send")
    ap.add_argument("--idle", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--under")
    args = ap.parse_args()
    kind_rx = re.compile(args.kind) if args.kind else None

    per_kind_thread = defaultdict(Counter)
    incl = defaultdict(lambda: defaultdict(Counter))
    self_ = defaultdict(lambda: defaultdict(Counter))
    count = Counter()
    for trace_path, profile_path in args.pair:
        trace = json.load(open(trace_path))
        offset = trace["clock"]["monoMs"] - trace["clock"]["perfNow"]
        kinds = kinds_of(trace)
        handled = handled_lines(trace)
        samples = [s for s in trace["samples"] if s.get("id") is not None and not s.get("cancelled") and not s.get("error")]
        windows = []  # (lo, hi, kind) in mono ms
        for s in samples:
            kind = kinds[s["id"]]
            if kind_rx and not kind_rx.search(kind):
                continue
            if args.alone and any(o is not s and o["start"] < s["end"] and o["end"] > s["start"] for o in samples):
                continue
            lo, hi = s["start"], s.get("firstByte", s["end"])
            if args.window == "handler":
                h = handled.get(s["id"])
                if not h:
                    continue
                hi = min(h[1], hi)
                lo = hi - h[0]
            if args.window == "wait":
                h = handled.get(s["id"])
                if not h:
                    continue
                hi = min(h[1], hi) - h[0]
                if hi <= lo:
                    continue
            windows.append((lo + offset, hi + offset, kind))
            count[kind] += 1
        if args.idle:
            # Gaps with nothing in flight, from the first measured key to the last answer.
            spans = sorted((s["start"], s["end"]) for s in trace["samples"])
            t = trace["measureFrom"]
            for a, b in spans:
                if a > t + 1:
                    windows.append((t + offset, a + offset, "idle"))
                t = max(t, b)
            count["idle"] += 1  # per run
        windows.sort()
        profile, syms = load(profile_path)
        sym = Symbolizer(profile, syms)
        for th in profile["threads"]:
            tname = re.sub(r"[-_ ]?\d+$", "", th.get("name") or "?") or "?"
            S, ST, FT, FN, RT = th["samples"], th["stackTable"], th["frameTable"], th["funcTable"], th["resourceTable"]
            strings = th["stringArray"]
            cpu = S.get("threadCPUDelta") or [None] * S["length"]
            cache = {}

            def frames_of(fi):
                addr, func = FT["address"][fi], FT["func"][fi]
                res = FN["resource"][func]
                lib = RT["lib"][res] if res is not None and res >= 0 else None
                if addr is None or addr < 0 or lib is None:
                    return [strings[FN["name"][func]]]
                return sym.frames(lib, addr)

            def resolve(si):
                if si in cache:
                    return cache[si]
                chain = []
                s = si
                while s is not None:
                    chain.append(ST["frame"][s])
                    s = ST["prefix"][s]
                out = [strip_hash(f) for fi in reversed(chain) for f in frames_of(fi)]
                cache[si] = out
                return out

            wi = 0
            for i in range(S["length"]):
                si, tm = S["stack"][i], S["time"][i]
                if si is None:
                    continue
                w = cpu[i] / 1000.0 if cpu[i] is not None else profile["meta"]["interval"]
                if not w:
                    continue
                for lo, hi, kind in windows:
                    if lo <= tm <= hi:
                        stack = resolve(si)
                        if args.under:
                            at = next((j for j, f in enumerate(stack) if args.under in f), None)
                            if at is None:
                                break
                            stack = stack[at:]
                        per_kind_thread[kind][tname] += w
                        self_[kind][tname][stack[-1]] += w
                        for f in set(stack):
                            if not GENERIC.search(f):
                                incl[kind][tname][f] += w
                        break
    out = {}
    for kind in sorted(per_kind_thread, key=lambda k: -sum(per_kind_thread[k].values()) / count[k]):
        n = count[kind]
        threads = per_kind_thread[kind]
        print(f"\n=== {kind}: {n} {'runs' if kind == 'idle' else 'requests'}; CPU ms per {'run' if kind == 'idle' else 'request'} by thread: "
              + ", ".join(f"{t} {ms / n:.1f}" for t, ms in threads.most_common(6)))
        out[kind] = {"n": n, "threads": {t: ms / n for t, ms in threads.items()}, "incl": {}, "self": {}}
        for tname, _ in threads.most_common(2):
            print(f"  -- {tname}: inclusive")
            for f, ms in incl[kind][tname].most_common(args.top):
                print(f"    {ms / n:7.2f}  {short(f)}")
            print(f"  -- {tname}: self")
            for f, ms in self_[kind][tname].most_common(max(8, args.top // 2)):
                print(f"    {ms / n:7.2f}  {short(f)}")
            out[kind]["incl"][tname] = {f: ms / n for f, ms in incl[kind][tname].most_common(60)}
            out[kind]["self"][tname] = {f: ms / n for f, ms in self_[kind][tname].most_common(60)}
    if args.json:
        json.dump(out, open(args.json, "w"), indent=1)


def short(name, n=150):
    name = re.sub(r"<[^<>]*(<[^<>]*>[^<>]*)*>", "<…>", name) if len(name) > n else name
    return name if len(name) <= n else name[: n - 1] + "…"


if __name__ == "__main__":
    main()
