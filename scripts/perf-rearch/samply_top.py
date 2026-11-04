#!/usr/bin/env python3
"""Summarizes a samply profile (samply-user record --save-only --unstable-presymbolicate) of tsc-rs or Go.

usage: python3 -I scripts/perf-rearch/samply_top.py PROFILE.json.gz [options]
  --top N            rows per table (default 25)
  --window FILE      only samples inside probe-edit.mjs's --window file (monoStart..monoEnd, CLOCK_MONOTONIC ms,
                     samply's sample clock on Linux)
  --from MS --to MS  same, as CLOCK_MONOTONIC ms
  --thread REGEX     only threads whose name matches
  --json FILE        also write the tables as JSON
  --callers FUNC     the callers (inclusive CPU) of functions whose name contains FUNC
  --timeline MS      CPU per phase in slices of MS ms (where the serial stretches are)
  --raw-threads      keep thread numbers (checker-3) instead of merging threads by name
  --addr2line        resolve frames with `addr2line -i` from the binary instead of the .syms.json sidecar
                     (use when the sidecar is missing)

Weights are on-CPU time: samply's threadCPUDelta per sample (µs). Frames come from the .syms.json sidecar,
inlined frames included (innermost first in the sidecar, expanded outer to inner here).
Tables: CPU per thread name; CPU per phase (the innermost frame whose module names a phase; see PHASES);
phase x thread; top functions by self and by inclusive CPU, each with its phase.
"""
import argparse
import gzip
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# (phase, regex on the demangled function name). The innermost matching frame of a sample names its phase:
# a parse that the checker triggers counts as parse, a checker query from declaration emit counts as check.
P = r"(?:ts_goport|goport_\w+)::"  # the crate is ts_goport; its lib is goport_core
PHASES = [
    ("parse", P + r"(frontend::)?(parser|scanner|scanner_util)\b|" + P + r"(frontend::)?ast_factory|" + P + r"jsdoc"),
    ("bind", P + r"binder\b"),
    ("check", P + r"(checker|evaluator|pseudochecker|effect)\b"),
    ("emit", P + r"(emitter|printer|transformers|declarations|sourcemap|transpile|outputpaths)\b"),
    ("load/resolve", P + r"(frontend::)?(module|compiler|vfs|tspath|tsoptions|packagejson|osutil|nativepath|"
                     r"core_nodemodules|bundled|program|options|options_json|modulespecifiers)\b"),
    ("lsp", P + r"(ls|lsp|project|jsonrpc|api|format|astnav|fswatch|ipc|lsproto)\b"),
    ("diagnostics", P + r"(diagnostics|diag|diagnostics_loc|locale)\b"),
    ("cmd", P + r"(cmd|execute)\b"),
]
# Go oracle frames (package paths).
GO_PHASES = [
    ("parse", r"internal/(parser|scanner)\."),
    ("bind", r"internal/binder\."),
    ("check", r"internal/checker\."),
    ("emit", r"internal/(printer|transformers|declarations|sourcemap)\."),
    ("load/resolve", r"internal/(module|compiler|vfs|tspath|tsoptions|packagejson)\."),
    ("lsp", r"internal/(ls|lsp|project)\."),
]
# Frames left out of the inclusive table (thread entry, closures, iterator plumbing).
GENERIC = re.compile(r"^(<?(std|core|alloc)::|start_thread|clone3|__libc_start|_start$|main$|rayon|crossbeam|"
                     r"<rayon|<crossbeam|runtime\.goexit|runtime\.main)")
ALLOC = re.compile(r"(^|::)(je_|_rjem_|mallocx|sdallocx|malloc|free|realloc|calloc|__rust_alloc|__rust_dealloc|"
                   r"alloc::alloc|mi_|__munmap|munmap|mmap|madvise)")


def load(path):
    p = json.load(gzip.open(path)) if str(path).endswith(".gz") else json.load(open(path))
    sidecar = Path(re.sub(r"\.json(\.gz)?$", ".json.syms.json", str(path)))
    syms = json.load(open(sidecar)) if sidecar.exists() else None
    return p, syms


class Symbolizer:
    def __init__(self, profile, syms, use_addr2line=False):
        self.libs = profile["libs"]
        self.by_lib = {}
        self.cache = {}
        self.use_addr2line = use_addr2line
        if syms:
            st = syms["string_table"]
            for lib in syms["data"]:
                table = lib["symbol_table"]
                addrs = {}
                for addr, idx in lib.get("known_addresses", []):
                    e = table[idx]
                    frames = [st[f["function"]] for f in e.get("frames") or [] if f.get("function") is not None]
                    frames = frames or [st[e["symbol"]]]
                    addrs[addr] = list(reversed(frames))  # outer -> inner
                self.by_lib[(lib["debug_name"], lib.get("code_id"))] = addrs
                self.by_lib.setdefault((lib["debug_name"], None), addrs)

    def frames(self, lib_index, addr):
        key = (lib_index, addr)
        if key in self.cache:
            return self.cache[key]
        lib = self.libs[lib_index] if lib_index is not None else None
        out = None
        if lib:
            addrs = self.by_lib.get((lib["debugName"], lib.get("codeId"))) or self.by_lib.get((lib["debugName"], None))
            if addrs:
                out = addrs.get(addr)
            if out is None and self.use_addr2line and lib.get("path"):
                out = addr2line(lib["path"], addr)
            if out is None:
                out = [f"{lib['debugName']}+0x{addr:x}"]
        else:
            out = [f"0x{addr:x}"]
        self.cache[key] = out
        return out


def addr2line(binary, addr):
    try:
        r = subprocess.run(["addr2line", "-i", "-f", "-C", "-e", binary, hex(addr)], capture_output=True, text=True,
                           timeout=30)
        names = r.stdout.splitlines()[0::2]
        return list(reversed(names)) if names and names[0] != "??" else None
    except Exception:
        return None


GO_RUNTIME = re.compile(r"^(runtime\.|internal/runtime/|memeqbody|aeshash|sync\.|sync/atomic\.|indexbytebody|gogo$|"
                        r"cmpbody|internal/bytealg|github\.com/zeebo/xxh3)")
GO_GC = re.compile(r"^runtime\.(gcBgMarkWorker|gcDrain|bgsweep|bgscavenge|gcAssist|mallocgc|growslice|newobject|"
                   r"makeslice|makemap|mapassign|sweepone|scanobject|markroot)|^internal/runtime/gc/")


def classify(stack, rules, go=False):
    """The phase of the innermost frame that names one. Go: runtime frames (maps, hashing, atomics) belong to
    the package frame that calls them; a stack with no package frame is gc/runtime. Stacks with a GC or
    allocation frame are split out as gc/alloc (under any phase) by analyze()."""
    for name in reversed(stack):  # innermost first
        if go and GO_RUNTIME.search(name):
            continue
        for phase, rx in rules:
            if rx.search(name):
                return phase
    return "gc/runtime" if go else "other"


def strip_hash(name):
    return re.sub(r"::h[0-9a-f]{16}$", "", name)


def analyze(profile, syms, args):
    sym = Symbolizer(profile, syms, args.addr2line)
    rules_rs = [(p, re.compile(r)) for p, r in PHASES]
    rules_go = [(p, re.compile(r)) for p, r in GO_PHASES]
    lo = hi = None
    if args.window:
        w = json.load(open(args.window))
        lo, hi = w["monoStart"], w["monoEnd"]
    if args.from_ is not None:
        lo = args.from_
    if args.to is not None:
        hi = args.to
    thread_rx = re.compile(args.thread) if args.thread else None
    self_t, incl_t = Counter(), Counter()
    func_phase = {}
    leaf_phase = defaultdict(Counter)
    gc_ms = Counter()
    timeline = defaultdict(Counter)
    t_first = min((t["samples"]["time"][0] for t in profile["threads"] if t["samples"]["length"]), default=0)
    if lo is not None:
        t_first = max(t_first, lo)
    by_thread, by_phase, phase_thread = Counter(), Counter(), defaultdict(Counter)
    alloc_self = 0.0
    callers = defaultdict(Counter)
    total = 0.0
    for t in profile["threads"]:
        name = t.get("name") or "?"
        if not args.raw_threads:
            name = re.sub(r"[-_ ]?\d+$", "", name) or name
        if thread_rx and not thread_rx.search(t.get("name") or ""):
            continue
        S, ST, FT, FN, RT = t["samples"], t["stackTable"], t["frameTable"], t["funcTable"], t["resourceTable"]
        strings = t["stringArray"]
        cpu = S.get("threadCPUDelta") or [None] * S["length"]
        weight = S.get("weight") or [1] * S["length"]
        stack_cache = {}

        def frames_of(fi):
            addr = FT["address"][fi]
            func = FT["func"][fi]
            res = FN["resource"][func]
            lib = RT["lib"][res] if res is not None and res >= 0 else None
            if addr is None or addr < 0 or lib is None:
                return [strings[FN["name"][func]]]
            return sym.frames(lib, addr)

        def resolve(si):
            if si in stack_cache:
                return stack_cache[si]
            chain = []
            s = si
            while s is not None:
                chain.append(ST["frame"][s])
                s = ST["prefix"][s]
            out = []
            for fi in reversed(chain):
                out.extend(frames_of(fi))
            out = [strip_hash(f) for f in out]
            stack_cache[si] = out
            return out

        for i in range(S["length"]):
            si = S["stack"][i]
            if si is None:
                continue
            tm = S["time"][i]
            if lo is not None and (tm < lo or tm > hi):
                continue
            w = (cpu[i] / 1000.0) if cpu[i] is not None else weight[i] * profile["meta"]["interval"]
            if not w:
                continue
            stack = resolve(si)
            go = any("internal/" in f or f.startswith("runtime.") for f in stack[-6:])
            phase = classify(stack, rules_go if go else rules_rs, go)
            if go and any(GO_GC.search(f) for f in stack):
                gc_ms[phase] += w
            total += w
            if args.timeline:
                timeline[int((tm - t_first) // args.timeline)][phase] += w
            by_thread[name] += w
            by_phase[phase] += w
            phase_thread[phase][name] += w
            leaf = stack[-1]
            self_t[leaf] += w
            leaf_phase[leaf][phase] += w
            if ALLOC.search(leaf):
                alloc_self += w
            for f in set(stack):
                if GENERIC.search(f):
                    continue
                incl_t[f] += w
                if f not in leaf_phase:
                    func_phase.setdefault(f, classify([f], rules_go if go else rules_rs, go))
            if args.callers:  # noqa
                for j, f in enumerate(stack):
                    if args.callers in f and j > 0:
                        callers[f][stack[j - 1]] += w
    for f, c in leaf_phase.items():
        func_phase[f] = c.most_common(1)[0][0]
    return {"total_ms": total, "by_thread": by_thread, "by_phase": by_phase, "phase_thread": phase_thread,
            "self": self_t, "incl": incl_t, "func_phase": func_phase, "alloc_self_ms": alloc_self,
            "callers": callers, "gc_ms": gc_ms, "timeline": timeline}


def short(name, n=110):
    name = re.sub(r"<[^<>]*(<[^<>]*>[^<>]*)*>", "<…>", name) if len(name) > n else name
    return name if len(name) <= n else name[: n - 1] + "…"


def report(r, top):
    tot = r["total_ms"] or 1
    out = [f"total on-CPU {tot:.0f} ms; allocator/munmap self {r['alloc_self_ms']:.0f} ms "
           f"({100 * r['alloc_self_ms'] / tot:.1f}%)", "", "| thread | CPU ms | % |", "|---|---|---|"]
    for k, v in r["by_thread"].most_common(top):
        out.append(f"| {k} | {v:.0f} | {100 * v / tot:.1f} |")
    gc = r.get("gc_ms") or {}
    out += ["", "| phase | CPU ms | % | top threads |" + (" of it Go GC/alloc ms |" if gc else ""),
            "|---|---|---|---|" + ("---|" if gc else "")]
    for k, v in r["by_phase"].most_common():
        th = ", ".join(f"{n} {ms:.0f}" for n, ms in r["phase_thread"][k].most_common(3))
        out.append(f"| {k} | {v:.0f} | {100 * v / tot:.1f} | {th} |" + (f" {gc.get(k, 0):.0f} |" if gc else ""))
    for title, c in (("self", r["self"]), ("inclusive", r["incl"])):
        out += ["", f"| top {title} | phase | CPU ms | % |", "|---|---|---|---|"]
        for k, v in c.most_common(top):
            out.append(f"| `{short(k)}` | {r['func_phase'].get(k, '')} | {v:.0f} | {100 * v / tot:.1f} |")
    if r.get("timeline"):
        phases = [k for k, _ in r["by_phase"].most_common()]
        out += ["", "timeline: CPU ms per phase in each slice (slice start, ms since the first sample)", "",
                "| from | " + " | ".join(phases) + " |", "|---|" + "---|" * len(phases)]
        step = r["timeline_step"]
        for b in sorted(r["timeline"]):
            c = r["timeline"][b]
            out.append(f"| {b * step:.0f} | " + " | ".join(f"{c.get(k, 0):.0f}" for k in phases) + " |")
    for f, cs in r["callers"].items():
        out += ["", f"callers of `{short(f)}`:"] + [f"  {v:8.0f} ms  {short(k)}" for k, v in cs.most_common(10)]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("profile")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--window")
    ap.add_argument("--from", dest="from_", type=float)
    ap.add_argument("--to", type=float)
    ap.add_argument("--thread")
    ap.add_argument("--json")
    ap.add_argument("--callers")
    ap.add_argument("--addr2line", action="store_true")
    ap.add_argument("--raw-threads", action="store_true")
    ap.add_argument("--timeline", type=float, help="slice width in ms")
    args = ap.parse_args()
    profile, syms = load(args.profile)
    r = analyze(profile, syms, args)
    r["timeline_step"] = args.timeline
    print(report(r, args.top))
    if args.json:
        j = {k: (dict(v.most_common(200)) if isinstance(v, Counter) else v) for k, v in r.items()
             if k not in ("phase_thread", "callers", "func_phase", "timeline", "timeline_step")}
        j["phase_thread"] = {k: dict(v) for k, v in r["phase_thread"].items()}
        j["gc_ms"] = dict(r["gc_ms"])
        Path(args.json).write_text(json.dumps(j, indent=1))


if __name__ == "__main__":
    main()
