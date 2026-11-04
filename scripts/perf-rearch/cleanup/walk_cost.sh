#!/usr/bin/env bash
# C1 round 2: the cost of Go's d.ts walk (the noEmit walk skip removed) in user-space instructions
# (icount.py), and whether the build info matches Go's.
# usage: walk_cost.sh <base bin> <head bin> <out file> [runs]
# Scenarios, each `tsc -p <cfg> --incremental --noEmit --composite false --singleThreaded` with a
# scratch build info: a cold run (not counted), then one edit, then the counted warm run.
#   synth85  the 85-file project of tests/multi_program.rs (Astra's scenario): Shape.x number -> string
#   query, umami, hono  a copy of the shared project (never edited in place): an exported const
#            appended to a widely imported file (query-core utils.ts, umami lib/types.ts, hono types.ts)
# After the last head run, Go (tsgo-oracle-3e9236361fa9) runs the same steps once and its build info
# is compared with head's (go=head or go!=head). The last line is DONE or FAIL rc=N.
set -uo pipefail
[[ ${1:-help} == help || $# -lt 3 ]] && { sed -n '2,11p' "$0"; exit 0; }
base=$(realpath "$1"); head=$(realpath "$2"); out=$3; runs=${4:-2}
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
go=$HOME/.local/bin/tsgo-oracle-3e9236361fa9
P=$HOME/.cache/perf-rearch/projects
work=$HOME/.cache/perf-rearch/c1/walk-cost
rm -rf "$work"; mkdir -p "$work"; : >"$out"; rc=0
# name|project dir (copied)|config|edited file|new text appended (empty for synth85)
specs=("synth85|||src/a.ts|"
       "query|$P/query|packages/query-core/tsconfig.prod.json|packages/query-core/src/utils.ts|export const c1WalkProbe = 1"
       "umami|$P/umami/|tsconfig.json|src/lib/types.ts|export const c1WalkProbe = 1"
       "hono|$P/hono|tsconfig.build.json|src/types.ts|export const c1WalkProbe = 1")
synth() { # <dir> <shape member> <x>: the 85-file project with a.ts
  python3 -I - "$1" "$2" "$3" <<'EOF'
import os, sys
root, member, x = sys.argv[1:]
os.makedirs(root + "/src", exist_ok=True)
def w(p, t):
    open(os.path.join(root, p), "w").write(t)
w("tsconfig.json", '{"compilerOptions":{"incremental":true,"noEmit":true,"strict":true,"skipLibCheck":true,"types":[]},"include":["src"]}')
for i in range(40):
    w(f"src/b{i}.ts", f'import {{ make }} from "./a";\nexport const v{i} = make();\n')
    w(f"src/c{i}.ts", f'import {{ v{i} }} from "./b{i}";\nexport const n{i}: number = v{i}.x;\n')
w("src/u.ts", 'export const u: number = "u";\n')
w("src/g.ts", "export const g = 1;\ndeclare global {\n    var gv: number;\n}\n")
w("src/ug.ts", "export const ug: number = gv;\n")
w("src/d.d.ts", 'import { v0 } from "./b0";\nexport declare const d: typeof v0;\n')
w("src/a.ts", f"export interface Shape {{ {member} }}\nexport const make = (): Shape => ({{ x: {x} }});\n")
EOF
}
for spec in "${specs[@]}"; do
  IFS='|' read -r name src cfg edited append <<<"$spec"
  dir=$work/$name
  if [[ $name == synth85 ]]; then cfg=tsconfig.json; else cp -a "$src" "$dir"; rm -rf "$dir/.git"; fi
  [[ $name == synth85 ]] || cp "$dir/$edited" "$work/$name.orig"
  info=$work/$name.tsbuildinfo
  flags=(-p "$cfg" --incremental --noEmit --composite false --tsBuildInfoFile "$info" --singleThreaded --pretty false)
  reset() { # cold state
    rm -f "$info"
    if [[ $name == synth85 ]]; then rm -rf "$dir"; synth "$dir" "x: number" 1; else cp "$work/$name.orig" "$dir/$edited"; fi
  }
  edit() {
    if [[ $name == synth85 ]]; then synth "$dir" "x: string" '"1"'; else printf '\n%s;\n' "$append" >>"$dir/$edited"; fi
  }
  for ((r = 0; r < runs; r++)); do
    for side in base head; do
      bin=$base; [[ $side == head ]] && bin=$head
      reset; (cd "$dir" && GOPORT_LAUNCH=0 "$bin" "${flags[@]}" >/dev/null 2>&1)
      edit
      n=$(cd "$dir" && GOPORT_LAUNCH=0 python3 -I "$here/icount.py" "$bin" "${flags[@]}" 2>&1 >/dev/null |
            sed -n 's/^user instructions: //p')
      [[ -n $n ]] || rc=1
      printf '%s\t%s\t%s\n' "$name" "$side" "${n:-NA}" >>"$out"
      [[ $side == head ]] && cp "$info" "$work/$name.head.tsbuildinfo"
    done
  done
  reset; (cd "$dir" && "$go" "${flags[@]}" >/dev/null 2>&1); edit; (cd "$dir" && "$go" "${flags[@]}" >/dev/null 2>&1)
  if cmp -s "$info" "$work/$name.head.tsbuildinfo"; then echo "$name go=head" >>"$out"; else echo "$name go!=head" >>"$out"; fi
  [[ $name == synth85 ]] || rm -rf "$dir"
done
if ((rc)); then echo "FAIL rc=$rc" >>"$out"; else echo DONE >>"$out"; fi
