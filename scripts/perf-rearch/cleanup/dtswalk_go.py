#!/usr/bin/env python3
"""C1: replays the projects and steps of two tests/multi_program.rs tests with one tsc binary and
writes the golden files that the tests compare with (record them from the pinned Go oracle,
tsgo-oracle-3e9236361fa9):

  no-emit      no_emit_large_project_reports_like_fresh_runs: `incremental` + `noEmit`, 12 steps
  emit-builds  no_emit_runs_between_builds_keep_go_emits: emitting builds and `--noEmit` runs, 6 steps

Per scenario OUT/<scenario>/report.txt holds, per step, `== <step>: exit <code>`, the stdout and
`-- stderr` with the stderr (the project dir as <project>), and OUT/<scenario>/step<n>.tsbuildinfo
the build info after the step. A summary line per step (sha256 and size of the build info) goes to
stdout, so two binaries can be compared with diff.

usage: dtswalk_go.py <tsc bin> <out dir> [scenario...]   (scratch projects under <out dir>/scratch)"""
import hashlib
import os
import shutil
import subprocess
import sys

if len(sys.argv) < 3:
    sys.exit(__doc__)
tsc, out = sys.argv[1], os.path.abspath(sys.argv[2])
scenarios = sys.argv[3:] or ["no-emit", "emit-builds"]
IMPORTERS = 40


def a(members, x):
    return f"export interface Shape {{ {members} }}\nexport const make = (): Shape => ({{ x: {x} }});\n"


def b(i):
    return f'import {{ make }} from "./a";\nexport const v{i} = make();\n'


def g(gv):
    return f"export const g = 1;\ndeclare global {{\n    var gv: {gv};\n}}\n"


def write_large_project(root, options):
    """tests/multi_program.rs write_large_project (without src/a.ts)."""
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root + "/src")

    def w(path, text):
        with open(os.path.join(root, path), "w") as f:
            f.write(text)

    w("tsconfig.json", f'{{"compilerOptions":{options},"include":["src"]}}')
    for i in range(IMPORTERS):
        w(f"src/b{i}.ts", b(i))
        w(f"src/c{i}.ts", f'import {{ v{i} }} from "./b{i}";\nexport const n{i}: number = v{i}.x;\n')
    w("src/u.ts", 'export const u: number = "u";\n')
    w("src/g.ts", g("number"))
    w("src/ug.ts", "export const ug: number = gv;\n")
    w("src/d.d.ts", 'import { v0 } from "./b0";\nexport declare const d: typeof v0;\n')
    return w


def no_emit():
    """tests/multi_program.rs no_emit_large_steps: (step, file, text, args)."""
    a_ts, b0_ts, g_ts = "src/a.ts", "src/b0.ts", "src/g.ts"
    steps = [
        ("cold", a_ts, a("x: number", "1")),
        ("wide change", a_ts, a("x: string", '"1"')),
        ("revert", a_ts, a("x: number", "1")),
        ("error in a.ts", a_ts, a("x: number", '"1"')),
        ("fix", a_ts, a("x: number", "1")),
        ("second wide change", a_ts, a("x: number; y?: number", "1")),
        ("narrow change", b0_ts, 'export const v0 = { x: "0" };\n'),
        ("narrow revert", b0_ts, b(0)),
        ("no change", b0_ts, b(0)),
        ("global change", g_ts, g("string")),
        ("global revert", g_ts, g("number")),
        ("same d.ts", a_ts, a("x: number; y?: number", "2")),
    ]
    options = '{"incremental":true,"noEmit":true,"strict":true,"skipLibCheck":true,"types":[]}'
    return options, "tsconfig.tsbuildinfo", [(s, f, t, []) for s, f, t in steps]


def emit_builds():
    """tests/multi_program.rs no_emit_runs_between_builds_keep_go_emits."""
    b0_body = b(0) + "const local = 1;\n"
    ne = ["--listEmittedFiles", "--noEmit"]
    em = ["--listEmittedFiles"]
    steps = [
        ("build", "src/a.ts", a("x: number", "1"), em),
        ("wide change, --noEmit", "src/a.ts", a("x: string", '"1"'), ne),
        ("build after the wide change", "src/a.ts", a("x: string", '"1"'), em),
        ("revert, --noEmit", "src/a.ts", a("x: number", "1"), ne),
        ("b0.ts body, --noEmit", "src/b0.ts", b0_body, ne),
        ("build after both", "src/b0.ts", b0_body, em),
    ]
    options = ('{"incremental":true,"declaration":true,"outDir":"out","strict":true,'
               '"skipLibCheck":true,"types":[]}')
    return options, "out/tsconfig.tsbuildinfo", steps


SCENARIOS = {"no-emit": no_emit, "emit-builds": emit_builds}
for name in scenarios:
    options, info_rel, steps = SCENARIOS[name]()
    root = os.path.join(out, "scratch", name)
    w = write_large_project(root, options)
    dest = os.path.join(out, name)
    os.makedirs(dest, exist_ok=True)
    report = []
    for n, (step, path, text, args) in enumerate(steps):
        w(path, text)
        run = subprocess.run([tsc, "-p", "tsconfig.json", "--pretty", "false", *args], cwd=root,
                             capture_output=True, timeout=300)
        stdout = run.stdout.decode().replace(root, "<project>")
        stderr = run.stderr.decode().replace(root, "<project>")
        report.append(f"== {step}: exit {run.returncode}\n{stdout}-- stderr\n{stderr}")
        info = open(os.path.join(root, info_rel), "rb").read()
        with open(os.path.join(dest, f"step{n}.tsbuildinfo"), "wb") as f:
            f.write(info)
        print(f"{name} {n} {step}: exit {run.returncode} info {hashlib.sha256(info).hexdigest()[:16]} "
              f"{len(info)} stderr {len(run.stderr)}")
    with open(os.path.join(dest, "report.txt"), "w") as f:
        f.write("".join(report))
shutil.rmtree(os.path.join(out, "scratch"), ignore_errors=True)
