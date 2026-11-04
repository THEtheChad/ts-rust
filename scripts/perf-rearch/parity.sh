#!/usr/bin/env bash
# parity.sh: the output-parity gate of perf/first-principles. tsc-rs against Go tsc at the pin
# (3e9236361fa9), everything compared byte for byte, per item MATCH / DIFF / GO-UNSTABLE.
#
# usage: scripts/perf-rearch/parity.sh quick|full <tsgo bin> [--base DIR] [--out DIR] [--checkout DIR]
#                                       [--tiers t1,t2,...]
#        scripts/perf-rearch/parity.sh js [--out DIR]          JS tsc 6.0.3 against Go on the CLI tier
#        scripts/perf-rearch/parity.sh compare <base dir> <run dir>
#        scripts/perf-rearch/parity.sh known <run dir>         the items of a run that are not good
#        scripts/perf-rearch/parity.sh help
#
# Tiers (docs/perf-rearch/parity.md has what each covers, its run time and the base on main):
#   cli     parity_cli.py: real project configs (parity-projects.json) x variants (noemit, emit, decl,
#           incr, listfiles, extdiag, pretty, showconfig, -b): exit, stdout, stderr, written files
#   tests   parity_tests.py: the checkout's cargo test binaries and go_baselines (TestLocal in 4 shards),
#           as ci.yml runs them, per test name and per TestLocal case. It builds the checkout
#           (--checkout, default this worktree): the tier tests the source, not <tsgo bin>
#   lsp     parity_lsp.py: lspdiff sessions (umami, ts-pattern; full adds hono and more positions)
#   fuzz    parity_fuzz.py: difffuzz cases 0..N-1 of seed 1 (quick 1,500, full 30,000)
#   corpus  parity_fuzz.py --corpus: every pin test case (about 12,700) through the CLI (full only)
# quick: cli (quick configs and variants, tsc-rs once), tests (go_baselines only), lsp, fuzz.
# full: all tiers, tsc-rs twice on cli and fuzz (a difference between its own runs is a DIFF).
# The tests tier runs beside the others. Go results are cached per Go binary under
# ~/.cache/perf-rearch/parity/go-cache/, so a run with a warm cache starts only tsc-rs.
#
# Output: --out (default ~/.cache/perf-rearch/parity/runs/<time>-<mode>-<bin sha>): meta.json,
# <tier>/ (results.json and details: cli/items/<item>/diff.txt, fuzz/diffs/<case>/, lsp/<session>/),
# <tier>.log, and results.json (all tiers). With --base it ends with `parity_compare.py compare`:
# a failure is any item that was MATCH or PASS in the base and is not now (PARITY FAIL, exit 1).
# The base on main: ~/.cache/perf-rearch/parity/base-main/.
#
# One gate run at a time (a lock: the scratch paths are fixed so that paths in output agree with the
# Go cache). A full run takes about 1 to 1.5 hours with a warm Go cache; run it under
#   systemd-run --user --collect -p MemoryMax=30G -p MemorySwapMax=0 --unit=parity-<name> \
#     bash -c 'scripts/perf-rearch/parity.sh full <bin> --base ... > <log> 2>&1; echo "DONE rc=$?" >> <log>'
set -uo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }
GO=${PARITY_GO:-$HOME/.local/bin/tsgo-oracle-3e9236361fa9}
GO_LSP=${PARITY_GO_LSP:-$HOME/.local/bin/tsgo-lsp-oracle-3e9236361fa9}
JS_TSC=${PARITY_JS_TSC:-$HOME/.cache/perf-rearch/js-tsc/node_modules/typescript/bin/tsc}
STATE=$HOME/.cache/perf-rearch/parity

mode=${1:-help}
case $mode in
help | -h | --help) usage 0 ;;
compare) (($# == 3)) || usage; exec python3 -I "$here/parity_compare.py" compare "$2" "$3" ;;
known) (($# == 2)) || usage; exec python3 -I "$here/parity_compare.py" known "$2" ;;
quick | full) (($# >= 2)) || usage; bin=$(realpath -- "$2") || exit 2; shift 2 ;;
js) bin=$JS_TSC; shift ;;
*) usage ;;
esac
[[ -x $bin || $mode == js ]] || { echo "parity.sh: not executable: $bin" >&2; exit 2; }

base= out= checkout=$(git -C "$here" rev-parse --show-toplevel) tiers=
while (($#)); do
  case $1 in
  --base) base=$(realpath -- "$2") || exit 2; shift 2 ;;
  --out) out=$(realpath -m -- "$2"); shift 2 ;;
  --checkout) checkout=$(realpath -- "$2") || exit 2; shift 2 ;;
  --tiers) tiers=${2//,/ }; shift 2 ;;
  *) usage ;;
  esac
done
case $mode in
quick) : "${tiers:=cli tests lsp fuzz}"; rs_runs=1; fuzz_count=1500 ;;
full) : "${tiers:=cli tests lsp fuzz corpus}"; rs_runs=2; fuzz_count=30000 ;;
js) tiers=js ;;
esac
sha=$(sha256sum "$bin" | cut -c1-64)
[[ -n $out ]] || out=$STATE/runs/$(date +%Y%m%d-%H%M%S)-$mode-${sha:0:10}
[[ ! -e $out/results.json ]] || { echo "parity.sh: $out has results.json (results are never replaced)" >&2; exit 2; }
mkdir -p "$out" "$STATE"

exec 9> "$STATE/.lock"
flock -n 9 || { echo "parity.sh: another gate run holds $STATE/.lock; waiting" >&2; flock 9; }

load() { cut -d' ' -f1-3 /proc/loadavg; }
now() { date -u +%FT%TZ; }
python3 -I - "$out/meta.json" << PY
import json, sys
json.dump({'mode': '$mode', 'rs': '$bin', 'rsSha': '$sha', 'go': '$GO', 'goLsp': '$GO_LSP',
           'checkout': '$checkout', 'checkoutHead': '$(git -C "$checkout" rev-parse HEAD)',
           'tiers': '$tiers'.split(), 'start': '$(now)', 'loadStart': '$(load)', 'host': '$(hostname)'},
          open(sys.argv[1], 'w'), indent=1)
PY
echo "== parity $mode: $bin (${sha:0:12}), tiers: $tiers, out $out, load $(load)"

tier() { # tier <name> <command...>: runs one tier, its log in <out>/<name>.log
  local name=$1 start rc
  shift
  start=$(date +%s)
  "$@" > "$out/$name.log" 2>&1
  rc=$?
  echo "-- $name: exit $rc, $(($(date +%s) - start)) s, $(tail -n1 "$out/$name.log")"
}

pids=()
for t in $tiers; do
  case $t in
  tests) tier tests python3 -I "$here/parity_tests.py" --out "$out/tests" --tier "$mode" --checkout "$checkout" &
    pids+=($!) ;;
  esac
done
for t in $tiers; do
  case $t in
  cli) tier cli python3 -I "$here/parity_cli.py" --rs "$bin" --go "$GO" --out "$out/cli" --tier "$mode" --rs-runs "$rs_runs" ;;
  lsp) tier lsp python3 -I "$here/parity_lsp.py" --rs "$bin" --go "$GO_LSP" --out "$out/lsp" --tier "$mode" ;;
  fuzz) tier fuzz python3 -I "$here/parity_fuzz.py" --rs "$bin" --go "$GO" --out "$out/fuzz" --seed 1 \
    --count "$fuzz_count" --jobs 12 --rs-runs "$rs_runs" ;;
  corpus) tier corpus python3 -I "$here/parity_fuzz.py" --corpus --rs "$bin" --go "$GO" --out "$out/corpus" --jobs 16 ;;
  js) tier js python3 -I "$here/parity_cli.py" --rs "node $JS_TSC" --label-rs js-tsc --go "$GO" --out "$out/js" \
    --tier full --rs-runs 1 ;;
  tests) ;;
  *) echo "parity.sh: unknown tier $t" >&2 ;;
  esac
done
((${#pids[@]})) && wait "${pids[@]}"

python3 -I - "$out/meta.json" << PY
import json, sys
m = json.load(open(sys.argv[1])); m.update(end='$(now)', loadEnd='$(load)')
json.dump(m, open(sys.argv[1], 'w'), indent=1)
PY
python3 -I "$here/parity_compare.py" merge "$out"
[[ -n $base ]] || { echo "no --base: not compared"; exit 0; }
python3 -I "$here/parity_compare.py" compare "$base" "$out"
