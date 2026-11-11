#!/bin/bash
# Lanes B4 and H9: a copy of the parity harness (scripts/perf-rearch) for starve-repro.sh, whose
# parity_lsp.py reads two variables and writes the servers' logs:
#   B4_RS_RUNS   the --rs-runs/--rs-env options of a full run, as JSON (default: the gate's)
#   B4_GO_CACHE  a private Go cache root, so a harness under test never publishes into the shared go-cache
#   and every session runs lspdiff with --server-logs (<run>.server.log: the window/logMessage stream).
# The gate's own parity_lsp.py has none of this; the copy is a scratch tool, never committed.
#
# usage: harness-copy.sh <git ref | scripts/perf-rearch dir> <out dir>
#   prints the copy's scripts/perf-rearch dir (pass it to starve-repro.sh as <harness dir>)
set -euo pipefail
src=$1; out=$2
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
rm -rf "$out"; mkdir -p "$out"
if [ -d "$src" ]; then
  mkdir -p "$out/scripts"
  cp -a "$src" "$out/scripts/perf-rearch"
  rm -rf "$out/scripts/perf-rearch/__pycache__"
else
  git -C "$repo" archive "$src" scripts/perf-rearch | tar -x -C "$out"
fi
h=$out/scripts/perf-rearch
python3 -I - "$h/parity_lsp.py" <<'EOF'
import sys
p = sys.argv[1]
t = open(p).read()
edits = [
    ("RS_RUNS = {'quick': [], 'full': ['--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1']}",
     "RS_RUNS = {'quick': [], 'full': (json.loads(os.environ['B4_RS_RUNS']) if os.environ.get('B4_RS_RUNS') else ['--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1'])}"),
    ("go_dir = HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}'",
     "go_dir = (Path(os.environ['B4_GO_CACHE']) if os.environ.get('B4_GO_CACHE') else HOME / '.cache/perf-rearch/parity/go-cache') / go_sha"),
    ("'--rs', args.rs, '--out', str(sout),", "'--rs', args.rs, '--out', str(sout), '--server-logs',"),
]
for old, new in edits:
    if t.count(old) != 1:
        sys.exit(f'harness-copy: parity_lsp.py: anchor found {t.count(old)} times: {old[:70]!r}')
    t = t.replace(old, new)
open(p, 'w').write(t)
EOF
echo "$h"
