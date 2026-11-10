#!/usr/bin/env python3
"""The source a build actually compiles, from rustc: cfg-expanded source per crate, cut into one
piece per source module file.

Used by quality.py (the escape-hatch inventory, `quality.sh gating`); docs/perf-rearch/quality.md
section 8 explains the method. Run `python3 -I scripts/perf-rearch/qexpand.py <rev> [variant]` to
print a summary of one expansion.

How it works:
- A detached worktree, ~/.cache/perf-rearch/quality/expand-wt (its own target dir), is checked out
  at the revision. `scripts/run-cargo-capped.sh check --locked --release -p ts_goport --lib --bins`
  runs there on CI's toolchain (`ci_toolchain`) with qexpand_wrapper.py as RUSTC_WORKSPACE_WRAPPER.
  For every workspace crate cargo compiles (ts_goport's lib and bins, goport_core, goport_util,
  goport_lsproto, goport_cell), the wrapper runs cargo's exact rustc command a second time with
  `-Zunpretty=expanded`, and keeps rustc's dep-info. `cargo check --release` gives the release
  profile's cfg (no debug_assertions) without codegen.
- Variants: `default` (the default features: the shipped build's code), `features` (the default
  plus `diagnostics,test-hooks`), and `wasm` (`--profile wasm --target wasm32-wasip1
  --no-default-features -p ts_wasm`, the wasm build's code, as scripts/wasm/build.sh builds it).
- Attribution. The expanded source keeps `mod` nesting, and an out-of-line module keeps its
  `#[path]` attribute but loses its file. `pieces` walks the module tree and resolves each module's
  file with rustc's own rules (rustc_expand `mod_dir_path`/`mod_file_path`): a `#[path]` is
  relative to the declaring module's directory, `name.rs` children live in `<dir>/name/`, inline
  modules push their name. Whether a module was out of line (`mod x;`) or inline (`mod x { }`) is
  read from the declaring source file's tokens at the same inline nesting. Every resolved file
  must be in rustc's dep-info for that crate, or the run fails: attribution is exact or an error,
  never a guess.
- Each piece is the expanded text of one module file: its module body with its out-of-line child
  modules cut out (replaced by `mod name;`); inline child modules stay. A file that two modules
  include (`#[path]` twice) gives two pieces.
- `env_deps`: the names rustc read through `env!`/`option_env!` (dep-info `# env-dep:`); the
  expanded source holds only their values.
- Cached by the crates/ tree, the root Cargo.toml and Cargo.lock blobs, the toolchain and the
  variant under ~/.cache/perf-rearch/quality/expand/ (gzip JSON).
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QX_VERSION = 3  # bump when the piece format or the method changes
CACHE = Path(os.environ.get("PERF_QUALITY_CACHE", Path.home() / ".cache/perf-rearch/quality"))
REPO = Path(__file__).resolve().parents[2]
WRAPPER = Path(__file__).resolve().parent / "qexpand_wrapper.py"

# cargo check arguments of each build. `--release` gives the release profile's cfg (no
# debug_assertions); the wasm build's own profile (`wasm`: panic = "abort") gives its cfg.
VARIANTS = {
    "default": {"args": ["--release", "-p", "ts_goport", "--lib", "--bins"], "features": []},
    "features": {"args": ["--release", "-p", "ts_goport", "--lib", "--bins", "--features", "diagnostics,test-hooks"],
                 "features": ["diagnostics", "test-hooks"]},
    "wasm": {"args": ["--profile", "wasm", "-p", "ts_wasm", "--target", "wasm32-wasip1", "--no-default-features"],
             "features": []},
}

# a lexer for the module walk: strings, chars and comments are skipped as wholes; lifetimes are not
# chars ('a); raw strings with any number of #
WALK_RE = re.compile(
    r"""
    (?P<lc>//[^\n]*)
  | (?P<bc>/\*)
  | (?P<raw>[bc]?r(?P<h>\#*)"[\s\S]*?"(?P=h))
  | (?P<str>[bc]?"(?:\\[\s\S]|[^"\\])*")
  | (?P<chr>b?'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|[^\n])|[^'\\\n])')
  | (?P<life>'[A-Za-z_][A-Za-z0-9_]*)
  | (?P<id>(?:r\#)?[A-Za-z_][A-Za-z0-9_]*)
  | (?P<num>[0-9][0-9A-Za-z_]*)
  | (?P<p>[{}\[\]();\#=!])
    """,
    re.X,
)


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def ci_toolchain(sha: str) -> str:
    """CI's toolchain at `sha`: the top-level `RUSTUP_TOOLCHAIN` of .github/workflows/ci.yml (the
    toolchain of CI's clippy and of the shipped bins); HEAD's when the revision has none."""
    for rev in (sha, "HEAD"):
        try:
            text = git("show", f"{rev}:.github/workflows/ci.yml")
        except subprocess.CalledProcessError:
            continue
        m = re.search(r"^env:\s*\n((?:[ \t]+.*\n|\s*\n)*)", text, re.M)
        if m:
            t = re.search(r"^[ \t]+RUSTUP_TOOLCHAIN:\s*['\"]?([\w.\-]+)", m.group(1), re.M)
            if t:
                return t.group(1)
    sys.exit("qexpand: no top-level RUSTUP_TOOLCHAIN in .github/workflows/ci.yml")


def tokens(text: str):
    """(kind, text, start) for identifiers, the punctuation the walk needs, and strings."""
    out = []
    pos = 0
    n = len(text)
    while True:
        m = WALK_RE.search(text, pos)
        if not m:
            return out
        g = m.lastgroup
        if g == "h":
            g = "raw"
        if g == "bc":  # nested block comments
            depth, j = 1, m.end()
            while depth and j < n:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            pos = j
            continue
        pos = m.end()
        if g == "lc":
            continue
        out.append((g, m.group(), m.start()))


def attr_path(toks, i: int, j: int) -> str | None:
    """The value of a `#[path = "..."]` among the attributes in toks[i:j]."""
    for k in range(i, j - 3):
        if toks[k][1] == "#" and toks[k + 1][1] == "[" and toks[k + 2][1] == "path" and toks[k + 3][1] == "=":
            s = toks[k + 4][1]
            return s[s.index('"') + 1 : s.rindex('"')]
    return None


def source_mod_decls(text: str) -> dict[tuple, dict[str, set[str]]]:
    """For each inline-module nesting (a tuple of names) of a source file, the module names
    declared out of line (`mod x;`) and inline (`mod x {`). Attributes do not matter here: the
    expanded source says which declarations survived and carries their `#[path]`."""
    toks = tokens(text)
    out: dict[tuple, dict[str, set[str]]] = {}
    stack: list[tuple[str, int]] = []  # (inline module name, brace depth at its `{`)
    depth = 0
    for i, (k, t, _) in enumerate(toks):
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
            if stack and stack[-1][1] == depth + 1:
                stack.pop()
        elif t == "mod" and k == "id" and i + 2 < len(toks) and toks[i + 1][0] == "id":
            name = toks[i + 1][1].removeprefix("r#")
            ctx = tuple(s for s, _ in stack)
            d = out.setdefault(ctx, {"outline": set(), "inline": set()})
            if toks[i + 2][1] == ";":
                d["outline"].add(name)
            elif toks[i + 2][1] == "{":
                d["inline"].add(name)
                d.setdefault("bodies", {}).setdefault(name, []).append((toks[i + 2][2] + 1, i + 2))
                stack.append((name, depth + 1))
    ends = {}
    st = []
    for i, (_, t, p) in enumerate(toks):
        if t == "{":
            st.append(i)
        elif t == "}" and st:
            ends[st.pop()] = p
    for d in out.values():
        for name, bodies in d.get("bodies", {}).items():
            d["bodies"][name] = [text[a : ends.get(j, len(text))] for a, j in bodies]
    return out


def content_tokens(text: str) -> set[str]:
    """The distinct identifiers, numbers and string literals of a text, for telling two candidate
    sources apart."""
    return {t for k, t, _ in tokens(text) if k in ("id", "num", "str", "raw")}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def read_dep_info(path: Path) -> tuple[list[str], list[str]]:
    files: list[str] = []
    env: list[str] = []
    for ln in path.read_text().splitlines():
        if ln.startswith("# env-dep:"):
            env.append(ln[len("# env-dep:") :].split("=", 1)[0])
        elif ln.endswith(":") and not ln.startswith("#") and ln.strip():
            files.append(os.path.normpath(ln[:-1].replace("\\ ", " ")))
    return files, env


class AttributionError(Exception):
    pass


def resolve_outline(parent: dict, name: str, path_attr: str | None, deps: set[str]) -> tuple[str, str | None]:
    """rustc's file for `mod name;` in module `parent` (rustc_expand module.rs `mod_file_path`,
    `default_submod_path`): (file, the child's `relative` dir component). A `#[path]` file is
    relative to the declaring module's dir and owns its own dir (like a mod.rs); `name.rs` owns
    `<dir>/name/`; `name/mod.rs` owns `<dir>/name/`."""
    if path_attr is not None:
        return os.path.normpath(os.path.join(parent["dir"], path_attr)), None
    base = os.path.join(parent["dir"], parent["relative"]) if parent["relative"] else parent["dir"]
    a, b = os.path.normpath(f"{base}/{name}.rs"), os.path.normpath(f"{base}/{name}/mod.rs")
    if a in deps and b in deps:
        raise AttributionError(f"both {a} and {b} are compiled")
    return (a, name) if a in deps else (b, None)


def pieces(expanded: str, root: str, deps: set[str], read_source) -> tuple[list[dict], list[dict]]:
    """Cut one crate's expanded source into pieces, one per module file (see the module doc).

    `root` is the crate root file, `deps` rustc's dep-info file list (normalized, relative to the
    workspace root) and `read_source(path)` the source text at the revision. Returns the pieces
    and every module of the build ({modpath, file, inline})."""
    toks = tokens(expanded)
    n = len(toks)
    close: dict[int, int] = {}
    st: list[int] = []
    for j, (_, t, _) in enumerate(toks):
        if t == "{":
            st.append(j)
        elif t == "}" and st:
            close[st.pop()] = j
    decl_cache: dict[str, dict] = {}

    def decls(path: str) -> dict:
        if path not in decl_cache:
            decl_cache[path] = source_mod_decls(read_source(path))
        return decl_cache[path]

    # module frames: file module = {file, dir, relative, ctx (inline names inside the file), start, cuts}
    out: list[dict] = []
    root_frame = {"file": root, "dir": os.path.dirname(root), "relative": None, "ctx": (), "modpath": "crate",
                  "body": (0, len(expanded)), "cuts": [], "file_frame": None}
    root_frame["file_frame"] = root_frame
    frames = [root_frame]  # the module stack
    modules = [{"modpath": "crate", "file": root, "inline": False}]
    brace_owner: list[dict | None] = []  # per open `{`: the module frame it opens, or None
    attr_start = None  # first token of the attributes before the current item
    i = 0
    while i < n:
        k, t, pos = toks[i]
        if t == "#" and i + 1 < n and toks[i + 1][1] in ("[", "!"):
            if attr_start is None:
                attr_start = i
            j = i + 1 + (toks[i + 1][1] == "!")
            depth = 0
            while j < n:
                if toks[j][1] == "[":
                    depth += 1
                elif toks[j][1] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        if k == "id" and t == "mod" and i + 2 < n and toks[i + 1][0] == "id" and toks[i + 2][1] == "{":
            name = toks[i + 1][1].removeprefix("r#")
            parent = frames[-1]
            pfile = parent["file_frame"]
            path_attr = attr_path(toks, attr_start, i) if attr_start is not None else None
            d = decls(pfile["file"]).get(parent["ctx"], {"outline": set(), "inline": set()})
            outline = name in d["outline"]
            how = "declaration"
            if outline and name in d["inline"]:
                # Both forms under different cfgs (core.rs `lib_names`: a file on native, inline on
                # wasm). The file's candidate path decides when rustc did not read it; otherwise the
                # expanded body must clearly match one of the two sources.
                cand = resolve_outline(parent, name, path_attr, deps)[0]
                if cand not in deps:
                    outline = False
                else:
                    body = expanded[toks[i + 2][2] + 1 : toks[close[i + 2]][2]]
                    # Jaccard similarity of distinct tokens: a stub of five names matches the
                    # inline twin, a table of thousands the file, whichever is bigger
                    e = content_tokens(body)
                    s_out = jaccard(e, content_tokens(read_source(cand)))
                    s_in = max(jaccard(e, content_tokens(b)) for b in d["bodies"][name])
                    if abs(s_out - s_in) < 0.2:
                        raise AttributionError(f"{pfile['file']}: `mod {name}` is declared both inline and out of "
                                               f"line, and the expanded body matches neither clearly "
                                               f"(file {s_out:.2f}, inline {s_in:.2f})")
                    outline = s_out > s_in
                how = "content" if cand in deps else "dep-info"
            decl_start = toks[attr_start][2] if attr_start is not None else pos
            if outline:
                f, rel = resolve_outline(parent, name, path_attr, deps)
                if f not in deps:
                    raise AttributionError(f"module {parent['modpath']}::{name} resolves to {f}, which rustc did not read "
                                           f"(not in the dep-info)")
                fr = {"file": f, "dir": os.path.dirname(f), "relative": rel, "ctx": (),
                      "modpath": f"{parent['modpath']}::{name}", "cuts": [], "decl_start": decl_start, "how": how}
                fr["file_frame"] = fr
            else:
                if path_attr is not None:
                    dirp, rel = os.path.normpath(os.path.join(parent["dir"], path_attr)), None
                else:
                    base = os.path.join(parent["dir"], parent["relative"]) if parent["relative"] else parent["dir"]
                    dirp, rel = os.path.normpath(os.path.join(base, name)), None
                fr = {"file": pfile["file"], "dir": dirp, "relative": rel, "ctx": parent["ctx"] + (name,),
                      "modpath": f"{parent['modpath']}::{name}", "file_frame": pfile}
            open_pos = toks[i + 2][2]
            fr["body_start"] = open_pos + 1
            modules.append({"modpath": fr["modpath"], "file": fr["file"], "inline": not outline})
            frames.append(fr)
            brace_owner.append(fr)
            attr_start = None
            i += 3
            continue
        if t == "{":
            brace_owner.append(None)
        elif t == "}":
            owner = brace_owner.pop() if brace_owner else None
            if owner is not None:
                frames.pop()
                if owner["file_frame"] is owner:  # an out-of-line module ends
                    owner["body"] = (owner["body_start"], pos)
                    out.append(owner)
                    pf = frames[-1]["file_frame"]
                    pf["cuts"].append((owner["decl_start"], pos + 1, owner["modpath"].rsplit("::", 1)[1]))
        if k == "id" and t == "pub":
            if attr_start is None:
                attr_start = i  # the item starts at its visibility
        elif not (k == "id" and t in ("crate", "super", "in", "self")) and t not in ("(", ")"):
            attr_start = None
        i += 1
    out.append(root_frame)
    res = []
    for fr in out:
        a, b = fr["body"]
        parts = []
        cur = a
        for cs, ce, name in sorted(fr["cuts"]):
            parts.append(expanded[cur:cs])
            parts.append(f"mod {name};")
            cur = ce
        parts.append(expanded[cur:b])
        res.append({"file": fr["file"], "modpath": fr["modpath"], "how": fr.get("how", "declaration"),
                    "text": "".join(parts)})
    return res, modules


# --------------------------------------------------------------------------
# the cargo run


def cache_key(sha: str, variant: str, toolchain: str) -> str:
    parts = [git("rev-parse", f"{sha}:crates").strip()]
    for p in ("Cargo.toml", "Cargo.lock"):
        try:
            parts.append(git("rev-parse", f"{sha}:{p}").strip()[:12])
        except subprocess.CalledProcessError:
            parts.append("none")
    return f"{parts[0]}-{parts[1]}-{parts[2]}-{toolchain}-{variant}-v{QX_VERSION}"


def expand(sha: str, variant: str = "default", log=sys.stderr) -> dict:
    """The expansion of `sha` for `variant` (cached): {"toolchain", "variant", "features",
    "crates": [{crate, kind, root, files, env_deps, unclaimed}], "pieces": [{crate, kind, modpath,
    file, how, text}], "modules": [{crate, kind, modpath, file, inline}], "seconds"}."""
    sha = git("rev-parse", "--verify", f"{sha}^{{commit}}").strip()
    toolchain = ci_toolchain(sha)
    key = cache_key(sha, variant, toolchain)
    cache = CACHE / "expand" / f"{key}.json.gz"
    if cache.exists():
        with gzip.open(cache, "rt") as f:
            return json.load(f)
    (CACHE / "expand").mkdir(parents=True, exist_ok=True)
    with open(CACHE / "expand" / "worktree.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cache.exists():
            with gzip.open(cache, "rt") as f:
                return json.load(f)
        res = expand_locked(sha, variant, toolchain, log)
    tmp = cache.with_suffix(".tmp")
    with gzip.open(tmp, "wt", compresslevel=3) as f:
        json.dump(res, f)
    tmp.replace(cache)
    return res


def expand_locked(sha: str, variant: str, toolchain: str, log) -> dict:
    t0 = time.time()
    wt = CACHE / "expand-wt"
    if not (wt / ".git").exists():
        subprocess.run(["git", "worktree", "add", "--detach", str(wt), sha], cwd=REPO, check=True, capture_output=True)
    else:
        subprocess.run(["git", "checkout", "--detach", "--force", sha], cwd=wt, check=True, capture_output=True)
        subprocess.run(["git", "clean", "-fdq", "--", "crates"], cwd=wt, check=True, capture_output=True)
    spec = VARIANTS[variant]
    cmd = ["scripts/run-cargo-capped.sh", "check", "--locked", *spec["args"]]
    print(f"qexpand: {variant} expansion of {sha[:9]} on {toolchain} (cargo check {' '.join(spec['args'])}, about 1 min)", file=log)
    env = {"TS_CARGO_NIGHTLY": "0", "TS_CARGO_INCREMENTAL": "0", "TS_CARGO_SCCACHE": "0"}
    out = run_wrapped(wt, wt / "crates", cmd, toolchain, variant, env)
    try:
        return collect(sha, variant, toolchain, out, wt, t0, ("ts_goport.lib", "ts_wasm.lib"))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def run_wrapped(cwd: Path, src: Path, cmd: list[str], toolchain: str, label: str, extra_env: dict) -> Path:
    """Runs `cmd` (a cargo check) in `cwd` with qexpand_wrapper.py as RUSTC_WORKSPACE_WRAPPER and
    returns the directory of its outputs. The .rs files under `src` are touched first: cargo reruns
    rustc only for a crate whose sources are newer than its output, and every workspace crate must
    be compiled (and expanded) in this run."""
    now = time.time()
    for p in src.rglob("*.rs"):
        os.utime(p, (now, now))
    out = CACHE / "tmp" / f"qx-{os.getpid()}-{label}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    env = dict(os.environ, RUSTUP_TOOLCHAIN=toolchain, QX_OUT=str(out), RUSTC_WORKSPACE_WRAPPER=str(WRAPPER), **extra_env)
    for k in ("RUSTC_WRAPPER", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"):
        env.pop(k, None)
    if "CARGO_TARGET_DIR" not in extra_env:
        env.pop("CARGO_TARGET_DIR", None)
    logf = CACHE / "expand" / f"last-{label}.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    with open(logf, "w") as lf:
        r = subprocess.run(cmd, cwd=cwd, env=env, stdout=lf, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        tail = logf.read_text()[-3000:]
        shutil.rmtree(out, ignore_errors=True)
        raise SystemExit(f"qexpand: cargo check failed (rc={r.returncode}); log {logf}:\n{tail}")
    return out


def expand_crate(crate_dir: Path, features: list[str], toolchain: str, label: str, target: str | None = None) -> dict:
    """The expansion of a standalone crate (its own workspace), for quality_test.py's fixture:
    `cargo check --release` with `features` (and `--target`), in a scratch target dir."""
    cmd = ["cargo", "check", "--release", "--quiet", *(["--features", ",".join(features)] if features else []),
           *(["--target", target] if target else [])]
    target = CACHE / "tmp" / f"qx-target-{crate_dir.name}"
    out = run_wrapped(crate_dir, crate_dir, cmd, toolchain, label, {"CARGO_TARGET_DIR": str(target)})
    try:
        return collect("fixture", label, toolchain, out, crate_dir, time.time(), None)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def collect(sha: str, variant: str, toolchain: str, out: Path, wt: Path, t0: float, top) -> dict:
    crates = []
    all_pieces = []
    all_modules = []
    found = sorted(p.name[: -len(".rs")] for p in out.glob("*.rs"))
    if not found or (top and not any(f.startswith(top) for f in found)):
        raise SystemExit(f"qexpand: no expansion of the top crate in {out} (found {found}); was the wrapper run?")
    for stem in found:
        meta = json.loads((out / f"{stem}.json").read_text())
        files, env_deps = read_dep_info(out / f"{stem}.d")
        roots = [os.path.normpath(a) for a in meta["args"] if a.endswith(".rs") and not a.startswith("-")]
        if len(roots) != 1:
            raise SystemExit(f"qexpand: {stem}: no single crate root in the rustc args: {roots}")
        root = roots[0]
        deps = set(files)
        text = (out / f"{stem}.rs").read_text(errors="replace")

        def read_source(p: str) -> str:
            return (wt / p).read_text(errors="replace")

        ps, mods = pieces(text, root, deps, read_source)
        claimed = {p["file"] for p in ps}
        rs_deps = sorted(f for f in deps if f.endswith(".rs"))
        crates.append({"crate": meta["crate"], "kind": meta["kind"], "root": root, "features": meta["features"],
                       "target": meta["target"], "files": sorted(claimed), "env_deps": env_deps,
                       # .rs files rustc read that no module claims: include!/include_str!/include_bytes!
                       "unclaimed": [f for f in rs_deps if f not in claimed]})
        for p in ps:
            all_pieces.append({"crate": meta["crate"], "kind": meta["kind"], **p})
        for m in mods:
            all_modules.append({"crate": meta["crate"], "kind": meta["kind"], **m})
    return {"sha": sha, "variant": variant, "toolchain": toolchain, "features": VARIANTS.get(variant, {}).get("features", []),
            "crates": crates, "pieces": all_pieces, "modules": all_modules, "seconds": round(time.time() - t0, 1)}


def main() -> None:
    rev = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    variant = sys.argv[2] if len(sys.argv) > 2 else "default"
    r = expand(rev, variant)
    print(f"{r['variant']} {r['sha'][:9]} on {r['toolchain']}: {len(r['pieces'])} pieces, {r['seconds']} s")
    for c in r["crates"]:
        print(f"  {c['crate']} ({c['kind']}): {len(c['files'])} files, env-deps {c['env_deps']}, "
              f"unclaimed .rs {len(c['unclaimed'])}" + (f" {c['unclaimed'][:4]}" if c["unclaimed"] else ""))


if __name__ == "__main__":
    main()
