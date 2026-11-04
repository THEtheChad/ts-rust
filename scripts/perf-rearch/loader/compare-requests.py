#!/usr/bin/env python3
"""Compares the recorded protocol of load-dump-session.mjs runs byte for byte: every
server-to-client request and notification with its payload, every response, and the
pulled diagnostics in full. No normalization.
usage: python3 -I compare-requests.py REF.json OTHER.json [OTHER.json ...]
Prints, per OTHER file, EXACT or DIFF with the path and the two values of the first
difference."""
import json, sys

KEYS = ("serverRequests", "notifications", "responses", "diags")

def first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return path, a, b
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>")
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d: return d
        return None
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d: return d
        if len(a) != len(b):
            return f"{path}.length", len(a), len(b)
        return None
    return None if a == b else (path, a, b)

ref = json.load(open(sys.argv[1]))
for p in sys.argv[2:]:
    other = json.load(open(p))
    d = first_diff({k: ref.get(k) for k in KEYS}, {k: other.get(k) for k in KEYS})
    if d is None:
        print(f"EXACT {p}")
    else:
        path, a, b = d
        print(f"DIFF {p} at {path}: {json.dumps(a)[:300]} vs {json.dumps(b)[:300]}")
