#!/usr/bin/env python3
"""Compares the server-to-client requests (client/registerCapability, unregisterCapability, ...)
and the pulled diagnostics of load-dump-session.mjs runs: tsc-rs (S1a on/off) against Go.
usage: python3 -I compare-requests.py GO.json RS.json [RS.json ...]
Prints, per RS file, EXACT (same requests in the same order), SAME-NET (the same
registrations in force at the end, with the same watcher globs; ids compared without their
counters, transient register/unregister pairs dropped) or DIFF with the first difference."""
import json, re, sys

def load(p):
    return json.load(open(p))

def net_registrations(reqs):
    """The registrations still in force at the end (a register later unregistered is
    dropped), keyed by method and id with the watcher counter ("watcher 9.0") removed,
    each with its sorted watcher globs (or its options)."""
    live = {}
    for r in reqs:
        if r["method"] == "client/registerCapability":
            for reg in r["params"]["registrations"]:
                live[reg["id"]] = reg
        elif r["method"] == "client/unregisterCapability":
            for reg in r["params"].get("unregisterations") or r["params"].get("unregistrations") or []:
                live.pop(reg["id"], None)
    out = {}
    for rid, reg in live.items():
        key = (reg["method"], re.sub(r" watcher \d+\.\d+$", "", rid))
        ws = (reg.get("registerOptions") or {}).get("watchers")
        out[key] = sorted(json.dumps(w, sort_keys=True) for w in ws) if ws is not None else json.dumps(reg.get("registerOptions"), sort_keys=True)
    return out

watchers = net_registrations

go = load(sys.argv[1])
gw = watchers(go["serverRequests"])
print(f"go: {len(go['serverRequests'])} requests, {sum(len(v) if isinstance(v, list) else 1 for v in gw.values())} watchers")
for p in sys.argv[2:]:
    rs = load(p)
    diag = "diags-same" if rs["diags"] == go["diags"] else "DIAGS-DIFF"
    if rs["serverRequests"] == go["serverRequests"]:
        print(f"EXACT {p} ({diag})"); continue
    rw = watchers(rs["serverRequests"])
    if rw == gw:
        print(f"SAME-NET {p} ({diag})"); continue
    for k in sorted(set(gw) | set(rw), key=str):
        if gw.get(k) != rw.get(k):
            a, b = gw.get(k), rw.get(k)
            if isinstance(a, list) and isinstance(b, list):
                print(f"DIFF {p} {k}: only go {sorted(set(a)-set(b))[:5]} only rs {sorted(set(b)-set(a))[:5]} ({diag})")
            else:
                print(f"DIFF {p} {k}: go {str(a)[:300]} rs {str(b)[:300]} ({diag})")
            break
