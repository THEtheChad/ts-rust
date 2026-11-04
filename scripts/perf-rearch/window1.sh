#!/usr/bin/env bash
# Timing window 1 (2026-11-04): Go vs main vs the merged perf head (E0+A1+B1) on an idle machine, plus the
# same-binary A/Bs of B1 (GOPORT_PLAIN_EARLY_EMIT=0) and A1 (GOPORT_REUSE_SYNTHETIC=0). Every wall run waits for
# load < 1.5 (bench.sh). Parts run in priority order and resume.
# usage: scripts/perf-rearch/window1.sh <main bin> <head bin> <out dir> [part...]
#   parts: cli-core lsp cli-emit-ab icount cli-rest thp   (default: all, in that order)
# The last output line is DONE or FAIL rc=N.
set -uo pipefail
[[ ${1:-help} == help || $# -lt 3 ]] && { sed -n '2,7p' "$0"; exit 0; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
main=$(realpath "$1"); head=$(realpath "$2"); out=$3; shift 3
parts=${*:-cli-core lsp cli-emit-ab icount cli-rest thp}
go=$HOME/.local/bin/tsgo-oracle-3e9236361fa9
golsp=$HOME/.local/bin/tsgo-lsp-oracle-3e9236361fa9
projects=ts-pattern,query,hono,zod,effect,umami,t3-web,t3-server,vscode
mkdir -p "$out"; rc=0
for part in $parts; do
  echo "$(date -Is) $part"
  case $part in
    # The "<= Go" question first: CLI check and emit, Go / main / head.
    cli-core) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects $projects \
                --runs 3 --scenarios noemit,emit,emit-js --out "$out/cli-core" >"$out/cli-core.log" 2>&1 || rc=1 ;;
    # LSP: Go / main / head / head with A1 off.
    # bench.sh lsp has no --env: head-noreuse is a wrapper that sets GOPORT_REUSE_SYNTHETIC=0 and execs head.
    lsp) noreuse="$out/tsgo-head-noreuse"
         printf '#!/bin/sh\nGOPORT_REUSE_SYNTHETIC=0 exec %q "$@"\n' "$head" >"$noreuse"; chmod +x "$noreuse"
         "$here/bench.sh" lsp --bin go="$golsp" --bin main="$main" --bin head="$head" --bin head-noreuse="$noreuse" \
           --runs 3 --out "$out/lsp" >"$out/lsp.log" 2>&1 || rc=1 ;;
    # B1 same-binary A/B on the emit scenarios.
    cli-emit-ab) "$here/bench.sh" cli --bin head="$head" --bin head-barrier="$head" \
                   --env head-barrier:GOPORT_PLAIN_EARLY_EMIT=0 --bin go="$go" --projects umami,hono,ts-pattern,vscode,t3-web \
                   --runs 5 --scenarios emit,emit-js,emit-cjs --out "$out/cli-emit-ab" >"$out/cli-emit-ab.log" 2>&1 || rc=1 ;;
    icount) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects $projects \
              --scenarios icount --icount-runs 2 --out "$out/icount" >"$out/icount.log" 2>&1 || rc=1 ;;
    cli-rest) "$here/bench.sh" cli --bin go="$go" --bin main="$main" --bin head="$head" --projects $projects \
                --single-ref --runs 3 --scenarios emit-cjs,single,incr,build --out "$out/cli-rest" >"$out/cli-rest.log" 2>&1 || rc=1 ;;
    thp) "$here/bench.sh" cli --bin head="$head" --bin head-thp0="$head" --env head-thp0:GOPORT_THP_GUARD=0 --bin go="$go" \
           --projects zod,t3-server,vscode --scenarios noemit --runs 3 --out "$out/thp" >"$out/thp.log" 2>&1 || rc=1 ;;
    *) echo "unknown part $part" >&2; rc=1 ;;
  esac
  "$here/bench.sh" table "$out"/*/ --out "$out/gap.md" >/dev/null 2>&1 || true
done
if [[ $rc == 0 ]]; then echo DONE; else echo "FAIL rc=$rc"; fi
