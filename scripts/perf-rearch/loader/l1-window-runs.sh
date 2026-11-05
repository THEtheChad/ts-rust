#!/usr/bin/env bash
# Idle-gated observation-to-watch window runs (loader v2 L1, round 5): ROUNDS rounds of
# {umami, hono} x {view, base, go}, interleaved, each started at load < 1.5, one log per run
# (<proj>-<side>-<round>.log) in OUT, a line per run in OUT/results.txt, then DONE.
# usage: l1-window-runs.sh OUT VIEW_BIN BASE_BIN GO_BIN [ROUNDS=5]
#   VIEW_BIN: a tsc-rs diagnostics build (GOPORT_LOAD_OBS_LOG); BASE_BIN: the base with
#   l1-window-base-spike.patch; GO_BIN: the pinned Go oracle with l1-window-go-spike.patch
#   (built as the LSP oracle: in tsc/, CGO_ENABLED=0 go build -trimpath ./cmd/tsc).
# Analysis: l1-window.py pair --sides view,go ..., dist <side> ...; hono with --loads all.
# Run it under systemd-run --user --collect (about 5 minutes when idle). L1_WINDOW_MAX_LOAD
# (default 1.5) is the load gate; raise it only for a functional check, not for numbers.
set -u
(($# >= 4)) || { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
out=$1 view=$2 base=$3 go=$4 rounds=${5:-5}
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$out" && cd "$out" || exit 2
for ((r = 1; r <= rounds; r++)); do for proj in umami hono; do for side in view base go; do
  while awk -v max="${L1_WINDOW_MAX_LOAD:-1.5}" '{exit !($1>=max)}' /proc/loadavg; do sleep 5; done
  case $side in view) b=$view ;; base) b=$base ;; go) b=$go ;; esac
  root=$HOME/.cache/lspdiff/projects/umami f=()
  [[ $proj == hono ]] && { root=$HOME/.cache/perf-rearch/projects/hono f=(--file src/hono.ts); }
  before=$(cut -d' ' -f1 /proc/loadavg)
  rm -f "$proj-$side-$r.log"
  GOPORT_LOAD_OBS_LOG=$PWD/$proj-$side-$r.log node "$here/l1-window-run.mjs" "$b" "$root" "${f[@]}" >/dev/null ||
    { echo "FAIL rc=$? $proj $side $r" >> results.txt; exit 1; }
  echo "$proj $side $r load $before->$(cut -d' ' -f1 /proc/loadavg) $(python3 -I "$here/l1-window.py" "$side" "$proj-$side-$r.log" | cut -c1-90)" >> results.txt
done; done; done
echo DONE >> results.txt
