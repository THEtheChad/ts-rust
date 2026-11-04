#!/usr/bin/env bash
# The server A1_REAL_BIN with the synthetic reuse off (GOPORT_REUSE_SYNTHETIC=0): the same-built
# "before" side of an A/B (perf/first-principles A1).
GOPORT_REUSE_SYNTHETIC=0 exec "$A1_REAL_BIN" "$@"
