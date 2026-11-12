#!/usr/bin/env bash
# Runs one test of a built test binary RUNS times, in LOOPS loops at once, beside
# clock-steps.py, to measure a flake rate across wall clock steps (lane C1).
#
# usage: flake-loop.sh OUT_DIR TEST_BIN TEST_NAME [RUNS] [LOOPS]
#
# OUT_DIR gets:
#   runs.log   one line per run: loop, run, boot-time start and end (s, from
#              /proc/uptime, as clock-steps.py prints), PASS or FAIL
#   steps.log  clock-steps.py over the whole loop (boot time of each step)
#   fail-<loop>-<run>.log  the output of each failed run
#   summary    the counts
# The test runs as `TEST_BIN --exact TEST_NAME --test-threads 1`. Heavy: run it
# under the heavy-run lock (docs/perf-rearch/brief.md, "Machine").
set -u
if [ "${1:-}" = help ] || [ $# -lt 3 ]; then
	sed -n '2,15p' "$0"
	exit 0
fi
out=$1 bin=$2 name=$3 runs=${4:-200} loops=${5:-4}
mkdir -p "$out"
: >"$out/runs.log"
here=$(cd "$(dirname "$0")/.." && pwd)
python3 "$here/clock-steps.py" --seconds 86400 >"$out/steps.log" &
steps=$!
uptime_s() { cut -d' ' -f1 /proc/uptime; }
loop() {
	local l=$1 n=$2 i start end
	for ((i = 1; i <= n; i++)); do
		start=$(uptime_s)
		if "$bin" --exact "$name" --test-threads 1 >"$out/run-$l.tmp" 2>&1; then
			echo "$l $i $start $(uptime_s) PASS" >>"$out/runs.log"
		else
			end=$(uptime_s)
			mv "$out/run-$l.tmp" "$out/fail-$l-$i.log"
			echo "$l $i $start $end FAIL" >>"$out/runs.log"
		fi
	done
	rm -f "$out/run-$l.tmp"
}
per=$(((runs + loops - 1) / loops))
for ((l = 1; l <= loops; l++)); do
	loop "$l" "$per" &
done
wait $(jobs -p | grep -v "^$steps\$")
kill "$steps" 2>/dev/null
wait "$steps" 2>/dev/null
pass=$(grep -c ' PASS$' "$out/runs.log")
fail=$(grep -c ' FAIL$' "$out/runs.log")
nsteps=$(grep -c 'wall clock step -' "$out/steps.log")
echo "runs $((pass + fail)) pass $pass fail $fail backward-steps $nsteps" | tee "$out/summary"
