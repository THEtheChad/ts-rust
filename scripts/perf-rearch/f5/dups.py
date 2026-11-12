#!/usr/bin/env python3
"""Lane F5: Go functions with Rust ports in more than one file family (B3's `dups.txt`).

usage: scripts/perf-rearch/f5/dups.py <calls.jsonl> [--json]
  <calls.jsonl>   the output of `scripts/perf-rearch/b3_markers.py calls <rev> --out FILE`

A file family is a Rust file without its `_pN` part (gorel.family). Each line:
  GoName [pkg/file.go:line] gocog=N :: rustfile:line fn cogN missA/B | ...
sorted by Go cognitive complexity, largest first. `--json` prints one object per Go function.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gorel  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    rows = [json.loads(line) for line in Path(argv[0]).read_text().splitlines() if line.strip()]
    by_go: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_go[r["go"]].append(r)
    dups = []
    for go, rs in by_go.items():
        fams = {gorel.family(r["file"]) for r in rs}
        if len(fams) > 1:
            dups.append((go, rs))
    dups.sort(key=lambda d: (-d[1][0]["go_cog"], d[1][0]["go_name"]))
    if "--json" in argv:
        for go, rs in dups:
            print(json.dumps({"go": go, "go_name": rs[0]["go_name"], "go_cog": rs[0]["go_cog"],
                              "rust": [{"file": r["file"], "start": r["start"], "fn": r["fn"], "cog": r["cog"],
                                        "conf": r["conf"]} for r in rs]}))
        return 0
    print(f"{len(dups)} Go functions with Rust ports in >1 file family")
    strip = "crates/ts_goport/src/"
    for go, rs in dups:
        r0 = rs[0]
        parts = " | ".join(
            f"{r['file'].removeprefix(strip)}:{r['start']} {r['fn']} cog{r['cog']} miss{len(r['missing'])}/{len(r['go_calls'])}"
            for r in sorted(rs, key=lambda r: (r["file"], r["start"])))
        print(f"{r0['go_name']} [{go}] gocog={r0['go_cog']} :: {parts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
