#!/bin/bash
# usage: TT_BIN=<server> TT_OUT=<file> sm-wrap.sh <args>  (exec the server, record its smaps_rollup)
python3 -I "$(dirname "$0")/smaps_sampler.py" $$ "$TT_OUT" </dev/null >/dev/null 2>&1 &
exec "$TT_BIN" "$@"
