#!/usr/bin/env bash
# L1 V-replay oracle on real projects (loader v2, the per-load OS view).
#
# For each case, on a scratch copy of the project at a fixed path (node_modules linked; the
# project input is not changed):
#   1. record: a session of the view's bin with GOPORT_LOAD_VIEW_DUMP (each load's view);
#   2. mutate the disk: change the text of MUTATE (default 8) source files that the recorded
#      load read (not the open file; names stay, so the config's file walk, which runs before
#      the load and outside the view, sees the same disk);
#   3. replay: the same session with the serial loader (GOPORT_RESOLVE_AHEAD=0) reading the
#      recorded views (GOPORT_LOAD_VIEW_REPLAY), on the mutated disk;
#   4. fresh: the base commit's bin (no view), serial, on the mutated disk (sensitivity: it
#      must differ).
# The replay must equal the record in every dump record and every stream message
# (dump-sections.py with the record as the reference); replay_miss must be 0. A replayed
# view is not reconciled (it holds no live observation); the record's disk does not change
# during its session. Needs bins built with the `diagnostics` cargo feature (the base too: its
# serial loader, GOPORT_RESOLVE_AHEAD=0, and the load dump are compiled only with it).
# usage: l1-replay-oracle.sh <tsgo bin, diagnostics build> <base tsgo bin> <out dir>   (ONLY=<regex> picks cases)
set -u
bin=$(realpath "$1"); base=$(realpath "$2"); mkdir -p "$3"; out=$(realpath "$3")
here=$(cd "$(dirname "$0")" && pwd)
P=~/.cache/perf-rearch/projects
U=~/.cache/lspdiff/projects/umami
cases=(
  "umami-open open $U"
  "umami-edit edit $U"
  "hono-open open $P/hono FILE=src/hono.ts"
  "tspattern-open open $P/ts-pattern FILE=src/index.ts"
)
mutate=${MUTATE:-8}
fail=0
for c in "${cases[@]}"; do
  read -r name scen src f1 <<<"$c"
  [[ $name =~ ${ONLY:-.} ]] || continue
  file=${f1#FILE=}
  scratch=$out/scratch-$name
  rm -rf "$scratch" && mkdir -p "$scratch"
  for e in "$src"/* "$src"/.[!.]*; do
    [[ -e $e ]] || continue
    b=$(basename "$e")
    [[ $b == .git ]] && continue
    if [[ $b == node_modules ]]; then ln -s "$e" "$scratch/$b"; else cp -a "$e" "$scratch/$b"; fi
  done
  rm -f "$out/$name".{rec,rep,fresh}.* "$out/$name.view"
  run() { # <label> <bin> <env...>
    local label=$1 b=$2; shift 2
    env "$@" S1A_SETTLE_MS=4000 S1A_FILE=$file GOPORT_LOAD_TIMES=1 \
      node "$here/load-dump-session.mjs" "$b" "$out/$name.$label" "$scen" "$scratch" || echo "ERROR $name $label"
  }
  run rec "$bin" GOPORT_LOAD_VIEW_DUMP="$out/$name.view"
  # The files to change: source files that the load read (cached files in the scratch copy,
  # not under node_modules), not the open file.
  opened=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["file"])' "$out/$name.rec.json")
  opened=${opened/#$src/$scratch}
  mapfile -t files < <(awk -v s="$scratch/" -v o="$opened" '$1=="cached_file" && index($2,s)==1 && $2 !~ /node_modules/ && $2 != o && $2 ~ /\.(ts|tsx|mts|cts)$/ {print $2}' "$out/$name.rec.dump" | sort -u | head -n "$mutate")
  for f in "${files[@]}"; do printf '\nexport const __l1ReplayMutation: number = "not a number";\n' >> "$f"; done
  run rep "$bin" GOPORT_LOAD_VIEW_REPLAY="$out/$name.view" GOPORT_RESOLVE_AHEAD=0
  run fresh "$base" GOPORT_RESOLVE_AHEAD=0
  python3 -I "$here/dump-sections.py" dumps --off "$out/$name.rec.dump" "$out/$name.rec.dump" --on "$out/$name.rep.dump" > "$out/$name.dump-verdict"
  python3 -I "$here/dump-sections.py" stream --off "$out/$name.rec.stream" "$out/$name.rec.stream" --on "$out/$name.rep.stream" > "$out/$name.stream-verdict"
  python3 -I "$here/dump-sections.py" dumps --off "$out/$name.rec.dump" "$out/$name.rec.dump" --on "$out/$name.fresh.dump" > "$out/$name.fresh-verdict"
  misses=$(python3 -I -c 'import json,re,sys
s=json.load(open(sys.argv[1]))["stderr"]
print(sum(int(m) for m in re.findall(r"replay_miss=(\d+)", s)), len(re.findall(r"goport load-view:", s)))' "$out/$name.rep.json")
  dv=$(tail -1 "$out/$name.dump-verdict"); sv=$(tail -1 "$out/$name.stream-verdict"); fv=$(tail -1 "$out/$name.fresh-verdict")
  read -r miss views <<<"$misses"
  if [[ $dv == "RECORDS OK" && $sv == "RECORDS OK" && $miss == 0 && $fv != "RECORDS OK" && ${#files[@]} -gt 0 ]]; then
    echo "OK $name: replay = record (dump, stream); $views replayed views, replay_miss 0; ${#files[@]} files changed; fresh serial differs ($(grep -c '^  FAIL' "$out/$name.fresh-verdict") records)"
  else
    echo "FAIL $name: dump $dv, stream $sv, replay_miss $miss over $views views, fresh $fv, changed ${#files[@]}"; fail=1
    grep -h '^  FAIL\|^FAIL' "$out/$name.dump-verdict" "$out/$name.stream-verdict" | head -10 | cut -c1-250 | sed 's/^/    /'
  fi
  rm -rf "$scratch"
  gzip -f "$out/$name".{rec,rep,fresh}.{dump,stream} "$out/$name.view"
done
[[ $fail == 0 ]] && echo "L1 REPLAY ORACLE OK" || echo "L1 REPLAY ORACLE FAIL"
