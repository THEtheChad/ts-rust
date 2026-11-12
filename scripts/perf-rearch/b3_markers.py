#!/usr/bin/env python3
"""Lane B3: list the `// Go:` markers that name nothing at the pin, with hints for classifying them.

usage: scripts/perf-rearch/b3_markers.py [unresolved] [<rev>] [--out FILE]   (default rev HEAD, out stdout as JSON lines)
       scripts/perf-rearch/b3_markers.py calls [<rev>] [--out FILE]
       scripts/perf-rearch/b3_markers.py help

`calls`: for each Rust function that gorel maps to one Go function at the pin (marker, prose or a
high name match; not a lifted closure, a compound or a method family), the names each side calls
and two staleness signals (the pre-pin port check of lane B3):
  key, file, fn, start, conf, go (id), go_name, cog, go_cog
  go_calls, rust_calls                 normalized callee names (lowercase, no `_`), keywords dropped
  missing                              Go callees with no Rust callee of the same normalized name
  miss_ratio                           len(missing) / len(go_calls)
  go_dyn                               the Go body calls a ts#64159 rooted-path API that treats encoded
                                       dynamic names (`^/~ts-uri~/`) case-sensitively (CaseSensitivity
                                       .ComparePaths/.ContainsPath/.StartsWithDirectory/
                                       .RelativeFilePathFromDirectory/.CommonDirectoryOfFiles/.PathKey/…)
  rust_dyn                             the Rust body calls a dynamic-aware helper (compare_rooted_text,
                                       contains_rooted_path, contains_path_key, relative_path_within_directory,
                                       relative_path_from_directory, to_path, canonical_dynamic_uri_path, …)
  rust_legacy                          the Rust body calls a helper that ignores dynamic names
                                       (get_canonical_file_name, compare_paths, contains_path,
                                       starts_with_directory, trim_file_path_prefix, get_relative_path_*)
A row with go_dyn and rust_legacy but not rust_dyn is the shape of Astra's W2-FE finding 2.

For each Rust production function whose `// Go:` marker gorel.py does not resolve, at the pin or in
the Go trees outside it (GOROOT `src`, x/text; the `markers_unresolved` count of `quality.sh
report`), one JSON object:
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
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gorel  # noqa: E402
import quality  # noqa: E402


def mapped(rev: str):
    sha = subprocess.run(["git", "rev-parse", rev], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
    snap = quality.build_snapshot(sha, False)
    gof = gorel.go_functions(quality.CACHE)
    gi = gorel.GoIndex(gof["funcs"])
    ext, _ = gorel.ext_functions(quality.CACHE)
    xi = gorel.GoIndex(ext) if ext else None
    files = sorted({f["file"] for f in snap["funcs"]})
    lines_of = gorel.read_blobs(sha, files, quality.REPO)
    rfs = gorel.map_functions(snap["funcs"], lines_of, gi, xi)
    return gof, gi, lines_of, rfs


GO_DYN_RE = re.compile(r"(?<!tspath)\.(CompareFilePaths|ComparePaths|CompareFileNameStems|ContainsFilePath|ContainsPath|StartsWithDirectory|"
                       r"RelativeFilePathFromDirectory|RelativePathWithinDirectory|CommonDirectoryOfFiles|SplitFilePathAtComponent|"
                       r"PathKey|PathKeyForFileName|RelativeTo|CaseInsensitiveKey)\((?!\))")
RUST_DYN_RE = re.compile(r"\b(compare_rooted_text|contains_rooted_path|contains_path_key|relative_path_within_directory|relative_path_from_directory|"
                         r"relative_path_from_normalized_paths|to_path|to_path_cow|canonical_dynamic_uri_path|is_encoded_dynamic_file_name|path_key\w*)\s*\(")
RUST_LEGACY_RE = re.compile(r"\b(get_canonical_file_name|compare_paths|compare_paths_case_\w+|contains_path|starts_with_directory|trim_file_path_prefix|"
                            r"get_relative_path_\w+|convert_to_relative_path|get_path_components_relative_to)\s*\(")
GO_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(?:\[[^\]()]*\])?\(")
RUST_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(?:::<[^>]*>)?(?:!)?\s*\(")
SKIP = {"if", "for", "switch", "return", "func", "len", "append", "make", "new", "panic", "cap", "copy", "delete", "min", "max",
        "string", "int", "byte", "rune", "uint32", "int32", "uint", "bool", "float64", "go", "defer", "range", "fn", "match", "while",
        "loop", "some", "ok", "err", "none", "assert", "debugassert", "debugassertne", "debugasserteq", "matches", "vec", "format",
        "unreachable", "println", "eprintln", "write", "writeln", "into", "clone", "unwrap", "expect", "asref", "asstr", "tostring",
        "to_owned", "toowned", "iter", "map", "collect", "is_some", "issome", "isnone", "unwrapor", "unwrapordefault", "default",
        "box", "rc", "arc", "self", "super", "crate", "as", "unsafe", "contains", "get", "push", "pop", "insert", "remove"}


def calls_of(text: str, rx) -> set[str]:
    return {gorel.norm(m.group(1)) for m in rx.finditer(text)} - SKIP


def go_body(gof: dict, g: dict) -> str:
    path = Path(gof["root"]) / g["pkg"] / g["file"]
    lines = path.read_text(errors="replace").split("\n")
    return "\n".join(lines[g["line"] - 1: g["end"]])


def cmd_calls(rev: str) -> list[dict]:
    gof, gi, lines_of, rfs = mapped(rev)
    rows = []
    for rf in rfs:
        g = rf["go"]
        if not g or rf["closure"] is not None or "+" in g["id"] or ":*" in g["id"] or rf["conf"] not in ("marker", "marker+split", "prose", "high"):
            continue
        gt = go_body(gof, g)
        rt = "\n".join(lines_of[rf["file"]][rf["start"] - 1: rf["end"]])
        gc, rc = calls_of(gt, GO_CALL_RE), calls_of(rt, RUST_CALL_RE)
        gc.discard(gorel.norm(g["name"]))
        rc.discard(gorel.norm(rf["fn"]))
        missing = sorted(gc - rc)
        rows.append({
            "key": rf["key"], "file": rf["file"], "fn": rf["name"], "start": rf["start"], "conf": rf["conf"], "go": g["id"],
            "go_name": (g["recv"] + "." if g["recv"] else "") + g["name"], "cog": rf["cog"], "go_cog": g["cog"],
            "go_calls": sorted(gc), "rust_calls": sorted(rc), "missing": missing,
            "miss_ratio": round(len(missing) / len(gc), 3) if gc else 0.0,
            "go_dyn": sorted({m.group(1) for m in GO_DYN_RE.finditer(gt)}),
            "rust_dyn": sorted({m.group(1) for m in RUST_DYN_RE.finditer(rt)}),
            "rust_legacy": sorted({m.group(1) for m in RUST_LEGACY_RE.finditer(rt)}),
        })
    return rows


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    cmd = "unresolved"
    if argv and argv[0] in ("unresolved", "calls"):
        cmd, argv = argv[0], argv[1:]
    rev = argv[0] if argv else "HEAD"
    rows = cmd_calls(rev) if cmd == "calls" else cmd_unresolved(rev)
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if out:
        Path(out).write_text(text)
        print(f"{len(rows)} rows ({cmd}) -> {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_unresolved(rev: str) -> list[dict]:
    _, gi, lines_of, rfs = mapped(rev)
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
        if not rf["marker"] or rf["conf"].startswith(("marker", "prose", "ext")):
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
    return rows

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
