# W2-CC: checker_p20–p35, node builder, emit resolver, services, symbol tables

Lane W2-CC of wave 2 (code-quality phase). Brief: `w2-brief.md`. Started on
`perf/first-principles` @ `7034840f0`; rebased onto `32e0dfd3d` (W2-CA,
W2-CB, W2-LS and Q6 merged) for the idiom pass and the final evidence.
Files: `checker/checker_p20..p35.rs`, `nodebuilder*.rs`, `nodebuilder_impl_p*`,
`emit_resolver_p*`, `services.rs`, `pseudo_type_node_builder.rs`,
`printer_impl.rs`, `symbol_*.rs`, `types.rs`, `links.rs`, `exports.rs`,
`extras.rs`, `jsdoc.rs`, `fnfields.rs`, `flat_map.rs`, `node_copy.rs`, `mod.rs`.

Figures are cognitive complexity from `quality.sh ranking` (Rust / Go, Δ).
"Before" is `7034840f0`. "After" is before the idiom pass (below), which
changed the rows it names.

The lane ran as root plus five helpers (forks of root, each in its own
worktree, disjoint file groups: A node builder/services/symbol_accessibility,
B1 p20–p23, B2 p24–p25, C1 p26–p30, C2 p31–p35). Root wrote the emit
resolver commits and cherry-picked the helpers' commits in that order.

## Go idioms

First as W2-CA (`w2-ca.md`): `core.IfElse` and Go's tagless `switch` were
left as Rust if/else and else-if chains. After root's rule ("Go helper
idioms" in `w2-brief.md`, 2026-11-09) and the W2-CB merge, an idiom pass
spelled Go's `core.IfElse` as `if_else` (checker_p01) wherever Go calls it
and both arms are plain values; see "Idiom pass". Tagless switches stay.
Go's `core.OrElse` on a handle is `.get().unwrap_or(..)` or `or_else_type`;
its `core.Some`/`Every`/`Find`/`Map`/`FirstNonNil`/`MapNonNil` are iterator
adapters (many were flag-and-break loops; the same calls run in the same
order).

## Port bloat targets

In order of excess at the base. 45 rows with Δ ≥ 10 in these files before;
15 after this table (Σ Δ 686 → 200); 5 after the idiom pass (Σ Δ 54).

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| create_late_bound_index_signatures | emit_resolver_p2 | 83 / 53 (+30) | 66 / 53 (+13) | The `with_checker` closure put Go's whole body one nesting deeper. The body is `create_late_bound_index_signatures_locked`, which takes the checker (Go's body after `checkerMu.Lock()`). Left: two `core.IfElse` modifier choices. | 5abc9540d |
| array_or_tuple_type_to_node | nodebuilder_impl_p3 | 73 / 44 (+29) | left | All `core.IfElse` (Array/ReadonlyArray name; `...`, `?`, rest-array choices at nesting 5, about +32), plus +2 for the loop over `core.SameMapIndex`. | — |
| get_widened_type_with_context | checker_p20 | 43 / 18 (+25) | 20 / 18 (+2) | `core.SameMap`, `core.Some`, `core.OrElse` were push loops, a flag with a break and an if. Left: else-if chain for Go's switch. | 8e630104b |
| create_union_or_intersection_property | checker_p24 | 169 / 145 (+24) | 157 / 145 (+12) | Out of the loop: the lazy string literal type for `getApplicableIndexInfoForName` (`get_applicable_index_info_for_name_literal`), the stack-to-`Declarations` step (`declarations_from_stack`, own 2), the lazy `slices.Clone` of write types as `get_or_insert_with`. Left: one `core.OrElse` and one `core.IfElse` at nesting 4, the intern1 late-bound match. | bb093687b |
| type_to_type_node (+ type_to_type_node_body) | nodebuilder_impl_p4 | 168 / 144 (+24) | 154 / 144 (+10) | Three inline literal-value matches call the `get_string/number/boolean_literal_value` ports; Go's single alias test is one let-chain; the variance symbol is read in the condition, as in Go. Left: `core.IfElse` (intrinsic keyword, sub-/super-) and the two Go defers the wrapper runs. | 74664d3c3 |
| get_object_type_instantiation | checker_p25 | 56 / 32 (+24) | 48 / 32 (+16) | Go's `typeParameters == nil` branch sat inside the instfast1 match: `compute_outer_type_parameters_of_instantiation` (summed back, 4), with Go's `else if`; `match … unreachable!` on the mapper sources is `expect`. Left: two `core.IfElse` flag choices at nesting 4 (about +12) and the instfast1 detach and alias-key perf code. | 76cfedf8a |
| remove_subtypes | checker_p28 | 104 / 81 (+23) | 80 / 81 (−1) | `core.Some` (empty-object scan) and `core.Find` (key property) were flag loops; Go's one test for a type parameter with a union constraint is one let-chain. | 01f7dd604 |
| mark_linked_references | checker_p31 | 127 / 108 (+19) | 97 / 108 (−11) | The emitscan1 scan was a closure in the identifier case: `find_mark_linked_references_ancestors` (`#[inline]`, marked `ast/utilities.go:970 FindManyAncestors`, which Go calls here; 4 against 11). The JSX-tag test is one condition again. | ddf6af2ee |
| get_symbol_of_name_or_property_access_expression | checker_p35 | 95 / 76 (+19) | left | All five `core.IfElse` (about +20). | — |
| instantiate_contextual_type | checker_p33 | 45 / 26 (+19) | 26 / 26 (0) | `core.Some` loop under the Signature test is one condition with `any`; the boolean-union test reads the borrowed `types()` (no clone). | ab467de0b |
| index_info_to_object_computed_names_or_signature_declaration | nodebuilder_impl_p2 | 45 / 27 (+18) | 27 / 27 (0) | `core.Every`, `Filter`, `Map` were loops with flags, breaks and `continue`. The map still visits every component after a bail, as Go's does. | f9d91d421 |
| has_base_type | checker_p22 | 23 / 5 (+18) | 4 / 3 (+1) | Go's recursive closure `check` is `has_base_type_check` again, with Go's two tests. Perf code: `target_type_in_place` (`#[inline]`, marked `getTargetType`, 4 vs 1) and `some_base_type_has_base` (own 3, `#[inline]`). | 3f25a034e |
| get_property_type_for_index_type | checker_p30 | 188 / 171 (+17) | 182 / 171 (+11) | Go's single `INCLUDE_UNDEFINED && !own-enum-access` test was a flag plus a nested if; `core.Map` was a loop. Left: two tagless switches, one `core.IfElse`. | 58171b54b |
| get_union_signatures | checker_p23 | 68 / 52 (+16) | 52 / 52 (0) | `core.MapNonNil` for the this types (loop, two nested ifs) is `filter_map`. | 9eaa8597e |
| filter_type | checker_p29 | 32 / 16 (+16) | 15 / 16 (−1) | The lazy `core.Filter`/`core.Same` machinery is `filter_union_types_if_any_dropped` (own 7, `#[inline]`); the origin filter is iterator `filter`. | f642565c3 |
| get_property_symbols_from_contextual_type | services | 29 / 13 (+16) | 13 / 13 (0) | `core.Filter` and both `core.MapNonNil` are iterator filter/map; the inline `core.Deduplicate` copy calls `scanner_util::deduplicate` (same order, first of each). | b2836bef8 |
| is_deferred_type_reference_node | checker_p26 | 21 / 5 (+16) | 5 / 5 (0) | `core.Some` loops are `any`; `hasDefault \|\| Some` is one expression. | 329a0e5ed |
| get_possible_symbol_reference_positions | services | 29 / 14 (+15) | 16 / 14 (+2) | Our Go-byte search machinery moved out: `ReferenceSearchText::new` (3), `go_byte_before` (2), `go_byte_at` (2), all `#[inline]`; `go_offsets_to_port_offsets` (cold). Go's container default is a plain `if`. | f2fbc0bc7 |
| get_union_type_worker | checker_p28 | 75 / 60 (+15) | 66 / 60 (+6) | The unionsort1 keyed `containsType` search two loops deep is `some_named_union_contains_type` (`#[inline]`, "part of Go `getUnionTypeWorker`", summed). Left: two `core.IfElse`, two tagless switches. | c8e48613c |
| resolve_mapped_type_members | checker_p23 | 58 / 43 (+15) | 56 / 43 (+13) | `core.OrElse` on the target is `unwrap_or`. Left: three `core.IfElse` flag choices deep in Go's closure (about +15). | 72818ac8f |
| instantiate_type_worker | checker_p24 | 54 / 39 (+15) | 46 / 39 (+7) | Two inline instfast1 matches (detached list, in-place read of an owned list) call one `instantiate_stored_types_if_changed` (`#[inline]`, Go's `instantiateTypes` + `core.Same`). Left: else-if chain for Go's switch. | 529b9e22c |
| get_type_reference_serialization_kind | emit_resolver_p2 | 54 / 40 (+14) | 40 / 40 (0) | `with_checker` closure, as create_late_bound_index_signatures. | a08b9f959 |
| get_contextual_type_for_yield_operand | checker_p32 | 28 / 14 (+14) | 14 / 14 (0) | Three `core.OrElse` are `get().unwrap_or`. | 76094e2a8 |
| is_empty_object_type | checker_p29 | 16 / 2 (+14) | 5 / 2 (+3) | `core.Some`/`Every` are `any`/`all`; the object case is one expression. Left: Go's switch as early returns. | f7a922bd4 |
| report_implicit_any | checker_p20 | 51 / 38 (+13) | left | All residue: two `core.IfElse` and else-if chains for two Go switches. | — |
| get_type_of_assignment_pattern | services | 16 / 3 (+13) | 3 / 3 (0) | Five `core.OrElse` if/else expressions are `get().unwrap_or` (no new `or_else_type` copy). | 4863a64be |
| serialize_return_type_for_signature | nodebuilder_impl_p2 | 45 / 33 (+12) | 34 / 33 (+1) | The pseudo type was an `Option` from the start, so its matches had dead `None` arms; it becomes `None` only where Go sets `pt = nil`. A missing map entry reads as nil, so Go's `!ok \|\| nil` is one test. | 8eb77511f |
| combine_union_or_intersection_parameters | checker_p23 | 39 / 27 (+12) | 36 / 27 (+9) | The extra rest slot is `i32::from(bool)`; `core.OrElse` to unknown is `unwrap_or`. Left: the tagless switch on the names, three `core.IfElse`. | d30416ae7 |
| get_resolved_type_parameter_default | checker_p24 | 36 / 24 (+12) | 27 / 24 (+3) | `core.FirstNonNil` was a nested loop with a break. Left: the portonly1 early return. | 36ee501b8 |
| create_tuple_target_type | checker_p27 | 22 / 10 (+12) | left | All three `core.IfElse` flag choices. | — |
| get_apparent_type | checker_p24 | 17 / 5 (+12) | 15 / 5 (+10) | The cached apparent type of a mapped type is `get_apparent_type_of_mapped_type_cached` (`#[inline(always)]`). Left: all +10 is the else-if chain for Go's switch. | d0733256e |
| get_symbol_at_location | checker_p34 | 108 / 97 (+11) | left | The Rust stand-in for Go's three `fallthrough`s (extra kind test and nesting each); `#[inline(never)]` (effinline1). Splitting the arms would invent helpers Go lacks. | — |
| get_template_literal_type | checker_p32 | 40 / 29 (+11) | 35 / 29 (+6) | The tcsplit1/perffu1 reserve is `template_literal_text_capacity` (own 5, `#[inline]`); splitting the buffer into Go's `newTexts` is `template_texts_of_buffer` (new types only). Left: the else-if chain in `addSpans`, the no-marker fast path, the `sb.Len()` limit test. | d16b4e3f6 |
| get_type_from_array_or_tuple_type_node | checker_p26 | 31 / 20 (+11) | 20 / 20 (0) | `core.Some` and `core.Map` are iterators. | 329a0e5ed |
| get_index_type_ex | checker_p29 | 12 / 1 (+11) | left | Go's 9-case switch as early returns plus two `core.IfElse`. | — |
| get_reduced_apparent_type | checker_p24 | 11 / 0 (+11) | 1 / 0 (+1) | Both fast paths (object types; settled intersections, sentdisc1) are `get_reduced_apparent_type_cached` (own 11, `#[inline(always)]`, returns `Option`). | d0733256e |
| get_type_facts_worker (+ type_facts_of_flags, type_facts_slow) | checker_p34 | 74 / 64 (+10) | left | Hot, already split by unionfn1. The else-if chain for Go's switch, Go's `if strict { return A }; return B` as expressions, the chkfacts1 early-out. | — |
| create_access_from_symbol_chain | nodebuilder_impl_p1 | 63 / 53 (+10) | 58 / 53 (+5) | The private-name print is `go_export_name_of_private_name` (own 1); Go's parent default is a plain `if`. Left: the members test is two tests so the member lookup can borrow the name after `getMembersOfSymbol`. | d68cd5650 |
| get_binding_element_type_from_parent_type | checker_p20 | 47 / 37 (+10) | 44 / 37 (+7) | `core.OrElse` on the indexed access type is `unwrap_or`. Left: two `core.IfElse`, a borrow block in the initializer test. | 0ca7cde27 |
| get_type_arguments (+ resolve_type_arguments) | checker_p24 | 31 / 21 (+10) | 26 / 21 (+5) | `core.Map` over tuple elements was a push loop; the constvar1 overwrite record is `store_resolved_type_arguments`. Left: `core.IfElse` for the error node, the split's count return. | ee05822cb |
| is_this_property_assignment_declaration_redundant | emit_resolver_p2 | 22 / 12 (+10) | 15 / 12 (+3) | `with_checker` closure, as above. | a08b9f959 |
| is_symbol_accessible_worker | symbol_accessibility | 19 / 9 (+10) | 14 / 9 (+5) | `core.FirstNonNil` loop with a break is map/find. Left: the `core.IfElse` error node. | b88421ebd |
| elaborate_never_intersection | checker_p24 | 16 / 6 (+10) | 6 / 6 (0) | Two `core.Find` flag-and-break loops are `iter().find`. | deffaad9f |
| new_object_type | checker_p28 | 11 / 1 (+10) | left | The 8-case switch (+8) and the PERF `Kind` match in the `new_type_with` closure (+2, no-memcpy layout). | — |
| get_class_member_decorator_context_override_type | checker_p33 | 11 / 1 (+10) | 6 / 1 (+5) | The nested `core.IfElse` on isPrivate/isStatic is one match on the pair. Left: two `core.IfElse` property types. | 1aae64319 |

The four functions the prompt named for their excess only, at the end:
`create_union_or_intersection_property` +24 → +6,
`get_property_type_for_index_type` +17 → +6, `type_to_type_node_body` (row
`type_to_type_node`) +24 → +3; `pseudo_type_equivalent_to_type` is +9
(195 / 186), under the target line, and was not touched.

## Own-code targets

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| get_property_of_type_ex_body | checker_p21 | own 30 | port 31 / 23 (+8, wrapper included) | Not own code: the body of Go `getPropertyOfTypeEx`, whose doc named only the Rust wrapper. Marker and doc line, no code change. Left: else-if chains for Go's two switches and the propfilt1 early-out. | e7fde3f0f |

No other function in these files is own code over 25 (before or after).

## Duplication

No block was added. `or_else_type`: helper A first added a third file-local
copy of the `core.OrElse` helper (checker_p05 and checker_p22 have one each,
other lanes' files); root replaced it with the handle's `get().unwrap_or`
in the same commit, so services.rs has none. The checker_p04/p05/p22 copies
are W2-CB's files.

## Idiom pass

Base `3fb50ef58` (rebased on W2-CB), after `or_else_type_p22` became the
shared `or_else_type` (af7415de8). Every converted arm is a constant, a flag,
a `&'static` value or a handle already computed, so no lazy arm became eager.

| Row | File | Before | After | Converted | Kept, and why | Commit |
|---|---|---|---|---|---|---|
| array_or_tuple_type_to_node | nodebuilder_impl_p3 | 73 / 44 (+29) | 41 / 44 (−3) | Array/ReadonlyArray name; `...`, `?`, rest-array choices | The array/readonly returns are if/else in Go. The token and array nodes were already made before the choice, in Go's order. | 2e7fd7c6d |
| get_symbol_of_name_or_property_access_expression | checker_p35 | 95 / 76 (+19) | 75 / 76 (−1) | all 5 (two unknown-to-nil, three meanings) | — | b690bba6d |
| get_object_type_instantiation | checker_p25 | 48 / 32 (+16) | 36 / 32 (+4) | two CouldContainTypeVariables flags | instfast1 code is the rest | 441e02a50 |
| create_late_bound_index_signatures_locked | emit_resolver_p2 | 66 / 53 (+13) | left | — | Both arms create nodes (`NewModifier`, `NewModifierList`): eager would make a modifier on every call. | — |
| resolve_mapped_type_members | checker_p23 | 56 / 43 (+13) | 41 / 43 (−2) | Optional, Readonly, StripOptional flags | — | 9653bb366 |
| report_implicit_any | checker_p20 | 51 / 38 (+13) | 43 / 38 (+5) | Member message, `"[]"` | Go's if/else on noImplicitAny; switches | 05d398043 |
| create_tuple_target_type | checker_p27 | 22 / 10 (+12) | 10 / 10 (0) | Optional, Readonly ×2 | — | 32606e0fb |
| create_union_or_intersection_property | checker_p24 | 157 / 145 (+12) | 151 / 145 (+6) | index info Readonly flag | `core.OrElse` on accessor flags (no flags helper); intern1 match | 932e9c40b |
| get_index_type_ex | checker_p29 | 12 / 1 (+11) | 8 / 1 (+7) | two include flags | the switch as early returns | e7377f82b |
| get_property_type_for_index_type | checker_p30 | 182 / 171 (+11) | 177 / 171 (+6) | undefined-like type | INCLUDE_UNDEFINED returns are if/else in Go; switches | ef57f7fe2 |
| type_to_type_node | nodebuilder_impl_p4 | 154 / 144 (+10) | 147 / 144 (+3) | intrinsic keyword, sub-/super- | the two Go defers | afd9c0cbc |
| combine_union_or_intersection_parameters | checker_p23 | 36 / 27 (+9) | 29 / 27 (+2) | Optional flag; Rest/Optional check flag | the type choice is if/else in Go and makes an array type | 9653bb366 |
| get_binding_element_type_from_parent_type | checker_p20 | 44 / 37 (+7) | 39 / 37 (+2) | AllowMissing, PossiblyOutOfBounds | — | 05d398043 |
| get_union_type_worker | checker_p28 | 66 / 60 (+6) | 62 / 60 (+2) | PrimitiveUnion, ContainsIntersections | switches | 6ff166d98 |
| get_type_arguments (+ resolve_type_arguments) | checker_p24 | 26 / 21 (+5) | 22 / 21 (+1) | error node | — | 932e9c40b |
| is_symbol_accessible_worker | symbol_accessibility | 14 / 9 (+5) | 9 / 9 (0) | JS-file error node | — | cc7f71d6b |
| get_class_member_decorator_context_override_type | checker_p33 | 6 / 1 (+5) | 1 / 1 (0) | cache kind back to Go's nested `core.IfElse` (was a match on the pair), true/false types | — | 9bf35be91 |

Left at Δ ≥ 10 in these files: `create_late_bound_index_signatures` +13
(above), `get_symbol_at_location` +11 (Go's `fallthrough`s),
`get_type_facts_worker` +10, `get_apparent_type` +10 and `new_object_type`
+10 (tagless switches). No own-code row over 25.

## Evidence (head `cc7f71d6b`, on `32e0dfd3d`)

- **Parity:** `parity.sh full`, base `base-current`:
  `~/.cache/perf-rearch/w2-cc/parity-full-cc7f71d6b`. **PARITY OK**: 187,196
  items, 0 lost, 0 not run, 0 new items not good, 0 crashes on good items,
  0 newly good by a referee, 36 grandfathered. cli 170 MATCH; lsp 2,629
  MATCH + 29 GO-BROKEN (base); tests 139,909 PASS, 1 FLAKY
  (`tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm`, the same flake
  W2-CA saw), 1,731 ignored; fuzz 29,955 MATCH, 36 DIFF (grandfathered),
  8 GO-BROKEN, 1 JS-AGREES (base); corpus 12,727 MATCH. (Before the idiom
  pass, `5236b5183`: also PARITY OK, tests 139,910 PASS.)
- **Tests** (`test --release --workspace`, `TS_GO_REPO` at the pin): plain
  51 binaries, 2,115 passed, 0 failed, 2 ignored; with
  `ts_goport/diagnostics,ts_goport/test-hooks` 52 binaries, 2,143 passed,
  0 failed, 2 ignored.
- **Clippy** (CI's command on 1.95.0, `-D warnings`): plain and with the
  features clean. The first run found one `manual_retain` (c5c3c78bd).
- **Gating:** `quality.sh gating HEAD`: GATING OK (0 findings, 3 allowed).
- **icount** (`icount_ab.sh`, `--profile goport` on 1.95.0, 3 runs per side,
  interleaved; base `32e0dfd3d`, head `cc7f71d6b`):

  | Cell | Base | Head | Δ | Spread b/h |
  |---|---:|---:|---:|---|
  | query noemit-st | 683,340,828 | 683,102,162 | −0.035% | 0.017/0.013% |
  | query emit-st | 718,987,920 | 718,766,892 | −0.031% | 0.003/0.023% |
  | query emit-mt | 818,001,025 | 816,875,846 | −0.138% | 0.327/0.204% |
  | umami noemit-st | 16,494,531,393 | 16,490,381,742 | −0.025% | 0.008/0.017% |
  | umami emit-st | 19,013,132,022 | 19,012,262,344 | −0.005% | 0.005/0.017% |
  | umami emit-mt | 22,288,576,459 | 22,344,944,345 | +0.253% | 0.167/0.164% |
  | hono noemit-st | 2,141,277,015 | 2,138,111,499 | −0.148% | 0.004/0.010% |
  | hono emit-st | 2,855,359,180 | 2,853,671,004 | −0.059% | 0.022/0.015% |
  | hono emit-mt | 3,706,018,629 | 3,696,152,250 | −0.266% | 0.016/0.296% |

  umami emit-mt rechecked with 5 interleaved runs per side: +0.036%, spread
  0.504/0.525% (noise; its single-threaded emit cell is −0.005%).
  No single-threaded cell is above 0.
- **Quality** (`quality.sh report cc7f71d6b --base 32e0dfd3d`; the branch
  moved to `68755a4fa` (W2-EF) during the run, so the merge base is the
  honest base): prod functions over cog 25 670 → 663, over 50 205 → 199,
  sum 90,626 → 90,067; duplication 3.48% → 3.48% (679 → 678 blocks, no new
  block); escape hatches 11 → 11 (switches 4 → 4); clippy (repo config)
  0 → 0, `--force-warn clippy::all` 1,128 → 1,128. Go-relative: port
  Δ ≥ 10 rows 89 → 49, Δ ≥ 25 10 → 7, port excess 5,192 → 4,643; own > 25
  136 → 135, own excess 2,150 → 2,145.
- **Switches:** none added, none removed.
