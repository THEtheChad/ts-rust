#!/bin/bash
# CPU burner for lane ND2 (the watch-ts-pattern-f12 load race): N busy loops for SECONDS, run as a
# systemd scope in app-batch.slice at nice 10, the slice and priority of the repro itself, so the
# repro's servers compete with the burners for CPU as the gate's servers did at load ~30.
#   scripts/perf-rearch/nd2/burn.sh [N=48] [SECONDS=600]
#   systemctl --user stop nd2-burn.scope   # stops it early
set -eu
n=${1:-48} secs=${2:-600}
exec systemd-run --user --scope --collect --slice=app-batch.slice --nice=10 --unit=nd2-burn \
  timeout "$secs" bash -c 'for i in $(seq '"$n"'); do (while :; do :; done) & done; wait'
