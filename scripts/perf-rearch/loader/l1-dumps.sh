#!/usr/bin/env bash
# L1 dump-equality proof (loader v2, the per-load OS view): each scenario runs with
# GOPORT_LOAD_VIEW=1 and =0 (S1a on in both)
# (2 times each; L1_REPS and L1_REPS_VARY set it;
# interleaved) and once with GOPORT_RESOLVE_AHEAD=0 (the serial loader, no
# resolve ahead at all), and compares record by record (dump-sections.py): the load dumps
# section by section, and the whole message stream (every message in both directions, in
# order, raw; load-dump-session.mjs <out>.stream). A record that varies between the two
# view-off runs is inconclusive and listed (<out>/<case>.dump-verdict, .stream-verdict).
# A case is "name scenario root [root2] [FILE=rel] [FILE2=rel]".
# usage: l1-dumps.sh <tsgo bin> <out dir>  (L1_ONLY=<regex> picks cases; L1_REPS, L1_REPS_VARY: runs per side, default 2)
set -u
bin=$(realpath "$1"); mkdir -p "$2"; out=$(realpath "$2")
only=${L1_ONLY:-.}
here=$(cd "$(dirname "$0")" && pwd)
P=~/.cache/perf-rearch/projects
U=~/.cache/lspdiff/projects/umami
cases=(
  "umami-open open $U"
  "tspattern-open open $P/ts-pattern - FILE=src/index.ts"
  "hono-open open $P/hono - FILE=src/hono.ts"
  "query-open open $P/query/packages/query-core"
  "t3web-open open $P/t3code/apps/web"
  "umami-second second $U $P/ts-pattern"
  "t3-second second $P/t3code/apps/web $P/t3code/apps/server"
  "umami-unsaved unsaved $U"
  "hono-unsaved unsaved $P/hono - FILE=src/hono.ts"
  "umami-pkgjson pkgjson $U"
  "query-pkgjson pkgjson $P/query/packages/query-core"
  "umami-edit edit $U"
  "umami-autoimport autoimport $U"
  "t3web-autoimport autoimport $P/t3code/apps/web"
  "umami-cancel cancel $U"
  "tspattern-fsmut fsmut $P/ts-pattern - FILE=src/index.ts"
  "umami-fsmut fsmut $U"
  "hono-edit edit $P/hono - FILE=src/hono.ts"
)
fail=0
for c in "${cases[@]}"; do
  read -r name scen root root2 f1 f2 <<<"$c"
  [[ $name =~ $only ]] || continue
  [[ ${root2:-} == - ]] && root2=
  file=${f1#FILE=}; file2=${f2#FILE2=}
  # More runs where the session has work that varies without S1a (background auto-import,
  # keyed second loads): a record that varies between the off runs is inconclusive, and
  # more off runs make fewer records so.
  case $scen in autoimport|fsmut|edit) reps=${L1_REPS_VARY:-2} ;; *) reps=${L1_REPS:-2} ;; esac
  offs=(); ons=()
  for rep in $(seq 1 "$reps"); do
    for v in 1 0; do
      S1A_FILE=$file S1A_FILE2=$file2 GOPORT_LOAD_VIEW=$v GOPORT_LOAD_TIMES=1 GOPORT_RESOLVE_AHEAD_STATS=1 node "$here/load-dump-session.mjs" "$bin" "$out/$name-$v-$rep" "$scen" "$root" ${root2:-} || echo "ERROR $name $v-$rep"
      if [[ $v == 1 ]]; then ons+=("$out/$name-$v-$rep"); else offs+=("$out/$name-$v-$rep"); fi
    done
  done
  S1A_FILE=$file S1A_FILE2=$file2 GOPORT_RESOLVE_AHEAD=0 node "$here/load-dump-session.mjs" "$bin" "$out/$name-serial" "$scen" "$root" ${root2:-} || echo "ERROR $name serial"
  stats=$(python3 -I -c 'import json,re,sys
for f in sys.argv[1:]:
    s=json.load(open(f))["stderr"]
    print(" | ".join(re.sub(r"goport load-view: t0=[\d.]+ t1=[\d.]+ ","",l)[:200] for l in s.splitlines() if "goport load-view" in l))' "$out/$name-1-1.json")
  # The verdict: record by record (dump-sections.py), for the dumps (section by section)
  # and for the whole message stream (every message in order, raw).
  python3 -I "$here/dump-sections.py" dumps --off "${offs[@]/%/.dump}" --on "${ons[@]/%/.dump}" --serial "$out/$name-serial.dump" > "$out/$name.dump-verdict"
  python3 -I "$here/dump-sections.py" stream --off "${offs[@]/%/.stream}" --on "${ons[@]/%/.stream}" --serial "$out/$name-serial.stream" > "$out/$name.stream-verdict"
  dv=$(tail -1 "$out/$name.dump-verdict"); sv=$(tail -1 "$out/$name.stream-verdict")
  inc=$(grep -c 'INCONCLUSIVE' "$out/$name.dump-verdict" "$out/$name.stream-verdict" | awk -F: '{s+=$2} END {print s}')
  exc=$(grep -c 'EXCLUDED' "$out/$name.dump-verdict" | awk -F: '{s+=$NF} END {print s}')
  ser=$(grep -c '^  SERIAL FAIL' "$out/$name.dump-verdict" "$out/$name.stream-verdict" | awk -F: '{s+=$2} END {print s}')
  if [[ $dv == "RECORDS OK" && $sv == "RECORDS OK" ]]; then
    echo "OK $name ($reps on, $reps off): dump $dv, stream $sv; $inc inconclusive, $exc excluded records; serial differences: $ser (dump lines $(wc -l < "$out/$name-0-1.dump"))"
  else
    echo "FAIL $name: dump $dv, stream $sv"; fail=1
    grep -h '^  FAIL\|^FAIL' "$out/$name.dump-verdict" "$out/$name.stream-verdict" | head -20 | sed 's/^/    /'
  fi
  { grep -h 'INCONCLUSIVE' "$out/$name.dump-verdict" "$out/$name.stream-verdict" || true; } | cut -c1-220 | sed 's/^/    /' | head -8
  gzip -f "$out/$name"-*.dump "$out/$name"-*.stream
  echo "  stats(on, run a): $stats"
done
[[ $fail == 0 ]] && echo "L1 RECORDS OK" || echo "L1 RECORDS FAIL"
