# Close-out of the code-quality phase (lane C2, 2026-11-12)

The code-quality phase ran from `e81a14807` (the end of the perf phase: C2 follow-up merged) to the merged head
`3733fe44b` (`54a2a38d5` plus a plan note; all lanes in, B10 last). This report has the final evidence:
- output is still identical to Go at the pin `3e9236361fa9`;
- every timing is still ≤ Go;
- the quality before and after.

No compiler source changed in this lane. The one tool fix is listed under "Timing".

## Parity

- **Run:** `scripts/perf-rearch/parity.sh full <bin> --base base-current`.
  - bin: `tsgo-head-3733fe44b-rel`, a release build on nightly, as the brief says, under the heavy-run rule.
  - base: `b8-e2941610d-full`.
  - out: `~/.cache/perf-rearch/parity/runs/c2close-3733fe44b-full`.
- **Result: PARITY OK.**

  > compared 188,055 items (186,324 base items that a full run must have): 0 lost, 0 not run, 0 known-bad not as
  > pinned, 0 new items not good, 0 crashes on good items, 36 grandfathered, 0 stale grandfather entries.

| tier | result |
|---|---|
| cli | 179 MATCH |
| tests | 140,037 PASS, 1,731 IGNORED (141,768 names) |
| lsp | 3,316 MATCH, 29 GO-BROKEN |
| api | 73 MATCH |
| fuzz | 29,955 MATCH, 8 GO-BROKEN, 1 JS-AGREES, 36 DIFF (all grandfathered: Go and tsc-rs both time out on deep nesting, or tsc-rs reproduces Go's crash #65078/#65079 on purpose) |
| corpus | 12,727 MATCH |

**New items since base-current** (37, all good):
- **api, 10 MATCH:**
  - B10: `ms-paths-fold-insensitive`, `ms-imports-fold-insensitive` and `ms-exports-fold-insensitive`, plus the
    controls `ms-paths-fold-sensitive` and `ms-imports-fold-sensitive`;
  - B9: `batch-page-cap-int64`, `format-insertion-past-int32`, `positions-index-int64`,
    `raw-options-target-int32` and `typeacq-empty-include`.
- **cli, 6 MATCH:**
  - F5: `resolution-mode-source-phase/cts/{noemit,emit,decl}`;
  - B9: `buildinfo-int-range/code-int32/{noemit,emit,incr}`.
- **tests, 21 PASS:** B9's `go_baselines` int-width and build-info tests, such as
  `api_session_int_width_test::format_node_for_insertion_position_past_i32` and
  `build_info_corrupt::file_id_past_int32_panics_index_out_of_range`.

No item left MATCH for a referee status in this run: GO-BROKEN 0, GO-PARTITION 0 and JS-AGREES 0 newly good.

Root moves `base-current` to this run.

TIMING_SECTION

## icount, phase start against end

- **Method:** `icount.py` counts user-space instructions. Both sides are `--profile goport` builds on rustc 1.95.0
  (`tsgo-e81-e81a14807-goport`, `tsgo-head-3733fe44b-goport`) with fresh lib blobs (`lib-blobs.sh stale`: clean at
  both revisions).
- **Settings:** a plain program (`--incremental false --composite false --pretty false`), and emit to a scratch
  `--outDir`. 3 runs per side, interleaved.
- **Script:** `scripts/perf-rearch/cleanup/icount_ab.sh` plus a `noemit-mt` scenario (the copy is in the lane dir,
  `~/.cache/perf-rearch/closeout/icount_ab4.sh`). Data: `~/.cache/perf-rearch/closeout/icount.tsv`.

| project | scenario | e81a14807 median (min..max) | head median (min..max) | Δ |
|---|---|---|---|---|
| query | noemit-st | 0.6837G (0.6836G..0.6837G) | 0.6837G (0.6837G..0.6837G) | +0.009% |
| query | emit-st | 0.7193G (0.7193G..0.7193G) | 0.7194G (0.7194G..0.7194G) | +0.015% |
| query | noemit-mt | 0.7772G (0.7766G..0.7774G) | 0.7770G (0.7764G..0.7778G) | -0.019% |
| query | emit-mt | 0.8181G (0.8158G..0.8182G) | 0.8180G (0.8164G..0.8186G) | -0.015% |
| umami | noemit-st | 16.4933G (16.4932G..16.4950G) | 16.4958G (16.4957G..16.4958G) | +0.015% |
| umami | emit-st | 19.0088G (19.0085G..19.0088G) | 19.0177G (19.0176G..19.0178G) | +0.047% |
| umami | noemit-mt | 19.5639G (19.5056G..19.5801G) | 19.6265G (19.5252G..19.6579G) | +0.320% |
| umami | emit-mt | 22.3403G (22.3139G..22.3407G) | 22.2767G (22.2499G..22.3011G) | -0.285% |
| hono | noemit-st | 2.1402G (2.1402G..2.1403G) | 2.1390G (2.1389G..2.1390G) | -0.055% |
| hono | emit-st | 2.8544G (2.8544G..2.8544G) | 2.8541G (2.8540G..2.8541G) | -0.010% |
| hono | noemit-mt | 2.9374G (2.9363G..2.9387G) | 2.9349G (2.9339G..2.9366G) | -0.082% |
| hono | emit-mt | 3.7026G (3.7023G..3.7030G) | 3.6994G (3.6986G..3.6995G) | -0.086% |

- **Single-threaded: every cell is within +0.3%.** The largest is umami emit at +0.047%; hono is −0.01% to
  −0.06%.
- **Multi-threaded:** these counts include the work of waiting, which depends on timing.
  - umami noemit is +0.32%. Its run ranges overlap (e81 19.506..19.580G, head 19.525..19.658G), and its
    single-threaded twin is +0.015%. That is umami's known default-thread variation of about ±0.7%, not a cost.
  - umami emit is −0.29%. Every other multi-threaded cell is −0.02% to −0.09%.

## Quality, phase start against end

- **Run:** `quality.sh report HEAD --base e81a14807` and `quality.sh ranking HEAD --top 60`, both under the
  heavy-run rule.
- **Output:** `~/.cache/perf-rearch/closeout/quality-report/` and `quality-ranking.md`. There is also a
  `--no-clippy` snapshot of `e81a14807` in `quality-e81/`.
- **Baseline:** the phase-start baseline `q0-f853c4dea` measures the same `crates/` tree as `e81a14807`;
  `f853c4dea` is a docs commit on top of it.

| measure | phase start (`q0-f853c4dea` = `e81a14807`) | head `3733fe44b` |
|---|---|---|
| prod functions | 21,871 | 22,294 |
| cognitive > 25 | 713 | **541** |
| cognitive > 50 | 218 | 174 |
| cognitive max / sum | 509 / 93,244 | 456 / 86,980 |
| cyclomatic > 25 | 408 | 348 |
| port bloat: rows with Δcog ≥ 10 | 166 | **10** |
| of those, Δ less residue ≥ 10 | 145 | 4 |
| Δcog ≥ 25 | 26 | 1 |
| Σ positive Δcog | 7,084 | 4,135 |
| own code over cognitive 25 | 117 (Σ excess 1,845) | **0** |
| duplication | 3.58% (17,099 lines, 698 blocks) | **3.16%** (15,033 lines, 637 blocks) |
| clippy, repo config (CI toolchain 1.95.0) | 1 in q0 (`deprecated`), 0 when re-measured on 1.95.0 | **0** |
| clippy, `--force-warn clippy::all` (stable 1.99.1) | 2,120 | **1,132** |
| env names read in shipped code | 21 in q0 (old cfg parser); 19 by rustc's expanded source on the same tree | **11** |
| of them, switches | 8 | 4 |
| unresolved `// Go:` markers (name nothing in any Go tree) | 1,257 | 1,153 |
| hygiene (prod) | unwrap 265, expect 621, panic! 551, TODO 237, allow attr 124 | unwrap 262, expect 620, panic! 541, TODO 233, allow attr 136 |
DUPS_ROW

The 10 port-bloat rows with Δcog ≥ 10 at the head (Rust cognitive minus Go's, closures and splits summed into
their row):

| Δcog | Δ less residue | residue | Rust cog | Go cog | Rust function | Go function |
|---:|---:|---:|---:|---:|---|---|
| +34 | +5 | 29 | 103 | 69 | `checker/inference_p1.rs:1784` infer_to_template_literal_type (+ closure choose_template_literal_placeholder_type 39) | `checker/inference.go:577` inferToTemplateLiteralType |
| +17 | +2 | 15 | 156 | 139 | `checker/utilities_p1.rs:847` compare_types | `checker/utilities.go:418` CompareTypes |
| +14 | +0 | 14 | 18 | 4 | `ls/displaypartswriter.rs:269` classification_for_symbol | `ls/displaypartswriter.go:168` classificationForSymbol |
| +14 | +11 | 3 | 15 | 1 | `frontend/json.rs:1907` json_check_nesting_depth | `std/encoding/json/jsontext/state.go:302` stateMachine.pushObject |
| +13 | +13 | 0 | 66 | 53 | `checker/emit_resolver_p1.rs:1341` create_late_bound_index_signatures (+ split `_locked` 66) | `checker/emitresolver.go:746` CreateLateBoundIndexSignatures |
| +13 | +13 | 0 | 65 | 52 | `ast/node.rs:5169` subtree_facts_of_data | `ast/ast.go:1169` computeSubtreeFacts |
| +10 | +10 | 0 | 149 | 139 | `ls/autoimport/registry.rs:1283` RegistryBuilder::update_indexes | `ls/autoimport/registry.go:796` updateIndexes |
| +10 | +0 | 18 | 74 | 64 | `checker/checker_p34.rs:218` get_type_facts_worker (+ splits 50, 18) | `checker/checker.go:31626` getTypeFactsWorker |
| +10 | +0 | 10 | 31 | 21 | `api/proto.rs:3716` new_type_response | `api/proto.go:1242` newTypeResponse |
| +10 | +0 | 10 | 15 | 5 | `checker/checker_p24.rs:753` get_apparent_type | `checker/checker.go:22191` getApparentType |

- **Rows that remain Δ ≥ 10 once their tagless-switch residue is paid for (4):** json_check_nesting_depth,
  create_late_bound_index_signatures, subtree_facts_of_data and update_indexes. The other 6 rows' Δ is the
  if/else-if cost of Go's `switch {`.
- **Own code over 25:** none (117 at phase start). The largest drops:
  - `effect/layergraph.rs` extract_layer_graph, 145 → 4;
  - `ast/utilities_p1.rs` merge_symbol_ids, 97 → 1;
  - `effect/typeparser/piping_flow.rs` compute_piping_flows, 95 → 10.
- **The largest functions are still Go-sized ports.** structured_type_related_to_worker is cog 456 against
  Go's 641-line function; check_grammar_modifiers is 302; resolve_name is 272.
- **Env names read in the default build (11):**
  - behavior (4): `GOPORT_LAUNCH`, `GOPORT_THP_GUARD`, `TSC_RS_EFFECT` (Effect rules, off by default) and
    `TSGO_EFFECT_API`;
  - tuning (7): `GOPORT_BIND_THREADS`, `GOPORT_EMIT_THREADS`, `GOPORT_MAPPED_THREADS`, `GOPORT_PARSE_THREADS`,
    `GOPORT_RESOLVE_AHEAD_THREADS`, `GOPORT_THP_GUARD_MIB` and `GOPORT_BUILD_VERSION` (compile time).
- **Gone from the shipped build since phase start:**
  - the five `GOPORT_FLOWSKIP_*` tuning reads;
  - `GOPORT_CHECK_VERSION_TABLES`, `GOPORT_TRACE` and `GOPORT_THP_GUARD_DEBUG`, now in the
    diagnostics/test-hooks build only. In total 32 names are read only there.
- **Clippy:** the top `clippy::all` lints still left are needless_return 292, identity_op 182, type_complexity 139,
  too_many_arguments 134 and match_single_binding 120. Fixed since phase start: collapsible_if −409,
  needless_late_init −118, len_zero −64, needless_borrow −49 and assign_op_pattern −40.
- **Churn since phase start:** prod +23,777/−24,257 lines (net −480), test net +3,804, 597 files.

## Correctness fixes found during the phase

The quality phase was meant to be behavior-neutral. Its audits and parity-tier work still found real output
differences from Go, and each was fixed to Go's answer and pinned by a parity item or test. None of them was in
checker logic (`plan.md` B9 notes). Sources: `plan.md` lines ~1530–2505 and the reviews it names.

| lane (merge) | what it was | real differences from Go found and fixed |
|---|---|---|
| B2 (`eaf77bac4`) | API path case sensitivity | Emit took case sensitivity from the OS, not the program's file system (D2). The common source directory folded case on dynamic names; Go emits `outDir/A/x.*` and `outDir/a/y.*` (D1, Go's `CommonDirectoryOfFiles` ported). Follow-on fixes: the source-map `sources` path, the build-info `root`, a TS5056 duplicate-output key, `x.TS` TS6054, and config `include` dropping a case twin. The api tier went from 11 DIFF to all MATCH; 14 pins were removed. |
| B3 (`9a4c34ea6`) | audit of stale pre-pin ports | Audit only: it confirmed D1 and D2 (fixed by B2) and listed 7 suspect call sites. |
| B4 (`9513cf308`) | LSP refresh-count variation | No difference: lspdiff's pacing caused it, and Go coalesces the same refreshes when starved. Go's refresh sequence is pinned by a test; `tsgo` is byte-identical. |
| B5 (`e218fc92b`) | direct tests for B2's risks 8, 9, 11 and 14 | No difference and no production change. The expected answers come from Go probes, and each test fails only with its fix reverted. |
| B6 (`a983b2618`) | Go's generated LSP preferences parser | `maximumHoverLength` 4294967306 wrapped in an `i32` (Go wraps on a 64-bit `int`). Formatter sizes were `i32` (`tabSize` 2^32). `i64` values went through `f64`. Empty and nil slices differed in `MarshalJSONTo`. A 2^62 indent aborted the process, where Go gives a recoverable InternalError. The prefs battery went from 7 DIFF to 0. |
| B7 (`e449b2c7a`) | project-reference mapper and symlinks | No symlink difference, but three base-directory differences: a programmatic `paths` without `pathsBasePath` gave TS2307; automatic types and lib replacement resolved from the host directory; declaration emit wrote `import("../shared/value").V` where Go writes `import("alias").V`. |
| B8 (`d9b0d013a`) | checker workers read the OS disk (`WORKER_FS`) | API emit over a virtual file system read module specifiers, TS2835 and package.json from the disk. `tsc -w` missed a rebuild when a probed-only file was created. Callback-FS builds used the disk (TS2835 against TS2834). The incremental d.ts mtime restore bypassed the host. `outputs_overlap` made stat and realpath callbacks Go never makes. A nested API build could deadlock (Go's `buildMu` ported). |
| B9 (`9429e8a57`) | A1 audit: Go `int` width, nil against empty | API `maxResponseBytesPerPage` was `i32`. An empty `TypeAcquisition.include/exclude` was lost. Float-to-`int32` enum options saturated where Go wraps. `formatNodeForInsertion` panicked at positions ≥ 2^31. The build-info reader wrapped ids and `int32` fields where Go rejects or keeps them. One case is better than Go (the A5 root-range overflow: Go hangs, tsc-rs answers). |
| B10 (`be997ed55`) | one Go port of HasPrefix/HasSuffix | The `deps.rs` copies folded ASCII only, where Go uses Unicode simple folding. With non-ASCII `paths`, package.json `imports` or `exports` on a case-insensitive host, Go picks the non-relative specifier and tsc-rs fell back to a relative path or TS2883. Pinned by 3 api items and an 18-row go1.27.1 unit test. |
| F5 (`8a868f3bf`) | one port per Go function on output paths | `program.rs` had a stale resolution-mode copy without ts#63915: a `.cts` file with `import.source("a")` gave `import("a").X` where Go gives `import("b").X` (cli item `resolution-mode-source-phase`). |
| H7 (`11dcc9470`) | api parity tier (sealed `tsgo --api` sessions) | Harness: it pinned the 14 api differences that B2 fixed, and it fixed a false ERROR in the H5 tripwires (an inherited fd closing after the watch started). |
| H8 (`2e08cbde7`) | lsp parity tier hardening | Harness only: the `initialize` answer is now compared, number parsing is lossless, home writes are seen, and an exec failure no longer counts as GO-BROKEN. No tsc-rs difference (its nondeterminism lead went to B4 and was harness pacing). |
| H9 (`890053a7c`) | lspdiff paced by the server, not the clock | Harness only: notification and ATA-progress barriers. Refresh counts under CPU starvation are now stable on both sides (Go 12 in 8 of 8, tsc-rs 12 in 50 of 50). No tsc-rs difference. |
| C1 (`1d325e66b`) | content-mapper watch flake | Test only: a clock step made the build info older than its config. 4 sibling tests fixed; `tsgo` is byte-identical. |
