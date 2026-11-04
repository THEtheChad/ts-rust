#!/usr/bin/env bash
# An LSP server command for probe-edit.mjs that counts the server's user-space instructions
# (scripts/perf-rearch/icount.py). ICOUNT_REAL is the server binary, ICOUNT_OUT the file that
# gets the server's stderr and icount's "user instructions: N" line.
#   ICOUNT_REAL=<tsgo> ICOUNT_OUT=<file> node probe-edit.mjs scripts/perf-rearch/a1/icount-server.sh <project> ...
exec python3 -I "$(dirname "$0")/../icount.py" "$ICOUNT_REAL" "$@" 2>>"$ICOUNT_OUT"
