#!/usr/bin/env bash
# Timing window 2 (2026-11-05): Go vs main (91f3d021c) vs the perf head (E0+A1+B1+S1a+C1+ND1+L1+P1), all three
# built by the caller with the same stable toolchain and --profile goport. Every wall run waits for load < 1.5
# (bench.sh), sides interleaved. Parts run in priority order and resume (bench.py skips recorded runs).
# usage: scripts/perf-rearch/window2.sh <main bin> <head bin> <out dir> [part...]
#   parts: cli-core lsp incr-edit open12 qbuild cli-rest icount thp   (default: all, in that order)
#   RUNS=N sets the runs per cell (default 3; window2-top-up passes use 5 for cells within 10% of Go)
# The last output line is DONE or FAIL rc=N.
set -uo pipefail
[[ ${1:-help} == help || $# -lt 3 ]] && { sed -n '2,9p' "$0"; exit 0; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
main=$(realpath "$1"); head=$(realpath "$2"); out=$3; shift 3
parts=${*:-cli-core lsp incr-edit open12 qbuild cli-rest icount thp}
runs=${RUNS:-3}
go=$HOME/.local/bin/tsgo-oracle-3e9236361fa9
golsp=$HOME/.local/bin/tsgo-lsp-oracle-3e9236361fa9
honolsp=$HOME/.cache/perf-rearch/parity-projects/repos/honojs__hono
projects=ts-pattern,query,hono,zod,effect,umami,t3-web,t3-server,vscode
mkdir -p "$out"; rc=0
for part in $parts; do
  echo "$(date -Is) $part"
  case $part in
    cli-core) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects $projects \
                --runs "$runs" --scenarios noemit,emit,emit-js --out "$out/cli-core" >"$out/cli-core.log" 2>&1 || rc=1 ;;
    # ttfd, probes (20 edits on a .tsx and a .ts), concurrent typing at 120/250/500 ms per key, on umami.
    lsp) "$here/bench.sh" lsp --bin go="$golsp" --bin main="$main" --bin head="$head" \
           --runs "$runs" --out "$out/lsp" >"$out/lsp.log" 2>&1 || rc=1 ;;
    # Warm --incremental --noEmit rebuild after one edit (C1's walk): query, umami, hono, the 85-file fixture.
    incr-edit) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" \
                 --projects query,umami,hono,synth85 --runs "$runs" --scenarios incr-edit --out "$out/incr-edit" \
                 >"$out/incr-edit.log" 2>&1 || rc=1 ;;
    # hono: first diagnostic request after 12 didOpens, alone.
    open12) "$here/bench.sh" lsp --bin go="$golsp" --bin main="$main" --bin head="$head" --project "$honolsp" \
              --scenarios open12 --runs "$runs" --out "$out/open12" >"$out/open12.log" 2>&1 || rc=1 ;;
    # query -b warm (7 ms vs 8 ms in window 1): many runs.
    qbuild) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects query \
              --runs "$((runs > 9 ? runs : 9))" --scenarios build --out "$out/qbuild" >"$out/qbuild.log" 2>&1 || rc=1 ;;
    cli-rest) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects $projects \
                --single-ref --runs "$runs" --scenarios emit-cjs,single,incr,build --out "$out/cli-rest" \
                >"$out/cli-rest.log" 2>&1 || rc=1 ;;
    icount) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects $projects \
              --scenarios icount --icount-runs 2 --out "$out/icount" >"$out/icount.log" 2>&1 || rc=1 ;;
    thp) "$here/bench.sh" cli --bin head="$head" --bin head-thp0="$head" --env head-thp0:GOPORT_THP_GUARD=0 --bin go="$go" \
           --projects zod,t3-server,vscode --scenarios noemit --runs "$runs" --out "$out/thp" >"$out/thp.log" 2>&1 || rc=1 ;;
    *) echo "unknown part $part" >&2; rc=1 ;;
  esac
  "$here/bench.sh" table "$out"/*/ --out "$out/gap.md" >/dev/null 2>&1 || true
done
if [[ $rc == 0 ]]; then echo DONE; else echo "FAIL rc=$rc"; fi
