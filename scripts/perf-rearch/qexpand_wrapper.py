#!/usr/bin/env -S python3 -I
"""RUSTC_WORKSPACE_WRAPPER for qexpand.py: rustc's cfg-expanded source of each workspace crate.

Cargo runs `<this> <rustc> <args...>` for every workspace target it compiles. This runs rustc
unchanged (cargo's own check, so its artifacts and exit code are untouched), then, when that
succeeded and QX_OUT is set, runs the same command line again with `-Zunpretty=expanded`
(RUSTC_BOOTSTRAP=1 on the stable toolchain the run uses) and writes:

  QX_OUT/<crate>.<lib|bin>.rs     the expanded source (cfg-false items removed, macros expanded,
                                   out-of-line modules inlined with their #[path] attributes kept)
  QX_OUT/<crate>.<lib|bin>.d      rustc's dep-info of that compilation: every source file it read
                                   and every `env!`/`option_env!` name (`# env-dep:`)
  QX_OUT/<crate>.<lib|bin>.json   the rustc arguments (crate name, cfgs, features, target)

Build scripts (`build_script_*`) are passed through. Not meant to be run by hand.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def arg_value(args: list[str], flag: str) -> list[str]:
    """Values of `--flag v` and `--flag=v`."""
    out = []
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            out.append(args[i + 1])
        elif a.startswith(flag + "="):
            out.append(a[len(flag) + 1 :])
    return out


def main() -> int:
    rustc, args = sys.argv[1], sys.argv[2:]
    rc = subprocess.call([rustc, *args], close_fds=False)  # keep cargo's jobserver fds
    out = os.environ.get("QX_OUT")
    names = arg_value(args, "--crate-name")
    if rc != 0 or not out or not names or names[0].startswith("build_script_") or names[0] == "___":
        return rc
    name = names[0]
    types = arg_value(args, "--crate-type")
    kind = "bin" if types == ["bin"] else "lib"
    out_dir = arg_value(args, "--out-dir")
    extra = [a.removeprefix("extra-filename=") for a in arg_value(args, "-C") if a.startswith("extra-filename=")]
    stem = str(Path(out) / f"{name}.{kind}")
    if out_dir:
        dep = Path(out_dir[0]) / f"{name}{extra[0] if extra else ''}.d"
        if not dep.exists():
            sys.stderr.write(f"qexpand_wrapper: no dep-info at {dep}\n")
            return 1
        shutil.copyfile(dep, stem + ".d")
    # The same compilation, printing the expanded source: no emit, and a scratch out dir so the
    # dep-info this run may write does not touch cargo's.
    skip_next = False
    exp = []
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in ("--emit", "--out-dir", "--error-format", "--json", "-o"):
            skip_next = True
            continue
        if a.startswith(("--emit=", "--out-dir=", "--error-format=", "--json=")):
            continue
        exp.append(a)
    with tempfile.TemporaryDirectory(prefix="qx-", dir=out) as tmp, open(stem + ".rs.tmp", "wb") as so, \
            open(stem + ".log", "wb") as se:
        env = dict(os.environ, RUSTC_BOOTSTRAP="1")
        r = subprocess.call([rustc, *exp, "--out-dir", tmp, "-Zunpretty=expanded"], stdout=so, stderr=se, env=env, close_fds=False)
    if r != 0:
        sys.stderr.write(f"qexpand_wrapper: -Zunpretty=expanded failed for {name} ({kind}), rc={r}; see {stem}.log\n")
        return r
    os.replace(stem + ".rs.tmp", stem + ".rs")
    cfgs = arg_value(args, "--cfg")
    Path(stem + ".json").write_text(json.dumps({
        "crate": name, "kind": kind, "crate_types": types, "cfg": cfgs,
        "features": sorted(c.split('"')[1] for c in cfgs if c.startswith("feature=")),
        "target": (arg_value(args, "--target") or [None])[0], "edition": (arg_value(args, "--edition") or [None])[0],
        "args": args,
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
