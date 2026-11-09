#!/usr/bin/env bash
# Code-quality report for the Rust sources (crates/) of a revision. Reports only; it gates nothing.
# usage: scripts/perf-rearch/quality.sh report <rev> [--base <rev>] [--no-clippy] [--no-go-relative] [--hatches] [--top N] [--out DIR]
#        scripts/perf-rearch/quality.sh ranking <rev> [--top 60] [--out FILE]
#        scripts/perf-rearch/quality.sh history [--base main] [--branch perf/first-principles] [--out DIR]
#        scripts/perf-rearch/quality.sh install     build rust-code-analysis-cli into ~/.cache/perf-rearch/tools/rca
#        scripts/perf-rearch/quality.sh clean       remove the clippy worktree and the snapshot cache
#        scripts/perf-rearch/quality.sh help        this text
# - report: one snapshot (and a delta against --base) as markdown on stdout, plus report.json and report.md
#   under ~/.cache/perf-rearch/quality/reports/<rev>[-vs-<base>]/ (or --out). Clippy runs here only
#   (cargo clippy --workspace, lib+bins, the workspace lints) in the reusable detached worktree
#   ~/.cache/perf-rearch/quality/clippy-wt with its own target dir: about 4 min cold, about 1-2 min
#   warm; --no-clippy skips it. --hatches prints the full escape-hatch inventory.
# - Go-relative (in report, on by default; --no-go-relative skips it): each Rust prod function mapped to
#   its Go counterpart at the pin (`// Go:` markers, then names; gorel.py, Go side scripts/perf-rearch/gocog),
#   ranked as port bloat (Rust cog - Go cog) and own code over cog 25, with per-area totals and, with
#   --base, the rows that moved. About 3 s per new crates/ tree, cached per crates/ tree and Go tree.
#   Go tree: PERF_QUALITY_GO_ROOT (default the 3e9236361fa9 checkout), go: PERF_QUALITY_GO.
# - ranking: the Go-relative planning page (top N of each ranking, per-area and per-file totals).
# - history: a snapshot at --base and at every first-parent merge of --branch since --base (and its head),
#   each delta against the previous point; no clippy. About 10 s per new crates/ tree, cached.
# - Lanes: run `quality.sh report HEAD --base perf/first-principles` before reporting and paste the summary.
# - Metrics: per-function cognitive and cyclomatic complexity (rust-code-analysis), escape hatches
#   (GOPORT_*/TSC_RS_* env reads, bool gates, branches), duplication (token windows), churn
#   (prod/test lines), hygiene (unsafe, unwrap, expect, panics, allow attributes, TODO markers), clippy.
#   docs/perf-rearch/quality.md explains each one and its weaknesses.
# - The source is read with `git archive` into a temporary directory; no checkout is touched.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tools="$HOME/.cache/perf-rearch/tools"
cache="${PERF_QUALITY_CACHE:-$HOME/.cache/perf-rearch/quality}"
case "${1:-help}" in
help | -h | --help)
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  ;;
install)
  mkdir -p "$tools/rca"
  cargo install --locked rust-code-analysis-cli --version 0.0.25 --root "$tools/rca"
  ;;
clean)
  if [[ -d "$cache/clippy-wt" ]]; then git -C "$here" worktree remove --force "$cache/clippy-wt"; fi
  rm -rf "$cache/snap" "$cache/tmp"
  ;;
report | history | ranking)
  if [[ ! -x "$tools/rca/bin/rust-code-analysis-cli" ]]; then
    echo "rust-code-analysis-cli is missing: run $0 install" >&2
    exit 2
  fi
  exec python3 "$here/quality.py" "$@"
  ;;
*)
  echo "unknown command: $1 (see $0 help)" >&2
  exit 2
  ;;
esac
