#!/usr/bin/env bash
# C2 follow-up: user-space instruction counts (icount.py) of the language server process of two
# tsgo builds in one LSP session each, interleaved run by run. The session is
# loader/load-dump-session.mjs `edit`: open a file, pull its diagnostics, add an import through
# didChange (an overlay; nothing is written to the project), pull again: the second, keyed
# program load resolves ahead, so the loader waits for worker answers (AheadQueue::wait_or_take).
# Only the server is counted (icount.py wraps the server binary), not node. The counts include
# the worker threads and their spins, so they vary more than --singleThreaded CLI counts.
# usage: icount_lsp_ab.sh <base bin> <head bin> <out file> [runs (5)] [project (umami)] [file]
# The out file gets one "<side>\t<run>\t<instructions>" line per run and ends DONE or FAIL rc=N.
set -uo pipefail
[[ ${1:-help} == help || $# -lt 3 ]] && { sed -n '2,10p' "$0"; exit 0; }
base=$(realpath "$1"); head=$(realpath "$2"); out=$(realpath -m "$3"); runs=${4:-5}
project=${5:-$HOME/.cache/lspdiff/projects/umami}; file=${6:-}
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "${out%/*}/icount-lsp.XXXXXX")
for side in base head; do
  bin=$base; [[ $side == head ]] && bin=$head
  printf '#!/bin/sh\nexec python3 -I %q %q "$@"\n' "$here/icount.py" "$bin" >"$work/$side"
  chmod +x "$work/$side"
done
: >"$out"; rc=0
for ((r = 0; r < runs; r++)); do
  for side in base head; do
    S1A_FILE=$file GOPORT_LAUNCH=0 node "$here/loader/load-dump-session.mjs" "$work/$side" \
      "$work/$side-$r" edit "$project" >/dev/null 2>&1 || rc=1
    n=$(python3 -I -c 'import json,re,sys
m=re.search(r"user instructions: (\d+)", json.load(open(sys.argv[1]))["stderr"])
print(m.group(1) if m else "NA")' "$work/$side-$r.json" 2>/dev/null)
    [[ ${n:-NA} != NA ]] || rc=1
    printf '%s\t%s\t%s\n' "$side" "$r" "${n:-NA}" >>"$out"
  done
done
rm -rf "$work"
if ((rc)); then echo "FAIL rc=$rc" >>"$out"; else echo DONE >>"$out"; fi
