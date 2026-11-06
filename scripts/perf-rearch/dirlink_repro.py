#!/usr/bin/env python3
"""Astra's dangling directory-link repro (reviews/2026-11-06-astra-d1-i3.md, finding 1).

Copies the project of ~/.cache/perf-rearch/scratch/astra-i3-dirlink-* (src/left/*.ts and
src/right/*.ts, outDir out; or --project) to a scratch dir. Before each run it makes an empty
out/ with the dangling link out/left -> right, so out/left/f<i>.js and out/right/f<i>.js name one
file once the emit makes out/right. Each run's result is the hash of the whole out/ tree (paths,
kinds, link targets, bytes), stdout and the exit code.

usage: dirlink_repro.py BIN [--runs 20] [--mode plain|incremental|build] [--arg A ...] [--env K=V ...]
  plain        tsgo -p tsconfig.json <args>
  incremental  tsgo -p tsconfig.json --incremental --tsBuildInfoFile <scratch>/x.tsbuildinfo <args>
               (the build info is removed before each run: every run is a full build)
  build        tsgo -b tsconfig.json --force <args>
Prints one JSON line {"bin", "mode", "args", "runs", "results": {hash: count}, "exits": {...}}.
Exit 1 when the runs give more than one result (unless --allow-varying).
"""
import argparse, glob, hashlib, json, os, shutil, subprocess, sys, tempfile


def tree_hash(root):
    h = hashlib.sha256()
    for top, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for name in sorted(dirs + files):
            p = os.path.join(top, name)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                h.update(f"l {rel} {os.readlink(p)}\n".encode())
            elif os.path.isdir(p):
                h.update(f"d {rel}\n".encode())
            else:
                h.update(f"f {rel} ".encode() + hashlib.sha256(open(p, "rb").read()).digest() + b"\n")
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--mode", default="plain", choices=["plain", "incremental", "build"])
    ap.add_argument("--project")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--allow-varying", action="store_true")
    ap.add_argument("--arg", dest="extra", action="append", default=[])
    a = ap.parse_args()
    project = a.project or sorted(glob.glob(os.path.expanduser(
        "~/.cache/perf-rearch/scratch/astra-i3-dirlink-*")))[0]
    env = dict(os.environ)
    for kv in a.env:
        k, v = kv.split("=", 1)
        env[k] = v
    base = os.path.expanduser("~/.cache/perf-rearch/d1i3")
    scratch = tempfile.mkdtemp(prefix="dirlink-repro-", dir=base)
    try:
        shutil.copytree(os.path.join(project, "src"), os.path.join(scratch, "src"))
        shutil.copy(os.path.join(project, "tsconfig.json"), scratch)
        binary = os.path.abspath(a.bin)
        info = os.path.join(scratch, "x.tsbuildinfo")
        if a.mode == "build":
            cmd = [binary, "-b", "tsconfig.json", "--force"] + a.extra
        elif a.mode == "incremental":
            cmd = [binary, "-p", "tsconfig.json", "--incremental", "--tsBuildInfoFile", info] + a.extra
        else:
            cmd = [binary, "-p", "tsconfig.json"] + a.extra
        out = os.path.join(scratch, "out")
        results, exits = {}, {}
        for _ in range(a.runs):
            shutil.rmtree(out, ignore_errors=True)
            for stale in glob.glob(os.path.join(scratch, "*.tsbuildinfo")):
                os.unlink(stale)
            os.makedirs(out)
            os.symlink("right", os.path.join(out, "left"))
            r = subprocess.run(cmd, cwd=scratch, capture_output=True, env=env)
            exits[r.returncode] = exits.get(r.returncode, 0) + 1
            h = hashlib.sha256(tree_hash(out).encode() + r.stdout.replace(scratch.encode(), b"<dir>")
                               + str(r.returncode).encode()).hexdigest()[:16]
            results[h] = results.get(h, 0) + 1
        print(json.dumps({"bin": a.bin, "mode": a.mode, "args": a.extra, "runs": a.runs,
                          "results": results, "exits": exits}))
        return 0 if len(results) == 1 or a.allow_varying else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


sys.exit(main())
