#!/bin/bash
# D1 gain bench (docs/perf-rearch/design/d1.md section 1): times Go and 8 checker settings (noemit, 3 runs,
# load-gated by bench.sh) on vscode, t3-server, umami, hono and zod, with c4 (the default) as the reference.
#
# Usage: run-gain.sh [OUT_DIR]      default OUT_DIR: ~/.cache/perf-rearch/scratch/d1/gain
#   TSGO=<bin>       the tsc-rs bin that the wrappers in bins/ run (default: the window-1 head bin)
#   GO_ORACLE=<bin>  the Go side (default: the pinned oracle)
# Waits for perf-window1 while it runs. Long: run it under systemd-run (brief, "Long jobs"); the log ends
# DONE or FAIL rc=<N>.
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
B=$here/bins
GO=${GO_ORACLE:-$HOME/.local/bin/tsgo-oracle-3e9236361fa9}
out=${1:-$HOME/.cache/perf-rearch/scratch/d1/gain}
cd "$repo"
while systemctl --user is-active --quiet perf-window1; do sleep 30; done
echo "start $(date -Is)"
scripts/perf-rearch/bench.sh cli --bin go="$GO" --bin c4="$B/c4" --bin c8="$B/c8" --bin c12="$B/c12" --bin c16="$B/c16" \
  --bin c4co="$B/c4co" --bin fast="$B/fast" --bin fastnolazy="$B/fastnolazy" --bin faststeal="$B/faststeal" \
  --projects vscode,t3-server,umami,hono,zod --scenarios noemit --runs 3 --ref c4 --out "$out"
rc=$?
if [ $rc -eq 0 ]; then echo DONE; else echo "FAIL rc=$rc"; fi
