#!/usr/bin/env bash
# Runs one heavy command under the machine's heavy-run rule (brief.md, "Heavy runs") and blocks until it ends.
#   scripts/perf-rearch/lsp-queued-cancel/heavy.sh <name> <command...>
# Log: ~/.cache/perf-rearch/lsp-queued-cancel/logs/<name>.log, ending DONE or FAIL rc=<N>.
set -euo pipefail
name=$1; shift
logs=$HOME/.cache/perf-rearch/lsp-queued-cancel/logs; mkdir -p "$logs"
log=$logs/$name.log; : > "$log"
cmd=$(printf '%q ' "$@")
systemd-run --user --collect --quiet --slice=app-batch.slice --nice=10 --property=TasksMax=24000 \
  --unit="lqc-$name-$$" --setenv=PATH="$PATH" --setenv=HOME="$HOME" \
  --setenv=TS_CARGO_JOBS="${TS_CARGO_JOBS:-16}" --setenv=CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER=env \
  ${TS_CARGO_NIGHTLY+--setenv=TS_CARGO_NIGHTLY=$TS_CARGO_NIGHTLY} \
  ${TS_CARGO_INCREMENTAL+--setenv=TS_CARGO_INCREMENTAL=$TS_CARGO_INCREMENTAL} \
  ${RUSTUP_TOOLCHAIN+--setenv=RUSTUP_TOOLCHAIN=$RUSTUP_TOOLCHAIN} \
  --working-directory="$PWD" \
  flock /home/agent/.cache/ts-rust-heavy.lock bash -c "$cmd >>'$log' 2>&1; rc=\$?; if [ \$rc = 0 ]; then echo DONE >>'$log'; else echo FAIL rc=\$rc >>'$log'; fi"
until tail -n1 "$log" | grep -qE '^(DONE|FAIL rc=[0-9]+)$'; do sleep 5; done
tail -n1 "$log"
