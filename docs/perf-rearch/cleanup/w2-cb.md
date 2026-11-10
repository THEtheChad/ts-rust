# W2-CB: checker_p01–p19, grammarchecks_p1–p3, jsx_p1–p2

Lane W2-CB of wave 2 (brief: `w2-brief.md`). Base `f59c229c5`
(`perf/first-principles`). The targets come from
`quality.sh ranking HEAD --top 300` at the base: every port-bloat row with
Δ ≥ 10 and every own-code row over 25 in this lane's files, largest excess
first. Cognitive figures are rust-code-analysis sums (closures included),
Rust / Go, as `quality.sh` counts them.

## Shared helpers (checker_p01)

Go's `core.IfElse`, `core.OrElse`, `core.Some`/`Every`/`Find`/`Filter`/`Map`
are calls, so they add nothing to Go's cognitive count. Written out in Rust
as `if c { a } else { b }` or as a loop with a flag and `break`, each one
costs 2 to 4 plus nesting. That was most of the bloat in this lane. The
fix is to write them as Go does:

- `if_else(b, when_true, when_false)` (Go `core.IfElse`) in `checker_p01`.
  Both values are evaluated, as in Go, so it is used only with arguments
  that have no side effects (diagnostic messages, flags, handles and values
  already computed).
- `or_else_node`, `or_else_symbol`, `or_else_type`, `or_else_signature`
  (Go `core.OrElse`, one per handle type) in `checker_p01`. The per-file
  copies in `checker_p04` and `checker_p05` were removed. `checker_p22`
  (W2-CC) keeps `or_else_type_p22`.
- `core.Some`/`Every`/`Find`/`Filter`/`Map` become `any`/`all`/`find`/
  `filter`/`map` with Go's closure bodies.

All the helpers are `#[inline(always)]`. The ranking sums the four
`or_else_*` into one row against `core.OrElse` (8 against 1).

## Targets

| # | Function | File | Before Rust / Go | After | What | Commit |
|---|---|---|---|---|---|---|
| 1 | check_member_for_override_modifier_worker | checker_p05 | 81 / 41 | 41 | `if_else` | 88ad47436 |
| 2 | check_alias_symbol | checker_p08 | 133 / 96 | 102 | `or_else_*`, `if_else`, `is_none_or` for Go's nil test | 0ea979b7e |
| 3 | create_union_of_signatures_for_overload_failure | checker_p11 | 44 / 16 | 4 (+4 part) | calls the existing `min_and_max` port (it was inlined); `MapNonNil` as iterators; the per-position closure moved to `overload_failure_parameter_at` (part of Go's function) | aa274a839 |
| 4 | check_export_assignment | checker_p06 | 75 / 49 | 51 | `if_else` | 31ccfa7f5 |
| 5 | invocation_error_details | checker_p11 | 58 / 33 | 33 | `if_else` | 7255453a0 |
| 6 | check_array_literal | checker_p09 | 56 / 37 | 41 | `or_else_type`, `if_else`. The rest is Go's two `switch`es written as `if` chains | 8e15e05d0 |
| 7 | instantiate_type_with_single_generic_call_signature | checker_p09 | 36 / 17 | 17 | calls the existing `has_overlapping_inferences`/`merge_inferences` ports (they were inlined); the origin bookkeeping moved to `concatenate_inferred_type_parameters` (own, 1) | 983ea53f9 |
| 8 | try_merge_union_of_object_type_and_empty_object | checker_p15 | 34 / 16 | 16 | Every/Find as iterators, `if_else` | 8c7b6f747 |
| 9 | is_deprecated_symbol | checker_p16 | 25 / 8 | 8 | Some/Every as iterators | 7a2ac8c6e |
| 10 | get_argument_arity_error | checker_p11 | 59 / 43 | 52 | Some, `if_else`. The rest (Δ 9) is Go's four `switch`es written as `if` chains | e05e64ef9 |
| 11 | check_yield_expression | checker_p12 | 39 / 23 | 23 | `or_else_*`, `if_else` | 2f6f33af5 |
| 12 | check_index_constraint_for_index_signature | checker_p06 | 33 / 17 | 17 | `or_else_node`, Some; the repeated `getParentOfSymbol(getSymbolOfDeclaration(d)) == s` became `is_declared_in` | 113856574 |
| 13 | infer_type_arguments | checker_p11 | 62 / 47 | 45 | Every/Some, `if_else`; the return-mapper write goes through `InferenceContext::set_return_mapper` (which keeps the rare box absent when clearing, as before) | e294a3c4e |
| 14 | check_index_constraint_for_property | checker_p06 | 39 / 24 | 27 | as #12 | 113856574 |
| 15 | has_correct_arity | checker_p10 | 38 / 23 | 28 | `NodeSlice::last` for Go's LastOrNil, `if_else`. The rest is Go's `switch` | ffb30fc6a |
| 16 | is_const_type_variable | checker_p15 | 25 / 10 | 2 + 14 (walk) | the constvar1 memo moved into `is_const_type_variable_memoized` (own, 3, `#[inline]`); the union arm uses `any` | 8cc029610 |
| 17 | get_union_index_infos | checker_p15 | 18 / 3 | 3 | Every/Map/Some as iterators | 5fd5df4d2 |
| 18 | check_class_like_declaration | checker_p05 | 75 / 61 | 61 | Every, `if_else` | febf01fc4 |
| 19 | get_iteration_types_of_method | checker_p07 | 52 / 38 | 38 | `or_else_type`, `if_else` | 23d1ed013 |
| 20 | elaborate_jsx_components | jsx_p1 | 52 / 38 | 43 | Go's `getInvalidTextualChildDiagnostic` closure became `new_invalid_textual_child_diagnostic` (with its `Rc<RefCell>` cache); the `Rc<dyn Fn>` to `&mut dyn FnMut` adaptation moved to `elaborate_jsx_elaboration_element` (own, 2) | 730d3e0eb |
| 21 | report_object_possibly_null_or_undefined_error | checker_p08 | 22 / 8 | 8 | `if_else` (nested, as Go) | 961a8c5bc |
| 22 | check_js_doc_type_is_in_js_file | checker_p03 | 22 / 9 | 9 | `if_else` | f3dca6c20 |
| 23 | get_cannot_find_name_diagnostic_for_name | checker_p15 | 18 / 5 | 6 | `if_else` | d5cc503f1 |
| 24 | check_identifier | checker_p12 | 84 / 72 | 81 | the PERF chkA handling of Go's `isEvolvingArrayOperationTarget` call moved to `is_evolving_array_operation_target_of_automatic` (own, 2, `#[inline]`). The rest (Δ 9) is Go's two `switch`es written as `if` chains | 172b35af1 |
| 25 | get_narrowed_type_of_symbol | checker_p15 | 69 / 57 | 56 | `position().map_or` for Go's `slices.Index`, `if_else` | 24626f5ef |
| 26 | elaborate_iterable_or_array_like_target_elementwise | jsx_p1 | 50 / 38 | 38 | `or_else_symbol` | 38188090c |
| 27 | get_instantiation_expression_type | checker_p12 | 35 / 23 | 29 (row) | the getInstantiatedSignatures closure uses filter/map for Go's Filter/SameMap. Round 2: the `errorType` pick is Go's `if`/`else` again (Go has no `core.IfElse` there) | 778caa5eb, de34688c3 |
| 28 | report_non_exported_member | checker_p17 | 29 / 17 | 17 | `find` for Go's findInMap, `if_else` | 4a9bdbd16 |
| 29 | check_grammar_variable_declaration_list | grammarchecks_p2 | 25 / 13 | 13 | `if_else` | 0859a8fa5 |
| 30 | get_suggested_import_source | checker_p17 | 18 / 6 | 8 | `if_else` | 51b80394a |
| 31 | get_suggestion_for_symbol_name_lookup | checker_p02 | 14 / 2 | 6 | the spell1 memo (key, lookup, store, reading a kept answer) moved to `global_spelling_suggestion_key`, `kept_global_spelling_suggestion`, `keep_global_spelling_suggestion` and `GlobalSpellingSuggestion::symbol` (own, ≤ 3 each) | 0be2ea0cb |
| 32 | late_bind_member | checker_p18 | 47 / 36 | 36 | `or_else_node` | d4bfcfddd |
| 33 | check_grammar_for_use_strict_simple_parameter_list | grammarchecks_p1 | 35 / 24 | 24 | the in-place statement walk (PERF portonly1) moved into `find_use_strict_prologue_in_list`, marked as Go `binder.FindUseStrictPrologue` | 1cdfe3d2b |
| 34 | discriminate_contextual_type_by_jsx_attributes | jsx_p1 | 23 / 12 | 13 | the two keep/push loops became `filter` with Go's closure bodies | fa03bc4d2 |
| 35 | some_signature | checker_p10 | 18 / 7 | 6 | `any` | a7931be4e |
| 36 | get_suggested_import_extension | checker_p17 | 12 / 1 | 9 | Round 1 made Go's `switch true` a table (cognitive 0). Round 2 put back the chain of tests: the Go helper idioms rule keeps a tagless switch as a chain, and its residue (Δ 8) is accepted. The `.tsx` case keeps `if_else` for Go's `core.IfElse` | 1d5410828, b121947b5 |
| 37 | check_variable_like_declaration | checker_p07 | 118 / 108 | 106 | `if_else`, Some | 30e256507 |
| 38 | check_array_literal_destructuring_element_assignment | checker_p14 | 33 / 23 | 23 | `if_else`, `or_else_type` | 26e56a09d |
| 39 | get_declaration_file_extension (tspath_p17) | checker_p17 | 10 / 0 | 0 | see duplication | aeb462d87 |

### Own code

| Function | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| xxh3_hash128 | checker_p19 | 35 | 2 | split into zeebo's length classes: `xxh3_hash128_0to16` (6), `_17to240` (15), `_large` (5), all `#[inline(always)]` | 03f04c9ad |
| visit_condition_body_child | checker_p05 | 30 | (summed) | not own code: Go's `visit` closure of `isSymbolUsedInConditionBody`. The doc now says "part of Go `isSymbolUsedInConditionBody`", so the ranking sums it into that row: 30 against Go 36. Same for `visit_binary_expression_chain_child` | a53a4648f |

### The three named functions

Each is a target only for its excess over Go. None has one:

| Function | Rust | Go | Action |
|---|---:|---:|---|
| check_grammar_modifiers (grammarchecks_p1) | 302 | 309 | none |
| resolve_external_module (checker_p17) | 230 | 241 | none |
| check_alias_symbol (checker_p08) | 133 → 102 | 96 | target #2 |

## Duplication

- `checker_p16` and `checker_p17` (`tspath_p17`) each had a copy of the body
  of Go's `getDeclarationFileExtensionFromNormalized`. Both now call
  `declaration_file_extension_of_base_file_name` in `checker_p16` (the
  memchr version, PERF namekeys1), after their own base-name step. The two
  base-name steps differ (p16 takes the program's absolute normalized names;
  p17 also handles `\\` and roots), so they stay. The p17 copy searched
  ".d." with `str::find`; memchr finds the same first index.
- The per-file `core.OrElse` copies in `checker_p04` and `checker_p05` were
  replaced by the shared helpers.
- `quality.sh report` shows 2 new 11-line blocks. Both repeat Go's own
  repetition: the `localIndexDeclaration` lines of the two index-constraint
  checks (`checker_p06`), and the optional-symbol lines of
  `tryMergeUnionOfObjectTypeAndEmptyObject` and `getSpreadSymbol`
  (`checker_p15`). Each pair was already the same in Go. Left as they are.

## Left below the target

The rows still at Δ 8–9 in these files (`check_identifier`,
`get_argument_arity_error`, `resolve_call_expression`, `resolve_call`,
`choose_overload`, ...) are under the Δ ≥ 10 bar. Most of what is left is
Go's `switch` statements written as `if`/`else if` chains. Go counts a
`switch` once; Rust counts each arm.

## Round 2 (Astra: ACCEPT WITH FIXES on b74ac373a)

Astra checked all 61 `if_else` and 36 `or_else_*` uses against Go. Two of
them break root's "Go helper idioms" rule (`w2-brief.md`), and both were
reverted: rows 27 and 36. The branch is rebased onto perf/first-principles
3d4c5edb1, which has W2-CA. The evidence for round 2 is at the end.

## Evidence, round 1 (head c07e169c4 code; base f59c229c5)

- **Lane files, Go-relative** (`quality.sh ranking`): 39 port targets with
  Σ Δ 615 → 0 targets; Σ positive Δ 1,225 → 679; own code over 25: 2
  (excess 15) → 0.
- **Parity full:** `runs/20261109-190643-full-3d2c1c7cba` (release bin of
  this code), PARITY OK. 0 lost, 0 not run, 0 new not good, 0 newly good by
  a referee. cli 170 MATCH, tests 139,910 PASS, lsp 2,629 MATCH + 29
  GO-BROKEN, fuzz 29,955 MATCH + 8 GO-BROKEN + 1 JS-AGREES + 36 DIFF (all
  grandfathered, as in the base), corpus 12,727 MATCH. An earlier full run
  (`20261109-183811-full-3d2c1c7cba`, same bin) had one ERROR:
  `lsp/umami-f12-p6`, "a process of the session touched an unkeyed path:
  ~/.config (close-nowrite)", so its 689 items were not run. The session run
  alone (`runs/w2cb-lsp-umami-f12-p6-rerun`) gave the base status on all 689
  (684 MATCH, 5 GO-BROKEN). The full rerun above was clean.
- **Parity quick** at an earlier head found a regression from this lane:
  `spelling_memo_sees_merge_during_its_scan` (LOST). The first spell1 memo
  split read `merge_version` after the scan, not before. That made an entry
  fresh that a merge during the scan had to leave old. It was fixed inside
  commit 0be2ea0cb and is a PASS in the full run.
- **Tests:** `test --release --workspace`: 2,121 passed, 0 failed. With
  `--features ts_goport/diagnostics,ts_goport/test-hooks`: 2,143 passed, 0
  failed. TS_GO_REPO was the pin checkout.
- **Clippy:** CI's command on 1.95.0 with `-D warnings`, plain and with the
  features: clean. It caught one `obfuscated_if_else` in
  `check_alias_symbol`, which went back to Go's `if`.
- **icount** (`icount_ab.sh`, `--profile goport` builds of f59c229c5 and
  c07e169c4, 3 runs, medians):

  | project | cell | base | head | Δ |
  |---|---|---:|---:|---:|
  | query | noemit-st | 692,377,683 | 692,373,083 | −0.001% |
  | query | emit-st | 728,514,325 | 728,507,033 | −0.001% |
  | query | emit-mt | 825,751,425 | 827,757,816 | +0.243% (5-run recheck: +0.100%, spread 0.12–0.15%) |
  | umami | noemit-st | 16,553,769,826 | 16,552,214,023 | −0.009% |
  | umami | emit-st | 19,081,682,667 | 19,079,400,953 | −0.012% |
  | umami | emit-mt | 22,249,809,758 | 22,227,432,714 | −0.101% (spread 0.5–0.9%) |
  | hono | noemit-st | 2,166,284,305 | 2,166,371,739 | +0.004% |
  | hono | emit-st | 2,876,933,224 | 2,876,878,296 | −0.002% |
  | hono | emit-mt | 3,733,884,805 | 3,734,444,150 | +0.015% |

  Every single-threaded cell is within ±0.012%. The multi-threaded cells
  move with the load (25 to 30 while measured).
