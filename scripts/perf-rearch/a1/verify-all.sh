#!/usr/bin/env bash
# Runs verify-typing.mjs over the react-jsx projects (perf/first-principles A1). Writes one
# verify log per session to $OUT and prints a summary line per session.
#   scripts/perf-rearch/a1/verify-all.sh <tsgo> [out dir]
set -u
BIN=$1
OUT=${2:-$HOME/.cache/perf-rearch/a1/verify}
HERE=$(cd "$(dirname "$0")" && pwd)
P=$HOME/.cache/perf-rearch/projects
mkdir -p "$OUT"
status=0
run() {
  local name=$1 project=$2 edit=$3 mode=$4; shift 4
  node "$HERE/verify-typing.mjs" --bin "$BIN" --project "$project" --edit "$edit" --log "$OUT/$name.log" --mode "$mode" "$@" || status=1
}
run umami-lspdiff "$HOME/.cache/lspdiff/projects/umami" 'src/app/(main)/boards/BoardCloneForm.tsx' 1 --open src/lib/date.ts
run umami-perf "$P/umami" src/components/common/ActionForm.tsx all --open 'src/app/(main)/boards/BoardCloneForm.tsx'
run react-query "$P/query/packages/react-query" src/QueryClientProvider.tsx 1 --open src/useQuery.ts
run t3code-web "$P/t3code/apps/web" src/markdown-github-alerts.test.tsx 1
run jsx-fixture "$HOME/.cache/perf-rearch/a1/projects/jsx-pubdiag" src/App.tsx all --open src/util.ts
exit $status
