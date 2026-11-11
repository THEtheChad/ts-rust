#!/bin/bash
# Lane B4: runs the lsp tier's watch-hono-f12 session repeatedly with the server on one CPU that
# busy loops share, and prints the number of workspace/diagnostic/refresh requests at the load
# checkpoint of each server run (Go sends 12 there when it is not starved).
#
# usage: starve-repro.sh <harness dir> <out dir> <server cmd> <iterations> [rs-runs json]
#   <harness dir>  a scripts/perf-rearch copy whose parity_lsp.py reads B4_RS_RUNS (the lspdiff
#                  --rs-runs/--rs-env options of a full run, as JSON) and B4_GO_CACHE (a private Go
#                  cache root, so a harness edit does not publish into the shared go-cache)
#   <server cmd>   the --rs command; the Go oracle works too (its runs are then the "rs" runs)
# env: BURN (busy loops, default 6), BURN_CPU (default 7), B4_GO_CACHE (passed through).
# Run it as a heavy job (docs/perf-rearch/brief.md, "Machine"): one systemd-run under the shared lock.
set -u
harness=$1; out=$2; server=$3; n=$4; runs=${5:-'["--rs-runs","2","--rs-env","GOPORT_PARSE_THREADS=1"]'}
cpu=${BURN_CPU:-7}
pids=()
for ((k = 0; k < ${BURN:-6}; k++)); do taskset -c "$cpu" bash -c 'while :; do :; done' & pids+=($!); done
trap 'kill "${pids[@]}" 2>/dev/null' EXIT
mkdir -p "$out"
for ((i = 1; i <= n; i++)); do
  dir=$out/it$i
  B4_RS_RUNS="$runs" python3 "$harness/parity_lsp.py" --rs "/usr/bin/taskset -c $cpu $server" --out "$dir" \
    --tier full --only '^watch-hono-f12$' --no-js > "$dir.log" 2>&1 || echo "it$i parity_lsp rc=$?"
  for f in "$dir"/watch-hono-f12/*/*.jsonl; do
    [ -e "$f" ] || continue
    python3 -I - "$f" "it$i" <<'EOF'
import json, os, sys
for line in open(sys.argv[1]):
    e = json.loads(line)
    if e.get('step') == 'load server messages':
        n = sum(1 for x in e['result'] if x.get('request') == 'workspace/diagnostic/refresh')
        print(sys.argv[2], os.path.basename(sys.argv[1]), n)
EOF
  done
done
