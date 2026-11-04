#!/usr/bin/env bash
# Instructions per .tsx edit on umami (perf/first-principles A1), load-independent: for each server, runs
# probe-edit.mjs (perf/lsp-edit-cost) with 5 and with 25 edits under icount-server.sh, and prints
# (I(25) - I(5)) / 20 per rep, side by side, interleaved. Also keeps each run's --logs file.
#   scripts/perf-rearch/a1/icount-edits.sh <out dir> <probe-edit.mjs> <reps> <mode: full|first-only> name=bin...
set -u
OUT=$1 PROBE=$2 REPS=$3 MODE=$4; shift 4
HERE=$(cd "$(dirname "$0")" && pwd)
U=$HOME/.cache/lspdiff/projects/umami
EDIT='src/app/(main)/boards/BoardCloneForm.tsx'
mkdir -p "$OUT"
extra=(); [ "$MODE" = first-only ] && extra=(--first-only)
count() { # name bin edits rep
  local f="$OUT/$1-$MODE-e$3-r$4"
  rm -f "$f.stderr"
  echo "load $(cut -d' ' -f1 /proc/loadavg)" > "$f.out"
  GOPORT_LAUNCH=0 ICOUNT_REAL=$2 ICOUNT_OUT="$f.stderr" node "$PROBE" "$HERE/icount-server.sh" "$U" --edit "$EDIT" --edits "$3" --logs "$f.logs" "${extra[@]}" >> "$f.out" 2>&1
  echo "load $(cut -d' ' -f1 /proc/loadavg)" >> "$f.out"
  sed -n 's/^user instructions: //p' "$f.stderr" | tail -1
}
for rep in $(seq 1 "$REPS"); do
  for spec in "$@"; do
    name=${spec%%=*} bin=${spec#*=}
    a=$(count "$name" "$bin" 5 "$rep"); b=$(count "$name" "$bin" 25 "$rep")
    echo "$name $MODE rep $rep: I(5)=$a I(25)=$b per-edit=$(( (b - a) / 20 ))"
  done
done
