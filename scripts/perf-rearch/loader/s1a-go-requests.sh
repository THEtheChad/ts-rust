#!/usr/bin/env bash
# Runs the Go LSP oracle through load-dump-session.mjs on each scenario of an s1a-dumps.sh
# run (same scenario, roots and files) and compares the server requests and diagnostics of
# the tsc-rs runs (S1a on, off, serial) with Go (compare-requests.py).
# usage: s1a-go-requests.sh <s1a-dumps out dir> <go out dir>
set -u
D=$1; O=$2; mkdir -p "$O"
here=$(cd "$(dirname "$0")" && pwd)
G=${GO_LSP:-$HOME/.local/bin/tsgo-lsp-oracle-3e9236361fa9}
for j in "$D"/*-0a.json; do
  n=$(basename "$j" -0a.json)
  mapfile -t f < <(python3 -I -c '
import json, os, sys
d = json.load(open(sys.argv[1]))
print(d["scenario"]); print(d["root"]); print(d.get("root2") or "")
print(os.path.relpath(d["file"], d["root"]))' "$j")
  S1A_FILE=${f[3]} node "$here/load-dump-session.mjs" "$G" "$O/$n" "${f[0]}" "${f[1]}" ${f[2]:+"${f[2]}"}
  echo "== $n: $(python3 -I "$here/compare-requests.py" "$O/$n.json" "$D/$n-1a.json" "$D/$n-0a.json" "$D/$n-serial.json" | sed "s|$D/||" | tr '\n' ' ')"
done
