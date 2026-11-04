#!/usr/bin/env bash
# The whole baseline set (docs/perf-rearch/baseline.md) for one tsc-rs binary against Go, in order: CLI instruction
# counts (load-independent), CLI wall scenarios, LSP scenarios, and the THP-guard check. Every part resumes.
# usage: scripts/perf-rearch/baseline.sh <tsc-rs binary> <out dir> [part...]   parts: icount cli lsp thp (default all)
#        scripts/perf-rearch/baseline.sh help
# Runs for hours on a quiet machine (every wall run waits for load < 1.5): start it under systemd-run, see README.md.
# The log of each part is <out dir>/<part>.log; the last line of this script's output is DONE or FAIL rc=N.
set -uo pipefail
[[ ${1:-help} == help || $# -lt 2 ]] && { sed -n '2,7p' "$0"; exit 0; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
rs=$(realpath "$1"); out=$2; shift 2
parts=${*:-icount cli lsp thp}
go=$HOME/.local/bin/tsgo-oracle-3e9236361fa9
golsp=$HOME/.local/bin/tsgo-lsp-oracle-3e9236361fa9
projects=ts-pattern,query,hono,zod,effect,umami,t3-web,t3-server,vscode
mkdir -p "$out"
rc=0
for part in $parts; do
  echo "$(date -Is) $part"
  case $part in
    icount) "$here/bench.sh" cli --bin rs="$rs" --bin go="$go" --projects $projects --scenarios icount \
              --icount-runs 2 --out "$out/icount" >"$out/icount.log" 2>&1 || rc=1 ;;
    cli) "$here/bench.sh" cli --bin rs="$rs" --bin go="$go" --projects $projects --single-ref --runs 3 \
           --scenarios noemit,emit,emit-js,emit-cjs,single,incr,build --out "$out/cli" >"$out/cli.log" 2>&1 || rc=1 ;;
    lsp) "$here/bench.sh" lsp --bin rs="$rs" --bin go="$golsp" --runs 3 --out "$out/lsp" >"$out/lsp.log" 2>&1 || rc=1 ;;
    # The THP guard (huge pages off) on the multi-threaded check, per the checker audit.
    thp) "$here/bench.sh" cli --bin rs="$rs" --bin rs-thp0="$rs" --env rs-thp0:GOPORT_THP_GUARD=0 --bin go="$go" \
           --projects zod,t3-server,vscode --scenarios noemit --runs 3 --out "$out/thp" >"$out/thp.log" 2>&1 || rc=1 ;;
    *) echo "unknown part $part" >&2; rc=1 ;;
  esac
done
"$here/bench.sh" table "$out"/{icount,cli,lsp,thp} --out "$out/gap.md" >/dev/null 2>&1 || true
if [[ $rc == 0 ]]; then echo DONE; else echo "FAIL rc=$rc"; fi
