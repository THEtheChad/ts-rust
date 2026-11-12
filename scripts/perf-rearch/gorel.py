#!/usr/bin/env python3
"""Go-relative complexity for quality.py: map each Rust production function to its Go counterpart at
the pin and rank port bloat (ported code much more complex than Go's) and own code (no Go
counterpart) over the cognitive limit.

Imported by quality.py (`quality.sh report`); docs/perf-rearch/quality.md, "Go-relative complexity",
defines the metric, the fairness rule and the mapping. Results are cached under
~/.cache/perf-rearch/quality/gorel by the crates/ tree, the Go tree and this file's version.

Go side: scripts/perf-rearch/gocog (go/ast, standard library only), built once per source hash with
GOTOOLCHAIN=local and GOPROXY=off, run on <go root>/internal and <go root>/cmd, cached per Go tree.
gocog also lists struct fields and package-level vars and consts (`kind`), which only a marker can
name, and each function's tagless-switch residue.

Go outside the pin (EXT_TREES): the standard library of GOROOT (`src`, the toolchain the pin's go.mod
names, go1.27.1) and golang.org/x/text v0.42.0 (the version the pin's go.mod selects) from the module
cache. Their packages are named `std/<path>` and `x/text/<path>`. Only markers resolve there: a
Rust function whose marker names nothing at the pin (outside effect/) is looked up in these trees
by the marker's path as written (`internal/language/parse.go` is x/text's internal package).

Mapping, per Rust function (rca's list: prod functions, closures folded into their function):
1. Marker: the `// Go: [<path>.go[:<line>]] <sym>` comment in the comment and attribute block right
   above the `fn`, resolved against the Go functions at the pin. Confidence "marker".
2. Otherwise the name matcher: snake_case to a normalized name (lowercase, no `_`), `foo_exported`
   to Go's exported `Foo`, `T::new` to `NewT`; candidates are filtered by package (from the Rust
   path), then the receiver or impl type, then the Go file (Rust file stem without `_pN`).
   Confidence "high" (one candidate in the path's packages agreeing on file or receiver),
   "medium" (one candidate there with neither, or one in a package the file's other functions
   map to, agreeing), "low" (that package, not agreeing, a long name) or "ambiguous" (more than one
   left). A receiver differs only where the markers show the pairing. Low and ambiguous matches
   are kept in the mapping and ranked as own code, with a note.
3. A Rust function the matcher does not place but whose name is a named Go closure of the Go
   function its own file maps (`foo := func` in Go, a separate `fn foo` in Rust) is that closure,
   lifted: it is summed into the parent's row (the fairness rule).
4. A Rust function nested in another Rust function is folded into its parent (rca sums it there)
   unless it maps to a Go function of its own; then it has its own row and its parent's figure
   excludes it.
Everything else is own code.

Each port-bloat row also carries `residue`, the tagless-switch residue of its Go side (gocog), and
`diff_less_residue` = diff - min(residue, max(diff, 0)): what is left of a positive Δ once the
else-if chains that mirror Go's tagless switches are paid for. Targets stay on the raw Δ; the
column explains rows (docs/perf-rearch/quality.md, "Tagless-switch residue").
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

GOREL_VERSION = 9  # bump when the mapping or the rows change (invalidates the cache)
HERE = Path(__file__).resolve().parent
GOCOG_SRC = HERE / "gocog"
GO_BIN = Path(os.environ.get("PERF_QUALITY_GO", Path.home() / ".local/go/bin/go"))
GO_ROOT = Path(os.environ.get("PERF_QUALITY_GO_ROOT", Path.home() / ".explore/repos/microsoft__TypeScript@3e9236361fa9/tsc"))
GO_DIRS = ("internal", "cmd")
# Go outside the pin: (pkg prefix, label, root, the dirs gocog reads; None: every top-level dir but these)
GOROOT_SRC = Path(os.environ.get("PERF_QUALITY_GOROOT_SRC", Path.home() / ".local/go/src"))
XTEXT_ROOT = Path(os.environ.get("PERF_QUALITY_XTEXT", Path.home() / "go/pkg/mod/golang.org/x/text@v0.42.0"))
EXT_SKIP_DIRS = {"cmd", "vendor", "testdata"}
EXT_TREES = (("std", "GOROOT src", GOROOT_SRC), ("x/text", "golang.org/x/text", XTEXT_ROOT))
EXT_NO_LOOKUP = ("effect/",)  # Effect's markers name its Go plugin, which is not on disk
SRC = "crates/ts_goport/src/"
OWN_LIMIT = 25
LONG_NAME = 10  # normalized length a name needs for a match through file affinity alone
TEST_PKG_RE = re.compile(r"(^|/)(testutil|fourslash|testrunner)(/|$)|test$|mock$")

# Rust directory (under crates/ts_goport/src, longest prefix wins) -> Go packages. A directory not
# listed maps to internal/<dir> with a leading frontend/ removed; "*" adds the subpackages.
DIR_PKGS = {
    "frontend/vfs": ["internal/vfs", "internal/vfs/*"],
    "frontend/vfs/loadview": [],  # the loader's views: our own
    "frontend/compiler/load_view_scope": [],
    "emitter": ["internal/compiler", "internal/checker", "internal/printer"],
    "program": ["internal/compiler"],
    "declarations": ["internal/transformers/declarations"],
    "baseline": ["internal/testutil/tsbaseline", "internal/testutil/baseline"],
    "cmd": ["cmd/tsc"],
    "bin": ["cmd/tsc", "internal/execute"],
    "execute/tsc": ["internal/execute/tsc", "internal/diagnosticwriter"],
    "modulespecifiers": ["internal/modulespecifiers", "internal/tspath", "internal/packagejson", "internal/module"],
    "astdata": ["internal/ast"],
    "scanner_util": ["internal/scanner"],
    "gostd": [],  # a port of Go's standard library: its markers resolve in EXT_TREES, not at the pin
    "effect": [],  # our own
}
# root files and frontend/*.rs whose stem is not a Go package name
STEM_PKGS = {
    "program": ["internal/compiler"],
    "options": ["internal/core", "internal/tsoptions"],
    "options_json": ["internal/core", "internal/tsoptions"],
    "diag": ["internal/diagnostics"],
    "diagnostics_loc": ["internal/diagnostics"],
    "scanner_util": ["internal/scanner"],
    "gostring": ["internal/stringutil"],
    "flags": ["internal/ast"],
    "flags_macros": ["internal/ast"],
    "ast_factory": ["internal/ast"],
}
# the wave-2 lane areas (first matching prefix of the path under crates/ts_goport/src)
AREAS = (
    ("checker", ("checker/",)),
    ("frontend", ("frontend/", "program/", "program.rs", "scanner_util")),
    ("ls", ("ls/",)),
    ("execute/build/emit", ("execute/", "emitter/", "bin/", "cmd/")),
    ("effect", ("effect/",)),
)
AREA_NAMES = [a for a, _ in AREAS] + ["other"]


def area_of(path: str) -> str:
    p = path.removeprefix(SRC)
    if p == path:
        return "other"
    for name, prefixes in AREAS:
        if p.startswith(prefixes):
            return name
    return "other"


def subarea_of(path: str) -> str:
    p = path.removeprefix(SRC)
    if p == path:
        return path.split("/")[1] if path.count("/") > 1 else path
    parts = p.split("/")
    if parts[0] in ("frontend", "ls", "execute", "effect", "transformers", "project", "lsp", "api") and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else parts[0].removesuffix(".rs") + ".rs"


# --------------------------------------------------------------------------
# names

def norm(name: str) -> str:
    return name.removeprefix("r#").rstrip("_").replace("_", "").lower()


def strip_generics(s: str) -> str:
    out, depth = [], 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip()


def split_rust_name(name: str) -> tuple[str, str | None]:
    """`Scanner<'a>::scan` -> ("scan", "Scanner"); a lowercase scope part (a mod or a fn) is no receiver."""
    parts = [strip_generics(p) for p in strip_generics(name).split("::")]
    fn = parts[-1]
    recv = parts[-2] if len(parts) > 1 and parts[-2][:1].isupper() else None
    if recv:
        recv = recv.split()[-1].lstrip("&*")  # `&'a Foo`, `dyn Foo`
    return fn, recv


def file_stem(path: str) -> str:
    stem = path.rsplit("/", 1)[-1].removesuffix(".rs").removesuffix(".go")
    return re.sub(r"_p\d+$", "", stem)


def stem_key(stem: str) -> str:
    return stem.replace("_", "").lower()


# --------------------------------------------------------------------------
# the Go side

def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def go_tree_key(root: Path, dirs: tuple[str, ...] = GO_DIRS) -> str:
    try:
        head = _run(["git", "-C", str(root), "rev-parse", *[f"HEAD:{d}" for d in dirs]]).split()
        dirty = _run(["git", "-C", str(root), "status", "--porcelain", "--", *dirs])
        return hashlib.sha1(("".join(head) + dirty).encode()).hexdigest()[:16]
    except (subprocess.CalledProcessError, FileNotFoundError):
        h = hashlib.sha1(str(root).encode())
        for d in dirs:
            for p in sorted((root / d).rglob("*.go")):
                st = p.stat()
                h.update(f"{p}:{st.st_size}:{st.st_mtime_ns}".encode())
        return h.hexdigest()[:16]


def gocog_hash() -> str:
    h = hashlib.sha1()
    for p in sorted(GOCOG_SRC.glob("*")):
        if p.suffix in (".go", ".mod"):
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()[:12]


def gocog_binary(cache: Path) -> Path:
    exe = cache / "tools" / f"gocog-{gocog_hash()}"
    if not exe.exists():
        exe.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOFLAGS": "", "GOWORK": "off"}
        _run([str(GO_BIN), "build", "-o", str(exe) + ".tmp", "."], cwd=GOCOG_SRC, env=env)
        os.replace(str(exe) + ".tmp", exe)
    return exe


def go_functions(cache: Path, root: Path = GO_ROOT, dirs: tuple[str, ...] = GO_DIRS) -> dict:
    key = f"{go_tree_key(root, dirs)}-{gocog_hash()}"
    f = cache / "go" / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    out = json.loads(_run([str(gocog_binary(cache)), str(root), *dirs]))
    out["key"] = key
    out["root"] = str(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.with_suffix(".tmp").write_text(json.dumps(out))
    f.with_suffix(".tmp").replace(f)
    return out


def tree_version(prefix: str, root: Path) -> str:
    """go1.27.1 (GOROOT's VERSION file) or v0.42.0 (the module directory's @version)."""
    if prefix == "std":
        v = root.parent / "VERSION"
        return v.read_text().split("\n", 1)[0].strip() if v.exists() else "?"
    return root.name.rpartition("@")[2] or "?"


def ext_functions(cache: Path, trees=EXT_TREES) -> tuple[list[dict], list[dict]]:
    """The Go trees outside the pin: (functions with `std/` or `x/text/` package names, a note per tree)."""
    funcs, notes = [], []
    for prefix, label, root in trees:
        if not root.is_dir():
            notes.append({"prefix": prefix, "label": label, "root": str(root), "missing": True})
            continue
        dirs = tuple(sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in EXT_SKIP_DIRS and not p.name.startswith(".")))
        out = go_functions(cache, root, dirs)
        for g in out["funcs"]:
            funcs.append({**g, "pkg": f"{prefix}/{g['pkg']}"})
        notes.append({"prefix": prefix, "label": label, "root": str(root), "key": out["key"], "version": tree_version(prefix, root),
                      "functions": sum(1 for g in out["funcs"] if not g.get("kind"))})
    return funcs, notes


# --------------------------------------------------------------------------
# markers

GO_MARKER_RE = re.compile(r"^\s*//+\s*Go:\s+(?P<rest>.*)$")
PATH_RE = re.compile(r"^(?:internal/)?(?P<path>[\w./-]+\.go)(?::(?P<line>\d+))?(?:-\d+)?\s+")
SYM_RE = re.compile(r"^(?:\((?:\w+\s+)?\*?(?P<recv1>\w+)(?:\[[^\]]*\])?\)\s*\.?\s*|(?P<recv2>\w+)\.)?(?P<name>\w+)(?:\(\))?\.?(?:\s|$)")
# prose in the comment block: "body of Go `X`", "split out of `X`"; "Go `X`" or "Go fooBar"; a Go line range
PROSE_PART_RE = re.compile(r"\b(?:body|part|rest|half|tail|remainder|arms|cases|branches|steps) of Go(?:'s)? `?(?P<a>\w+)`?|\bsplit out of (?:Go(?:'s)? )?`?(?P<b>\w+)`?")
PROSE_NAME_RE = re.compile(r"\bGo(?:'s)? (?:`(?P<a>\w+)`|(?P<b>[a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*))")
PROSE_RANGE_RE = re.compile(r"\b(?:internal/)?(?P<path>[\w/]+\.go):(?P<a>\d+)(?:-(?:\w+\.go:)?(?P<b>\d+))?")
# a Go reference inside a `// Go:` paragraph, and the joiner that makes two references one compound
REF_RE = re.compile(r"(?P<raw>(?:internal/)?(?P<path>[\w./-]+\.go))(?::(?P<line>\d+))?\s+(?P<sym>\((?:\w+\s+)?\*?\w+(?:\[[^\]]*\])?\)\.?\s*\w+|\w+(?:\.\w+)?)")
JOIN_RE = re.compile(r"^[^.;:]{0,40}$")
JOIN_WORD_RE = re.compile(r"\b(?:and|with|plus)\b|,|\+")
PARA_STOP_RE = re.compile(r"^\s*(?://+\s*(?:Go:|PORT|PERF|TODO|NOTE|SAFETY)|///|//!)")
BLOCK_LINE_RE = re.compile(r"^\s*(//|#!?\[|$|\]|\))")


def read_blobs(sha: str, paths: list[str], repo: Path) -> dict[str, list[str]]:
    """The text of each path at sha, through one `git cat-file --batch`."""
    req = "".join(f"{sha}:{p}\n" for p in paths).encode()
    raw = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input=req, capture_output=True, check=True).stdout
    out, i = {}, 0
    for p in paths:
        nl = raw.index(b"\n", i)
        head = raw[i:nl].split()
        size = int(head[2]) if len(head) == 3 else 0
        out[p] = raw[nl + 1: nl + 1 + size].decode(errors="replace").split("\n")
        i = nl + 1 + size + 1
    return out


def parse_marker(text: str) -> dict | None:
    """A `// Go: [<path>.go[:<line>]] <sym>` comment, or None (a statement note such as `// Go: w.wg.Wait()`)."""
    m = GO_MARKER_RE.match(text)
    if not m:
        return None
    rest = m.group("rest").strip()
    path = line = raw_path = None
    pm = PATH_RE.match(rest)
    if pm:
        path, line = pm.group("path"), pm.group("line")
        raw_path = pm.group(0).split(":")[0].split()[0]
        rest = rest[pm.end():]
    s = SYM_RE.match(rest)
    if not s:
        return None
    return {"kind": "go", "path": path, "raw_path": raw_path, "line": int(line) if line else None,
            "recv": s.group("recv1") or s.group("recv2"), "name": s.group("name"), "text": m.group("rest").strip()}


def paragraph_refs(mk: dict, para: str) -> None:
    """Fill `also` (references joined to the first one by "and", "with" or a comma, outside parentheses:
    one Rust function porting several Go functions) and `alt` (the other references, tried in order
    when the first one names nothing at the pin, as after a rename) of a `// Go:` marker."""
    refs = list(REF_RE.finditer(para))
    mk["also"], mk["alt"] = [], []
    prev_end = refs[0].end() if refs else 0
    for r in refs[1:]:
        sym = SYM_RE.match(r.group("sym") + " ")
        if not sym:
            continue
        ref = {"kind": "go", "path": r.group("path"), "raw_path": r.group("raw"), "line": int(r.group("line")) if r.group("line") else None,
               "recv": sym.group("recv1") or sym.group("recv2"), "name": sym.group("name"), "text": r.group(0)}
        between = para[prev_end:r.start()]
        depth = para[:r.start()].count("(") - para[:r.start()].count(")")
        if depth <= 0 and JOIN_RE.match(between) and JOIN_WORD_RE.search(between):
            mk["also"].append(ref)
            prev_end = r.end()
        else:
            mk["alt"].append(ref)


def markers_above(lines: list[str], start: int) -> list[dict]:
    """Markers in the comment and attribute block right above line `start` (1-based): the `// Go:`
    lines nearest first, then prose from the block's comment text read as one paragraph ("body of
    Go `X`" and "split out of `X`"; a Go line range `file.go:a-b`; "Go `X`" or "Go fooBar")."""
    i = start - 2
    while i >= 0 and start - 2 - i < 60 and BLOCK_LINE_RE.match(lines[i]):
        i -= 1
    block = lines[i + 1: start - 1]
    go, text = [], []
    for k, ln in enumerate(block):
        mk = parse_marker(ln)
        if mk:
            para = [GO_MARKER_RE.match(ln).group("rest")]
            for nxt in block[k + 1:]:
                if not nxt.lstrip().startswith("//") or PARA_STOP_RE.match(nxt):
                    break
                para.append(nxt.lstrip().lstrip("/").strip())
            paragraph_refs(mk, " ".join(para))
            go.append(mk)
        elif ln.lstrip().startswith("//"):
            text.append(ln.lstrip().lstrip("/!").strip())
    go.reverse()  # nearest first
    para = " ".join(text)
    prose = []
    for pm in PROSE_PART_RE.finditer(para):
        prose.append({"kind": "prose", "how": "part", "name": pm.group("a") or pm.group("b")})
    for pm in PROSE_RANGE_RE.finditer(para):
        a = int(pm.group("a"))
        prose.append({"kind": "prose", "how": "range", "path": pm.group("path"), "lines": (a, int(pm.group("b") or a))})
    for pm in PROSE_NAME_RE.finditer(para):
        prose.append({"kind": "prose", "how": "name", "name": pm.group("a") or pm.group("b")})
    for p in prose:
        p.setdefault("path", None)
        p.setdefault("line", None)
        p.setdefault("recv", None)
        p.setdefault("name", None)
        p["text"] = para[:160]
    return go + prose


# --------------------------------------------------------------------------
# mapping

class GoIndex:
    """Go functions (and the fields, vars and consts gocog lists, `kind`) of one or more trees. The
    name matcher (`by_norm`, `closures`) sees functions only; markers (`by_name`, `in_file`) see all."""

    def __init__(self, gofuncs: list[dict]):
        self.funcs = [g for g in gofuncs if not g.get("kind")]
        self.members = [g for g in gofuncs if g.get("kind")]
        self.pkgs = sorted({g["pkg"] for g in gofuncs})
        self.by_norm: dict[str, list[dict]] = defaultdict(list)
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        self.in_file: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.closures: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
        for g in gofuncs:
            g["id"] = f"{g['pkg']}/{g['file']}:{g['line']}" + (f":{g['name']}" if g.get("kind") else "")
            g.setdefault("residue", 0)
            self.by_name[g["name"]].append(g)
            self.in_file[(g["pkg"], g["file"])].append(g)
            if g.get("kind"):
                continue
            self.by_norm[norm(g["name"])].append(g)
            for c in g.get("closures") or ():
                c.setdefault("residue", 0)
                self.closures[norm(c["name"])].append((g, c))
        self.pkg_last = defaultdict(list)
        for p in self.pkgs:
            self.pkg_last[p.rsplit("/", 1)[-1]].append(p)

    def expand(self, pkgs: list[str]) -> set[str]:
        out = set()
        for p in pkgs:
            if p.endswith("/*"):
                out |= {q for q in self.pkgs if q.startswith(p[:-1]) and not TEST_PKG_RE.search(q)}
            elif p in self.pkgs:
                out.add(p)
        return out

    def path_pkgs(self, path: str) -> set[str] | None:
        """The Go packages a Rust file's path names; None for a directory of our own (DIR_PKGS [])."""
        rel = path.removeprefix(SRC)
        if rel == path:
            return set()
        d = rel.rsplit("/", 1)[0] if "/" in rel else ""
        stem = file_stem(rel)
        best = None
        for k in DIR_PKGS:
            if (d == k or d.startswith(k + "/") or (d == "" and stem == k)) and (best is None or len(k) > len(best)):
                best = k
        if best is not None:
            if not DIR_PKGS[best]:
                return None
            pk = self.expand(DIR_PKGS[best])
        else:
            dd = d.removeprefix("frontend").strip("/")
            pk = self.expand([f"internal/{dd}", f"cmd/{dd}"]) if dd else set()
        for s in (stem, stem.split("_")[0]):
            pk |= self.expand(STEM_PKGS.get(s, []))
            for p in self.pkg_last.get(s, []):
                if not TEST_PKG_RE.search(p):
                    pk.add(p)
        return pk

    def files_named(self, path: str, pkgs: set[str]) -> list[tuple[str, str]]:
        """Go files at the pin that a marker path (`checker/relater.go` or `relater.go`) names."""
        pkgpart, _, base = path.rpartition("/")
        hits = [k for k in self.in_file if k[1] == base and
                (not pkgpart or k[0] == "internal/" + pkgpart or k[0] == pkgpart or k[0].endswith("/" + pkgpart))]
        if not pkgpart and len(hits) > 1:
            hits = [k for k in hits if k[0] in pkgs] or hits
        return hits


def name_keys(fn: str, recv: str | None) -> list[tuple[str, str | None]]:
    """(normalized Go name, required case of its first letter) to look up for a Rust fn name."""
    keys = []
    if fn.endswith("_exported"):
        keys.append((norm(fn.removesuffix("_exported")), "upper"))
    keys.append((norm(fn), None))
    if fn == "new" and recv:
        keys.append((norm("new" + recv), None))
    return keys


def recv_eq(rust_recv: str | None, go_recv: str) -> bool:
    return bool(rust_recv) and rust_recv.lower() == go_recv.lower()


def by_line(cands: list, line: int | None, line_of) -> list:
    if len(cands) > 1 and line:
        best = min(abs(line_of(c) - line) for c in cands)
        cands = [c for c in cands if abs(line_of(c) - line) == best]
    return cands


def family_sum(fs: list[dict]) -> dict:
    """One Go "function" standing for a method family: the same name on several receivers in one file
    (ast_generated.go's ForEachChild per node type), which a Rust enum dispatch ports as one fn. A
    field name on several structs (ast_generated.go's `Body`) is a family the same way."""
    g0 = min(fs, key=lambda g: g["line"])
    return {"pkg": g0["pkg"], "file": g0["file"], "line": g0["line"], "end": max(g["end"] for g in fs), "recv": f"*{len(fs)}",
            "name": g0["name"], "cog": sum(g["cog"] for g in fs), "cyc": sum(g["cyc"] for g in fs),
            "lines": sum(g["lines"] for g in fs), "residue": sum(g.get("residue", 0) for g in fs),
            "id": f"{g0['pkg']}/{g0['file']}:*{g0['name']}", "closures": [], "kind": g0.get("kind", "")}


def related(go_name: str, rust_fn: str) -> bool:
    a, b = norm(go_name), norm(rust_fn)
    return a.startswith(b) or b.startswith(a)


def resolve_marker(mk: dict, rf: dict, gi: GoIndex, pkgs: set[str]) -> tuple[dict, dict | None] | None:
    """The Go function (and closure, when the marker names a named closure) a marker names at the pin."""
    if mk["kind"] == "prose" and mk["how"] == "range":
        files = gi.files_named(mk["path"], pkgs)
        a, b = mk["lines"]
        fs = [g for k in files for g in gi.in_file[k] if g["line"] <= a and b <= g["end"]]
        return (fs[0], None) if len(fs) == 1 and related(fs[0]["name"], rf["fn"]) else None
    name, recv = mk["name"], mk["recv"]
    if mk["kind"] == "prose" and mk["how"] == "name" and not related(name, rf["fn"]):
        return None

    def recv_filter(cs, r):
        if r and len(cs) > 1:
            cs = [g for g in cs if g["recv"] == r or g["pkg"].endswith("/" + r)] or cs
        return cs

    def funcs_first(cs):
        """A function of that name over a field, var or const of it (as before gocog listed those)."""
        return [g for g in cs if not g.get("kind")] or cs

    if mk["path"]:
        files = gi.files_named(mk["path"], pkgs)
        if files:
            fs = funcs_first([g for k in files for g in gi.in_file[k] if g["name"] == name])
            fs = recv_filter(fs, recv)
            if len(fs) > 1 and not recv and rf["recv"]:
                fs = [g for g in fs if recv_eq(rf["recv"], g["recv"])] or fs
            if len(fs) > 1 and mk["line"]:
                near = [g for g in fs if abs(g["line"] - mk["line"]) <= 2]
                if len(near) == 1:
                    fs = near
            if len(fs) == 1:
                return fs[0], None
            if len(fs) > 1 and not recv and len({g["recv"] for g in fs}) == len(fs) and all(g["recv"] for g in fs):
                return family_sum(fs), None
            if fs:
                return None
            cl = [(g, c) for k in files for g in gi.in_file[k] for c in g.get("closures") or () if c["name"] == name]
            cl = by_line(cl, mk["line"], lambda gc: gc[1]["line"])
            return cl[0] if len(cl) == 1 else None
    cs = recv_filter(funcs_first(gi.by_name.get(name, [])), recv)
    if mk["path"] and "/" in mk["path"]:
        pkgpart = mk["path"].rpartition("/")[0]
        cs = [g for g in cs if g["pkg"] == "internal/" + pkgpart or g["pkg"].endswith("/" + pkgpart)]
    if len(cs) > 1:
        cs = [g for g in cs if g["pkg"] in pkgs] or cs
    if len(cs) > 1 and rf["recv"]:
        cs = [g for g in cs if recv_eq(rf["recv"], g["recv"])] or cs
    if mk["kind"] == "prose":
        cs = [g for g in cs if g["pkg"] in pkgs or recv_eq(rf["recv"], g["recv"])]
    if len(cs) == 1:
        return cs[0], None
    if not cs:
        cl = [(g, c) for g, c in gi.closures.get(norm(name), ()) if c["name"] == name and g["pkg"] in pkgs]
        if len(cl) == 1:
            return cl[0]
    return None


def compound(fs: list[dict]) -> dict:
    """One Go "function" standing for several that one Rust function ports (`// Go: a.go:1 A and a.go:9 B`)."""
    g0 = fs[0]
    return {"pkg": g0["pkg"], "file": g0["file"], "line": g0["line"], "end": g0["end"], "recv": g0["recv"],
            "name": " + ".join(g["name"] for g in fs), "cog": sum(g["cog"] for g in fs), "cyc": sum(g["cyc"] for g in fs),
            "lines": sum(g["lines"] for g in fs), "residue": sum(g.get("residue", 0) for g in fs),
            "id": "+".join(g["id"] for g in fs), "closures": []}


def resolve_marker_paragraph(mk: dict, rf: dict, gi: GoIndex, pkgs: set[str]) -> tuple[dict, dict | None] | None:
    """resolve_marker, plus a `// Go:` paragraph's joined references (a compound) and, when the first
    reference names nothing at the pin, its other references in order."""
    hit = resolve_marker(mk, rf, gi, pkgs)
    if hit is None:
        for ref in mk.get("alt", []) + mk.get("also", []):
            hit = resolve_marker(ref, rf, gi, pkgs)
            if hit:
                return hit
        return None
    if hit[1] is None and mk.get("also"):
        fs = [hit[0]]
        for ref in mk["also"]:
            h = resolve_marker(ref, rf, gi, pkgs)
            if h and h[1] is None and h[0]["id"] not in {g["id"] for g in fs}:
                fs.append(h[0])
        if len(fs) > 1:
            return compound(fs), None
    return hit


def resolve_external(mks: list[dict], rf: dict, xi: GoIndex) -> tuple[dict, dict | None] | None:
    """A `// Go:` marker resolved in the trees outside the pin (EXT_TREES), by its path as written
    (`internal/language/parse.go` is not `language/parse.go` there). A marker with no path counts only
    with a qualifier that the hit agrees with (`time.daysIn`: package `time` or receiver `time`)."""

    def as_written(ref):
        return {**ref, "path": ref.get("raw_path") or ref["path"]}

    for mk in mks:
        if mk["kind"] != "go" or not (mk["path"] or mk["recv"]):
            continue
        m = {**as_written(mk), "alt": [as_written(r) for r in mk.get("alt", [])], "also": [as_written(r) for r in mk.get("also", [])]}
        hit = resolve_marker_paragraph(m, rf, xi, set())
        if hit is None:
            continue
        g = hit[0]
        if not mk["path"] and not (g["recv"] == mk["recv"] or g["pkg"].endswith("/" + mk["recv"])):
            continue
        return hit
    return None


def order_markers(mks: list[dict], rf: dict) -> list[dict]:
    """A `// Go:` marker naming the Rust function first, then the others nearest first, then prose."""
    keys = {k for k, _ in name_keys(rf["fn"], rf["recv"])}
    named = [m for m in mks if m["kind"] == "go" and norm(m["name"]) in keys]
    return named + [m for m in mks if m not in named]


def match_by_name(rf: dict, gi: GoIndex, pkgs: set[str], aff: set[str], recv_ok: set) -> tuple[dict | None, str, list[dict]]:
    """The name matcher: (Go function, confidence, ambiguous candidates)."""
    fn, recv = rf["fn"], rf["recv"]
    cands = []
    for k, case in name_keys(fn, recv):
        for g in gi.by_norm.get(k, ()):
            if case == "upper" and not g["name"][:1].isupper():
                continue
            # a different receiver only where the markers show that Rust impl taking that Go type's methods
            if recv and g["recv"] and not recv_eq(recv, g["recv"]) and (recv.lower(), g["recv"].lower()) not in recv_ok:
                continue
            cands.append(g)
        if cands:
            break
    if not cands:
        return None, "none", []
    stem = stem_key(file_stem(rf["file"]))

    def narrow(cs):
        if len(cs) > 1:
            # an exported/unexported pair: Rust names the exported one `foo_exported`
            low = [g for g in cs if not g["name"][:1].isupper()]
            if low and len(low) < len(cs) and not fn.endswith("_exported"):
                cs = low
        for test in (lambda g: recv_eq(recv, g["recv"]), lambda g: stem_key(file_stem(g["file"])) == stem,
                     lambda g: recv_eq(recv, g["recv"]) and stem_key(file_stem(g["file"])) == stem):
            if len(cs) > 1:
                sub = [g for g in cs if test(g)]
                if sub:
                    cs = sub
        return cs

    for tier, scope in (("path", pkgs), ("affinity", aff)):
        cs = [g for g in cands if g["pkg"] in scope]
        if not cs:
            continue
        cs = narrow(cs)
        if len(cs) > 1:
            return None, "ambiguous", cs
        g = cs[0]
        agree = recv_eq(recv, g["recv"]) or stem_key(file_stem(g["file"])) == stem
        same_kind = (recv is None) == (not g["recv"])  # both free functions or both methods
        if tier == "path" and (agree or (len(norm(fn)) >= 8 and same_kind)):
            return g, "high" if agree else "medium", []
        if tier == "affinity" and (agree or (len(norm(fn)) >= LONG_NAME and same_kind)):
            return g, "medium" if agree else "low", []
        return None, "none", []
    return None, "none", []


def file_affinity(rfs: list[dict], gi: GoIndex) -> dict[str, set[str]]:
    """Per Rust file, Go packages that hold at least 3 and 25% of its uniquely named matches."""
    hits: dict[str, Counter] = defaultdict(Counter)
    for rf in rfs:
        if len(norm(rf["fn"])) < 6:
            continue
        cs = gi.by_norm.get(norm(rf["fn"].removesuffix("_exported")), [])
        pk = {g["pkg"] for g in cs}
        if len(pk) == 1:
            hits[rf["file"]][next(iter(pk))] += 1
    out = {}
    for f, c in hits.items():
        tot = sum(c.values())
        out[f] = {p for p, n in c.items() if n >= 3 and n >= 0.25 * tot}
    return out


def nest(rfs: list[dict]) -> None:
    """Set each Rust function's `parent` (the innermost function whose span holds it)."""
    by_file = defaultdict(list)
    for rf in rfs:
        by_file[rf["file"]].append(rf)
    for fs in by_file.values():
        fs.sort(key=lambda f: (f["start"], -f["end"]))
        stack: list[dict] = []
        for f in fs:
            while stack and not (stack[-1]["start"] <= f["start"] and f["end"] <= stack[-1]["end"]):
                stack.pop()
            f["parent"] = stack[-1]["key"] if stack else None
            stack.append(f)


def family(path: str) -> str:
    """A Rust file without its `_pN` part: the split parts of one Go file share it."""
    d, _, base = path.rpartition("/")
    return d + "/" + file_stem(base)


def map_functions(funcs: list[dict], lines_of: dict[str, list[str]], gi: GoIndex, xi: GoIndex | None = None) -> list[dict]:
    rfs = []
    for f in funcs:
        fn, recv = split_rust_name(f["name"])
        rfs.append({**f, "fn": fn, "recv": recv, "closure": None, "part_of": None})
    nest(rfs)
    pk_cache: dict[str, set[str] | None] = {}
    # pass 1: markers
    file_marks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for rf in rfs:
        path = rf["file"]
        if path not in pk_cache:
            pk_cache[path] = gi.path_pkgs(path)
        pkgs = pk_cache[path] or set()
        mks = order_markers(markers_above(lines_of.get(path, []), rf["start"]), rf)
        rf["marker"] = next((m["text"] for m in mks if m["kind"] == "go"), None)
        rf["via"] = None
        hit = None
        for mk in mks:
            hit = resolve_marker_paragraph(mk, rf, gi, pkgs)
            if hit:
                rf["via"] = mk["kind"]
                break
        if rf["marker"]:
            file_marks[path][0] += 1
            file_marks[path][1] += bool(hit)
        if hit is None and xi is not None and rf["marker"] and not path.removeprefix(SRC).startswith(EXT_NO_LOOKUP):
            hit = resolve_external(mks, rf, xi)
            if hit:
                rf["via"] = "ext"
        rf["marker_hit"] = hit
    # markers of a file mostly naming code outside the pin: a port of other Go (Effect's, Go's std, x/text)
    external = {p for p, (n, ok) in file_marks.items() if n >= 3 and ok < 0.5 * n}
    # Rust impl types that hold another Go type's methods (Checker <- Relater, NodeBuilderImpl), from the markers
    pairs = Counter((rf["recv"].lower(), rf["marker_hit"][0]["recv"].lower()) for rf in rfs
                    if rf["marker_hit"] and rf["via"] != "ext" and rf["recv"] and rf["marker_hit"][0]["recv"])
    recv_ok = {p for p, n in pairs.items() if n >= 3}
    aff = file_affinity(rfs, gi)
    # pass 2: names, and the choice
    for rf in rfs:
        path = rf["file"]
        pkgs = pk_cache[path]
        if pkgs is None or path in external:
            g, conf, amb = None, "none", []
        else:
            g, conf, amb = match_by_name(rf, gi, pkgs, aff.get(path, set()) - pkgs, recv_ok)
        rf["name_match"], rf["name_conf"] = (g["id"] if g else None), conf
        rf["ambiguous"] = [x["id"] + " " + (x["recv"] + "." if x["recv"] else "") + x["name"] for x in amb]
        hit = rf.pop("marker_hit")
        rf["go"], rf["conf"] = None, "none"
        if hit:
            rf["go"], rf["closure"] = hit
            rf["conf"] = {"go": "marker", "ext": "ext"}.get(rf["via"], "prose")
        elif rf["marker"] and (pkgs is None or path in external):
            pass  # a marker naming code outside the pin
        elif rf["parent"] is not None and conf != "high":
            pass  # a nested fn stays in its parent unless it clearly is a Go function
        elif conf == "ambiguous":
            rf["conf"] = "ambiguous"
        elif g is not None:
            rf["go"], rf["conf"] = g, conf
        if rf["go"] is None and rf["parent"] is None and pkgs:
            # a Go closure lifted into its own Rust function: the closure of a Go function this file maps
            rf["closure_cands"] = gi.closures.get(norm(rf["fn"]), ())
    # a nested Rust fn that is a closure of a Go function stays in its parent, as Go's closure does
    for rf in rfs:
        if rf["parent"] is not None and rf["closure"] is not None:
            rf["go"], rf["closure"], rf["conf"] = None, None, "none"
    # splits and lifted closures: fold into the row of the same Go function in the same file family
    heads: dict[tuple[str, str], dict] = {}
    for rf in sorted(rfs, key=lambda r: (r["closure"] is not None, norm(r["fn"]) != norm(r["go"]["name"]) if r["go"] else True, -r["cog"])):
        if rf["go"] is None or rf["parent"] is not None or rf["closure"] is not None:
            continue
        k = (family(rf["file"]), rf["go"]["id"])
        if k in heads:
            rf["part_of"] = heads[k]["key"]
            rf["conf"] = rf["conf"] + "+split"
        else:
            heads[k] = rf
    for rf in rfs:
        if rf["parent"] is not None:
            continue
        if rf["closure"] is not None:
            h = heads.get((family(rf["file"]), rf["go"]["id"]))
            if h:
                rf["part_of"] = h["key"]
        elif rf["go"] is None and rf.get("closure_cands"):
            hits = [(g, c) for g, c in rf["closure_cands"] if (family(rf["file"]), g["id"]) in heads]
            if len(hits) == 1:
                rf["go"], rf["closure"] = hits[0]
                rf["part_of"] = heads[(family(rf["file"]), rf["go"]["id"])]["key"]
                rf["conf"] = "lifted"
        rf.pop("closure_cands", None)
    return rfs


# --------------------------------------------------------------------------
# rows

def tree_of(pkg: str) -> str:
    """"pin", or the EXT_TREES prefix of a package outside the pin ("std", "x/text")."""
    for prefix, _, _ in EXT_TREES:
        if pkg.startswith(prefix + "/"):
            return prefix
    return "pin"


def build_rows(rfs: list[dict]) -> tuple[list[dict], list[dict]]:
    children = defaultdict(list)
    for rf in rfs:
        if rf["parent"]:
            children[rf["parent"]].append(rf)

    def own_figure(rf):
        """rca's sum less the nested functions that have rows of their own."""
        cog, cyc, stack = rf["cog"], rf["cyc"], list(children[rf["key"]])
        while stack:
            c = stack.pop()
            if c["go"] is not None:
                cog -= c["cog"]
                cyc -= c["cyc"]
            else:
                stack.extend(children[c["key"]])
        return cog, cyc

    parts = defaultdict(list)
    for rf in rfs:
        if rf["part_of"]:
            parts[rf["part_of"]].append(rf)
    ports, own = [], []
    for rf in rfs:
        if rf["part_of"] or (rf["parent"] is not None and rf["go"] is None):
            continue  # summed into another row, or folded into its parent as rca sums it
        cog, cyc = own_figure(rf)
        row = {"key": rf["key"], "file": rf["file"], "line": rf["start"], "name": rf["name"], "area": area_of(rf["file"]),
               "subarea": subarea_of(rf["file"]), "cog": cog, "cyc": cyc, "sloc": rf["sloc"]}
        g = rf["go"]
        if g is not None and rf["conf"].startswith("low"):
            # a low-confidence name match is listed in the mapping but ranked as own code
            row["low_match"] = (g["recv"] + "." if g["recv"] else "") + g["name"]
            g = None
        if g is None:
            if rf["ambiguous"]:
                row["ambiguous"] = rf["ambiguous"]
            if rf["marker"]:
                row["marker"] = rf["marker"]
            for p in parts.get(rf["key"], ()):
                pc, py = own_figure(p)
                row["cog"] += pc
                row["cyc"] += py
                row["sloc"] += p["sloc"]
            own.append(row)
            continue
        if rf["closure"] is not None:  # a lifted Go closure whose Go function has no row in this file
            c = rf["closure"]
            go_cog, go_cyc, go_lines, go_line = c["cog"], c["cyc"], c["lines"], c["line"]
            go_name = (g["recv"] + "." if g["recv"] else "") + g["name"] + " closure " + c["name"]
            residue = c.get("residue", 0)
        else:
            go_cog, go_cyc, go_lines, go_line = g["cog"], g["cyc"], g["lines"], g["line"]
            go_name = (g["recv"] + "." if g["recv"] else "") + g["name"] + (f" ({g['kind']})" if g.get("kind") else "")
            residue = g.get("residue", 0)
        extra = []
        # the same name again in the file family is a `#[cfg]` variant (unix/windows) or a trait method
        # delegating to the inherent one: one of them runs, so the row takes the larger
        for p in sorted(parts.get(rf["key"], ()), key=lambda p: p["fn"] != rf["fn"] or p["closure"] is not None):
            pc, py = own_figure(p)
            if p["fn"] == rf["fn"] and p["closure"] is None:
                extra.append({"name": p["name"], "cog": pc, "kind": "variant"})
                if pc > cog:
                    cog, cyc, row["sloc"] = pc, py, p["sloc"]
                continue
            cog += pc
            cyc += py
            row["sloc"] += p["sloc"]
            if p["closure"] is not None and rf["closure"] is None:
                # Go's figure counts the closure in place (one nesting deeper); Rust's counts it standalone
                go_cog += p["closure"]["cog"] - p["closure"]["cog_inplace"]
            extra.append({"name": p["name"], "cog": pc, "kind": "closure" if p["closure"] is not None else "split"})
        diff = cog - go_cog
        row.update({
            "conf": rf["conf"], "cog": cog, "cyc": cyc,
            "go": f"{g['pkg']}/{g['file']}:{go_line}", "go_name": go_name, "tree": tree_of(g["pkg"]),
            "go_cog": go_cog, "go_cyc": go_cyc, "go_lines": go_lines,
            "diff": diff, "ratio": round(cog / max(1, go_cog), 2),
            "residue": residue, "diff_less_residue": diff - min(residue, max(diff, 0)),
        })
        if extra:
            row["parts"] = extra
        ports.append(row)
    return ports, own


def summarize_rows(rfs, ports, own) -> dict:
    conf = Counter(rf["conf"] for rf in rfs)
    s = {
        "rust_functions": len(rfs),
        "confidence": dict(conf.most_common()),
        "ported_rows": len(ports),
        "own_rows": len(own),
        "nested_folded": sum(1 for rf in rfs if rf["parent"] and rf["go"] is None),
        "parts": sum(1 for rf in rfs if rf["part_of"]),
        "lifted_closures": sum(1 for rf in rfs if rf["part_of"] and rf["closure"] is not None),
        "ambiguous": sum(1 for r in own if r.get("ambiguous")),
        "port_rust_gt_go": sum(1 for r in ports if r["diff"] > 0),
        "port_diff_ge10": sum(1 for r in ports if r["diff"] >= 10),
        "port_diff_ge25": sum(1 for r in ports if r["diff"] >= 25),
        # the residue column: of the Δ ≥ 10 targets, those still ≥ 10 once the residue is paid for
        "port_diff_ge10_less_residue_ge10": sum(1 for r in ports if r["diff"] >= 10 and r["diff_less_residue"] >= 10),
        "port_target_residue": sum(r["diff"] - r["diff_less_residue"] for r in ports if r["diff"] >= 10),
        "port_ext_rows": dict(Counter(r["tree"] for r in ports if r["tree"] != "pin")),
        "port_ratio_ge1_5_cog_gt25": sum(1 for r in ports if r["ratio"] >= 1.5 and r["cog"] > OWN_LIMIT),
        "port_excess_sum": sum(max(0, r["diff"]) for r in ports),
        "port_cog_gt25": sum(1 for r in ports if r["cog"] > OWN_LIMIT),
        "port_go_cog_gt25": sum(1 for r in ports if r["go_cog"] > OWN_LIMIT),
        "own_gt25": sum(1 for r in own if r["cog"] > OWN_LIMIT),
        "own_gt50": sum(1 for r in own if r["cog"] > 50),
        "own_excess_sum": sum(max(0, r["cog"] - OWN_LIMIT) for r in own),
    }
    # the name matcher against the markers: where both place a function, how often they agree
    mk = [rf for rf in rfs if rf["conf"].startswith("marker") and rf["closure"] is None and ":*" not in rf["go"]["id"] and "+" not in rf["go"]["id"]]
    both = [rf for rf in mk if rf["name_match"]]
    s["name_vs_marker"] = {
        "marker_resolved": len(mk),
        "name_matcher_placed": len(both),
        "agree": sum(1 for rf in both if rf["name_match"] == rf["go"]["id"]),
        "by_conf": {c: [sum(1 for rf in both if rf["name_conf"] == c and rf["name_match"] == rf["go"]["id"]),
                        sum(1 for rf in both if rf["name_conf"] == c)] for c in ("high", "medium", "low")},
    }
    s["markers_unresolved"] = sum(1 for rf in rfs if rf["marker"] and not rf["conf"].startswith(("marker", "prose", "ext")))
    s["markers_ext"] = sum(1 for rf in rfs if rf["marker"] and rf["conf"].startswith("ext"))
    s["markers_member"] = sum(1 for rf in rfs if rf["conf"].startswith(("marker", "prose")) and rf["go"].get("kind"))
    # calibration: on functions placed by a marker, a faithful port should score as Go does
    cal = [r for r in ports if r["conf"].startswith(("marker", "prose"))]
    s["calibration"] = {
        "rows": len(cal), "exact": sum(1 for r in cal if r["diff"] == 0), "within2": sum(1 for r in cal if abs(r["diff"]) <= 2),
        "rust_sum": sum(r["cog"] for r in cal), "go_sum": sum(r["go_cog"] for r in cal),
    }
    return s


def area_totals(ports, own) -> dict:
    out = {}
    for a in AREA_NAMES:
        p = [r for r in ports if r["area"] == a]
        o = [r for r in own if r["area"] == a]
        out[a] = {
            "ported": len(p), "port_diff_ge10": sum(1 for r in p if r["diff"] >= 10),
            "port_diff_ge25": sum(1 for r in p if r["diff"] >= 25), "port_excess_sum": sum(max(0, r["diff"]) for r in p),
            "port_target_excess": sum(r["diff"] for r in p if r["diff"] >= 10),
            "port_target_less_residue": sum(r["diff_less_residue"] for r in p if r["diff"] >= 10),
            "port_diff_ge10_less_residue_ge10": sum(1 for r in p if r["diff"] >= 10 and r["diff_less_residue"] >= 10),
            "own": len(o), "own_gt25": sum(1 for r in o if r["cog"] > OWN_LIMIT),
            "own_excess_sum": sum(max(0, r["cog"] - OWN_LIMIT) for r in o),
            "files_with_targets": len({r["file"] for r in p if r["diff"] >= 10} | {r["file"] for r in o if r["cog"] > OWN_LIMIT}),
        }
        subs = defaultdict(lambda: [0, 0, 0, 0, 0])
        for r in p:
            if r["diff"] >= 10:
                subs[r["subarea"]][0] += 1
                subs[r["subarea"]][1] += r["diff"]
                subs[r["subarea"]][4] += r["diff_less_residue"]
        for r in o:
            if r["cog"] > OWN_LIMIT:
                subs[r["subarea"]][2] += 1
                subs[r["subarea"]][3] += r["cog"] - OWN_LIMIT
        out[a]["subareas"] = {k: {"port_diff_ge10": v[0], "port_excess": v[1], "port_excess_less_residue": v[4], "own_gt25": v[2],
                                  "own_excess": v[3]}
                              for k, v in sorted(subs.items(), key=lambda kv: -(kv[1][1] + kv[1][3]))}
    return out


def go_relative(snap: dict, repo: Path, cache: Path, go_root: Path = GO_ROOT, ext_trees=EXT_TREES) -> dict:
    t0 = time.time()
    gof = go_functions(cache, go_root)
    ext, ext_notes = ext_functions(cache, ext_trees)
    ext_key = hashlib.sha1("".join(n.get("key", "missing:" + n["root"]) for n in ext_notes).encode()).hexdigest()[:8]
    key = f"{snap['tree']}-{gof['key']}-{ext_key}-r{GOREL_VERSION}"
    cf = cache / "gorel" / f"{key}.json"
    if cf.exists():
        out = json.loads(cf.read_text())
        out["seconds"] = round(time.time() - t0, 2)
        out["cached"] = True
        return out
    gi = GoIndex(gof["funcs"])
    xi = GoIndex(ext) if ext else None
    files = sorted({f["file"] for f in snap["funcs"]})
    lines_of = read_blobs(snap["sha"], files, repo)
    rfs = map_functions(snap["funcs"], lines_of, gi, xi)
    ports, own = build_rows(rfs)
    ports.sort(key=lambda r: (-r["diff"], -r["cog"]))
    own.sort(key=lambda r: -r["cog"])
    summary = summarize_rows(rfs, ports, own)
    summary["go_functions"] = len(gi.funcs)
    summary["go_members"] = dict(Counter(g["kind"] for g in gi.members))
    summary["go_closures_named"] = sum(len(g.get("closures") or ()) for g in gi.funcs)
    out = {
        "version": GOREL_VERSION, "tree": snap["tree"], "go_root": gof["root"], "go_key": gof["key"], "ext_trees": ext_notes,
        "summary": summary, "areas": area_totals(ports, own),
        "port_bloat": ports, "own": own,
        "ambiguous": [{"key": rf["key"], "name": rf["name"], "file": rf["file"], "line": rf["start"], "cog": rf["cog"],
                       "candidates": rf["ambiguous"]} for rf in rfs if rf["conf"] == "ambiguous"],
        # every Rust function: confidence, Go function id, the row it is summed into or folded into
        "mapping": {rf["key"]: [rf["conf"], rf["go"]["id"] if rf["go"] else None, rf["part_of"] or rf["parent"]] for rf in rfs},
    }
    out["cache"] = str(cf)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.with_suffix(".tmp").write_text(json.dumps(out))
    cf.with_suffix(".tmp").replace(cf)
    out["seconds"] = round(time.time() - t0, 2)
    out["cached"] = False
    return out


# --------------------------------------------------------------------------
# markdown

def short(path: str) -> str:
    return path.removeprefix(SRC)


def md_port_rows(rows: list[dict], top: int) -> list[str]:
    o = ["| Δcog | Δ less residue | residue | ratio | Rust cog | Go cog | Rust lines | Go lines | conf | Rust function | Go function |",
         "|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
    for r in rows[:top]:
        lift = (" (+ " + ", ".join(f"{x['kind']} {x['name'].split('::')[-1]} {x['cog']}" for x in r["parts"]) + ")") if r.get("parts") else ""
        o.append(f"| {r['diff']:+d} | {r['diff_less_residue']:+d} | {r['residue']} | {r['ratio']} | {r['cog']} | {r['go_cog']} | {r['sloc']} | "
                 f"{r['go_lines']} | {r['conf']} | "
                 f"`{short(r['file'])}:{r['line']}` {r['name']}{lift} | `{r['go'].removeprefix('internal/')}` {r['go_name']} |")
    return o


def md_own_rows(rows: list[dict], top: int) -> list[str]:
    o = ["| cog | cyc | lines | area | Rust function | note |", "|---:|---:|---:|---|---|---|"]
    for r in rows[:top]:
        note = ("ambiguous: " + "; ".join(r["ambiguous"][:3]) if r.get("ambiguous")
                else f"marker `{r['marker'][:80]}` not at the pin" if r.get("marker")
                else f"low-confidence name match `{r['low_match']}`" if r.get("low_match") else "")
        o.append(f"| {r['cog']} | {r['cyc']} | {r['sloc']} | {r['area']} | `{short(r['file'])}:{r['line']}` {r['name']} | {note} |")
    return o


def md_areas(areas: dict) -> list[str]:
    o = ["| area | ported rows | Δ≥10 (targets) | of them Δ less residue ≥10 | Δ≥25 | Σ Δ of targets | Σ Δ less residue of targets | "
         "Σ positive Δ | own fns | own >25 (targets) | Σ (cog − 25) of targets | files with targets |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot = Counter()
    for a, v in areas.items():
        tot.update({k: x for k, x in v.items() if isinstance(x, int)})
        o.append(f"| {a} | {v['ported']:,} | {v['port_diff_ge10']} | {v['port_diff_ge10_less_residue_ge10']} | {v['port_diff_ge25']} | "
                 f"{v['port_target_excess']:,} | {v['port_target_less_residue']:,} | {v['port_excess_sum']:,} | "
                 f"{v['own']:,} | {v['own_gt25']} | {v['own_excess_sum']:,} | {v['files_with_targets']} |")
    o.append(f"| **all** | {tot['ported']:,} | {tot['port_diff_ge10']} | {tot['port_diff_ge10_less_residue_ge10']} | {tot['port_diff_ge25']} | "
             f"{tot['port_target_excess']:,} | {tot['port_target_less_residue']:,} | {tot['port_excess_sum']:,} | "
             f"{tot['own']:,} | {tot['own_gt25']} | {tot['own_excess_sum']:,} | {tot['files_with_targets']} |")
    return o


def md_summary(g: dict) -> list[str]:
    s = g["summary"]
    nv = s["name_vs_marker"]
    c = Counter()
    for k, v in s["confidence"].items():
        c[k.split("+")[0]] += v
    mem = s.get("go_members", {})
    ext = "; ".join(f"{n['label']} {n['version']} at `{n['root']}` ({n['functions']:,} functions)" if not n.get("missing")
                    else f"{n['label']}: not on disk at `{n['root']}`" for n in g.get("ext_trees", []))
    exts = s.get("port_ext_rows", {})
    return [
        f"- Go at `{g['go_root']}`: {s['go_functions']:,} functions, {s['go_closures_named']} named closures; "
        f"for markers also {mem.get('field', 0):,} struct fields, {mem.get('var', 0):,} vars and {mem.get('const', 0):,} consts.",
        f"- Go outside the pin, for markers that name nothing at the pin: {ext or 'none'}.",
        f"- Rust prod functions {s['rust_functions']:,}: by `// Go:` marker {c['marker']:,} ({s['markers_member']} of them a field, var or const), "
        f"by a marker naming Go outside the pin {c['ext']:,}, by prose in the doc comment {c['prose']:,}, by name high {c['high']:,} / medium {c['medium']:,} / "
        f"low {c['low']:,}, lifted by name {c['lifted']}, ambiguous {c['ambiguous']}, none {c['none']:,}. "
        f"{s['parts']} are summed into another function's row (splits, same-named variants and lifted Go closures, {s['lifted_closures']} of them closures); "
        f"{s['nested_folded']:,} nested fns stay folded in their parent. Markers naming nothing in any Go tree: {s['markers_unresolved']:,}.",
        f"- Name matcher checked against the markers: it places {nv['name_matcher_placed']:,} of {nv['marker_resolved']:,} marked functions "
        f"and agrees on {nv['agree']:,} ({100 * nv['agree'] / max(1, nv['name_matcher_placed']):.1f}%); by confidence "
        + ", ".join(f"{k} {v[0]:,}/{v[1]:,}" for k, v in nv["by_conf"].items()) + ".",
        f"- Calibration (rows placed by a marker): Rust cog = Go cog in {100 * s['calibration']['exact'] / max(1, s['calibration']['rows']):.0f}%, "
        f"within 2 in {100 * s['calibration']['within2'] / max(1, s['calibration']['rows']):.0f}% of {s['calibration']['rows']:,}; "
        f"Σ Rust {s['calibration']['rust_sum']:,} vs Σ Go {s['calibration']['go_sum']:,}.",
        f"- Port bloat: {s['ported_rows']:,} ported rows ({', '.join(f'{k} {v:,}' for k, v in sorted(exts.items())) or 'none'} of them against Go outside the pin); "
        f"Rust above Go {s['port_rust_gt_go']:,}, Δcog ≥10 {s['port_diff_ge10']}, "
        f"≥25 {s['port_diff_ge25']}, ratio ≥1.5 with Rust cog >{OWN_LIMIT} {s['port_ratio_ge1_5_cog_gt25']}; Σ positive Δ {s['port_excess_sum']:,}. "
        f"Rows with Rust cog >{OWN_LIMIT}: {s['port_cog_gt25']}, with Go cog >{OWN_LIMIT}: {s['port_go_cog_gt25']}.",
        f"- Tagless-switch residue (explains rows; targets stay on the raw Δ): the {s['port_diff_ge10']} rows with Δ ≥10 hold "
        f"{s['port_target_residue']} points of it; {s['port_diff_ge10_less_residue_ge10']} of them stay ≥10 once it is paid for (Δ less residue).",
        f"- Own code: {s['own_rows']:,} functions; >{OWN_LIMIT} {s['own_gt25']}, >50 {s['own_gt50']}; Σ (cog − {OWN_LIMIT}) {s['own_excess_sum']:,}.",
    ]


def md_go_relative(g: dict, top: int = 20) -> list[str]:
    o = ["### Go-relative complexity", ""]
    o += md_summary(g)
    o += ["", f"Port bloat, top {top} by Δcog (Rust − Go; both include their closures; splits and lifted Go closures summed into their row):", ""]
    o += md_port_rows(g["port_bloat"], top)
    ratio = sorted((r for r in g["port_bloat"] if r["cog"] > OWN_LIMIT), key=lambda r: (-r["ratio"], -r["diff"]))
    o += ["", f"Port bloat, top {top // 2} by ratio (Rust cog >{OWN_LIMIT}):", ""]
    o += md_port_rows(ratio, top // 2)
    o += ["", f"Own code over {OWN_LIMIT}, top {top}:", ""]
    o += md_own_rows(g["own"], top)
    if g["ambiguous"]:
        o += ["", f"Ambiguous name matches ({len(g['ambiguous'])}; ranked as own code until a `// Go:` marker names one):", ""]
        for a in sorted(g["ambiguous"], key=lambda a: -a["cog"]):
            o.append(f"- `{short(a['file'])}:{a['line']}` {a['name']} (cog {a['cog']}): " + "; ".join(f"`{c.removeprefix('internal/')}`" for c in a["candidates"][:4]))
    o += ["", "Per area (Δ = Rust cog − Go cog; a port target is Δ ≥ 10, an own-code target cog > 25):", ""]
    o += md_areas(g["areas"])
    o += ["", f"_go-relative: {g['seconds']} s{' (cached)' if g.get('cached') else ''}_", ""]
    return o


SUMMARY_KEYS = ("ported_rows", "port_diff_ge10", "port_diff_ge10_less_residue_ge10", "port_diff_ge25", "port_excess_sum", "own_rows",
                "own_gt25", "own_gt50", "own_excess_sum")


def delta(base: dict, rev: dict) -> dict:
    """Go-relative changes from base to rev: summary counts, and the rows whose figures moved."""
    out = {"summary": {k: [base["summary"][k], rev["summary"][k]] for k in SUMMARY_KEYS}}
    for kind, val in (("port_bloat", "diff"), ("own", "cog")):
        b = {r["key"]: r for r in base[kind]}
        r_ = {r["key"]: r for r in rev[kind]}
        moved = []
        for k in b.keys() | r_.keys():
            x, y = b.get(k), r_.get(k)
            vx, vy = (x[val] if x else None), (y[val] if y else None)
            if vx != vy:
                row = y or x
                moved.append({"key": k, "name": row["name"], "file": row["file"], "line": row["line"], val: [vx, vy],
                              "cog": [x["cog"] if x else None, y["cog"] if y else None]})
        moved.sort(key=lambda m: -abs((m[val][1] or 0) - (m[val][0] or 0)))
        out[kind] = moved
    return out


def md_delta(d: dict, top: int = 20) -> list[str]:
    s = d["summary"]
    o = ["- Go-relative: " + ", ".join(f"{k} {v[0]:,} → {v[1]:,}" for k, v in s.items() if v[0] != v[1]) if any(v[0] != v[1] for v in s.values())
         else "- Go-relative: no change"]

    def fmt(v):
        return "-" if v is None else str(v)

    for kind, val, label in (("port_bloat", "diff", "Δcog against Go"), ("own", "cog", "own-code cog")):
        rows = [m for m in d[kind] if kind == "port_bloat" or max(m["cog"][0] or 0, m["cog"][1] or 0) > OWN_LIMIT]
        if rows:
            o.append(f"  - {label} moved ({len(rows)}):")
            for m in rows[:top]:
                o.append(f"    - `{short(m['file'])}` {m['name']}: {fmt(m[val][0])} → {fmt(m[val][1])}" + (f" (cog {fmt(m['cog'][0])} → {fmt(m['cog'][1])})" if kind == "port_bloat" else ""))
    o.append("")
    return o


def file_totals(g: dict) -> dict[str, dict]:
    """Per Rust file: the port-bloat targets (Δ ≥ 10) and own-code targets (cog > 25) with their excess."""
    out: dict[str, dict] = defaultdict(lambda: {"area": "", "port": 0, "port_excess": 0, "own": 0, "own_excess": 0})
    for r in g["port_bloat"]:
        if r["diff"] >= 10:
            f = out[r["file"]]
            f["area"], f["port"], f["port_excess"] = r["area"], f["port"] + 1, f["port_excess"] + r["diff"]
    for r in g["own"]:
        if r["cog"] > OWN_LIMIT:
            f = out[r["file"]]
            f["area"], f["own"], f["own_excess"] = r["area"], f["own"] + 1, f["own_excess"] + r["cog"] - OWN_LIMIT
    return out


def md_planning(g: dict, rev: str, sha: str, top: int = 60, files_per_area: int = 30) -> list[str]:
    """The wave-2 planning page: rankings, per-area and per-file totals (quality.sh ranking)."""
    o = [f"# Q0 ranking: Go-relative complexity at {rev} ({sha[:9]})", "",
         f"Generated by `scripts/perf-rearch/quality.sh ranking {rev} --top {top}` (gorel.py version {g['version']}); "
         "docs/perf-rearch/quality.md, \"Go-relative complexity\", defines every figure.", "",
         "- **Port bloat** (ported code): Δ = Rust cognitive − Go cognitive of the counterpart at the pin (or, for ports of Go's "
         "standard library and x/text, in GOROOT `src` and x/text). "
         "Both sides count their closures; Rust helpers that are splits of the Go function or lifted Go closures are summed into its row. "
         "A target is Δ ≥ 10.",
         "- **Residue** is the tagless-switch residue of the Go side: Σ (clauses − 1) over its `switch {` statements, the points an "
         "else-if chain pays for them. **Δ less residue** = Δ − min(residue, max(Δ, 0)). It explains a row; targets stay on the raw Δ.",
         f"- **Own code** (no Go counterpart): the limit is cognitive {OWN_LIMIT}. A target is cog > {OWN_LIMIT}; excess = cog − {OWN_LIMIT}.",
         "- Areas are the wave-2 lane areas, by path under `crates/ts_goport/src/`: checker (`checker/`); "
         "frontend (`frontend/`, `program/`, `program.rs`, `scanner_util*`); ls (`ls/`, with autoimport); "
         "execute/build/emit (`execute/`, `emitter/`, `bin/`, `cmd/`); effect (`effect/`); other (the rest: ast, binder, printer, "
         "transformers, declarations, project, lsp, api, gostd, …).", "", "## Summary", ""]
    o += md_summary(g)
    o += ["", "## Per area", ""]
    o += md_areas(g["areas"])
    ft = file_totals(g)
    for a in AREA_NAMES:
        v = g["areas"][a]
        o += ["", f"### {a}", ""]
        if v["subareas"]:
            o += ["| subarea | port targets | Σ Δ | Σ Δ less residue | own targets | Σ own excess |", "|---|---:|---:|---:|---:|---:|"]
            for k, x in v["subareas"].items():
                o.append(f"| {k} | {x['port_diff_ge10']} | {x['port_excess']:,} | {x['port_excess_less_residue']:,} | {x['own_gt25']} | {x['own_excess']:,} |")
            files = sorted(((f, x) for f, x in ft.items() if x["area"] == a), key=lambda fx: -(fx[1]["port_excess"] + fx[1]["own_excess"]))
            shown = f"top {files_per_area} of {len(files)}" if len(files) > files_per_area else f"{len(files)} files"
            o += ["", f"Files by Σ excess ({shown}):", "",
                  "| file | port targets | Σ Δ | own targets | Σ own excess |", "|---|---:|---:|---:|---:|"]
            for f, x in files[:files_per_area]:
                o.append(f"| `{short(f)}` | {x['port']} | {x['port_excess']:,} | {x['own']} | {x['own_excess']:,} |")
        else:
            o.append("No targets.")
    o += ["", f"## Port bloat: top {top} by Δ", ""]
    o += md_port_rows(g["port_bloat"], top)
    ratio = sorted((r for r in g["port_bloat"] if r["cog"] > OWN_LIMIT), key=lambda r: (-r["ratio"], -r["diff"]))
    o += ["", f"## Port bloat: top {top // 2} by ratio (Rust cog > {OWN_LIMIT})", ""]
    o += md_port_rows(ratio, top // 2)
    o += ["", f"## Own code: top {top} over {OWN_LIMIT}", ""]
    o += md_own_rows(g["own"], top)
    if g["ambiguous"]:
        o += ["", f"## Ambiguous name matches ({len(g['ambiguous'])}, ranked as own code)", ""]
        for x in sorted(g["ambiguous"], key=lambda x: -x["cog"]):
            o.append(f"- `{short(x['file'])}:{x['line']}` {x['name']} (cog {x['cog']}): " + "; ".join(f"`{c.removeprefix('internal/')}`" for c in x["candidates"][:4]))
    o.append("")
    return o


def sample_for_check(g: dict, n_random: int = 20, n_top: int = 40, seed: int = 9) -> list[str]:
    """Keys for the hand check: the top n_top by Rust cog and n_random others drawn at random."""
    rows = g["port_bloat"] + g["own"]
    top = sorted(rows, key=lambda r: -r["cog"])[:n_top]
    rest = [r for r in rows if r not in top]
    return [r["key"] for r in top] + [r["key"] for r in random.Random(seed).sample(rest, n_random)]
