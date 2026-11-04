import sys, re
# usage: python3 -I callers.py PROFILE.json.gz LEAF_REGEX [N]  (callers of matching leaf frames on the loader thread)
from collections import Counter
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import samply_top as st
profile, syms = st.load(sys.argv[1]); sym = st.Symbolizer(profile, syms)
leafpat = re.compile(sys.argv[2]); N=int(sys.argv[3]) if len(sys.argv)>3 else 15
c = Counter(); tot=0
for t in profile["threads"]:
    S, STb, FT, FN, RT = t["samples"], t["stackTable"], t["frameTable"], t["funcTable"], t["resourceTable"]
    strings = t["stringArray"]; cpu = S.get("threadCPUDelta") or [None]*S["length"]
    cache = {}
    def frames(si):
        if si in cache: return cache[si]
        chain=[]; s=si
        while s is not None: chain.append(STb["frame"][s]); s=STb["prefix"][s]
        out=[]
        for fi in reversed(chain):
            addr=FT["address"][fi]; func=FT["func"][fi]; res=FN["resource"][func]
            lib=RT["lib"][res] if res is not None and res>=0 else None
            out.extend([strings[FN["name"][func]]] if addr is None or addr<0 or lib is None else sym.frames(lib, addr))
        out=[st.strip_hash(f) for f in out]; cache[si]=out; return out
    rows=[]
    for i in range(S["length"]):
        si=S["stack"][i]
        if si is None: continue
        w=(cpu[i]/1000.0) if cpu[i] is not None else profile["meta"]["interval"]
        if w: rows.append((w, frames(si)))
    if not any(any(re.search(r"FilesParser>?::run_queue", f) for f in s) for _, s in rows): continue
    for w, s in rows:
        if not any("process_all_program_files" in f for f in s): continue
        if not leafpat.search(s[-1]): continue
        tot+=w
        # first 'interesting' caller frames (skip std/alloc/core)
        chain=[f for f in reversed(s[:-1]) if not re.match(r"^(<?(alloc|core|std)::|<alloc|<core|<std|_|rjem|do_r)", f)]
        c[" < ".join(st.short(f,60) for f in chain[:3])]+=w
    break
print(f"leaf /{sys.argv[2]}/ total {tot:.1f} ms")
for k,v in c.most_common(N): print(f"{v:6.1f}  {k}")
