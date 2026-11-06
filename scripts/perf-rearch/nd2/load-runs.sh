#!/bin/bash
# Lane ND2: the load repro. 48 CPU burners (burn.sh) plus ATA's npm made slower by a random 0 to
# MAX s per call (slow-npm, ND2_NPM_DELAY=rand:MAX), and JOBS repro.py instances side by side, as the
# gate runs 4 sessions. Prints the number of runs per server and variant, over all instances.
#   scripts/perf-rearch/nd2/load-runs.sh <harness dir> <rs bin> <out> [JOBS=4] [ROUNDS=2] [RS_RUNS=4] [GO_RUNS=2] [MAX=3]
# <harness dir> holds scripts/perf-rearch (a checkout, or `git archive <rev> scripts/perf-rearch`).
set -u
harness=$1 bin=$2 out=$3 jobs=${4:-4} rounds=${5:-2} rsruns=${6:-4} goruns=${7:-2} max=${8:-3}
here=$(cd "$(dirname -- "$0")" && pwd)
mkdir -p "$out"
"$here/burn.sh" 48 3000 >/dev/null 2>&1 &
sleep 15
pids=()
for k in $(seq "$jobs"); do
  PATH="$here/slow-npm:$PATH" ND2_NPM_DELAY="rand:$max" ND2_NPM_TIMES="$out/npm-times-$k" \
    systemd-run --user --scope --collect --slice=app-batch.slice --nice=10 -q \
    python3 "$harness/scripts/perf-rearch/nd2/repro.py" --rs "$bin" --out "$out/j$k" --rounds "$rounds" \
      --rs-runs "$rsruns" --go-runs "$goruns" --server-logs > "$out/j$k.out" 2>&1 &
  pids+=($!)
done
wait "${pids[@]}"
systemctl --user stop nd2-burn.scope
echo "load during the runs: $(grep -ho 'load [0-9.]*' "$out"/j*.out | awk '{print $2}' | sort -n | sed -n '1p;$p' | paste -sd-)"
grep -h '^round' "$out"/j*.out | awk '{side = ($5 ~ /^go/) ? "go" : "rs"; $1=$2=$3=$4=$5=""; print side, $0}' | sort | uniq -c
