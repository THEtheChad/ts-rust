#!/usr/bin/env python3
"""Output-layout repros on Windows (NTFS) and Linux, from WSL (lane WIN).

The cases of layout_repros.sh / dirlink_repro.py / aliased_repro.py, made self-contained so one
definition runs on both sides, plus the Windows-only kinds: directory symlinks (mklink /D),
junctions (mklink /J), file symlinks (mklink), hard links (mklink /H), case and Unicode-case
collisions (a case-sensitive source dir, `fsutil file setCaseSensitiveInfo`) and 8.3 short names.

A bin ending in `.exe` runs on Windows through WSL interop: its scratch dirs are under --win-work
(a /mnt/c path) and every link is made by cmd.exe. Any other bin runs on Linux, scratch under
--linux-work, with os.symlink (a junction becomes an absolute symlink, its Linux analog).

Before each run the scratch dir is emptied of everything but src/ and tsconfig.json, and the
case's links are made again. A run's result is the hash of the scratch tree without src/ (paths,
kinds, link targets with the scratch dir masked, bytes), stdout (scratch dir masked) and the exit
code. `tree` is the hash of the tree alone, for comparing a Windows run with a Linux run.

usage: win_layout_repros.py --bin NAME=PATH [--bin ...] [--runs 20] [--modes plain,incremental,build]
                            [--cases a,b,...] [--win-work DIR] [--linux-work DIR] [--out FILE]
Prints one JSON line per (bin, case, mode) and writes them all to --out (JSON list).
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile

CMD = "/mnt/c/Windows/System32/cmd.exe"


def win_path(path):
    """/mnt/c/x/y -> C:\\x\\y"""
    assert path.startswith("/mnt/") and len(path) > 6, path
    return path[5].upper() + ":" + path[6:].replace("/", "\\")


def module(path, name, fill, size=200):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('export const v_%s = "%s";\n' % (name, (fill + "-") * size))


TSCONFIG = ('{"compilerOptions": {"outDir": "out", "rootDir": "src", "target": "es2020", '
            '"module": "commonjs", "declaration": %s}, "include": ["src"]}\n')


def proj_left_right(right_prefix, n=60, right_file=False):
    def make(root):
        for i in range(n):
            module(f"{root}/src/left/f{i}.ts", f"l{i}", "left")
        if right_file:
            module(f"{root}/src/right.ts", "right", "right")
        else:
            for i in range(n):
                module(f"{root}/src/right/{right_prefix}{i}.ts", f"r{i}", "right")
    return make


def proj_files(n=160):
    def make(root):
        for i in range(n):
            module(f"{root}/src/f{i}.ts", f"f{i}", str(i % 10), 2000)
    return make


def proj_prefix(n=64):
    def make(root):
        for i in range(n):
            module(f"{root}/src/p{i}.ts", f"p{i}", "p")
            module(f"{root}/src/p{i}.js/x.ts", f"x{i}", "x")
    return make


def proj_case(names_per_i, n=40, case_sensitive_src=True):
    """Sources whose names differ only in (Unicode) case, in a case-sensitive src dir."""
    def make(root, windows):
        src = f"{root}/src"
        os.makedirs(src)
        if case_sensitive_src and windows:
            run_cmd(f'fsutil.exe file setCaseSensitiveInfo "{win_path(src)}" enable', root)
        for i in range(n):
            for k, name in enumerate(names_per_i):
                module(f"{src}/{name}{i}.ts", f"n{k}_{i}", f"{k}")
    make.needs_platform = True
    return make


def proj_shortnames(n=60):
    """`AB<i>CD~1.ts` made first, then `ab<i>cdefgh.ts` (whose 8.3 name is then `AB<i>CD~2.TS`).
    Their outputs `out/AB<i>CD~1.js` and `out/ab<i>cdefgh.js`: on a volume with 8.3 names on,
    whichever the emit makes first decides whether the other opens it (the long file's short name
    is `AB<i>CD~1.JS` when it comes first)."""
    def make(root):
        for i in range(n):
            module(f"{root}/src/AB{i:02d}CD~1.ts", f"s{i}", "short")
        for i in range(n):
            module(f"{root}/src/ab{i:02d}cdefgh.ts", f"g{i}", "long")
    return make


# Each case: (project maker, declaration, links). A link is (path, target, kind) with kind
# "dir" (directory symlink, relative target), "junction" (absolute target), "file" (file
# symlink), "hard" (hard link of an existing file) or "plainfile" (a regular file; target = text).
def dirlink(kind, links):
    return [(p, t, kind if k == "link" else k) for p, t, k in links]


CASES = {}
for kind in ("dir", "junction"):
    CASES[f"same-names/{kind}"] = (proj_left_right("f"), False, dirlink(kind, [("out/left", "out/right", "link")]))
    CASES[f"different-names/{kind}"] = (proj_left_right("g"), False, dirlink(kind, [("out/left", "out/right", "link")]))
    CASES[f"case-only/{kind}"] = (proj_left_right("F"), False, dirlink(kind, [("out/left", "out/right", "link")]))
    CASES[f"target-file/{kind}"] = (proj_left_right("f"), False,
                                    [("out/right", "not a directory", "plainfile")]
                                    + dirlink(kind, [("out/left", "out/right", "link")]))
    CASES[f"target-created-file/{kind}"] = (proj_left_right(None, right_file=True), False,
                                            dirlink(kind, [("out/left", "out/right.js", "link")]))
    CASES[f"chain/{kind}"] = (proj_left_right("f"), False,
                              dirlink(kind, [("out/hop", "out/right", "link"), ("out/left", "out/hop", "link")]))
    CASES[f"parent/{kind}"] = (proj_left_right("f"), False,
                               dirlink(kind, [("out/right", ".", "link"), ("out/left", ".", "link")]))
CASES["file-symlink"] = (proj_files(), False,
                         [("out/shared", "", "plainfile")] + [(f"out/f{i}.js", "out/shared", "file") for i in range(160)])
CASES["file-dangling"] = (proj_files(), False, [(f"out/f{i}.js", "out/shared", "file") for i in range(160)])
CASES["file-hardlink"] = (proj_files(), False,
                          [("out/shared", "", "plainfile")] + [(f"out/f{i}.js", "out/shared", "hard") for i in range(160)])
CASES["ancestor"] = (proj_prefix(), False, [])
CASES["case-ascii"] = (proj_case(["a", "A"]), True, [])
CASES["case-sigma"] = (proj_case(["σ", "ς", "Σ"]), True, [])
CASES["case-dotless-i"] = (proj_case(["ı", "i"]), True, [])
CASES["case-kelvin"] = (proj_case(["\u212a", "k"]), True, [])
CASES["shortname"] = (proj_shortnames(), True, [])


def run_cmd(line, cwd):
    """One cmd.exe line, cwd a /mnt/c path."""
    r = subprocess.run([CMD, "/c", line], cwd=cwd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"cmd {line!r}: {r.stdout!r} {r.stderr!r}")


def windows_setup_script(root, links):
    w = win_path(root)
    lines = ["@echo off", f'cd /d "{w}"',
             'if exist out rmdir /s /q out',
             'for %%f in (*) do if /i not "%%f"=="tsconfig.json" del /f /q "%%f"',
             'for /d %%d in (*) do if /i not "%%d"=="src" rmdir /s /q "%%d"',
             "mkdir out"]
    for path, target, kind in links:
        p = path.replace("/", "\\")
        parent = os.path.dirname(p)
        if parent and parent != "out":
            lines.append(f'if not exist "{parent}" mkdir "{parent}"')
        # Relative link targets are relative to the link's directory.
        rel = os.path.relpath(target, os.path.dirname(path)).replace("/", "\\") if target else ""
        if kind == "dir":
            lines.append(f'mklink /D "{p}" "{rel}" > nul || exit /b 1')
        elif kind == "junction":
            lines.append(f'mklink /J "{p}" "{w}\\{target.replace("/", chr(92))}" > nul || exit /b 1')
        elif kind == "file":
            lines.append(f'mklink "{p}" "{rel}" > nul || exit /b 1')
        elif kind == "hard":
            lines.append(f'mklink /H "{p}" "{target.replace("/", chr(92))}" > nul || exit /b 1')
        elif kind == "plainfile":
            lines.append(f'<nul set /p ="{target}" > "{p}"' if target else f'type nul > "{p}"')
    path = root + "-setup.cmd"
    with open(path, "w", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return win_path(path)


def linux_setup(root, links):
    for name in os.listdir(root):
        if name in ("src", "tsconfig.json"):
            continue
        p = os.path.join(root, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.unlink(p)
    os.makedirs(os.path.join(root, "out"))
    for path, target, kind in links:
        p = os.path.join(root, path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if kind in ("dir", "file"):
            os.symlink(os.path.relpath(target, os.path.dirname(path)), p)
        elif kind == "junction":
            os.symlink(os.path.normpath(os.path.join(root, target)), p)
        elif kind == "hard":
            os.link(os.path.join(root, target), p)
        elif kind == "plainfile":
            with open(p, "w") as f:
                f.write(target)


def tree_hash(root, windows):
    """The tree under root without src/ and tsconfig.json: paths, kinds, link targets and the
    bytes of each file, with the scratch dir masked."""
    h = hashlib.sha256()
    for top, dirs, files in os.walk(root):
        dirs.sort()
        if top == root:
            dirs[:] = [d for d in dirs if d != "src"]
        for name in sorted(dirs + files):
            p = os.path.join(top, name)
            rel = os.path.relpath(p, root)
            if top == root and name == "tsconfig.json":
                continue
            if os.path.islink(p):
                target = os.readlink(p).replace(root, "<dir>").replace("\\", "/")
                h.update(f"l {rel} {target}\n".encode())
            elif os.path.isdir(p):
                h.update(f"d {rel}\n".encode())
            else:
                try:
                    # Build info and messages hold the scratch dir: mask it, so runs in other
                    # scratch dirs (another bin, another platform) compare.
                    data = mask(open(p, "rb").read(), root, crlf=False)
                except OSError as e:
                    data = f"<unreadable {e.errno}>".encode()
                h.update(f"f {rel} ".encode() + hashlib.sha256(data).digest() + b"\n")
    return h.hexdigest()[:16]


def mask(text, root, crlf=True):
    for form in (root, win_path(root) if root.startswith("/mnt/") else None):
        if form:
            text = text.replace(form.encode(), b"<dir>")
            text = text.replace(form.replace("\\", "/").encode(), b"<dir>")
            text = text.replace(form[0].lower().encode() + form[1:].replace("\\", "/").encode(), b"<dir>")
    return text.replace(b"\r\n", b"\n") if crlf else text


def run_case(bin_path, case, modes, runs, work):
    windows = bin_path.endswith(".exe")
    make, declaration, links = CASES[case]
    root = tempfile.mkdtemp(prefix="lay-", dir=work)
    try:
        if getattr(make, "needs_platform", False):
            make(root, windows)
        else:
            make(root)
        with open(os.path.join(root, "tsconfig.json"), "w") as f:
            f.write(TSCONFIG % ("true" if declaration else "false"))
        setup = windows_setup_script(root, links) if windows else None
        out = []
        for mode in modes:
            args = {"plain": ["-p", "tsconfig.json"],
                    "incremental": ["-p", "tsconfig.json", "--incremental", "--tsBuildInfoFile", "x.tsbuildinfo"],
                    "build": ["-b", "tsconfig.json", "--force"],
                    "c4": ["-p", "tsconfig.json", "--checkers", "4"]}[mode]
            results, trees, exits, sample = {}, {}, {}, {}
            for _ in range(runs):
                if windows:
                    # WSL interop can fail to start a process under heavy load; retry.
                    for attempt in range(5):
                        r = subprocess.run([CMD, "/c", setup], cwd=root, capture_output=True)
                        if r.returncode == 0:
                            break
                    else:
                        raise RuntimeError(f"setup {case}: {r.stdout!r} {r.stderr!r}")
                else:
                    linux_setup(root, links)
                for attempt in range(5):
                    r = subprocess.run([bin_path] + args, cwd=root, capture_output=True)
                    # WSL interop under load: the Windows process did not start or its output was
                    # lost (exit 1, "UtilAcceptVsock"). Not the bin's result: set up and run again.
                    if not (windows and b"UtilAcceptVsock" in r.stdout + r.stderr):
                        break
                    subprocess.run([CMD, "/c", setup], cwd=root, capture_output=True)
                exits[r.returncode] = exits.get(r.returncode, 0) + 1
                tree = tree_hash(root, windows)
                stdout = mask(r.stdout + r.stderr, root)
                key = hashlib.sha256(f"{tree} {r.returncode} ".encode() + stdout).hexdigest()[:16]
                results[key] = results.get(key, 0) + 1
                trees[tree] = trees.get(tree, 0) + 1
                sample.setdefault(key, {"tree": tree, "exit": r.returncode,
                                        "stdout": stdout.decode("utf-8", "replace")[:600]})
            rec = {"bin": bin_path, "case": case, "mode": mode, "runs": runs, "results": results,
                   "trees": trees, "exits": exits, "samples": sample}
            print(json.dumps({k: rec[k] for k in ("case", "mode", "results", "trees", "exits")}), flush=True)
            out.append(rec)
        return out
    finally:
        if windows:
            subprocess.run([CMD, "/c", f'rmdir /s /q "{win_path(root)}" & del /q "{win_path(root)}-setup.cmd"'],
                           cwd=work, capture_output=True)
        else:
            shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", action="append", required=True)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--modes", default="plain,incremental,build")
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--win-work", default="/mnt/c/tsrs-win/repros")
    ap.add_argument("--linux-work", default=os.path.expanduser("~/.cache/perf-rearch/win-lane"))
    ap.add_argument("--out")
    a = ap.parse_args()
    records = []
    for spec in a.bin:
        name, path = spec.split("=", 1)
        work = a.win_work if path.endswith(".exe") else a.linux_work
        os.makedirs(work, exist_ok=True)
        for case in a.cases.split(","):
            for rec in run_case(path, case, a.modes.split(","), a.runs, work):
                rec["name"] = name
                records.append(rec)
                if a.out:
                    with open(a.out, "w") as f:
                        json.dump(records, f, indent=1)
    return 0


sys.exit(main())
