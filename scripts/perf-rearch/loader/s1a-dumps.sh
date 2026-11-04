#!/usr/bin/env bash
# S1a dump-equality proof: each scenario runs with GOPORT_FIRST_LOAD_AHEAD=1 and =0
# (twice each, interleaved) and once with GOPORT_RESOLVE_AHEAD=0 (the serial loader, no
# resolve ahead at all), and compares the load dumps and the whole recorded protocol (every
# server request and notification with its payload, every response, the full diagnostics)
# byte for byte. On a difference it prints the first differing protocol path
# (compare-requests.py).
# A case is "name scenario root [root2] [FILE=rel] [FILE2=rel]".
# usage: s1a-dumps.sh <tsgo bin> <out dir>
set -u
bin=$1; out=$2; mkdir -p "$out"
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
  [[ ${root2:-} == - ]] && root2=
  file=${f1#FILE=}; file2=${f2#FILE2=}
  for rep in a b; do
    for v in 1 0; do
      S1A_FILE=$file S1A_FILE2=$file2 GOPORT_FIRST_LOAD_AHEAD=$v GOPORT_RESOLVE_AHEAD_STATS=1 node "$here/load-dump-session.mjs" "$bin" "$out/$name-$v$rep" "$scen" "$root" ${root2:-} || echo "ERROR $name $v$rep"
    done
  done
  S1A_FILE=$file S1A_FILE2=$file2 GOPORT_RESOLVE_AHEAD=0 node "$here/load-dump-session.mjs" "$bin" "$out/$name-serial" "$scen" "$root" ${root2:-} || echo "ERROR $name serial"
  h() { sha256sum "$1" | cut -c1-16; }
  # The whole recorded protocol, no normalization (compare-requests.py KEYS).
  req() { python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps([d.get(k) for k in ("serverRequests", "notifications", "responses", "diags")]))' "$1" | sha256sum | cut -c1-16; }
  stats=$(python3 -I -c 'import json,re,sys
for f in sys.argv[1:]:
    s=json.load(open(f))["stderr"]
    print(" | ".join(re.sub(r"resolve-ahead: LoadStats ","",l) for l in s.splitlines() if "resolve-ahead" in l))' "$out/$name-1a.json")
  d1a=$(h "$out/$name-1a.dump"); d1b=$(h "$out/$name-1b.dump"); d0a=$(h "$out/$name-0a.dump"); d0b=$(h "$out/$name-0b.dump")
  r1a=$(req "$out/$name-1a.json"); r1b=$(req "$out/$name-1b.json"); r0a=$(req "$out/$name-0a.json"); r0b=$(req "$out/$name-0b.json")
  ds=$(h "$out/$name-serial.dump"); rs=$(req "$out/$name-serial.json")
  if [[ $ds == "$d0a" && $rs == "$r0a" ]]; then serial="serial=SAME"; else serial="serial=DIFF($ds)"; fi
  lines=$(wc -l < "$out/$name-1a.dump")
  if [[ $d1a == "$d0a" && $d1b == "$d0a" && $d0b == "$d0a" && $r1a == "$r0a" && $r1b == "$r0a" && $r0b == "$r0a" ]]; then
    echo "SAME $name dump=$d0a lines=$lines requests=$r0a $serial"
    rm -f "$out/$name"-{1a,1b,0b}.dump; gzip -f "$out/$name-0a.dump"
    [[ $serial == serial=SAME ]] && rm -f "$out/$name-serial.dump" || gzip -f "$out/$name-serial.dump"
  else
    echo "DIFF $name dump on=$d1a,$d1b off=$d0a,$d0b requests on=$r1a,$r1b off=$r0a,$r0b"; fail=1
    python3 -I "$here/compare-requests.py" "$out/$name-0a.json" "$out/$name-0b.json" "$out/$name-1a.json" "$out/$name-1b.json" | sed 's/^/    /' 
  fi
  echo "  stats(on, run a): $stats"
done
[[ $fail == 0 ]] && echo "DUMPS IDENTICAL" || echo "DUMPS DIFFER"
