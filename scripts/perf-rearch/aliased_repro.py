#!/usr/bin/env python3
"""Astra's aliased-output repro (reviews/2026-11-05-astra-d1-i2.md, finding 1).

Copies the 160-module project of ~/.cache/perf-rearch/scratch/astra-i2-aliased-files-*
(or --project) to a scratch dir, and for each run makes out/f<i>.js a symlink
(or hard link) to out/shared, runs the binary, and hashes out/shared.

usage: aliased_repro.py BIN [--runs 20] [--mode symlink|hardlink|dangling]
                        [--project DIR] [--env K=V ...] [--arg A ...]
Prints one JSON line: {"bin", "mode", "runs", "hashes": {hash: count},
"exits": {...}, "last_in_order": hash of the last module's plain output,
"first_in_order": ...}. Exit 1 when the runs give more than one hash.
"""
import argparse, glob, hashlib, json, os, shutil, subprocess, sys, tempfile

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--mode", default="symlink", choices=["symlink", "hardlink", "dangling"])
    ap.add_argument("--project")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--allow-varying", action="store_true", help="exit 0 even when runs vary (for Go)")
    ap.add_argument("--arg", dest="extra", action="append", default=[], help="an extra tsgo argument")
    a = ap.parse_args()
    project = a.project or sorted(glob.glob(os.path.expanduser(
        "~/.cache/perf-rearch/scratch/astra-i2-aliased-files-*/work")))[0]
    env = dict(os.environ)
    for kv in a.env:
        k, v = kv.split("=", 1)
        env[k] = v
    scratch = tempfile.mkdtemp(prefix="aliased-repro-", dir=os.path.expanduser("~/.cache/perf-rearch/d1i3"))
    try:
        shutil.copytree(os.path.join(project, "src"), os.path.join(scratch, "src"))
        shutil.copy(os.path.join(project, "tsconfig.json"), scratch)
        a.bin = os.path.abspath(a.bin)
        cmd = [a.bin, "-p", "tsconfig.json"] + a.extra
        # Program order and the plain outputs.
        lst = subprocess.run(cmd + ["--listFilesOnly"], cwd=scratch, capture_output=True, text=True, env=env)
        order = [os.path.basename(l)[:-3] for l in lst.stdout.splitlines()
                 if "/src/" in l and l.endswith(".ts") and not l.endswith(".d.ts")]
        out = os.path.join(scratch, "out")
        subprocess.run(cmd, cwd=scratch, capture_output=True, env=env)
        plain = {m: hashlib.sha256(open(os.path.join(out, m + ".js"), "rb").read()).hexdigest() for m in order}
        hashes, exits = {}, {}
        for _ in range(a.runs):
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out)
            shared = os.path.join(out, "shared")
            if a.mode != "dangling":
                open(shared, "w").close()
            for m in order:
                link = os.path.join(out, m + ".js")
                if a.mode == "hardlink":
                    os.link(shared, link)
                else:
                    os.symlink("shared", link)
            r = subprocess.run(cmd, cwd=scratch, capture_output=True, env=env)
            exits[r.returncode] = exits.get(r.returncode, 0) + 1
            h = hashlib.sha256(open(shared, "rb").read()).hexdigest() if os.path.exists(shared) else "missing"
            hashes[h] = hashes.get(h, 0) + 1
        res = {"bin": a.bin, "mode": a.mode, "runs": a.runs, "args": a.extra, "env": a.env,
               "hashes": hashes, "exits": exits,
               "matches": {h: [m for m in order if plain[m] == h] for h in hashes},
               "last_in_order": order[-1] if order else None, "first_in_order": order[0] if order else None}
        print(json.dumps(res))
        return 0 if len(hashes) == 1 or a.allow_varying else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

sys.exit(main())
