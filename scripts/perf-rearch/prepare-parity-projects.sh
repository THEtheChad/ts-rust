#!/usr/bin/env bash
# prepare-parity-projects.sh: makes the project inputs of the parity CLI tier under
# ~/.cache/perf-rearch/parity-projects/repos/<owner>__<repo>.
#
# usage: scripts/perf-rearch/prepare-parity-projects.sh [name...]   (default: all; help: this text)
#   names: ts-pattern query hono zod effect rhf svelte
#
# The commits are the pins of the repo's scripts/prepare-*-inputs.mjs. Those scripts also pin the
# Node and npm binaries by hash and write only under the main checkout's target/project-inputs, so
# this script makes the same inputs the simple way: a shallow fetch of the pinned commit, then the
# project's own package manager at the version its package.json or prepare script names, with its
# lockfile frozen and no install scripts (npm ci for ts-pattern's package-lock, bun 1.2.20 for hono,
# downloaded and checked against the prepare script's sha256). svelte then runs its type generator
# (packages/svelte/scripts/generate-types.js), as prepare-svelte-input.mjs does. Each install runs
# in a 6 GB systemd scope. An existing project dir is left as it is.
# umami (LSP and CLI) is ~/.cache/lspdiff/projects/umami, from perf/lsp-concurrency.
set -euo pipefail
P=$HOME/.cache/perf-rearch/parity-projects
R=$P/repos
mkdir -p "$R" "$P/logs" "$P/tools"
[[ ${1:-} != help && ${1:-} != -h ]] || { sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0; }

# name|github repo|commit|install command (run in the project dir)
specs=(
  "ts-pattern|gvergnaud/ts-pattern|c92ca435c7e1827e0fd55c539080ef1bfd6fe3f0|npm ci --ignore-scripts --no-audit --no-fund"
  "query|TanStack/query|44645e9eb1dafba5f2f229adb328582075484f36|corepack pnpm@11.9.0 install --frozen-lockfile --ignore-scripts"
  "hono|honojs/hono|06880c4a2b04de9dd74217f26dd831209b9c01f1|$P/tools/bun-dl/bun-linux-x64/bun install --frozen-lockfile --ignore-scripts"
  "zod|colinhacks/zod|43f729db4aa0cedff6d6b3261f33f8556b3c7102|corepack pnpm@10.12.1 install --frozen-lockfile --ignore-scripts"
  "effect|Effect-TS/effect|0d083ba26b2e1afec8d3e8d83db0d05683b6602b|corepack pnpm@11.20.0 install --frozen-lockfile --ignore-scripts"
  "rhf|react-hook-form/react-hook-form|bb3360f4db62489ed4ab4e09ccb4211a275da707|corepack pnpm@11.7.0 install --frozen-lockfile --ignore-scripts && cd app && corepack pnpm@11.7.0 install --frozen-lockfile --ignore-scripts"
  "svelte|sveltejs/svelte|4d5139552dac8593c5e020846fa7ce8e96ea97b7|corepack pnpm@10.33.4 install --frozen-lockfile --ignore-scripts && cd packages/svelte && node scripts/generate-types.js"
)

bun() {
  local d=$P/tools/bun-dl
  [[ -x $d/bun-linux-x64/bun ]] && return
  mkdir -p "$d"
  curl -fsSL --proto =https -o "$d/bun.zip" https://github.com/oven-sh/bun/releases/download/bun-v1.2.20/bun-linux-x64.zip
  echo "4e9edc4cba0c7c1623a288be01e53bbde11a4d073f2cf339cab026627858b548  $d/bun.zip" | sha256sum -c --quiet
  python3 -I -m zipfile -e "$d/bun.zip" "$d"
  chmod +x "$d/bun-linux-x64/bun"
}

want=("$@")
for spec in "${specs[@]}"; do
  IFS='|' read -r name repo commit install <<< "$spec"
  ((${#want[@]})) && [[ " ${want[*]} " != *" $name "* ]] && continue
  dir=$R/${repo/\//__}
  if [[ -e $dir ]]; then echo "$name: $dir exists, left as it is"; continue; fi
  [[ $name != hono ]] || bun
  git init -q "$dir"
  git -C "$dir" remote add origin "https://github.com/$repo"
  git -C "$dir" fetch -q --depth 1 origin "$commit"
  git -C "$dir" checkout -q FETCH_HEAD
  echo "$name: installing ($P/logs/prep-$name.log)"
  systemd-run --user --scope --quiet --collect -p MemoryMax=6G -p MemorySwapMax=0 \
    env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 CI=1 bash -c "cd '$dir' && $install" > "$P/logs/prep-$name.log" 2>&1 ||
    { echo "$name: install failed, see $P/logs/prep-$name.log" >&2; exit 1; }
done
echo DONE
