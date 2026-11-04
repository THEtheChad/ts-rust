#!/usr/bin/env bash
# C1: instruction counts (icount.py, user space) of two tsgo builds, interleaved run by run.
# usage: icount_ab.sh <base bin> <head bin> <out file> [runs]
# Scenarios per project (query core, umami, hono): noemit and emit with --singleThreaded (checker and
# emit work, load-independent), and emit multi-threaded (early emit, pool and twins; counts include
# waits). Plain program (--incremental false --composite false); emit goes to a scratch outDir.
# The last line is DONE or FAIL rc=N.
set -uo pipefail
[[ ${1:-help} == help || $# -lt 3 ]] && { sed -n '2,7p' "$0"; exit 0; }
base=$(realpath "$1"); head=$(realpath "$2"); out=$3; runs=${4:-2}
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
P=$HOME/.cache/perf-rearch/projects
scratch=$HOME/.cache/perf-rearch/c1/icount-scratch
projects=("query:$P/query:packages/query-core/tsconfig.prod.json" "umami:$P/umami:tsconfig.json"
          "hono:$P/hono:tsconfig.build.json")
plain=(--incremental false --composite false --pretty false)
: >"$out"; rc=0
for spec in "${projects[@]}"; do
  IFS=: read -r name dir cfg <<<"$spec"
  for scen in noemit-st emit-st emit-mt; do
    case $scen in
      noemit-st) args=(--noEmit --singleThreaded) ;;
      emit-st) args=(--noEmit false --outDir "$scratch/out" --singleThreaded) ;;
      emit-mt) args=(--noEmit false --outDir "$scratch/out") ;;
    esac
    for ((r = 0; r < runs; r++)); do
      for side in base head; do
        bin=$base; [[ $side == head ]] && bin=$head
        rm -rf "$scratch/out"; mkdir -p "$scratch"
        n=$(cd "$dir" && GOPORT_LAUNCH=0 python3 -I "$here/icount.py" "$bin" -p "$cfg" "${plain[@]}" "${args[@]}" \
              2>&1 >/dev/null | sed -n 's/^user instructions: //p')
        [[ -n $n ]] || rc=1
        printf '%s\t%s\t%s\t%s\n' "$name" "$scen" "$side" "${n:-NA}" >>"$out"
      done
    done
  done
done
rm -rf "$scratch"
if ((rc)); then echo "FAIL rc=$rc" >>"$out"; else echo DONE >>"$out"; fi
