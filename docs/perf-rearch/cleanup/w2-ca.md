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
| is_matching_reference_kinds | flow_p2 | 61 | 13 | Not own code: the body of Go `isMatchingReference` (flow.go:1597, 41; the marker sits on the one-line wrapper). Both Go switches stay; the long arms are helpers: `is_matching_identifier_reference` 8 (memo, `#[inline]`), `is_matching_declaration_reference` 4 (chkA, `#[inline]`), `is_matching_property_access_pair` 2 (flowcost1, `#[inline]`), `is_matching_synthetic_property_access` 1 (synthref1), `is_matching_access_reference` 15 (Go's access case). Summed 43 against Go's 41. | 943d84e34 |
| FileIndex::build | flow_skip | 51 | 0 | Its stages: `Forest::new` 6 (next pointers, nest steps, candidate slots), `Forest::children` 7, `Forest::tour` 14 (Euler tour), `scan_candidates` 6, `Scan::candidate` 4, `Pools::add` 6, `mention_ranges` 4. | c32855f77 |
| flow_skip_check | flow_skip | 48 | 3 | `FileIndex::refuse_unreached_or_short` 6, `flow_skip_segments` 5, `FileIndex::mentions_any_on_path` 0, `flow_skip_chain_sites_resolved` 11, `FileIndex::segment_after` 8, `flow_skip_candidates_on_segment` 4, `flow_skip_settle_candidate` 4; all `#[inline]` (every tested walk). | 213162066 |
| Scan::binary | flow_skip | 37 | 10 | `Scan::equality` 5, `Scan::constructor_reference` 6, `Scan::boolean_comparison` 6; event order unchanged. | 47e8538d1 |
| flow_skip_ref_keys | flow_skip | 37 | 3 | `RefKeys::identifier` 1 and `RefKeys::access` 9 (two copies of the identifier key build were one), `flow_skip_known_identifier` 2 (two copies), `flow_skip_plain_chain` 3. | 280c2ac02 |
| flow_skip_test_worker | flow_skip | 37 | 7 | `flow_skip_switch_hot_file` 8, `flow_skip_count_walk` 7 (both `#[inline]`), `flow_skip_build_index` 0 (`#[inline(never)]`, once per file). | fe03e4314 |
| flow_skip_settle, next_of | flow_skip | 25, 22 | 21, 18 | Shared helpers: `static_flow_nodes` (3 copies), `local_flow` (2), `Class::bits` (2), `flow_skip_resolved_symbol` (4); `Scan::typeof_` is `self.t(_, true)`, the same events. | 845813aff |

The flow_skip and flow_p2 splits were written by a helper agent in its own
worktree from a measured plan (rca on prototypes), then cherry-picked.

## Duplication

`quality.sh report`: 693 → 686 blocks, 16,943 → 16,753 duplicated lines
(3.55% → 3.51%), no new blocks. Blocks with both sides in this lane's files:

| Block | Done | Commit |
|---|---|---|
| relater_p1 `check_type_related_to_ex` ↔ inference_p2 `check_normalized_type_identical_to` (overflow record and depth-limit trace) | `record_complexity_overflow` | d077b7cfe |
| relater_p4 identity ↔ general indexed-access cases (part extraction) | `indexed_access_pair` | 2de4ce929 |
| flow_p3 `get_flow_type_in_constructor` ↔ `get_flow_type_in_static_blocks` (two blocks; Go repeats them too) | `this_property_access_name`, `this_property_flow_type_at_return`; node creation order unchanged | 23b796ce1 |
| flow_p2 `narrow_type_by_switch_on_type_of` ↔ `narrow_type_by_switch_on_true` (clauses, default path) | `switch_clauses_and_default` | bbb7cd121 |
| relater_p3 string-literal fast path ↔ `infer_from_literal_parts_to_template_literal` (`go_plain` dispatch) | `match_literal_parts_to_template_literal_target` | bdd395ffa |
| flow_skip: identifier keys, resolved symbols, static flow nodes, class bits | shared helpers (see own-code table) | 845813aff, 280c2ac02 |
| inference_p1 four `AppendIfUnique` copies | `append_if_unique` | 79582a228 |
| relater_p1 two `isDeeplyNestedType` scans | one scan | 3c4a25875 |
| inference_p1 `infer_from_object_types` `[...T, ...rest]` ↔ `[...rest, ...T]` | left: Go's two cases differ in their slices; a shared helper would hide which is which | — |
| inference_p2 `is_type_identical_in_matching` ↔ relater_p1 `is_type_related_to_kind` | left: the documented identmatch1 second copy (UPSTREAM.md pin-bump check) | — |

Two more blocks pair a file of this lane with another lane's file
(utilities_p2 ↔ binder_p3, utilities_p2 ↔ checker_p13); left to them.

## Inlining (`--profile goport`, `nm` sizes, base f59c229c5 against head 552393584)

The hot functions keep their bodies: `is_related_to_ex` 5161 → 5126,
`structured_type_related_to_worker` 17505 → 16932, `is_simple_type_related_to_kind`
2674 → 2589 (the primitive rules are inlined), `infer_from_types` unchanged,
`get_inferred_type` 10448 → 9824, `compare_types` 5836 → 5880. Plain
`is_deeply_nested_type` (365) is inlined into its callers. New out-of-line
symbols: `is_matching_property_access_pair` (1246, out of `is_matching_reference_kinds`
4853 → 3100), `is_type_matched_by_template_literal_target` (1990, template literal
targets only), `indexed_access_parts` (143, generic mapped targets only),
`flow_skip_build_index` (`#[inline(never)]` by design). icount shows no cost
from them.

## icount (`icount_ab.sh`, `--profile goport`, 3 runs per side, interleaved)

Base `f59c229c5`, head `552393584` (all commits above).

| Cell | Base | Head | Δ | Spread b/h |
|---|---:|---:|---:|---|
| query noemit-st | 692,234,856 | 692,074,086 | −0.023% | 0.064/0.006% |
| query emit-st | 728,431,627 | 728,192,310 | −0.033% | 0.013/0.013% |
| query emit-mt | 825,291,300 | 826,883,234 | +0.193% | 0.337/0.190% |
| umami noemit-st | 16,550,998,699 | 16,544,019,750 | −0.042% | 0.047/0.004% |
| umami emit-st | 19,077,100,581 | 19,070,155,680 | −0.036% | 0.001/0.007% |
| umami emit-mt | 22,339,165,829 | 22,389,850,416 | +0.227% | 0.296/0.271% |
| hono noemit-st | 2,166,661,714 | 2,166,816,310 | +0.007% | 0.032/0.008% |
| hono emit-st | 2,877,299,093 | 2,877,582,504 | +0.010% | 0.011/0.013% |
| hono emit-mt | 3,734,404,083 | 3,733,833,978 | −0.015% | 0.295/0.087% |

No single-threaded cell moves more than 0.05%. The multi-threaded cells are
within their run-to-run spread (they count waits).

Again at head `bdd395ffa` (after the duplication commits; the clippy commit
after it only drops two `&` and moves a function):

| Cell | Base | Head | Δ | Spread b/h |
|---|---:|---:|---:|---|
| query noemit-st | 692,441,218 | 692,179,808 | −0.038% | 0.041/0.014% |
| query emit-st | 728,542,014 | 728,332,914 | −0.029% | 0.014/0.014% |
| query emit-mt | 826,142,876 | 825,663,832 | −0.058% | 0.337/0.345% |
| umami noemit-st | 16,553,616,544 | 16,546,391,692 | −0.044% | 0.005/0.007% |
| umami emit-st | 19,080,893,749 | 19,074,265,287 | −0.035% | 0.008/0.000% |
| umami emit-mt | 22,225,331,314 | 22,242,748,785 | +0.078% | 0.436/0.347% |
| hono noemit-st | 2,166,698,827 | 2,167,132,134 | +0.020% | 0.006/0.010% |
| hono emit-st | 2,877,583,681 | 2,877,487,070 | −0.003% | 0.007/0.016% |
| hono emit-mt | 3,731,636,387 | 3,733,388,076 | +0.047% | 0.089/0.139% |

The duplication helpers are cold and out of line at head: `record_complexity_overflow`
625, `this_property_flow_type_at_return` 854, `switch_clauses_and_default` 557;
`match_literal_parts_to_template_literal_target` 3971 replaces two copies of
`match_literal_parts_to_template_literal` (1854 each). `structured_type_related_to_worker`
stays 16932.

## Clippy

CI's command on 1.95.0 (`clippy --locked -p ts_goport -p goport_core -p goport_util
-p goport_lsproto --all-targets -- -D warnings`), plain and with
`ts_goport/diagnostics,ts_goport/test-hooks`: clean at `3c3e43592` (the first run
found `needless_borrow` ×5 and `items_after_test_module` in the inference splits;
fixed there).
At `1efc6429f`, `--force-warn clippy::type_complexity` on goport_core is 86, as at
the base (the primitive-rules split takes the `ErrorReporter` alias).

## Parity

`parity.sh full` on the release bin of `bdd395ffa` (the commits after it are a
clippy fix, a type alias and docs), base `base-current`:
`~/.cache/perf-rearch/w2-ca/parity-full-bdd395ffa`. **PARITY OK**: 187,196
items compared, 0 lost, 0 not run, 0 new items not good, 0 crashes on good
items, 0 newly good by a referee (GO-BROKEN 0, GO-PARTITION 0, JS-AGREES 0),
36 grandfathered (fuzz: Go timeouts and the Go crashes tsc-rs reproduces).
Tiers: cli 170 MATCH; tests 139,909 PASS, 1 FLAKY
(`tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm`, failed then passed
alone), 1,731 ignored; lsp 2,629 MATCH, 29 GO-BROKEN (in the base); fuzz 29,955
MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN, 1 JS-AGREES (in the base); corpus
12,727 MATCH. Load at the start 35.8 (other agents).

## Tests

`run-cargo-capped.sh test --release` with `TS_GO_REPO` at the pin, at
`3c3e43592`: plain 51 binaries, 2,121 passed, 0 failed, 2 ignored; with
`ts_goport/diagnostics,ts_goport/test-hooks` 52 binaries, 2,143 passed, 0
failed, 2 ignored.

## Quality (`quality.sh report HEAD --base perf/first-principles`, at 739aadded)

- Prod functions over cog 25: 691 → 683; over 50: 216 → 212; max 490 → 461; sum 91,722 → 91,371.
- Go-relative: port Δ ≥ 10 rows 149 → 135, Δ ≥ 25 rows 20 → 17, port excess 6,147 → 5,867; own > 25 146 → 140, own > 50 31 → 29, own excess 2,343 → 2,222.
- Duplication 3.55% → 3.51% (693 → 686 blocks, no new block).
- Escape hatches 13 → 13 (switches 4 → 4); hygiene unchanged; clippy (repo config) 1 → 1.

No switch added or removed.

