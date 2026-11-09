#!/usr/bin/env bash
# Lane P3: a stable --profile goport tsgo of the checkout <dir>, copied to <out>. Log ends DONE or FAIL rc=N.
# usage: build.sh <checkout dir> <out bin>
set -uo pipefail
cd "$1" || exit 2
export TS_CARGO_JOBS=16 TS_CARGO_NIGHTLY=0 TS_CARGO_INCREMENTAL=0 RUSTUP_TOOLCHAIN=1.95.0
scripts/run-cargo-capped.sh build --profile goport -p ts_goport --bin tsgo
rc=$?
if [[ $rc == 0 ]]; then
  cp target/goport/tsgo "$2" && echo DONE || echo "FAIL rc=cp"
else
  echo "FAIL rc=$rc"
fi
