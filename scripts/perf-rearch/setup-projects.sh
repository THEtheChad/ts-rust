#!/usr/bin/env bash
# Prepares the perf-rearch benchmark projects under ~/.cache/perf-rearch/projects (PR_PROJECTS overrides):
# each repo at a pinned commit (shallow fetch), dependencies installed without install scripts.
# usage: scripts/perf-rearch/setup-projects.sh [name...]      default: every project below
#        scripts/perf-rearch/setup-projects.sh help
# Logs: <projects>/logs/install-<name>.log. A project whose checkout exists is not fetched again; delete it to redo.
# The pins and configs are in projects.tsv next to this script (name, repo url, sha, tsconfig, installer, notes).
# The benchmark never writes into these trees: bench.sh points every emit and build-info output at a scratch dir.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
[[ ${1:-} == help || ${1:-} == -h ]] && { sed -n '2,8p' "$0"; exit 0; }
root=${PR_PROJECTS:-$HOME/.cache/perf-rearch/projects}
mkdir -p "$root/logs"
checkout() { # dir url sha
  [[ -d $1/.git ]] && return
  git init -q "$1" && git -C "$1" fetch -q --depth 1 "$2" "$3" && git -C "$1" checkout -q FETCH_HEAD
}
install() { # dir installer
  cd "$1"
  case $2 in
    pnpm) pnpm install --frozen-lockfile --ignore-scripts ;;
    # package-lock.json projects: npm ci keeps the lockfile's exact node_modules layout (as scripts/bench-apps does).
    npm) npm ci --ignore-scripts --no-audit --no-fund ;;
    yarn) corepack yarn install --frozen-lockfile --ignore-scripts ;;
    # Hono: prepare-hono-inputs.mjs's bun and flags.
    bun) "${BUN:-$HOME/.cache/perf-rearch/tools/bun-1.2.20-dl/bun-linux-x64/bun}" install --frozen-lockfile \
           --ignore-scripts --backend=copyfile --linker=hoisted --no-progress ;;
    none) : ;;
    *) echo "unknown installer $2" >&2; return 1 ;;
  esac
}
while IFS=$'\t' read -r name url sha cfg inst _notes; do
  [[ $name == \#* || -z $name ]] && continue
  [[ $# == 0 || " $* " == *" $name "* ]] || continue
  repo=${url##*/}
  dir=$root/$repo
  if [[ $url == local:* ]]; then
    dir=$root/$name; [[ -e $dir ]] || ln -s "${url#local:}" "$dir"; echo "$name: local -> ${url#local:}"; continue
  fi
  echo "$name: $url @ $sha"
  checkout "$dir" "$url" "$sha"
  [[ -f $root/logs/installed-$repo ]] && continue
  if (install "$dir" "$inst") >"$root/logs/install-$name.log" 2>&1; then
    touch "$root/logs/installed-$repo"
  else
    echo "install failed: $name ($root/logs/install-$name.log)" >&2
  fi
done <"$here/projects.tsv"

# Fixes that make the configs check as in scripts/bench-apps (nothing else changes).
tools=$HOME/.cache/perf-rearch/tools
# VS Code: its postinstall puts the electron typings in node_modules.
if [[ -d $root/vscode/node_modules && ! -d $root/vscode/node_modules/electron ]]; then
  mkdir -p "$tools/electron" && (cd "$tools/electron" && echo '{ "private": true }' >package.json &&
    pnpm add --ignore-scripts --config.node-linker=hoisted electron@43.7.7 >/dev/null)
  cp -R "$tools/electron/node_modules/electron" "$root/vscode/node_modules/electron"
fi
# T3 Code: the configs without their Effect plugins (scripts/bench-apps/t3code.sh "noeffect"). Go ignores plugins;
# tsc-rs runs the Effect diagnostics built in, so with plugins the two would not do the same work.
if [[ -d $root/t3code/node_modules && ! -f $root/t3code/apps/web/tsconfig.noeffect.json ]]; then
  [[ -f $tools/ts6/node_modules/typescript/lib/typescript.js ]] || (mkdir -p "$tools/ts6" && cd "$tools/ts6" &&
    echo '{ "private": true }' >package.json && pnpm add --ignore-scripts typescript@6.0.3 >/dev/null)
  # shellcheck disable=SC2016
  (cd "$root/t3code" && TS="$tools/ts6/node_modules/typescript/lib/typescript.js" node -e '
    const fs = require("fs"), ts = require(process.env.TS)
    const strip = (file, to, extendsFrom, extendsTo) => {
      const json = ts.parseConfigFileTextToJson(file, fs.readFileSync(file, "utf8")).config
      if (json.compilerOptions) delete json.compilerOptions.plugins
      if (json.extends === extendsFrom) json.extends = extendsTo
      fs.writeFileSync(to, JSON.stringify(json, null, 2))
    }
    strip("tsconfig.base.json", "tsconfig.base.noeffect.json")
    for (const p of ["apps/server", "apps/web", "packages/client-runtime", "packages/shared"])
      strip(`${p}/tsconfig.json`, `${p}/tsconfig.noeffect.json`, "../../tsconfig.base.json", "../../tsconfig.base.noeffect.json")
  ')
fi
echo ready
