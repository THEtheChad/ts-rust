#!/usr/bin/env python3
"""Go `core.IfElse` / `core.OrElse` sites per Go function, against the Rust port's `if_else` /
`or_else` uses in the function its `// Go:` marker names (lane Q8, `docs/perf-rearch/cleanup/q8.md`).

usage: idiom_sites.py [--go-cache FILE] [--pkg checker,ls] [--all]

Prints one row per Go function that calls `core.IfElse` or `core.OrElse`: Go counts, Rust counts
(summed over every Rust fn whose marker names that Go function, closures and nested helpers
included) and the Rust locations. By default only rows where Rust has fewer uses than Go are
printed. It is a finder: each row still needs a reading against Go, because Rust may spell a site
as `i32::from`, `unwrap_or`, a match or a lazy `if` on purpose.

Go functions come from gocog's cache (`~/.cache/perf-rearch/quality/go/*.json`, written by
`quality.sh`); the newest file is used unless --go-cache names one.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

GO_ROOT = Path.home() / ".explore/repos/microsoft__TypeScript@3e9236361fa9/tsc"
SRC = Path(__file__).resolve().parents[3] / "crates/ts_goport/src"
MARKER = re.compile(r"//\s*Go:\s*(?:internal/)?([\w/]+\.go)(?::(\d+))?\s+(?:\([^)]*\)\s*)?([\w.]+)")
FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:unsafe\s+)?fn\s+(\w+)")
GO_SITE = re.compile(r"\bcore\.(IfElse|OrElse)\b")
RS_SITE = re.compile(r"\b(if_else|or_else)\(")


def go_functions(cache: Path, pkgs: list[str]) -> dict[str, list[dict]]:
    funcs = json.loads(cache.read_text())["funcs"]
    by_file: dict[str, list[dict]] = defaultdict(list)
    for f in funcs:
        pkg = f["pkg"]
        if not pkg.startswith("internal/"):
            continue
        rel = pkg[len("internal/"):]
        if any(rel == p or rel.startswith(p + "/") for p in pkgs):
            by_file[f"{rel}/{f['file']}"].append(f)
    return by_file


def go_sites(by_file: dict[str, list[dict]]) -> dict[tuple[str, str, int], dict[str, int]]:
    counts: dict[tuple[str, str, int], dict[str, int]] = defaultdict(lambda: {"IfElse": 0, "OrElse": 0})
    for rel, funcs in by_file.items():
        path = GO_ROOT / "internal" / rel
        if rel.endswith("_test.go") or not path.exists():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in GO_SITE.finditer(line):
                owner = [f for f in funcs if f["line"] <= n <= f["end"]]
                if not owner:
                    continue
                f = min(owner, key=lambda f: f["end"] - f["line"])
                counts[(rel, f["name"], f["line"])][m.group(1)] += 1
    return counts


def rust_fn_bodies(path: Path):
    """Yield (marker (file, name), fn name, start line, body text) for each marked Rust fn."""
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        m = FN.match(lines[i])
        if not m:
            i += 1
            continue
        marker = None
        j = i - 1
        while j >= 0 and (lines[j].strip().startswith(("//", "#[", "///")) or not lines[j].strip()):
            mm = MARKER.search(lines[j])
            if mm:
                marker = (mm.group(1), mm.group(3).split(".")[-1])
                break
            if not lines[j].strip():
                break
            j -= 1
        depth, started, k = 0, False, i
        body = []
        while k < len(lines):
            line = lines[k]
            code = line.split("//")[0]
            depth += code.count("{") - code.count("}")
            started = started or "{" in code
            body.append(line)
            if started and depth <= 0:
                break
            if not started and code.rstrip().endswith(";"):
                break
            k += 1
        if marker:
            yield marker, m.group(1), i + 1, "\n".join(body)
        i += 1  # nested fns are visited too; their uses also count in the outer body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go-cache", type=Path)
    ap.add_argument("--pkg", default="checker,ls")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    cache = args.go_cache or max((Path.home() / ".cache/perf-rearch/quality/go").glob("*.json"),
                                 key=lambda p: p.stat().st_mtime)
    pkgs = args.pkg.split(",")
    sites = go_sites(go_functions(cache, pkgs))
    rust: dict[tuple[str, str], list[tuple[str, int, str, dict[str, int]]]] = defaultdict(list)
    for p in pkgs:
        for path in sorted((SRC / p).rglob("*.rs")):
            for (gfile, gname), fname, line, body in rust_fn_bodies(path):
                if "/" not in gfile:  # `// Go: completions.go:N name`: the Rust file's package
                    gfile = f"{path.parent.relative_to(SRC)}/{gfile}"
                uses = {"if_else": 0, "or_else": 0}
                for m in RS_SITE.finditer(body):
                    uses[m.group(1)] += 1
                rust[(gfile, gname)].append((str(path.relative_to(SRC)), line, fname, uses))
    print("go_file\tgo_func\tgo_line\tIfElse\tOrElse\trs_if_else\trs_or_else\trust")
    for (rel, name, line), c in sorted(sites.items(), key=lambda kv: -(kv[1]["IfElse"] + kv[1]["OrElse"])):
        locs = rust.get((rel, name), [])
        ie = oe = 0
        for _f, _ln, _fn, u in locs:
            ie += u["if_else"]
            oe += u["or_else"]
        if not args.all and ie >= c["IfElse"] and oe >= c["OrElse"]:
            continue
        where = ", ".join(f"{f}:{ln} {fn}" for f, ln, fn, _ in locs) or "-"
        print(f"{rel}\t{name}\t{line}\t{c['IfElse']}\t{c['OrElse']}\t{ie}\t{oe}\t{where}")


if __name__ == "__main__":
    main()
