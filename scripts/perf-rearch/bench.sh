#!/usr/bin/env bash
# perf-rearch timing tool: CLI and LSP scenarios, tsc-rs binaries against the Go oracle, load-gated and interleaved.
# usage: scripts/perf-rearch/bench.sh cli|lsp|table|help [options]   (bench.sh help: every option and the method)
# Long runs: wrap in systemd-run --user --collect (see scripts/perf-rearch/README.md); results go to --out DIR.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
exec python3 -I "$here/bench.py" "$@"
