# perf/first-principles: plan and log

Goal and rules: `brief.md`. Root keeps this file.

## Phase 1 (started 2026-11-03)

| Lane | Kind | Output |
|---|---|---|
| baseline | measure | `scripts/perf-rearch/bench*`, gap table vs Go, user-space samply, first profiles |
| parity | harness | `scripts/perf-rearch/parity*`: local gate vs Go (CLI, emit, LSP, tests), JS tsc cross-check |
| audit-frontend | read-only | scanner, parser, AST, factory, lib blobs |
| audit-program | read-only | program load, module resolution, file system, binder |
| audit-checker | read-only | checker types, relations, instantiation, caches, parallel checking |
| audit-emit | read-only | emitter, printer, declaration emit, source maps, output I/O |
| audit-lsp | read-only | session, snapshots, per-edit rebuild, dispatch, language service |

Phase 2: root merges the audits and the gap table into a ranked list.
An Astra (`gpt-6-astra`, high) review of that list runs before any
implementation. Implementations follow, each one measured and reviewed.

## Decisions

- 2026-11-03: upstream's code-shape parity rule and the repo's maintainer
  process (batches, `docs/typechecker-state`, remote hosts) don't apply on this
  branch (Chad). Behavior parity against Go at the pin is the bar.
