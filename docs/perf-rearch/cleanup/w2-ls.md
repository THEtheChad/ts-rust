# W2-LS: language service (`ls/`, `autoimport/`, `lsutil/`, `lsconv/`)

Lane W2-LS of wave 2 (code-quality phase). Brief: `w2-brief.md`. Base
`perf/first-principles` @ `b7594852e`. Files: everything under
`crates/ts_goport/src/ls/`.

Figures are cognitive complexity from `quality.sh ranking` (Rust / Go, Δ).
"Before" is `b7594852e`. The two Go idioms that score lower than their Rust
spelling (W2-CA, "Metric artifacts") are left as Rust here too and named where
they make up the rest of a Δ:

- **Tagless `switch`:** Go `switch { case a: … }` against a Rust else-if chain.
- **`core.IfElse(c, a, b)`** against a Rust `if c { a } else { b }`.

No `if_else` helper and no `match () { _ if … }`. `core.OrElse` on nodes is one
helper, `or_else_node` (ls/utilities.rs), because Go calls a helper there too.

## Port bloat targets

In order of excess at the base.

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| get_completion_data (+ its 18 closures) | completions_p1 | 589 / 529 (+60) | 532 / 529 (+3) | `core.Filter`, `core.FirstNonNil`, `core.CheckEachDefined` and `core.SingleElementSlice` were loops with flags: iterator filter/find/any/once in the same order. Go's single `\|\|` chain for the module-export test is one condition again (the name is a `&'static str`, so no split is needed). Go's `range` over the runes of `file.Text()[lineStart:position]` is `go_runes_of_byte_range` (own, 4), so the comment-prefix test is one `all`. Left: `core.IfElse` in five closures. | 512d3790c |
| create_signature_declaration_from_signature | codeactions_missingmemberfixer | 61 / 34 (+27) | 52 / 34 (+18) | The three `core.OrElse(body, fn.Body)` are `or_else_node`. Left: six `core.IfElse` (is-JS choices, the name choices, the method name). | 0a3a3b1d9 |
| get_exhaustive_case_snippets | completions_p4 | 84 / 63 (+21) | 73 / 63 (+10) | The boxed import adder is lent with `as_deref_mut` and a cast instead of a match; Go's `else if value := …; !hasValue(value)` is a `continue` guard, so the literal switch is at Go's depth; `core.MapIndex` is an iterator map. Left: two `core.IfElse` (+8). | fd010ea97 |
| get_js_doc_or_tag | jsdoc | 91 / 74 (+17) | 78 / 74 (+4) | Go's single `prop != nil && prop.ValueDeclaration() != nil && AddIfAbsent` test was two nested ifs (static and base-type cases). Left: the tagless switch. | b60da55e5 |
| get_references_for_this_keyword | findallreferences_p2 | 37 / 23 (+14) | 26 / 23 (+3) | The per-file loop with an immediately called closure is Go's `core.FlatMap`/`core.Filter` as iterators (nodes collected per file before `newNodeEntry`, as in Go); `core.FirstNonNil` is map/find. Left: `core.IfElse` (files, container) and Go's `break` as an else. | 2fe3f7f65 |
| Fix::edits | autoimport/fix | 29 / 15 (+14) | 23 / 15 (+8) | Effect-TS/tsgo patch 018 chose the named and namespace binding names inline (two copies): `Fix::new_binding_name` (own, 2). Left: two `core.IfElse` (+6) and the patch-018 qualifier call (Go has it commented out). | 8f6a38100 |
| classification_for_symbol | displaypartswriter | 18 / 4 (+14) | left | All of it is the 15-arm else-if chain for Go's tagless switch. | — |
| RegistryBuilder::update_indexes | autoimport/registry | 152 / 139 (+13) | 149 / 139 (+10) | Go's `wg.Go` closures return early on `ctx.Err() != nil`; the port nested the extraction under the opposite test (three copies). Left: the port's six `should_stop_build` stop points (two in the second pass's nested loops); they are control flow and can't move out. | f01792f4d |
| get_completions_for_path_mapping | string_completions | 34 / 21 (+13) | 32 / 21 (+11) | A no-op `if !modules.is_empty()` around `extend`. Left: two `core.IfElse` in loops (+8), Go's `strings.TrimPrefix` as a test (+1), the closure's extra `&&`. | 35fcd2f64 |
| get_meaning_from_location | utilities | 22 / 9 (+13) | left | The 11-arm else-if chain for Go's tagless switch and one `core.IfElse`. | — |
| get_quick_info_and_declaration_at_location (+ closures) | hover | 256 / 244 (+12) | 250 / 244 (+6) | `writeSymbol`: Go's tagged `switch meaning` is a match; `core.OrElse` of the declarations is `or_else_node`. Left: two tagless switches in `writeSymbol`. | a1c8314e8 |
| RefState::explicitly_inherits_from | findallreferences_p2 | 15 / 4 (+11) | 4 / 4 (0) | Labeled loops with a flag are Go's nested `core.Some` as iterator any (same order, same early stop). | 71ceb360f |
| dynamic_file_name_to_document_uri | lsconv/converters | 11 / 1 (+10) | removed | The marker named Go's one-line `DynamicFileNameToDocumentUri` wrapper; the body was Go's `dynamicFileNameToDocumentUri` with strict = false (86563b542 fixed the marker). The copy was kept until lsproto had it; `lsp/lsproto/lsp.rs` now ports both (one worker), with the same loose path and panic text, so `file_name_to_document_uri` calls it and the copy goes. | 86563b542, bb7ca4315 |

## Own-code targets

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| type_parsers | lsutil/userpreferences | 74 | 1 | Each entry was an inline closure of nested if-let, match and early returns. The enum parsers are named after the pin's generated `parsePreference<Type>` (`userpreferences_generated.go`, same cases and fallbacks; markers added) over one `lower_string_value`; the Tristate entry is `parse_tristate`. Each is 1. | 9b04e617e |
| type_serializers | lsutil/userpreferences | 33 | 1 | Each entry is its own `serialize_<type>` (≤ 2) with the same interface-conversion panic and strings; `non_empty_string_or_null` for the two module-specifier entries. | 9b04e617e |

## Duplication

`quality.sh report bb7ca4315 --base b7594852e`: 686 → 679 blocks, 16,753 →
16,624 duplicated lines (3.51% → 3.49%). The report lists 3 "new" blocks; all
three were blocks at the base whose lines moved (registry's extraction
goroutines, findallreferences_p1's label and string arms, which Go writes as two
cases too, and jsdoc's static and base-type lookups), not new copies.

| Block | Done | Commit |
|---|---|---|
| hover `write_type_classified` ↔ `write_signature_classified` (printer with the builder's idToSymbol map) | `write_built_node_classified` | 058af04b9 |
| findallreferences_p1 `definition_to_referenced_symbol_definition_info`: five copies of the plain node entry and four of the one-run text element (5 blocks) | `plain_node_entry`, `classified_text_element` (Go writes both as literals) | c272ba252 |
| completions_p1: three inline `core.CheckEachDefined` loops | one `any` each (`get_completion_data` commit) | 512d3790c |
| autoimport/fix two copies of the patch-018 binding name choice | `Fix::new_binding_name` | 8f6a38100 |
| lsconv/converters `dynamic_file_name_to_document_uri` ↔ lsp/lsproto/lsp.rs `dynamic_file_name_to_document_uri_worker` | the ls copy goes; the caller uses lsproto's | bb7ca4315 |
| signaturehelp (4 blocks: the parameter-writing loop of the TS and JS item builders) | left: Go repeats the loop in both functions, and they differ (append to `thisParameter` against an indexed slice) | — |
| autoimport/registry: the three `extractPackage` goroutines | left: Go has the same three copies with different inputs and cache keys; one closure would hold the cache borrow across the fallback reads | — |
| jsdoc `get_js_doc_or_tag` static ↔ base-type member lookup | left: Go repeats it; one case is a loop | — |

Blocks with one side in another lane's file (checker/jsdoc ↔ ls/jsdoc,
format/rules ↔ ls/utilities, ast/utilities_p3 ↔ ls/utilities, gostd/collate ↔
lsutil/organizeimports, checker/services ↔ ls/utilities, …) are left: fixing them
needs a file outside this lane.

`strings_to_lower` has five copies in this lane's files (completions_p1,
lsutil/formatcodeoptions, lsutil/organizeimports, lsutil/userpreferences,
autoimport/index) in three variants (`unicode::to_lower` per char,
`char::to_lowercase().next()`, `go_map_runes`). They differ on invalid bytes
and on Unicode version differences, so merging them is a behaviour change;
left for a lane that owns `gostring.rs`/`frontend/stringutil_ls.rs` too.

## Evidence

All on head `bb7ca4315` (every code commit of this lane), base `b7594852e`.
Logs and bins under `~/.cache/perf-rearch/w2-ls/` (job script `job.sh`, log
`job.log`).

- **Parity** (`parity.sh full`, release bin of `bb7ca4315`, base
  `base-current`): `~/.cache/perf-rearch/w2-ls/parity-full-bb7ca4315`.
  **PARITY OK**: 187,196 items compared, 0 lost, 0 not run, 0 new items not
  good, 0 crashes on good items, 0 newly good by a referee, 36 grandfathered.
  Tiers: cli 170 MATCH; tests 139,910 PASS, 1,731 ignored; **lsp 2,629 MATCH,
  29 GO-BROKEN (in the base)**; fuzz 29,955 MATCH, 36 DIFF (grandfathered), 8
  GO-BROKEN, 1 JS-AGREES (in the base); corpus 12,727 MATCH.
- **Tests** (`run-cargo-capped.sh test --release --workspace`, `TS_GO_REPO` at
  the pin): plain 51 binaries, 2,121 passed, 0 failed, 2 ignored; with
  `ts_goport/diagnostics,ts_goport/test-hooks` 52 binaries, 2,143 passed, 0
  failed, 2 ignored.
- **Clippy** (CI's command on 1.95.0, `-D warnings`): clean, plain and with the
  features. Repo-config clippy in the quality report 0 → 0; the forced
  `clippy::all` audit 1,128 → 1,128.
- **icount, CLI** (`icount_ab.sh`, `--profile goport` builds of both sides made
  here, 3 runs per side, interleaved):

  | Cell | Base | Head | Δ | Spread b/h |
  |---|---:|---:|---:|---|
  | query noemit-st | 683,461,529 | 683,414,093 | −0.007% | 0.030/0.008% |
  | query emit-st | 719,062,810 | 719,066,249 | +0.000% | 0.005/0.002% |
  | query emit-mt | 816,234,164 | 817,931,187 | +0.208% | 0.283/0.241% |
  | umami noemit-st | 16,497,875,812 | 16,497,701,335 | −0.001% | 0.014/0.001% |
  | umami emit-st | 19,013,562,818 | 19,013,257,080 | −0.002% | 0.001/0.002% |
  | umami emit-mt | 22,330,011,917 | 22,281,133,950 | −0.219% | 0.575/0.721% |
  | hono noemit-st | 2,141,353,038 | 2,141,270,974 | −0.004% | 0.018/0.016% |
  | hono emit-st | 2,855,340,048 | 2,855,350,490 | +0.000% | 0.003/0.003% |
  | hono emit-mt | 3,705,452,577 | 3,705,252,188 | −0.005% | 0.023/0.010% |

  Single-threaded cells within ±0.007%; the multi-threaded ones are inside
  their run-to-run spread (they count waits). The CLI runs none of this code.
- **icount, LSP** (`icount_lsp_ab.sh`, umami `edit` session, same bins, 5 runs
  per side, interleaved): base median 9,190,689,437 (spread 0.83%), head
  median 9,174,198,055 (spread 1.68%), −0.18%: inside the noise (the counts
  include the worker threads' spins). The session opens a file, pulls
  diagnostics and edits an import; it reaches the converters but none of the
  completion, hover or reference code, so the parity lsp tier is the check
  for those.
- **Gating** (`quality.sh gating bb7ca4315`): **GATING FAIL**, the same 11
  findings as the base (`quality.sh gating b7594852e` lists the identical
  table): the `program.rs` checker call log (7), `program/reuse_verify.rs`,
  `dts_twin_check`, `synthetic_live_slot_count`, `registered_programs`. None is
  in `ls/`; plan.md assigns them to lane Q6. This lane adds none.
- **Quality** (`quality.sh report bb7ca4315 --base b7594852e`): prod functions
  over cog 25 683 → 679, over 50 212 → 211, sum 91,371 → 91,161. Go-relative:
  port Δ ≥ 10 rows 135 → 128, Δ ≥ 25 17 → 15, port excess 5,867 → 5,728; own
  > 25 140 → 138, own > 50 29 → 28, own excess 2,222 → 2,165. The ls area: port
  targets 13 → 6 (Σ Δ 239 → 76), Σ positive Δ 600 → 461; own targets 2 → 0
  (Σ 57 → 0). Duplication 3.51% → 3.49%. Escape hatches 11 → 11 (switches
  4 → 4); hygiene unchanged.

## Notes for root

- W2-CB's `if_else` helper (`checker_p01.rs`, on its branch) is not in this
  base. Most of the six ls rows left are `core.IfElse` residue
  (create_signature_declaration_from_signature +18, get_exhaustive_case_snippets
  +8 of +10, get_completions_for_path_mapping +8 of +11, Fix::edits +6 of +8);
  they can use it once CB merges, where Go calls `core.IfElse` at that spot.
- `or_else_node` (ls/utilities.rs) is this lane's `core.OrElse` for nodes;
  if CB's `or_else_*` helpers merge with one for nodes, one of the two should
  go.

No switch added or removed.
