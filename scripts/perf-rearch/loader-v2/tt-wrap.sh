#!/bin/bash
# usage: TT_BIN=<server> TT_OUT=<file> tt-wrap.sh <args>  (exec's the server, samples thread times)
python3 -I "$(dirname "$0")/tt_sampler.py" $$ "$TT_OUT" </dev/null >/dev/null 2>&1 &
exec "$TT_BIN" "$@"
