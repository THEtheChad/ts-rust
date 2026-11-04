#!/usr/bin/env python3
"""B1 emit parity: plain `tsc -p` with emit, Go against tsc-rs bins.

usage: b1_emit_parity.py --out DIR --go BIN --rs LABEL=BIN [--rs LABEL=BIN ...]
                         [--cases quick|full] [--only SUBSTR] [--fixtures DIR]

For each case it runs every side with the same command line, the same
working directory and the same output directory (a scratch dir under --out,
moved away after each run, so output paths in messages are equal). It
compares, against Go and between the rs sides: stdout, stderr, the exit code
and the emitted tree (every file's bytes). With --diagnostics or
--extendedDiagnostics the time and memory lines are dropped before the
compare (counts stay). The rs sides also write GOPORT_CHECKER_CALL_LOG, and
the per-checker call logs of the rs sides are compared with each other.
The rs bins must be `diagnostics` feature builds (`scripts/run-cargo-capped.sh
build --release -p ts_goport --features diagnostics --bin tsgo`): the call log
and the phase report are not in a shipped build. For an A/B of the early emit,
build the base commit as the other rs side.

Projects come from ~/.cache/perf-rearch/projects (and umami from lspdiff);
outputs never go into a project. The error fixtures are made under
--fixtures (default: <out>/fixtures). Prints one line per case and a summary;
exit 1 when any compare differs.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
PROJECTS = HOME / ".cache/perf-rearch/projects"
UMAMI = HOME / ".cache/lspdiff/projects/umami"

# Plain program: no incremental or composite program.
PLAIN = ["--noEmit", "false", "--incremental", "false", "--composite", "false"]

PROJECTS_CFG = {
    "umami": (UMAMI, "tsconfig.json"),
    "hono": (PROJECTS / "hono", "tsconfig.build.json"),
    "query": (PROJECTS / "query", "packages/query-core/tsconfig.prod.json"),
    "zod": (PROJECTS / "zod", "packages/zod/tsconfig.json"),
    "effect": (PROJECTS / "effect", "packages/effect/tsconfig.noeffect.json"),
    "ts-pattern": (PROJECTS / "ts-pattern", "tsconfig.json"),
    # TS5011 (an option error) without rootDir, which skips the check.
    "ts-pattern-rootdir": (PROJECTS / "ts-pattern", "tsconfig.json", ["--rootDir", "src"]),
}

STAT_DROP = re.compile(
    r"^(.*time.*|.*Time.*|Memory used.*|.*memory.*|.*Memory.*|GC .*|Heap.*|Total .*)$"
)
STAT_LINE = re.compile(r"^[A-Za-z][A-Za-z /()-]*:\s+\S+")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


MINI_LIB = """interface Boolean {}
interface Function {}
interface CallableFunction {}
interface NewableFunction {}
interface IArguments {}
interface Number { toExponential: any; }
interface Object {}
interface RegExp {}
interface String { charAt: any; }
interface Array<T> { length: number; [n: number]: T; }
interface ReadonlyArray<T> {}
declare const console: { log(msg: any): void; };
"""


def make_fixtures(root: Path) -> dict:
    """The error fixtures. Each is a project dir with a tsconfig.json."""
    if root.exists():
        shutil.rmtree(root)
    fx = {}
    # Syntax errors: no check, emit anyway.
    d = root / "syntax"
    write(d / "tsconfig.json", '{"compilerOptions": {"strict": true, "rootDir": "src"}, "include": ["src"]}\n')
    write(d / "src/a.ts", "export const a: number = ;\n")
    write(d / "src/b.ts", 'import { a } from "./a";\nexport const b = a + 1;\n')
    fx["syntax"] = d
    # Option errors (program diagnostics): no check, emit anyway.
    d = root / "option"
    write(
        d / "tsconfig.json",
        '{"compilerOptions": {"declarationDir": "decl", "strict": true}, "include": ["src"]}\n',
    )
    write(d / "src/a.ts", "export const a: number = 1;\nexport function f(x: string) { return x.length; }\n")
    write(d / "src/b.ts", 'import { a } from "./a";\nexport const b: string = a;\n')
    fx["option"] = d
    # Global errors found by the check (Go reads them after the check).
    d = root / "global-check"
    write(
        d / "tsconfig.json",
        '{"compilerOptions": {"noLib": true, "target": "es2022", "module": "esnext", "rootDir": "src"}, "include": ["src"]}\n',
    )
    write(d / "src/lib.d.ts", MINI_LIB)
    write(d / "src/gen.ts", "export function* values() { yield 1; }\n")
    for i in range(12):
        write(
            d / f"src/m{i}.ts",
            f'import {{ values }} from "./gen";\nexport const v{i} = values();\n'
            f"export function g{i}(x: number) {{ return x * {i}; }}\n",
        )
    fx["global-check"] = d
    # Semantic errors in many files, with a global from the check.
    d = root / "semantic"
    write(
        d / "tsconfig.json",
        '{"compilerOptions": {"strict": true, "target": "es2022", "module": "esnext", "rootDir": "src"}, "include": ["src"]}\n',
    )
    for i in range(24):
        write(
            d / f"src/f{i}.ts",
            f"export const s{i}: string = {i};\nexport function h{i}(a: number) {{ return a.foo; }}\n"
            f"export enum E{i} {{ A = {i}, B }}\nexport const e{i} = E{i}.B;\n",
        )
    fx["semantic"] = d
    # Output path collisions (program diagnostics TS5055 and TS5056): an
    # input .js that its own output overwrites, and two inputs with one
    # output, both listed in `files`.
    d = root / "collision"
    write(
        d / "tsconfig.json",
        '{"compilerOptions": {"allowJs": true, "jsx": "preserve", "rootDir": "src"},'
        ' "files": ["src/a.ts", "src/a.js", "src/b.ts", "src/b.tsx", "src/c.ts"]}\n',
    )
    write(d / "src/a.ts", "export const a = 1;\n")
    write(d / "src/a.js", "export const a = 2;\n")
    write(d / "src/b.ts", "export const b = 1;\n")
    write(d / "src/b.tsx", "export const b = <div/>;\n")
    write(d / "src/c.ts", "export const c: number = 3;\n")
    fx["collision"] = d
    d = root / "overwrite-input"
    write(d / "tsconfig.json", '{"compilerOptions": {"allowJs": true, "rootDir": "src"}, "include": ["src"]}\n')
    write(d / "src/a.js", "export const a = 2;\n")
    write(d / "src/b.ts", "export const b: number = 1;\n")
    fx["overwrite-input"] = d
    # Write failures: a directory where an output file goes (made by the
    # harness in the output dir, `setup`).
    d = root / "writefail"
    write(
        d / "tsconfig.json",
        '{"compilerOptions": {"strict": true, "declaration": true, "sourceMap": true, "rootDir": "src"}, "include": ["src"]}\n',
    )
    for i in range(8):
        write(d / f"src/w{i}.ts", f"export const w{i}: number = {i};\nexport class C{i} {{ x = {i}; }}\n")
    fx["writefail"] = d
    return fx


def cases(kind: str, fx: dict) -> list:
    """(name, cwd, args, setup, out_is_file). `OUT` in args is the output dir."""
    c = []

    def proj(name, extra, label=None, setup=None):
        cwd, cfg, *base = PROJECTS_CFG[name]
        base = base[0] if base else []
        c.append((f"{name}:{label or ' '.join(extra) or 'default'}", cwd,
                  ["-p", cfg, *PLAIN, "--outDir", "OUT", *base, *extra], setup))

    quick_projects = ["umami", "hono", "query", "ts-pattern-rootdir"]
    all_projects = ["umami", "hono", "query", "zod", "effect", "ts-pattern", "ts-pattern-rootdir"]
    names = all_projects if kind == "full" else quick_projects
    for name in names:
        proj(name, [])
        proj(name, ["--declaration"])
        proj(name, ["--sourceMap", "--declaration", "--declarationMap"])
        if name in ("umami", "hono", "query", "ts-pattern", "ts-pattern-rootdir"):
            proj(name, ["--module", "commonjs", "--moduleResolution", "bundler"], "commonjs")
            proj(name, ["--module", "commonjs", "--moduleResolution", "node10", "--ignoreDeprecations", "6.0"], "commonjs-node10")
        proj(name, ["--noCheck", "--declaration"])
        proj(name, ["--noEmitOnError"])
        proj(name, ["--extendedDiagnostics", "--declaration"])
        proj(name, ["--diagnostics"])
        for n in (4, 8, 16):
            proj(name, ["--checkers", str(n), "--declaration", "--sourceMap"], f"checkers{n}")
        proj(name, ["--listEmittedFiles"])
    # The incremental path (tsbuildinfo), which this lane does not change.
    for name in ("hono", "ts-pattern-rootdir"):
        proj(name, ["--incremental", "--tsBuildInfoFile", "OUT/tsconfig.tsbuildinfo", "--declaration"], "incremental")
    if kind == "full":
        for name in ("umami", "effect"):
            proj(name, ["--verbatimModuleSyntax"])
        proj("umami", ["--singleThreaded", "--declaration"])
        proj("umami", ["--emitDeclarationOnly", "--declaration"])

    def fixture(name, extra, label=None, setup=None):
        c.append((f"fx-{name}:{label or ' '.join(extra) or 'default'}", fx[name],
                  ["-p", "tsconfig.json", "--outDir", "OUT", *extra], setup))

    # Without --outDir the outputs go next to the inputs: each side gets a
    # fresh copy of the fixture at one path (`FRESH:` cwd).
    def fixture_no_outdir(name, extra, label):
        c.append((f"fx-{name}:{label}", f"FRESH:{fx[name]}", ["-p", "tsconfig.json", *extra], None))

    fixture_no_outdir("overwrite-input", [], "no-outdir")
    fixture_no_outdir("overwrite-input", ["--declaration"], "no-outdir-declaration")
    for name in fx:
        if name == "overwrite-input":
            continue
        fixture(name, [])
        fixture(name, ["--declaration"])
        fixture(name, ["--noCheck", "--declaration"])
        fixture(name, ["--noEmitOnError", "--declaration"])
        fixture(name, ["--extendedDiagnostics"])
        fixture(name, ["--checkers", "8", "--declaration", "--sourceMap"], "checkers8")
    # Write failures: a directory at an output path; and the output dir is a file.
    def dir_at_output(out: Path):
        for i in (1, 5):
            (out / f"w{i}.js").mkdir(parents=True)
            (out / f"w{i}.d.ts").mkdir(parents=True)
    fixture("writefail", ["--declaration", "--sourceMap"], "dir-at-output", dir_at_output)

    def out_is_file(out: Path):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("not a dir\n")
    fixture("writefail", ["--declaration"], "outdir-is-file", out_is_file)
    proj("ts-pattern-rootdir", ["--declaration"], "dir-at-output",
         lambda out: [(out / f).mkdir(parents=True) for f in ("index.js", "types/Match.d.ts")])
    return c


def tree_digest(path: Path) -> dict:
    files = {}
    if path.is_file():
        return {".": hashlib.sha256(path.read_bytes()).hexdigest()}
    if not path.exists():
        return files
    for p in sorted(path.rglob("*")):
        rel = str(p.relative_to(path))
        if p.is_file():
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir() and not any(p.iterdir()):
            files[rel + "/"] = "dir"
    return files


def norm_stats(text: str, stats: bool) -> str:
    if not stats:
        return text
    out = []
    for line in text.splitlines(keepends=True):
        s = line.rstrip("\n")
        if STAT_LINE.match(s) and STAT_DROP.match(s):
            continue
        out.append(line)
    return "".join(out)


REPORT_HEADER = "emit phase times (GOPORT_EMIT_PHASE_TIMES)"
REPORT_ROUTING = "emit routing (files)"


def clean_env(extra: dict) -> dict:
    # Every GOPORT_* variable is removed from the inherited environment, then
    # each side sets what it depends on.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOPORT_")}
    env.update(extra)
    return env


def split_report(err: str):
    """(stderr without the phase report, routes) or raises ValueError.

    The report must be there once, in its known form: the header, indented
    table lines, the routing header and indented `<name> <count>` lines.
    Every other line stays.
    """
    lines = err.splitlines(keepends=True)
    at = [i for i, l in enumerate(lines) if l.rstrip("\n") == REPORT_HEADER]
    if len(at) != 1:
        raise ValueError(f"{len(at)} phase reports")
    i = at[0] + 1
    while i < len(lines) and lines[i].startswith("  "):
        if not re.match(r"^  (thread|loading|checker|pool|twin|flush|other|total|deferred writes)\b", lines[i]):
            raise ValueError(f"unknown report line {lines[i]!r}")
        i += 1
    if i >= len(lines) or lines[i].rstrip("\n") != REPORT_ROUTING:
        raise ValueError("no routing part")
    i += 1
    routes = {}
    while i < len(lines) and lines[i].startswith("  "):
        m = re.match(r"^  (.+?)\s+(\d+)\n?$", lines[i])
        if not m:
            raise ValueError(f"unknown routing line {lines[i]!r}")
        routes[m.group(1)] = int(m.group(2))
        i += 1
    return "".join(lines[: at[0]] + lines[i:]), routes


def expected_start(name: str, args: list) -> str:
    """How the early side (B) must send its emit: early, late or none."""
    if name.endswith(":incremental"):
        return "none"  # the incremental program (its own early emit)
    if "--noEmitOnError" in args:
        return "late|none"  # none: errors, so nothing is emitted
    if "--singleThreaded" in args:
        return "late"
    if name.startswith(("zod:", "effect:default", "effect:--verbatimModuleSyntax", "ts-pattern:")):
        return "late"  # zod fails F1 (nodenext); TS5069 / TS5011 option errors
    if name.endswith(":dir-at-output"):
        return "late"  # an output path that is not a regular file keeps the barrier
    if name.startswith("effect:") and "--declaration" not in args:
        return "late"  # composite off leaves declarationMap without declaration: TS5069
    if ":commonjs-node10" in name:
        return "late"  # TS5107 option error
    if name.startswith(("fx-syntax:", "fx-option:", "fx-collision:", "fx-overwrite-input:")):
        return "late"
    return "early"


def start_of(routes: dict) -> str:
    early = routes.get("emit sent with check (plain tsc -p)", 0)
    late = routes.get("emit sent after check", 0)
    if early and late:
        return "both"
    return "early" if early else "late" if late else "none"


def run_side(binary: str, cwd, args: list, out: Path, setup, env_extra: dict) -> dict:
    if out.exists() or out.is_symlink():
        if out.is_dir():
            shutil.rmtree(out)
        else:
            out.unlink()
    if setup:
        setup(out)
    if isinstance(cwd, str) and cwd.startswith("FRESH:"):
        source = Path(cwd[len("FRESH:"):])
        cwd = out.parent / "fresh"
        if cwd.exists():
            shutil.rmtree(cwd)
        shutil.copytree(source, cwd)
    argv = [binary] + [str(out) + a[3:] if a.startswith("OUT") else a for a in args]
    p = subprocess.run(argv, cwd=cwd, capture_output=True, env=clean_env(env_extra), timeout=900)
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "cwd": cwd}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--go", required=True)
    ap.add_argument("--rs", action="append", required=True,
                    help="LABEL=BIN; the last one is the early side whose routing is checked")
    ap.add_argument("--cases", default="quick")
    ap.add_argument("--only", default="")
    ap.add_argument("--fixtures", default="")
    ap.add_argument("--keep-trees", action="store_true")
    a = ap.parse_args()
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    fx = make_fixtures(Path(a.fixtures) if a.fixtures else root / "fixtures")
    sides = [("go", a.go)] + [tuple(s.split("=", 1)) for s in a.rs]
    rs_labels = [label for label, _ in sides[1:]]
    early_label = rs_labels[-1]
    out = root / "run" / "out"
    bad = 0
    total = 0
    early_cases = 0
    known = 0
    for name, cwd, args, setup in cases(a.cases, fx):
        if a.only and a.only not in name:
            continue
        total += 1
        stats = "--diagnostics" in args or "--extendedDiagnostics" in args
        case_dir = root / "cases" / re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        case_dir.mkdir(parents=True, exist_ok=True)
        results = {}
        diffs = []
        fresh = isinstance(cwd, str) and cwd.startswith("FRESH:")
        for label, binary in sides:
            # The compared run: no instrumentation.
            r = run_side(binary, cwd, args, out, setup, {})
            r["tree"] = tree_digest(r["cwd"] if fresh else out)
            r["stdout_n"] = norm_stats(r["stdout"].decode("utf-8", "replace"), stats)
            r["stderr_n"] = r["stderr"].decode("utf-8", "replace")
            (case_dir / f"{label}.stdout").write_bytes(r["stdout"])
            (case_dir / f"{label}.stderr").write_bytes(r["stderr"])
            (case_dir / f"{label}.rc").write_text(f"{r['rc']}\n")
            (case_dir / f"{label}.tree").write_text(
                "".join(f"{h} {f}\n" for f, h in sorted(r["tree"].items())))
            if a.keep_trees and out.exists():
                dest = case_dir / f"{label}.out"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.move(str(out), dest)
            r["route"] = "-"
            if label != "go":
                # The instrumented run: routing and a fresh call log.
                log = case_dir / f"{label}.calllog"
                if log.exists():
                    log.unlink()
                ri = run_side(binary, cwd, args, out, setup, {
                    "GOPORT_CHECKER_CALL_LOG": str(log),
                    "GOPORT_EMIT_PHASE_TIMES": "1",
                })
                err = ri["stderr"].decode("utf-8", "replace")
                try:
                    rest, routes = split_report(err)
                    (case_dir / f"{label}.phases").write_text(err)
                    r["route"] = start_of(routes)
                    if rest != r["stderr_n"]:
                        diffs.append(f"{label}:instrumented-stderr")
                    if ri["rc"] != r["rc"]:
                        diffs.append(f"{label}:instrumented-rc")
                except ValueError as e:
                    diffs.append(f"{label}:report({e})")
                if not log.exists():
                    diffs.append(f"{label}:no-calllog")
                    r["log"] = None
                else:
                    r["log"] = log.read_bytes()
            results[label] = r
        go = results["go"]
        for label in rs_labels:
            r = results[label]
            for key, what in (("rc", "rc"), ("stdout_n", "stdout"), ("stderr_n", "stderr"), ("tree", "tree")):
                if r[key] != go[key]:
                    diffs.append(f"{label}!=go:{what}")
        first = rs_labels[0]
        for label in rs_labels[1:]:
            if results[label].get("log") != results[first].get("log"):
                diffs.append(f"calllog:{label}!={first}")
        expect = expected_start(name, args)
        got = results[early_label]["route"]
        if got not in expect.split("|"):
            diffs.append(f"{early_label}:start={got}!={expect}")
        for label in rs_labels[:-1]:
            if results[label]["route"] == "early":
                diffs.append(f"{label}:started-early")
        if got == "early":
            early_cases += 1
        nfiles = len(go["tree"])
        nlog = (results[first].get("log") or b"").count(b"\n")
        # A difference with Go that every rs side has, byte for byte (the
        # pre-change side too), is main's, not this lane's.
        same_rs = all(
            results[l][k] == results[first][k]
            for l in rs_labels for k in ("rc", "stdout_n", "stderr_n", "tree"))
        if diffs and same_rs and all("!=go:" in d for d in diffs):
            status = "KNOWN(main) " + ",".join(diffs)
            known += 1
        elif diffs:
            status = "DIFF " + ",".join(diffs)
            bad += 1
        else:
            status = "MATCH"
        routes = " ".join(f"{label}:{results[label]['route']}" for label in rs_labels)
        print(f"{status:<8} {name:<58} rc={go['rc']} files={nfiles} log={nlog} {routes}", flush=True)
    print(f"summary: {total - bad - known}/{total} cases match, {known} known differences of main, "
          f"{bad} differences; {early_cases} cases emitted early on {early_label}", flush=True)
    if early_cases == 0:
        print("FAIL: no case took the early path", flush=True)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
