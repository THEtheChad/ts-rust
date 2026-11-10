#!/usr/bin/env bash
# Lane B2: user-space instruction A/B of two tsgo binaries on query-core, umami and hono (--singleThreaded),
# interleaved by run, noEmit and emit cells. Emit goes to a scratch dir; nothing is written into the projects
# (hono's composite build gets a scratch --tsBuildInfoFile).
# usage: scripts/perf-rearch/b2_icount_ab.sh <base bin> <head bin> <reps> <out tsv>
#        scripts/perf-rearch/b2_icount_ab.sh help
# Writes one line per run: cell, side, rep, instructions; then prints the medians, spreads and deltas.
set -u
if [ $# -lt 4 ] || [ "$1" = help ]; then sed -n 2,6p "$0"; exit 0; fi
BASE=$1 HEAD=$2 REPS=$3 OUT=$4
HERE=$(cd "$(dirname "$0")" && pwd)
P=$HOME/.cache/perf-rearch/projects
S=$(mktemp -d "${TMPDIR:-/tmp}/b2-icount.XXXXXX")
trap 'rm -rf "$S"' EXIT
cells=(
  "query-noemit|$P/query/packages/query-core|-p tsconfig.json --noEmit"
  "query-emit|$P/query/packages/query-core|-p tsconfig.json --outDir $S/o --declaration --incremental false --composite false"
  "umami-noemit|$P/umami|-p tsconfig.json --noEmit"
  "umami-emit|$P/umami|-p tsconfig.json --outDir $S/o --incremental false --composite false --noEmit false"
  "hono-noemit|$P/hono|-p tsconfig.build.json --noEmit --incremental false --composite false"
  "hono-emit|$P/hono|-p tsconfig.build.json --outDir $S/o --incremental false --composite false --tsBuildInfoFile $S/o.tsbuildinfo"
)
: > "$OUT"
for rep in $(seq 1 "$REPS"); do
  for cell in "${cells[@]}"; do
    IFS='|' read -r name dir args <<< "$cell"
    for side in base head; do
      bin=$BASE; [ $side = head ] && bin=$HEAD
      rm -rf "$S/o" "$S/o.tsbuildinfo"
      # shellcheck disable=SC2086
      n=$(cd "$dir" && GOPORT_LAUNCH=0 python3 -I "$HERE/icount.py" "$bin" $args --singleThreaded 2>&1 >/dev/null |
        awk '/user instructions/{print $3}')
      printf '%s\t%s\t%s\t%s\n' "$name" "$side" "$rep" "$n" >> "$OUT"
    done
  done
done
python3 -I - "$OUT" <<'EOF'
import sys, statistics as st, collections
d = collections.defaultdict(list)
for line in open(sys.argv[1]):
    n, s, r, v = line.split('\t')
    d[(n, s)].append(int(v))
for n in dict.fromkeys(k[0] for k in d):
    b, h = d[(n, 'base')], d[(n, 'head')]
    mb, mh = st.median(b), st.median(h)
    print(f"{n:14} base {mb/1e9:8.4f}G (spread {(max(b)-min(b))/mb*100:.3f}%)  "
          f"head {mh/1e9:8.4f}G (spread {(max(h)-min(h))/mh*100:.3f}%)  delta {(mh-mb)/mb*100:+.3f}%")
EOF
