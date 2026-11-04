#!/usr/bin/env python3
"""Loader-thread breakdown of a samply profile of tsc-rs (scratch, design-loader lane).

usage: python3 -I loader_thread.py PROFILE.json.gz [--anchor FUNC] [--depth N] [--top N] [--spans F1,F2,...]
Finds the threads whose samples contain FilesParser::run_queue (the serial loader), then:
  - spans: first/last sample time (ms since the thread's first sample) and on-CPU ms of each named function;
  - children: on-CPU ms of the frames directly below --anchor (default process_all_program_files), recursively to --depth.
"""
import sys
from collections import Counter, defaultdict
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import samply_top as st  # noqa: E402

args = sys.argv[1:]
path = args[0]
def opt(name, default):
    return args[args.index(name) + 1] if name in args else default
anchor = opt("--anchor", "process_all_program_files")
depth = int(opt("--depth", "2"))
top = int(opt("--top", "12"))
spans = opt("--spans", "process_all_program_files,FilesParser::run_queue,get_processed_files,publish_file_stores,"
                       "bind_all,bind_source_files,new_program_of_parts,create_program,fill_include_diagnostics,"
                       "take_prefetched,ParseTask::load,resolve_imports_and_module_augmentations,"
                       "load_source_file_meta_data,parse_source_file,adopt_detached,FilesParser::start,"
                       "run_queued,get_source_file,ls_program::new_program").split(",")

profile, syms = st.load(path)
sym = st.Symbolizer(profile, syms)
t0 = min((t["samples"]["time"][0] for t in profile["threads"] if t["samples"]["length"]), default=0)

def stacks(t):
    S, ST, FT, FN, RT = t["samples"], t["stackTable"], t["frameTable"], t["funcTable"], t["resourceTable"]
    strings = t["stringArray"]
    cpu = S.get("threadCPUDelta") or [None] * S["length"]
    cache = {}
    def frames_of(fi):
        addr = FT["address"][fi]; func = FT["func"][fi]; res = FN["resource"][func]
        lib = RT["lib"][res] if res is not None and res >= 0 else None
        if addr is None or addr < 0 or lib is None:
            return [strings[FN["name"][func]]]
        return sym.frames(lib, addr)
    def resolve(si):
        if si in cache: return cache[si]
        chain = []; s = si
        while s is not None:
            chain.append(ST["frame"][s]); s = ST["prefix"][s]
        out = []
        for fi in reversed(chain): out.extend(frames_of(fi))
        out = [st.strip_hash(f) for f in out]
        cache[si] = out
        return out
    for i in range(S["length"]):
        si = S["stack"][i]
        if si is None: continue
        w = (cpu[i] / 1000.0) if cpu[i] is not None else profile["meta"]["interval"]
        if not w: continue
        yield S["time"][i] - t0, w, resolve(si)

def short(f):
    return st.short(f, 100)

for t in profile["threads"]:
    samples = list(stacks(t))
    marker = opt("--marker", "FilesParser::run_queue")
    if not any(any(marker in f for f in s) for _, _, s in samples):
        continue
    total = sum(w for _, w, _ in samples)
    print(f"## thread {t.get('name')} tid {t.get('tid')}: on-CPU {total:.0f} ms, samples {samples[0][0]:.0f}..{samples[-1][0]:.0f} ms")
    print("\n| function | first ms | last ms | on-CPU ms |\n|---|---|---|---|")
    for name in spans:
        hit = [(tm, w) for tm, w, s in samples if any(name in f for f in s)]
        if hit:
            print(f"| `{name}` | {hit[0][0]:.0f} | {hit[-1][0]:.0f} | {sum(w for _, w in hit):.0f} |")
    # children tree under anchor
    tree = defaultdict(Counter)
    for tm, w, s in samples:
        idx = next((i for i, f in enumerate(s) if anchor in f), None)
        if idx is None: continue
        path = []
        for d in range(1, depth + 1):
            if idx + d >= len(s): path.append("(self)"); break
            path.append(s[idx + d])
            tree[tuple(path[:-1])][path[-1]] += w
        else:
            pass
    def show(prefix, level):
        for f, w in tree[prefix].most_common(top):
            print(f"{'  ' * level}- {w:6.0f} ms  {short(f)}")
            if level + 1 < depth:
                show(prefix + (f,), level + 1)
    print(f"\nframes under `{anchor}` (depth {depth}):")
    show((), 0)
    print()
