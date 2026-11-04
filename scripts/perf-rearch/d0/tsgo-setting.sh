#!/bin/sh
# D0-1A (checker partition): runs tsgo under one checker setting.
#
# Usage: tsgo-setting.sh SETTING TSGO [tsgo args...]
#
# Settings (all existing switches; no code change):
#   c1, c2, c8, c16  --checkers N
#   c4               no flag: the default (Go's 4 checkers)
#   fast             --fast (more checkers, lazy members, never order, arenas)
#   faststeal        --fast with GOPORT_FAST_STEAL=1 (work-stealing check pass)
#   fastnolazy       --fast with GOPORT_LAZY_MEMBERS=0 and GOPORT_FAST_NEVER_ORDER=0
#                    (the checker split changes; the other two fast parts are off)
#
# The variables are set here, not by the caller: the drivers strip GOPORT_* from the environment.
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
  fast) exec "$bin" --fast "$@" ;;
  faststeal) exec env GOPORT_FAST_STEAL=1 "$bin" --fast "$@" ;;
  fastnolazy) exec env GOPORT_LAZY_MEMBERS=0 GOPORT_FAST_NEVER_ORDER=0 "$bin" --fast "$@" ;;
  *) echo "tsgo-setting.sh: unknown setting $setting" >&2; exit 2 ;;
esac
