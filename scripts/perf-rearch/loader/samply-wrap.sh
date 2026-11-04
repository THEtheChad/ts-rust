#!/bin/sh
# Wraps an LSP server in samply for ttfd-logs.mjs: SAMPLY_BIN=<server> SAMPLY_OUT=<profile.json.gz>.
exec "${SAMPLY:-$HOME/.local/bin/samply-user}" record --save-only --unstable-presymbolicate -r "${SAMPLY_RATE:-4000}" -o "$SAMPLY_OUT" -- "$SAMPLY_BIN" "$@"
