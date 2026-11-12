#!/usr/bin/env python3
"""Lane F5: classify the Go functions that have Rust ports in more than one file family.

usage: scripts/perf-rearch/f5/triage.py <dups-before.jsonl> <dups-after.jsonl>
  the inputs are `dups.py <calls.jsonl> --json` at the base and at the head.
Prints a Markdown table: Go function | Rust copies (head) | class | reason, then the Go functions
that are gone at the head (folded). Classes:
  output    a stale copy at the next pin bump can change output (diagnostics, emit, resolution, LSP)
  internal  a layer, accessor or delegation over one port, or our own machinery: no second logic
  test      test-only or tool code
The class and reason come from the first rule that matches the file families of the copies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gorel  # noqa: E402

STRIP = "crates/ts_goport/src/"

# (families that must all be present, class, reason); first match wins
RULES: list[tuple[set[str], str, str]] = [
    ({"frontend/packagejson", "modulespecifiers/packagejson"}, "output",
     "thread-safe copy of the frontend package.json model (Rc vs Arc); PORT notes name both (F5)"),
    ({"frontend/packagejson", "modulespecifiers/host"}, "output",
     "modulespecifiers' package.json cache reads its thread-safe copy (Rc vs Arc)"),
    ({"frontend/stringutil_ls", "modulespecifiers/deps"}, "output",
     "copies differ (Go bytes vs Unicode fold): needs a Go-side probe and a parity item (F5 open item)"),
    ({"execute/tsc/diagnostics", "program"}, "test",
     "program.rs's diagnostic writer serves tests and goport_live_programs; tsc uses execute/tsc"),
    ({"execute/tsc/diagnostics", "ls/lsconv/converters"}, "output",
     "the LSP and tsc message flatteners (2 lines each); one module needs a shared writer trait"),
    ({"execute/tsc/diagnostics", "scanner_util"}, "internal", "file-position wrappers over the scanner port"),
    ({"program", "program/ls_program"}, "internal",
     "the CLI program and the LS program forms: delegations to one checker pool / diagnostics port"),
    ({"execute/tsc/emit", "program"}, "internal", "CompilerProgram trait methods delegating to the program forms"),
    ({"emitter/program_emit", "program/ls_program"}, "internal", "Program.Emit and HandleNoEmitOptions of the two program forms"),
    ({"frontend/compiler/program_lookup", "program"}, "internal",
     "Go Program method on both program forms; the emit-side one delegates to the Go frontend's (with_go)"),
    ({"frontend/compiler/program_new", "program"}, "internal",
     "Go Program method on both program forms; the emit-side one delegates to the Go frontend's (with_go)"),
    ({"frontend/compiler/file_loader", "program"}, "internal", "Go Program method on both program forms (delegation)"),
    ({"frontend/compiler/verify", "program"}, "internal", "Go Program method on both program forms (delegation)"),
    ({"frontend/compiler/project_references", "program/go_frontend"}, "internal",
     "the Go frontend's thread-safe copy of the project reference mapper's answers"),
    ({"frontend/compiler/program_new", "program/go_frontend"}, "internal", "thread-safe copy of a frontend program answer"),
    ({"frontend/compiler/program_new", "modulespecifiers/host"}, "internal", "ModuleSpecifierGenerationHost delegation"),
    ({"frontend/compiler/program_new", "program/ls_program"}, "internal", "LS program form delegation"),
    ({"frontend/compiler/program_new", "execute/incremental/checker_access"}, "internal", "incremental reads of program facts"),
    ({"frontend/tsoptions/parsed_command_line", "program"}, "internal", "ResolvedProjectReference accessor"),
    ({"ls/autoimport/aliasresolver", "program"}, "internal", "the alias resolver's own Go method of the same name"),
    ({"api/session", "frontend/compiler/program_new"}, "internal", "API accessor"),
    ({"ast/node", "frontend/parser/source_file"}, "internal",
     "SourceFile accessors on the two AST forms (arena Node, ParsedSourceFile)"),
    ({"ast/misc", "astdata/text"}, "internal", "PositionMap in two crate parts (goport_util has no ast)"),
    ({"contentmapper/muxconn", "ipc/conn_async"}, "output",
     "two AsyncConn ports (API over stdio, content mapper mux): protocol behaviour; fold needs one conn type"),
    ({"frontend/module/resolver", "frontend/module/types"}, "internal", "resolver trait wrappers over the resolver port"),
    ({"checker/inference", "checker/relater"}, "output",
     "inference's identity relation for matching (perf: no relation cache); types"),
    ({"checker/flow", "checker/flow_skip"}, "output", "flow_skip is our own pre-pass (perf)"),
    ({"checker/checker", "frontend/tspath/path"}, "output",
     "checker PERF forms of tspath helpers (borrowed slices, no copies); fold needs the frontend API to borrow"),
    ({"checker/checker", "frontend/tspath/extension"}, "output", "checker PERF form of a tspath extension helper"),
    ({"checker/checker", "frontend/module/resolver"}, "output", "checker PERF form of core.Pattern (Go byte offsets)"),
    ({"checker/checker", "frontend/module/util"}, "output", "checker copy of a module/util helper"),
    ({"checker/utilities", "frontend/module/util"}, "output", "checker copy of a module/util helper"),
    ({"checker/utilities", "frontend/tspath/path"}, "output", "checker copy of a tspath helper"),
    ({"checker/utilities", "program"}, "internal", "getPackagesMap: the program builds it once, the checker reads it"),
    ({"binder/binder", "frontend/module/resolver"}, "output", "binder's private core.Pattern copy (ambient module patterns)"),
    ({"binder/binder", "frontend/tspath/extension"}, "output", "binder's private tspath copy"),
    ({"binder/binder", "frontend/tspath/path"}, "output", "binder's private tspath copy"),
    ({"options", "frontend/tspath/extension"}, "output", "options.rs's private tspath copy (crate part boundary)"),
    ({"options", "frontend/tspath/path"}, "output", "options.rs's private tspath copy (crate part boundary)"),
    ({"frontend/stringutil_ls", "frontend/tspath/path"}, "output", "tspath's own string compare (perf form)"),
    ({"frontend/core_ls_ext", "scanner_util"}, "internal", "CompareBooleans in two crate parts"),
    ({"frontend/scanner/regexp", "gostring"}, "output", "regexp scanner's byte forms of the JS string rune helpers"),
    ({"frontend/scanner/comment_ranges", "printer/printer"}, "output", "printer's leading-comment walker (perf form)"),
    ({"printer/printer", "printer/utilities"}, "output", "getLiteralText: a Cow PERF form beside the port"),
    ({"frontend/tspath/path", "lsp/lsproto/lsp"}, "internal", "lsproto crate part's copy (no frontend dependency)"),
    ({"frontend/tspath/path", "sourcemap/source_mapper"}, "internal", "sourcemap's copy of a tspath helper"),
    ({"api/session", "frontend/tspath/path"}, "internal", "API session's copy of a tspath helper"),
    ({"ast/utilities", "binder/binder"}, "output", "binder's copy of an ast utility"),
    ({"ast/utilities", "checker/checker"}, "output", "checker's copy of an ast utility (kind-only form)"),
    ({"binder/binder", "checker/grammarchecks"}, "output", "grammar check's list form of a binder helper"),
    ({"frontend/compiler/resolve_ahead", "project/overlayfs"}, "internal", "resolve-ahead (our own) and the project FS"),
    ({"ls/search_thread", "project/snapshot"}, "internal", "LS search thread's snapshot accessors"),
    ({"ls/import_tracker", "ls/program_view"}, "output", "LS references: program_view's file-level form"),
    ({"frontend/module/resolver", "modulespecifiers/host"}, "output",
     "modulespecifiers' package.json lookups for checker threads (thread-safe HostFsCache), beside the resolver's"),
    ({"checker/checker", "checker/inference"}, "output", "inference's normalization for matching (perf form)"),
    ({"checker/checker", "frontend/core_nodemodules"}, "output", "checker copy of a core helper"),
    ({"checker/utilities", "frontend/tspath/extension"}, "output", "checker copy of a tspath helper"),
    ({"checker/nodebuilder_impl", "modulespecifiers/compare"}, "output", "node builder's copy of a modulespecifiers helper"),
    ({"frontend/tspath/path", "modulespecifiers/symlinks"}, "output", "symlinks' copy of a tspath helper"),
    ({"ast/utilities", "transformers/moduletransforms"}, "output", "module transform's file form of an ast utility"),
    ({"project/dirty"}, "internal", "dirty map layers"),
    ({"execute/build"}, "internal", "build orchestrator layers"),
    ({"execute/execute_tsc", "tracing"}, "internal", "tracing start/stop wrappers"),
    ({"frontend/vfs/osvfs", "tracing"}, "internal", "tracing's own file writes"),
    ({"frontend/vfs/cachedvfs"}, "internal", "cached VFS layers"),
    ({"checker/exports", "checker/fnfields"}, "internal", "checker field accessors"),
    ({"fswatch"}, "internal", "per-OS watcher variants"),
    ({"effect"}, "internal", "Effect (our own) beside an LS port"),
]


def classify(fams: set[str]) -> tuple[str, str]:
    for need, cls, reason in RULES:
        if all(any(f == n or f.startswith(n + "/") for f in fams) for n in need):
            return cls, reason
    return "internal", "same Go name on another type (an interface method or a wrapper); no second logic found by name"


def load(p: str) -> dict[str, dict]:
    return {r["go"]: r for r in (json.loads(x) for x in Path(p).read_text().splitlines() if x.strip())}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 0
    before, after = load(argv[0]), load(argv[1])
    counts: dict[str, int] = {}
    lines = ["| Go function | Rust copies (head) | class | reason |", "|---|---|---|---|"]
    for go, r in sorted(after.items(), key=lambda kv: (-kv[1]["go_cog"], kv[1]["go_name"])):
        fams = {gorel.family(x["file"]).removeprefix(STRIP) for x in r["rust"]}
        cls, reason = classify(fams)
        counts[cls] = counts.get(cls, 0) + 1
        copies = "<br>".join(f"`{x['file'].removeprefix(STRIP)}:{x['start']}` {x['fn'].split('::')[-1]}" for x in r["rust"])
        lines.append(f"| `{r['go_name']}` ({go.removeprefix('internal/')}, cog {r['go_cog']}) | {copies} | {cls} | {reason} |")
    gone = [before[g] for g in before if g not in after]
    print(f"{len(after)} Go functions at the head ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}); "
          f"{len(gone)} of the base's {len(before)} have one file family now.\n")
    print("\n".join(lines))
    print("\nOne file family at the head (folded, or the copies now share a family):\n")
    for r in sorted(gone, key=lambda r: r["go_name"]):
        print(f"- `{r['go_name']}` ({r['go'].removeprefix('internal/')})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
