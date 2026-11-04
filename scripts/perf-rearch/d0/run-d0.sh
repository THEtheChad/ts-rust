#!/bin/sh
# D0-1A: runs the checker-partition matrix under systemd (MemoryMax=20G) and writes a DONE or FAIL rc=N line.
# Usage: run-d0.sh OUT_DIR [TSGO_BIN]   (TSGO_BIN defaults to this worktree's target/release/tsgo)
# Starts two units: "projects" (probe, the project matrix, report) and "corpus" (the multi-file corpus).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$(mkdir -p "$1" && cd "$1" && pwd)
tsgo=${2:-$repo/target/release/tsgo}
drv="$here/d0_partition.py"
run_unit() {  # name, log, command
  systemd-run --user --collect -p MemoryMax=20G -p MemorySwapMax=0 --unit="$1" \
    bash -c "cd '$repo' && { $3; } > '$2' 2>&1; rc=\$?; if [ \$rc -eq 0 ]; then echo DONE >> '$2'; else echo \"FAIL rc=\$rc\" >> '$2'; fi"
}
run_unit d0-projects "$out/projects.log" \
  "python3 '$drv' probe --tsgo '$tsgo' --out '$out' && python3 '$drv' run projects --tsgo '$tsgo' --out '$out' --jobs 2 --runs 3 && python3 '$drv' report --out '$out'"
run_unit d0-corpus "$out/corpus.log" \
  "python3 '$drv' run corpus --tsgo '$tsgo' --out '$out' --jobs 4 --runs 3 && python3 '$drv' report --out '$out'"
echo "started: $out (logs: projects.log, corpus.log)"
