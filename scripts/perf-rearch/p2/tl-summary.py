#!/usr/bin/env python3
"""P2: one line per open12 timeline: init, each didOpen's handled time (server log), the first answer.
  scripts/perf-rearch/p2/tl-summary.py FILE.json..."""
import json, re, sys

for f in sys.argv[1:]:
    d = json.load(open(f))
    opens = []
    for e in d["events"]:
        m = re.match(r"handled method '([^']+)' in (.*)", e.get("text", ""))
        if m and m.group(1) == "textDocument/didOpen":
            v = m.group(2)
            ms = float(v[:-2]) if v.endswith("ms") and not v.endswith("µs") else float(re.sub("[^0-9.]", "", v)) / (1000 if "µs" in v else 1)
            if v.endswith("s") and not v.endswith("ms") and "µs" not in v:
                ms = float(v[:-1]) * 1000
            opens.append((round(e["at"], 1), round(ms, 1)))
    gaps = []
    prev = 0
    for at, ms in opens:
        gaps.append(round(at - ms - prev, 1))  # time before the open's handler started, after the previous ended
        prev = at
    print(f"{f.split('/')[-1]:24s} init {d['init_ms']:6.1f} first {d['first_ms']:6.1f} end {d['answers'][0]['end']:6.1f} "
          f"opens(ms) {[o[1] for o in opens[:5]]} sum {sum(o[1] for o in opens):6.1f} idle-gaps {sum(gaps):5.1f} {gaps[:5]}")
