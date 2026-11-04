#!/usr/bin/env python3
"""Compares load dumps (GOPORT_LOAD_DUMP) section by section.
usage: python3 -I dump-sections.py OFF_A OFF_B ON_A ON_B [SERIAL]
Each `== kind ==` section is hashed. Per section index it prints:
  same       every dump has the same text
  S1A-DIFF   the S1a-off runs agree with each other but an S1a-on run differs (a failure)
  UNSTABLE   the two S1a-off runs differ from each other (the section is not
             deterministic without S1a); the S1a-on runs are listed against the off set
  SERIAL-DIFF the off runs agree, S1a agrees, but the serial run (no resolve ahead) differs
Ends with SECTIONS OK when no section is S1A-DIFF."""
import hashlib, sys

def sections(path):
    out, cur, kind = [], [], None
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("== ") and line.rstrip().endswith(" =="):
            if kind is not None:
                out.append((kind, hashlib.sha256("".join(cur).encode()).hexdigest()[:12], len(cur)))
            kind, cur = line.strip(), []
        else:
            cur.append(line)
    if kind is not None:
        out.append((kind, hashlib.sha256("".join(cur).encode()).hexdigest()[:12], len(cur)))
    return out

files = sys.argv[1:]
dumps = [sections(f) for f in files]
n = max(len(d) for d in dumps)
fail = False
for i in range(n):
    row = [d[i] if i < len(d) else ("<none>", "-", 0) for d in dumps]
    kinds = {r[0] for r in row}
    h = [r[1] for r in row]
    off_a, off_b, on_a, on_b = h[:4]
    serial = h[4] if len(h) > 4 else None
    label = f"#{i} {row[0][0]} ({row[0][2]} lines)"
    if len(kinds) > 1:
        print(f"S1A-DIFF {label}: section kinds differ {kinds}"); fail = True
    elif len(set(h)) == 1:
        print(f"same     {label}")
    elif off_a != off_b:
        print(f"UNSTABLE {label}: off {off_a} {off_b}, on {on_a} {on_b}" + (f", serial {serial}" if serial else "")
              + (" (on within the off set)" if {on_a, on_b} <= {off_a, off_b} else " (on outside the off set)"))
    elif on_a != off_a or on_b != off_a:
        print(f"S1A-DIFF {label}: off {off_a}, on {on_a} {on_b}"); fail = True
    else:
        print(f"SERIAL-DIFF {label}: off/on {off_a}, serial {serial}")
print("SECTIONS OK" if not fail else "SECTIONS FAIL")
