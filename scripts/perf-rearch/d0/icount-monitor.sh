#!/bin/sh
# D0-1B: user-space instruction counts of the monitor: base (no monitor), monitor off, monitor on.
# usage: icount-monitor.sh BASE_BIN MONITOR_BIN OUT_DIR PROJECT_DIR [tsgo args...]
# Runs each side 3 times, interleaved, single-threaded (GOPORT_LAUNCH=0), on an overlay of PROJECT_DIR so
# the project never changes. Prints one line per run: side, run, instructions.
set -eu
base=$1 mon=$2 out=$3 proj=$4
shift 4
here=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$out"
for k in 1 2 3; do
  for side in base off on; do
    case $side in base) bin=$base ;; *) bin=$mon ;; esac
    s="$out/ov-$side-$k"
    rm -rf "$s"; mkdir -p "$s/upper" "$s/work"
    ev=""
    [ "$side" = on ] && ev="GOPORT_PARTITION_MONITOR=$out/events-$k.jsonl"
    n=$(env -u GOPORT_PARTITION_MONITOR GOPORT_LAUNCH=0 $ev python3 -I "$here/icount.py" \
      unshare -rm -- sh -c 'mount -t overlay overlay -o "lowerdir=$1,upperdir=$2,workdir=$3" "$1" && cd "$1" && shift 3 && exec "$@"' \
      ov "$proj" "$s/upper" "$s/work" "$bin" --singleThreaded "$@" 2>&1 >/dev/null | sed -n 's/^user instructions: //p')
    echo "$side $k $n"
    chmod -R u+rwx "$s" 2>/dev/null || true; rm -rf "$s"
  done
done
