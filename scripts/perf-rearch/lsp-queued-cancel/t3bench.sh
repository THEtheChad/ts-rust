#!/usr/bin/env bash
# The T3 Code typing bench of lane lsp-queued-cancel: scripts/lspdiff bench (concurrent, pulls=all,
# 120 ms/key, 6 files) with each edited file, one run per side at a time, sides interleaved
# (order rotates per run), each run started only at 1-minute load < 1.5, load logged before and after.
#   t3bench.sh <out dir> <runs> <cell>=<edited file>... -- <side>=<bin>...   (side go: the Go oracle)
# Run it under the heavy-run rule (heavy.sh). Aggregate with t3bench-agg.mjs <out dir>.
set -uo pipefail
out=$1; runs=$2; shift 2
cells=(); sides=()
while [ $# -gt 0 ] && [ "$1" != -- ]; do cells+=("$1"); shift; done; shift
sides=("$@")
P=$HOME/.cache/perf-rearch/projects/t3code
OTHERS=(.repos/alchemy-effect/packages/alchemy/src/AWS/MediaConvert/Probe.ts
  .repos/alchemy-effect/packages/alchemy/src/GCP/Compute/HttpHealthCheck.ts
  .repos/alchemy-effect/packages/alchemy/test/Cloudflare/R2/fixtures/presign/routes.ts
  .repos/effect-smol/packages/effect/src/persistence/RateLimiter.ts
  apps/web/src/components/chat/ThreadDetailsControl.tsx)
load1() { cut -d' ' -f1 /proc/loadavg; }
wait_idle() { while awk 'NR==1{exit !($1 >= 1.5)}' /proc/loadavg; do sleep 10; done; }
mkdir -p "$out"
for cell in "${cells[@]}"; do
  name=${cell%%=*}; edited=${cell#*=}
  for ((r = 0; r < runs; r++)); do
    n=${#sides[@]}
    for ((k = 0; k < n; k++)); do
      side=${sides[$(((k + r) % n))]}
      label=${side%%=*}; bin=${side#*=}
      dir=$out/$name/$label-r$r; mkdir -p "$out/$name"
      mkdir -p "$out/$name"
      [ -f "$dir/done" ] && continue
      wait_idle
      before=$(load1)
      if [ "$label" = go ]; then who=(--only go --go "$bin"); else who=(--only rs --rs "$bin"); fi
      t0=$(date +%s.%N)
      node scripts/lspdiff/lspdiff.mjs bench --project "$P" --file "$edited" $(printf -- '--file %s ' "${OTHERS[@]}") \
        --runs 1 --interval 120 --pulls all --modes concurrent --trace "${who[@]}" --out "$dir.tmp" >"$dir.log" 2>&1
      rc=$?
      after=$(load1); t1=$(date +%s.%N)
      mkdir -p "$dir"; mv "$dir.tmp"/*/* "$dir"/ 2>/dev/null; rm -rf "$dir.tmp"
      echo "{\"cell\":\"$name\",\"side\":\"$label\",\"run\":$r,\"rc\":$rc,\"loadBefore\":$before,\"loadAfter\":$after,\"wall\":$(awk "BEGIN{print $t1 - $t0}")}" | tee -a "$out/runs.jsonl"
      [ $rc = 0 ] && touch "$dir/done"
    done
  done
done
