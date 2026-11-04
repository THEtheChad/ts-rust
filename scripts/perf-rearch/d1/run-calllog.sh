#!/bin/bash
# D1: checker call logs (GOPORT_CHECKER_CALL_LOG) at --checkers 4 and 16 on vscode, t3-server, umami and hono,
# the input of refine_sim.py (docs/perf-rearch/design/d1.md section 2). Counts only (types and instantiations
# per file), which do not depend on load, so no load gate.
#
# Usage: run-calllog.sh [OUT_DIR]   default OUT_DIR: ~/.cache/perf-rearch/scratch/d1/calllog
#   TSGO=<bin>   the tsc-rs bin (default: the window-1 head bin); since C1 it must be a `diagnostics` feature
#                build (`scripts/run-cargo-capped.sh build --release -p ts_goport --features diagnostics --bin tsgo`)
# Then: refine_sim.py OUT_DIR/<project>-c4.log OUT_DIR/<project>-c16.log 4   (METRIC=types for the types proxy)
set -u
H=${TSGO:-$HOME/.cache/perf-rearch/bins/tsgo-head-b2febcd86-goport-w1}
P=${PR_PROJECTS:-$HOME/.cache/perf-rearch/projects}
out=${1:-$HOME/.cache/perf-rearch/scratch/d1/calllog}
mkdir -p "$out"
run() {
  local name=$1 dir=$2 cfg=$3 n=$4
  ( cd "$dir" && GOPORT_CHECKER_CALL_LOG="$out/$name-c$n.log" "$H" -p "$cfg" --noEmit --incremental false \
      --composite false --checkers "$n" >/dev/null 2>&1 )
  echo "$name c$n rc=$? load $(cut -d' ' -f1 /proc/loadavg)"
}
for n in 4 16; do
  run vscode "$P/vscode" src/tsconfig.json "$n"
  run t3-server "$P/t3code/apps/server" tsconfig.noeffect.json "$n"
  run umami "$P/umami" tsconfig.json "$n"
  run hono "$P/hono" tsconfig.build.json "$n"
done
echo DONE
