#!/usr/bin/env bash
# Lane B5: print Go's answers for the direct tests of B3 risks 8, 9, 11 and 14 (the path-case
# fixes of lane B2) at the pin.
#
# usage: scripts/perf-rearch/b5_go_probes.sh [<work dir>]
#        scripts/perf-rearch/b5_go_probes.sh help
#
# Copies go.mod, go.sum, internal/ and cmd/ of the pinned Go checkout
# (~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc, read-only) into <work dir> (default
# ~/.cache/perf-rearch/scratch/b5/go), adds the in-package probe tests of b5_go_probes/ and runs them:
#   project           TestB5OverlayProbe          overlayFS.GetAccessibleEntries (risk 14)
#   module            TestB5RealpathProbe         getOriginalAndResolvedFileName (risk 11)
#   modulespecifiers  TestB5ModuleSpecifierProbe  comparePathsByRedirect, getAllModulePaths,
#                                                 GetModuleSpecifier (risk 8),
#                                                 packageJsonPathsAreEqual (risk 9)
# The answers go to stdout. The Rust tests that hold them:
#   goport_core modulespecifiers::util::tests::redirect_sort_direction_like_go,
#   goport_core modulespecifiers::specifiers::tests::dynamic_redirect_module_path_order_like_go,
#   go_baselines units_emit::module_tests::test_module_specifier_of_redirects_differing_only_in_case,
#   go_baselines units_emit::module_tests::test_project_relative_specifier_across_packages_differing_only_in_case,
#   go_baselines units_emit::module_tests::test_realpath_differing_only_in_case_like_go,
#   go_baselines project_lsp::overlayfs_test::overlay_entries_fold_names_like_go.
set -euo pipefail
if [[ "${1:-}" == help || "${1:-}" == -h || "${1:-}" == --help ]]; then
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit 0
fi
go_repo="$HOME/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc"
work="${1:-$HOME/.cache/perf-rearch/scratch/b5/go}"
probes="$(cd "$(dirname "$0")" && pwd)/b5_go_probes"
case "$work" in "$go_repo"*|"$HOME/.cache/perf-rearch/projects"*) echo "refusing to write into $work" >&2; exit 2;; esac
mkdir -p "$work"
cp -r "$go_repo/go.mod" "$go_repo/go.sum" "$go_repo/internal" "$go_repo/cmd" "$work/"
chmod -R u+w "$work"
for pkg in project module modulespecifiers; do
  cp "$probes/$pkg/"*_test.go "$work/internal/$pkg/"
done
cd "$work"
for probe in "project TestB5OverlayProbe" "module TestB5RealpathProbe" "modulespecifiers TestB5ModuleSpecifierProbe"; do
  set -- $probe
  echo "== $1 $2"
  GOFLAGS=-mod=mod go test "./internal/$1" -run "^$2\$" -count=1 -v | grep -v -e '^=== RUN' -e '^--- PASS' -e '^PASS$' -e '^ok '
done
