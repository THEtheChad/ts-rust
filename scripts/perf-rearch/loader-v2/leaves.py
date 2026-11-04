import sys, re
# usage: python3 -I leaves.py PROFILE.json.gz [N]  (top leaf frames of the loader thread under process_all_program_files)
from collections import Counter
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import samply_top as st
profile, syms = st.load(sys.argv[1]); sym = st.Symbolizer(profile, syms)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 40
leaf = Counter(); leaf2 = Counter(); total = 0
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
        total += w; leaf[st.short(s[-1], 90)] += w
        leaf2[st.short(s[-2],70)+"  <-  "+st.short(s[-1],50)] += w if len(s)>1 else 0
    break
print(f"total {total:.0f}")
for k,v in leaf.most_common(N): print(f"{v:6.1f}  {k}")
