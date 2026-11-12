#!/usr/bin/env bash
# Code-quality report for the Rust sources (crates/) of a revision. Reports only; it gates nothing.
# usage: scripts/perf-rearch/quality.sh report <rev> [--base <rev>] [--no-clippy] [--no-go-relative] [--hatches] [--top N] [--out DIR]
#        scripts/perf-rearch/quality.sh ranking <rev> [--top 60] [--out FILE]
#        scripts/perf-rearch/quality.sh history [--base main] [--branch perf/first-principles] [--out DIR]
#        scripts/perf-rearch/quality.sh gating <rev> [--wasm] [--out DIR]
#        scripts/perf-rearch/quality.sh install     build rust-code-analysis-cli into ~/.cache/perf-rearch/tools/rca
#        scripts/perf-rearch/quality.sh clean       remove the clippy and expansion worktrees and the snapshot caches
#        scripts/perf-rearch/quality.sh help        this text
# - report: one snapshot (and a delta against --base) as markdown on stdout, plus report.json and report.md
#   under ~/.cache/perf-rearch/quality/reports/<rev>[-vs-<base>]/ (or --out). Clippy runs here only
#   (cargo clippy --workspace, lib+bins, the workspace lints) in the reusable detached worktree
#   ~/.cache/perf-rearch/quality/clippy-wt with its own target dir: about 4 min cold, about 1-2 min
#   warm; --no-clippy skips it. The repo-config pass runs on CI's toolchain (ci.yml RUSTUP_TOOLCHAIN),
#   the forced clippy::all audit on stable. --hatches prints the full escape-hatch inventory.
# - Shipped source: the escape-hatch inventory reads rustc's cfg-expanded source (qexpand.py: cargo
#   check --release on CI's toolchain with -Zunpretty=expanded, in the detached worktree
#   ~/.cache/perf-rearch/quality/expand-wt), the default build and the diagnostics,test-hooks build.
#   About 1 min of cargo per build for a new crates/ tree (more while another Cargo run holds the
#   slot), cached by tree under ~/.cache/perf-rearch/quality/expand.
# - gating: diagnostics, verification and test-hook code in the default build (qgating.py rules env,
#   module, topic, dead, name; --wasm adds the wasm32-wasip1 build). Exits 1 on a finding that
#   scripts/perf-rearch/gating-allow.txt does not allow. Lanes run it before reporting.
# - Go-relative (in report, on by default; --no-go-relative skips it): each Rust prod function mapped to
#   its Go counterpart at the pin (`// Go:` markers, then names; gorel.py, Go side scripts/perf-rearch/gocog),
#   ranked as port bloat (Rust cog - Go cog) and own code over cog 25, with per-area totals and, with
#   --base, the rows that moved. About 5 s per new crates/ tree, cached per crates/ tree and Go trees.
#   Go tree: PERF_QUALITY_GO_ROOT (default the 3e9236361fa9 checkout), go: PERF_QUALITY_GO.
#   Markers may name Go struct fields and package vars and consts (gocog lists them; the name matcher
#   does not use them). Ports of Go outside the pin are scored against that Go: GOROOT's src
#   (PERF_QUALITY_GOROOT_SRC, default ~/.local/go/src, go1.27.1) and golang.org/x/text v0.42.0
#   (PERF_QUALITY_XTEXT, default the module cache copy), the versions the pin's go.mod names. A
#   marker that names nothing at the pin is looked up there by its path as written (not in effect/).
#   A tree that is not on disk is skipped, and the report says so.
# - Residue (a column, not a target): the tagless-switch residue of a port row is, over the Go side's
#   `switch` statements with no tag (`switch {`, `switch x := f(); {`, in its function literals too),
#   the sum of (number of clauses - 1), `default` counted as a clause. It is exactly what a Rust
#   if / else-if / else chain for those switches costs more under rca's rules (+1 per `else`; Go's
#   case labels cost 0; the `if` costs what the `switch` does). "Δ less residue" = Δ - min(residue,
#   max(Δ, 0)), shown beside the raw Δ in the rankings and the per-area tables. Targets (Δ >= 10)
#   stay on the raw Δ; the column explains a row. gorel_test.py checks the rule on a Go/Rust pair.
# - ranking: the Go-relative planning page (top N of each ranking, per-area and per-file totals).
# - history: a snapshot at --base and at every first-parent merge of --branch since --base (and its head),
#   each delta against the previous point; no clippy. About 10 s per new crates/ tree, cached.
# - Lanes: run `quality.sh report HEAD --base perf/first-principles` before reporting and paste the summary.
# - Metrics: per-function cognitive and cyclomatic complexity (rust-code-analysis), escape hatches
#   (GOPORT_*/TSC_RS_*/TSGO_* env reads, bool gates, branches), duplication (token windows), churn
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
  if [[ -d "$cache/expand-wt" ]]; then git -C "$here" worktree remove --force "$cache/expand-wt"; fi
  rm -rf "$cache/snap" "$cache/tmp" "$cache/expand"
  ;;
report | history | ranking | gating)
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
