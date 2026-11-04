#!/usr/bin/env bash
# effect-parity.sh: lane E0 parity check of the Effect switch.
#
# Usage: effect-parity.sh <out-dir> <project-dir> <tsconfig> <name>=<env-prefix>:<bin> [...]
#   Runs, for each side, from <project-dir>:
#     <bin> -p <tsconfig> --noEmit --extendedDiagnostics --tsBuildInfoFile <out>/<name>/out.tsbuildinfo
#   with `env <env-prefix>` (`-` for none, `A=1,B=2` for several) and a fresh
#   build info file (same name on every side, so the files compare).
#   Writes <out>/<name>.out (stdout), <name>.err, <name>.rc and
#   <name>.cmp (stdout without the time, `Memory used` and `Memory allocs`
#   lines of --extendedDiagnostics, which differ between runs and
#   implementations).
#   BUILDINFO=0 drops --tsBuildInfoFile (a config without incremental).
#   PARITY_ARGS adds arguments after <tsconfig> on every side.
#   Then prints, for each later side, whether its .cmp, .rc and build info
#   equal the first side's.
# Example:
#   effect-parity.sh ~/.cache/perf-rearch/e0 ~/.cache/perf-rearch/projects/effect \
#     packages/effect/tsconfig.json go=-:$HOME/.local/bin/tsgo-oracle-3e9236361fa9 \
#     rs=-:target/release/tsgo
set -euo pipefail
if [[ $# -lt 4 || $1 == help || $1 == -h ]]; then
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi
out=$(realpath -m "$1"); project=$2; config=$3; shift 3
mkdir -p "$out"
names=()
for side in "$@"; do
  name=${side%%=*}; rest=${side#*=}; envs=${rest%%:*}; bin=$(realpath "${rest#*:}")
  names+=("$name")
  mkdir -p "$out/$name"; rm -f "$out/$name/out.tsbuildinfo"
  envargs=()
  [[ $envs != - ]] && envargs=(${envs//,/ })
  bi_args=(--tsBuildInfoFile "$out/$name/out.tsbuildinfo")
  [[ ${BUILDINFO:-1} == 0 ]] && bi_args=()
  rc=0
  (cd "$project" && env GOPORT_LAUNCH=0 "${envargs[@]}" "$bin" -p "$config" ${PARITY_ARGS:-} \
    --noEmit --extendedDiagnostics "${bi_args[@]}") \
    >"$out/$name.out" 2>"$out/$name.err" || rc=$?
  echo "$rc" >"$out/$name.rc"
  grep -Ev '^(.* time:? |Memory used|Memory allocs)' "$out/$name.out" >"$out/$name.cmp" || true
  echo "$name: rc=$rc stdout=$(wc -l <"$out/$name.out") lines, stderr=$(wc -c <"$out/$name.err") bytes"
done
first=${names[0]}
for name in "${names[@]:1}"; do
  same() { cmp -s "$1" "$2" && echo same || echo DIFFERENT; }
  bi=absent
  if [[ -f $out/$first/out.tsbuildinfo && -f $out/$name/out.tsbuildinfo ]]; then
    bi=$(same "$out/$first/out.tsbuildinfo" "$out/$name/out.tsbuildinfo")
  fi
  echo "$name vs $first: stdout(w/o time+memory) $(same "$out/$first.cmp" "$out/$name.cmp"), rc $(same "$out/$first.rc" "$out/$name.rc"), stderr $(same "$out/$first.err" "$out/$name.err"), buildinfo $bi"
done
