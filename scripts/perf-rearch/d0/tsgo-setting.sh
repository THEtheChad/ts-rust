#!/bin/sh
# D0-1A (checker partition): runs tsgo under one checker setting.
#
# Usage: tsgo-setting.sh SETTING TSGO [tsgo args...]
#
# Settings (all existing switches; no code change):
#   c1, c2, c8, c16  --checkers N
#   c4               no flag: the default (Go's 4 checkers)
#
# Fast mode (`--fast`, `GOPORT_FAST*`, `GOPORT_LAZY_MEMBERS`) was removed in lane C3, with its
# settings fast, faststeal and fastnolazy.
set -eu
setting=$1
bin=$2
shift 2
case $setting in
  c1) exec "$bin" --checkers 1 "$@" ;;
  c2) exec "$bin" --checkers 2 "$@" ;;
  c4) exec "$bin" "$@" ;;
  c8) exec "$bin" --checkers 8 "$@" ;;
  c16) exec "$bin" --checkers 16 "$@" ;;
  *) echo "tsgo-setting.sh: unknown setting $setting" >&2; exit 2 ;;
esac
