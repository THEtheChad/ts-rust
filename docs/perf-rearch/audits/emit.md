# Audit: emit (transforms, printer, declaration files, source maps, output I/O)

Condensed by root from the audit lane's report on 2026-11-03. Full report:
T3 thread "perf-rearch audit: emit/printer". Tools are in
`~/.cache/perf-rearch/scratch/emit-audit/tools/`. Measured on umami at load 5
to 88, so wall numbers are rough (minimums quoted). Output was byte-identical
with Go in every mode compared.

## Where emit runs

- **Plain `tsc -p`:** emit starts only after the whole check. Inside it, a
  file's JS goes to the 12-thread emit pool only if
  `js_emit_needs_checker` is false. Otherwise it runs on that file's
  checker thread (4 checkers), with printing handed to a d.ts "twin" thread
  after a `PrintPack` copy.
- **Incremental, composite and `-b`:** early emit, so each checker emits as
  soon as its own check ends.
- **Writes:** synchronous, as in Go.

## Measurements (umami, tsc-rs against Go)

| Mode | tsc-rs | Go |
|---|---|---|
| **JS only** | **min 0.126 s (typical 0.14–0.22 s)** | **min 0.100 s** |
| **JS + source maps** | **0.151 s** | **0.102 s** |
| **CommonJS, ES2017** | **min 0.179 s** | **min 0.159 s** |
| d.ts only | 0.12–0.145 s | 0.206–0.236 s |
| JS + d.ts + both maps | 0.21–0.31 s | 0.29–0.47 s |
| incremental emit | about 0.14 s | about 0.28 s |

The first three rows (in bold) are where tsc-rs is slower than Go. Without
`verbatimModuleSyntax`, import elision makes the checker the emit resolver,
so JS transforms run on the 4 checker threads while 12 pool threads sit
idle. JS emit time falls with `--checkers` 8 and 16.

## Candidates (ranked)

1. **Early emit for plain `tsc -p`.** Reuse the incremental path's gating
   (`early_emit_options_allow`, `check_cannot_see_outputs`, rules F1–F4).
   - **v1:** only when `declaration` is off, to keep the per-checker order
     check → declaration diagnostics → emit.
   - **Gain:** 0.05–0.1 s on umami (5–8% of wall).
   - **Size:** 100–250 lines, 2–4 days. **Risk:** low.
2. **JS emit off the checker threads, with a precomputed answer table.** On
   the checker, run only `mark_linked_references_recursively` and the alias,
   JSX and enum queries in the transform's own order, recorded per file.
   Then transform, print and write on the pool, with a resolver that panics
   if an answer is missing.
   - **Gain:** JS emit 4-way → about 16-way (umami 0.13–0.22 → 0.07–0.10 s).
     This closes the only mode where tsc-rs is slower.
   - **Size:** 600–1,000 lines, 1.5–2 weeks. **Risk:** medium, because the
     checker calls and their order must stay identical.
   - **Proof:** a dual-run check mode comparing the resolver call log and
     the JS bytes, plus the `--extendedDiagnostics` counts.
   - **Experiment:** compare JS-only emit with and without
     `--verbatimModuleSyntax` at load under 1.5. Kill it if the gain is
     under 30%.
3. **Skip the redundant declaration-diagnostics transform** when emit runs
   anyway: no noEmit, noEmitOnError or blocked output, not incremental, and
   no `--diagnostics`. Today the full d.ts transform runs twice (in Go too).
   - **Gain:** possibly 30–50% of d.ts work; unmeasured.
   - **Risk:** medium, since type creation order during emit could change
     union order in d.ts text.
   - **Experiment:** add one deliberate error and compare
     `--emitDeclarationOnly` times.
4. **Emit phase timers** (`GOPORT_EMIT_PHASE_TIMES`, about 1 day). Then,
   if transforms are over 40% of emit CPU, fuse the type eraser, import
   elision and runtime syntax into one pass (2–3 weeks).
5. **Print-time type erasure for simple files,** with no tree rebuild.
   - **Gain:** large for modern ESM. **Risk:** high (comments, parentheses,
     `export {}`).
   - Do it only after #4's timers, gated by a dual-run check.
6. **Coalesce stdout writes for pipes and terminals.** Matters only with
   many diagnostics; about 1 day.
7. **A movable per-file synthetic arena** (no `PrintPack` copy). Modest
   gain, but it makes #2 cheaper; 2–4 weeks.
8. **Skip the CLI's unused source-map copy** (`raw_source_map`). A few ms,
   about 30 lines.

## Not worth it

- TextWriter, visitor rebuilding (already prunes), emit-context tables.
- File-write syscalls, `go_string_bytes`.
- d.ts emit (already 1.5–2× faster than Go), the incremental path,
  diagnostic formatting.

## First three

#1, #2 (after the experiment), #4's timers.
