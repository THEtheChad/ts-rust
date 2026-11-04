#!/usr/bin/env python3
"""C1: replays the project and steps of tests/multi_program.rs
`no_emit_runs_between_builds_keep_go_emits` with one tsc binary, and prints per step its stdout
(the project dir as <project>), exit code and build info (sha256 and size). It also copies each
step's build info next to the scratch dir. Used to compare the shipped noEmit d.ts walk fallback
with Go (`tsgo-oracle-3e9236361fa9`) after GOPORT_DTS_WALK_FALLBACK was removed.
usage: dtswalk_go.py <tsc bin> <scratch dir>"""
import hashlib
import os
import shutil
import subprocess
import sys

if len(sys.argv) != 3:
    sys.exit(__doc__)
tsc, root = sys.argv[1], sys.argv[2]
IMPORTERS = 40
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root + "/src")


def write(path, text):
    with open(os.path.join(root, path), "w") as f:
        f.write(text)


def a(members, x):
    return f"export interface Shape {{ {members} }}\nexport const make = (): Shape => ({{ x: {x} }});\n"


def b(i):
    return f'import {{ make }} from "./a";\nexport const v{i} = make();\n'


def g(gv):
    return f"export const g = 1;\ndeclare global {{\n    var gv: {gv};\n}}\n"


write("tsconfig.json", '{"compilerOptions":{"incremental":true,"declaration":true,"outDir":"out",'
      '"strict":true,"skipLibCheck":true,"types":[]},"include":["src"]}')
for i in range(IMPORTERS):
    write(f"src/b{i}.ts", b(i))
    write(f"src/c{i}.ts", f'import {{ v{i} }} from "./b{i}";\nexport const n{i}: number = v{i}.x;\n')
write("src/u.ts", 'export const u: number = "u";\n')
write("src/g.ts", g("number"))
write("src/ug.ts", "export const ug: number = gv;\n")
write("src/d.d.ts", 'import { v0 } from "./b0";\nexport declare const d: typeof v0;\n')
b0_body = b(0) + "const local = 1;\n"
steps = [
    ("build", "src/a.ts", a("x: number", "1"), False),
    ("wide change, --noEmit", "src/a.ts", a("x: string", '"1"'), True),
    ("build after the wide change", "src/a.ts", a("x: string", '"1"'), False),
    ("revert, --noEmit", "src/a.ts", a("x: number", "1"), True),
    ("b0.ts body, --noEmit", "src/b0.ts", b0_body, True),
    ("build after both", "src/b0.ts", b0_body, False),
]
for n, (step, path, text, no_emit) in enumerate(steps):
    write(path, text)
    args = [tsc, "-p", "tsconfig.json", "--pretty", "false", "--listEmittedFiles"]
    if no_emit:
        args.append("--noEmit")
    run = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=120)
    info_path = root + "/out/tsconfig.tsbuildinfo"
    info = open(info_path, "rb").read()
    print(f"== {step}: exit {run.returncode} info {hashlib.sha256(info).hexdigest()[:16]} {len(info)}")
    print(run.stdout.replace(root, "<project>"), end="")
    shutil.copy(info_path, f"{root}.step{n}.tsbuildinfo")
