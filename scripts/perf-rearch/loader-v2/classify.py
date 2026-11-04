"""Classify loader-thread on-CPU ms under process_all_program_files by leaf category.
usage: python3 -I classify.py PROFILE.json.gz"""
import sys, re
from collections import Counter
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import samply_top as st
profile, syms = st.load(sys.argv[1])
sym = st.Symbolizer(profile, syms)
OS = re.compile(r"^(statx|__statx|stat64|__stat64|fstatat|__fstatat64|__GI___fstatat64|open64|__open64|__libc_open64|openat|__openat64|__GI___openat64|__libc_openat64|readlink|__readlink|read|__libc_read|__GI___libc_read|close|__close|__GI___close|__libc_close|getdents64|__getdents64|opendir|readdir|__opendir|syscall|__GI___readlink)$|std::sys::.*::fs::(stat|lstat|readlink|File::open|readdir|try_statx|canonicalize|metadata)|std::fs::(metadata|read_link|read_to_string|read|File::open|read_dir|symlink_metadata)")
def cats(stack):
    s = " | ".join(stack)
    out = []
    if any(OS.search(f.split("<")[0].strip()) or OS.search(f) for f in stack): out.append("os-call")
    for name, pat in [("resolver", "resolve_module_name|resolve_type_reference|DefaultResolver|resolutionState|ResolutionState"),
                      ("snapshot-read", "get_file_by_path|cache_source_file"),
                      ("parse-cache/take", "take_prefetched|ParseCache|parsecache|acquire"),
                      ("accept-ahead", "accept_ahead|take_resolved_ahead|AheadSeed"),
                      ("meta/scope", "load_source_file_meta|package_scope|get_package_scope"),
                      ("start/subtask", "FilesParser::start|add_sub_task"),
                      ("import-names", "ImportNames"),
                      ("hash", "xxh|Xxh|blake|Blake|md5|Md5|sha|hash_text|content_hash|Hasher")]:
        if re.search(pat, s): out.append(name)
    return out
tot = Counter(); total = 0
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
        if not w: continue
        rows.append((w, frames(si)))
    if not any(any(re.search(r"FilesParser>?::run_queue", f) for f in s) for _, s in rows): continue
    for w, s in rows:
        if not any("process_all_program_files" in f for f in s): continue
        total += w
        c = cats(s)
        tot["os-call (any)"] += w if "os-call" in c else 0
        for k in c:
            if k != "os-call": tot[k] += w
            if k != "os-call" and "os-call" in c: tot[k+" & os"] += w
        if not c: tot["(uncategorized)"] += w
print(f"loader thread under process_all_program_files: {total:.0f} ms on-CPU (profiled)")
for k, v in sorted(tot.items(), key=lambda x: -x[1]): print(f"  {k:28s} {v:6.1f}")
