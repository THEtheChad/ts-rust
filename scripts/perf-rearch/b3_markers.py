#!/usr/bin/env python3
"""Lane B3: list the `// Go:` markers that name nothing at the pin, with hints for classifying them.

usage: scripts/perf-rearch/b3_markers.py [<rev>] [--out FILE]   (default rev HEAD, out stdout as JSON lines)
       scripts/perf-rearch/b3_markers.py help

For each Rust production function whose `// Go:` marker gorel.py does not resolve (the
`markers_unresolved` count of `quality.sh report`), one JSON object:
  key, file, fn, start, end, cog       the Rust function (rca)
  marker                               the first `// Go:` marker text
  path, line, recv, name               the marker parsed (gorel.parse_marker)
  scope                                "own-dir" (a DIR_PKGS [] directory: effect/, gostd/, the load views),
                                       "non-pin-file" (the marker's Go file is in no package at the pin:
                                       Go's standard library, x/text, a file removed upstream),
                                       "external-file" (a file whose markers mostly name code outside the pin),
                                       "pin" (otherwise)
  file_at_pin                          the marker's Go file exists at the pin (gorel.GoIndex.files_named)
  in_file                              Go functions of that name in that file (id, recv, line)
  elsewhere                            Go functions of that exact name anywhere at the pin (id, recv)
  norm_elsewhere                       Go functions whose normalized name matches (case or `_` differences)
  closure                              Go named closures of that name (parent id)
  name_match, name_conf                the name matcher's placement of the Rust function
  conf                                 gorel's final confidence for the function
Reads the sources with git cat-file and the Go side from the gocog cache (quality.sh report fills it).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gorel  # noqa: E402
import quality  # noqa: E402


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    rev = argv[0] if argv else "HEAD"
    sha = subprocess.run(["git", "rev-parse", rev], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
    snap = quality.build_snapshot(sha, False)
    gof = gorel.go_functions(quality.CACHE)
    gi = gorel.GoIndex(gof["funcs"])
    files = sorted({f["file"] for f in snap["funcs"]})
    lines_of = gorel.read_blobs(sha, files, quality.REPO)
    rfs = gorel.map_functions(snap["funcs"], lines_of, gi)
    # external files, as map_functions computes them
    marks: dict[str, list[int]] = {}
    for rf in rfs:
        if rf["marker"]:
            n = marks.setdefault(rf["file"], [0, 0])
            n[0] += 1
            n[1] += rf["conf"].startswith(("marker", "prose"))
    external = {p for p, (n, ok) in marks.items() if n >= 3 and ok < 0.5 * n}
    rows = []
    for rf in rfs:
        if not rf["marker"] or rf["conf"].startswith(("marker", "prose")):
            continue
        mks = [m for m in gorel.order_markers(gorel.markers_above(lines_of[rf["file"]], rf["start"]), rf) if m["kind"] == "go"]
        mk = mks[0]
        pkgs = gi.path_pkgs(rf["file"])
        anywhere = gi.files_named(mk["path"], set()) if mk["path"] else []
        if pkgs is None:
            scope = "own-dir"
        elif mk["path"] and not anywhere:
            scope = "non-pin-file"
        elif rf["file"] in external:
            scope = "external-file"
        else:
            scope = "pin"
        files_at = gi.files_named(mk["path"], pkgs or set()) if mk["path"] else []
        name = mk["name"]
        rows.append({
            "key": rf["key"], "file": rf["file"], "fn": rf["name"], "start": rf["start"], "end": rf["end"], "cog": rf["cog"],
            "marker": rf["marker"], "path": mk["path"], "line": mk["line"], "recv": mk["recv"], "name": name,
            "scope": scope, "file_at_pin": bool(files_at), "file_anywhere": [k[0] + "/" + k[1] for k in anywhere][:4],
            "in_file": [(g["id"], g["recv"], g["line"]) for k in files_at for g in gi.in_file[k] if g["name"] == name],
            "elsewhere": [(g["id"], g["recv"]) for g in gi.by_name.get(name, [])][:12],
            "norm_elsewhere": [(g["id"], g["recv"], g["name"]) for g in gi.by_norm.get(gorel.norm(name), []) if g["name"] != name][:12],
            "closure": [g["id"] for g, c in gi.closures.get(gorel.norm(name), ()) if c["name"] == name][:6],
            "name_match": rf["name_match"], "name_conf": rf["name_conf"], "conf": rf["conf"],
        })
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if out:
        Path(out).write_text(text)
        print(f"{len(rows)} unresolved markers -> {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
