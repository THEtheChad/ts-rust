#!/usr/bin/env bash
# Every aliased-output and directory-link repro (Astra D1-I2/I3 rounds 1-2), RUNS runs per mode, on BIN.
# One line per (repro, mode): distinct results and exit codes. Ends with LAYOUT OK when every line has
# one result, else LAYOUT FAIL.
# usage: layout_repros.sh BIN [RUNS=20] [MODES="plain c4 incremental build"]
set -u
bin=$1 runs=${2:-20} modes=${3:-plain c4 incremental build}
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bad=0
line() { python3 -I -c '
import json, sys
d = json.load(sys.stdin)
n = len(d.get("results", d.get("hashes", {})))
print("%-22s %-12s results=%d exits=%s" % (sys.argv[1], sys.argv[2], n, d["exits"]))
sys.exit(0 if n == 1 else 1)' "$1" "$2"; }
for mode in $modes; do
  case $mode in
    plain) m=plain extra=() ;; c4) m=plain extra=(--arg=--checkers --arg=4) ;;
    incremental) m=incremental extra=() ;; build) m=build extra=() ;;
  esac
  for case in round1 different-names target-file case-only target-created-file absolute chain deep parent input-dir; do
    c=(); [ $case = round1 ] || c=(--case $case)
    python3 -I $here/dirlink_repro.py "$bin" --runs $runs --mode $m "${c[@]}" "${extra[@]}" --allow-varying | line $case $mode || bad=1
  done
  # The aliased-file repros (round 1 of D1-I2): plain and c4 only (they run -p with no build info).
  if [ $mode = plain ] || [ $mode = c4 ]; then
    for kind in symlink hardlink dangling; do
      python3 -I $here/aliased_repro.py "$bin" --runs $runs --mode $kind "${extra[@]}" --allow-varying | line "file-$kind" $mode || bad=1
    done
  fi
done
if [ $bad = 0 ]; then echo "LAYOUT OK"; else echo "LAYOUT FAIL"; fi
