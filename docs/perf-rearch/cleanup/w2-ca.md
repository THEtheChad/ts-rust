# W2-CA: relater, inference, flow, flow_skip, nameresolver, checker utilities, mapper

Lane W2-CA of wave 2 (code-quality phase). Brief: `w2-brief.md`. Base
`perf/first-principles` @ `f59c229c5`. Files: `checker/relater_p1..p5.rs`,
`inference_p1..p2.rs`, `flow_p1..p3.rs`, `flow_skip.rs`, `nameresolver.rs`,
`utilities_p1..p2.rs`, `mapper.rs`.

Figures are cognitive complexity from `quality.sh ranking` (Rust / Go, Δ).
"Before" is `f59c229c5`.

## Metric artifacts

Two Go idioms score lower than their natural Rust spelling, so part of many
Δs is not bloat. This lane leaves them as idiomatic Rust and names them where
they make up the rest of a Δ:

- **Tagless `switch`:** Go's `switch { case a: … case b: … }` scores +1 +
  nesting once; the Rust else-if chain scores +1 per `else`.
- **`core.IfElse(c, a, b)`** scores 0; `if c { a } else { b }` scores
  +1 + nesting, +1 for the `else`.

## Port bloat targets

In order of excess at the base. "Left" names what makes up the rest of the Δ.

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| get_inferred_type | inference_p2 | 107 / 55 (+52) | 76 / 55 (+21) | `core.Some`/`core.Every` were explicit loops with flags and breaks: iterator any/all over the same snapshots; the nil default is one if-expression. Left: `core.IfElse` (map closure, filter and fallback choices). | 1c0d4d519 |
| infer_to_template_literal_type | inference_p1 | 129 / 83 (+46) | 108 / 69 (+39) | Go's named closure `choose` lifted into `choose_template_literal_placeholder_type` (marker `inference.go:614 choose`, summed back; the Go side takes its standalone figure). Left: the 28-arm else-if chain for Go's tagless switch. | 2ef53213f |
| structured_type_related_to_worker | relater_p4 | 498 / 457 (+41) | 469 / 457 (+12) | Generic-mapped-target case: the indexed access parts were copied into tuples and matched before testing; `#[inline] indexed_access_parts` and let-chains give Go's single tests; `core.OrElse` is `Option::unwrap_or`. The template literal match (perffu1 early-out, clone) is `is_type_matched_by_template_literal_target` (`#[inline]`). Left: else-if chains for Go's switches. | 158284c87 |
| is_deeply_nested_type | relater_p1 | 64 / 29 (+35) | 35 / 29 (+6) | Two full copies of the scan: the plain one is gone; the keyed scan takes keys for a prefix of the stack (the rest are `RecursionKey::NONE`, Go's identity test) and `is_deeply_nested_type` calls it with none. | 3c4a25875 |
| infer_from_matching_types | inference_p1 | 46 / 22 (+24) | 26 / 22 (+4) | Four inline copies of `core.AppendIfUnique` (Go loop and identmatch1 copy): one `#[inline] append_if_unique`; the copy's matcher choice is an `#[inline]` dispatcher. | 79582a228 |
| compare_types | utilities_p1 | 161 / 139 (+22) | 156 / 139 (+17) | Go's `castTypeMapper[CompositeTypeMapper]` was a match closure: `TypeMapper::as_composite_type_mapper` in mapper.rs, beside the other casts, same panic text. Left: the 16-arm else-if chain for Go's switch (+15) and Go's `else if c := …; c != 0` (+3). | 373d7f2ed |
| property_related_to | relater_p5 | 51 / 29 (+22) | 37 / 29 (+8) | Two if-expressions on one test are one tuple choice; `core.OrElse` is `Option::unwrap_or`. | 67ce75850 |
| is_related_to_ex | relater_p3 | 94 / 73 (+21) | 87 / 73 (+14) | Two copies of the reporting `isSimpleTypeRelatedTo` call (`core.IfElse(reportErrors, r.reportError, nil)`): one `#[inline] is_simple_type_related_to_reporting`. Left: `core.IfElse` type choices for messages (+15). | 7f02bc396 |
| compare_signatures_related | relater_p2 | 143 / 123 (+20) | 126 / 123 (+3) | The marker message was two nested if/else pairs on the same two tests: one match on the pair. `core.OrElse` of the rest types is `unwrap_or`. | 934208be0 |
| infer_from_types | inference_p1 | 133 / 114 (+19) | 123 / 114 (+9) | `core.OrElse` candidate is `unwrap_or`; Go's single four-term top-level test is one `if` again (was nested around a copied `original_target`). Left: the else-if chain for Go's switch. Hot: edited in place, nothing moved out. | bf32e9a20 |
| infer_to_mapped_type | inference_p1 | 37 / 18 (+19) | 25 / 18 (+7) | Let-chain for Go's single inference test, `core.Map` as iterator map, `core.OrElse` over the results as `\|=`. Left: two `core.IfElse` values. | b14540bb2 |
| report_relation_error | relater_p5 | 50 / 33 (+17) | 44 / 33 (+11) | Effect-TS patch 004 (no Go counterpart) is `record_effect_relation_error` (own code, 3). Left: else-if chains for three Go switches (+9) and Go's multi-value cases as `\|\|` (+2). | 4d83aaace |
| is_type_identical_in_matching | inference_p2 | 17 / 0 (+17) | 17 / 20 (−3) | Mapping only: the marker named the one-line `isTypeIdenticalTo` wrapper; the body is Go's `isTypeRelatedTo` for the identity relation (its comment said so). | d3422e914 |
| is_simple_type_related_to | relater_p1 | 70 / 54 (+16) | 58 / 54 (+4) | The `PRIMITIVE_RULE_SOURCE` perf guard pushed 14 Go rules a level deeper: the rules are `is_simple_type_related_by_primitive_rules` (`#[inline(always)]`, "part of Go `isSimpleTypeRelatedTo`", summed back), the guard one test at the call. | 0be5bc6ce |
| infer_from_object_types | inference_p1 | 146 / 131 (+15) | 138 / 131 (+7) | The perf pair cache is `types_definitely_unrelated_in_inference` (`#[inline]`, own code); Go's single implied-arity test is a let-chain. Left: `core.IfElse`. | bd7173a2f |
| report_error_results | relater_p5 | 36 / 21 (+15) | 24 / 21 (+3) | `core.Find` loops are iterator `find`. | 39b83eddd |
| infer_types_from_template_literal_type | relater_p3 | 19 / 7 (+12) | 8 / 7 (+1) | The string-literal perf copy of Go's `inferFromLiteralPartsToTemplateLiteral` call is `infer_from_string_literal_to_template_literal` (`#[inline]`, own code 4); `core.MapIndex` is iterator map. | 62328239a |
| narrow_type_by_equality | flow_p1 | 47 / 36 (+11) | left | All +11 is three `core.IfElse(assumeTrue, EQ…, NE…)` inside Go's switch. | — |
| get_variances_worker | relater_p2 | 75 / 65 (+10) | left | +12 is two `core.IfElse` variance bits; the trace handling is 4 cheaper than Go's deferred closure. | — |
| is_enum_type_related_to | relater_p1 | 50 / 40 (+10) | 43 / 40 (+3) | `core.OrElse` on the enum values is `Option::or`. Left: two lazy symbol choices (`core.IfElse` with a call arm; kept lazy). | 54233376a |
| get_tuple_element_label_from_binding_element | relater_p3 | 37 / 27 (+10) | 27 / 27 (0) | `core.LastOrNil` is `NodeSlice::last`, `core.IfElse(rest, 1, 0)` is `i32::from`. | 3a8948c9c |
| get_type_at_flow_node | flow_p1 | 35 / 25 (+10) | left | +9 is the 10-arm else-if chain for Go's switch, +1 the `diagnostics` recording test (rca counts `cfg` code). Hot. | — |
| is_partially_inferable_type | inference_p2 | 13 / 3 (+10) | 4 / 3 (+1) | Go's one `\|\|` chain with iterator any. | 359edeb8e |
| properties_related_to | relater_p5 | 183 / 183 (0) | left | Matches Go line by line; nothing to cut. | — |


Not done on purpose: an `if_else(c, a, b)` helper for Go's `core.IfElse` or `match () { _ if … }` for Go's tagless `switch`. Both would lower the score without changing the code's shape; the Rust spellings stay, and the rows above say how much of each Δ they make up.

## Own-code targets

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
