# W2-CC: checker_p20–p35, node builder, emit resolver, services, symbol tables

Lane W2-CC of wave 2 (code-quality phase). Brief: `w2-brief.md`. Base
`perf/first-principles` @ `7034840f0` (source identical to `f59c229c5`).
Files: `checker/checker_p20..p35.rs`, `nodebuilder*.rs`, `nodebuilder_impl_p*`,
`emit_resolver_p*`, `services.rs`, `pseudo_type_node_builder.rs`,
`printer_impl.rs`, `symbol_*.rs`, `types.rs`, `links.rs`, `exports.rs`,
`extras.rs`, `jsdoc.rs`, `fnfields.rs`, `flat_map.rs`, `node_copy.rs`, `mod.rs`.

Figures are cognitive complexity from `quality.sh ranking` (Rust / Go, Δ).
"Before" is `7034840f0`.

The lane ran as root plus five helpers (forks of root, each in its own
worktree, disjoint file groups: A node builder/services/symbol_accessibility,
B1 p20–p23, B2 p24–p25, C1 p26–p30, C2 p31–p35). Root wrote the emit
resolver commits and cherry-picked the helpers' commits in that order.

## Metric artifacts

As W2-CA (`w2-ca.md`): Go's tagless `switch` (else-if chain in Rust) and
`core.IfElse` (if/else in Rust) score higher in Rust. They are left as
idiomatic Rust and named where they make up the rest of a Δ. No `if_else`
helper, no `match () { _ if … }`. Go's `core.OrElse` on a handle is
`.get().unwrap_or(..)`, its `core.Some`/`Every`/`Find`/`Map`/`FirstNonNil`/
`MapNonNil` are iterator adapters (they were flag-and-break loops in many
places; the same calls run in the same order).

## Port bloat targets

In order of excess at the base. 45 rows with Δ ≥ 10 in these files before,
15 after (all residue), Σ Δ of targets 686 → 200.

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| create_late_bound_index_signatures | emit_resolver_p2 | 83 / 53 (+30) | 66 / 53 (+13) | The `with_checker` closure put Go's whole body one nesting deeper. The body is `create_late_bound_index_signatures_locked`, which takes the checker (Go's body after `checkerMu.Lock()`). Left: two `core.IfElse` modifier choices. | 30b8840b4 |
| array_or_tuple_type_to_node | nodebuilder_impl_p3 | 73 / 44 (+29) | left | All `core.IfElse` (Array/ReadonlyArray name; `...`, `?`, rest-array choices at nesting 5, about +32), plus +2 for the loop over `core.SameMapIndex`. | — |
| get_widened_type_with_context | checker_p20 | 43 / 18 (+25) | 20 / 18 (+2) | `core.SameMap`, `core.Some`, `core.OrElse` were push loops, a flag with a break and an if. Left: else-if chain for Go's switch. | 220dffa29* |
| create_union_or_intersection_property | checker_p24 | 169 / 145 (+24) | 157 / 145 (+12) | Out of the loop: the lazy string literal type for `getApplicableIndexInfoForName` (`get_applicable_index_info_for_name_literal`), the stack-to-`Declarations` step (`declarations_from_stack`, own 2), the lazy `slices.Clone` of write types as `get_or_insert_with`. Left: one `core.OrElse` and one `core.IfElse` at nesting 4, the intern1 late-bound match. | afcdf452a* |
| type_to_type_node (+ type_to_type_node_body) | nodebuilder_impl_p4 | 168 / 144 (+24) | 154 / 144 (+10) | Three inline literal-value matches call the `get_string/number/boolean_literal_value` ports; Go's single alias test is one let-chain; the variance symbol is read in the condition, as in Go. Left: `core.IfElse` (intrinsic keyword, sub-/super-) and the two Go defers the wrapper runs. | ea548eeed* |
| get_object_type_instantiation | checker_p25 | 56 / 32 (+24) | 48 / 32 (+16) | Go's `typeParameters == nil` branch sat inside the instfast1 match: `compute_outer_type_parameters_of_instantiation` (summed back, 4), with Go's `else if`; `match … unreachable!` on the mapper sources is `expect`. Left: two `core.IfElse` flag choices at nesting 4 (about +12) and the instfast1 detach and alias-key perf code. | c554efb6d* |
| remove_subtypes | checker_p28 | 104 / 81 (+23) | 80 / 81 (−1) | `core.Some` (empty-object scan) and `core.Find` (key property) were flag loops; Go's one test for a type parameter with a union constraint is one let-chain. | e95572bcb* |
| mark_linked_references | checker_p31 | 127 / 108 (+19) | 97 / 108 (−11) | The emitscan1 scan was a closure in the identifier case: `find_mark_linked_references_ancestors` (`#[inline]`, marked `ast/utilities.go:970 FindManyAncestors`, which Go calls here; 4 against 11). The JSX-tag test is one condition again. | 7538e57f5* |
| get_symbol_of_name_or_property_access_expression | checker_p35 | 95 / 76 (+19) | left | All five `core.IfElse` (about +20). | — |
| instantiate_contextual_type | checker_p33 | 45 / 26 (+19) | 26 / 26 (0) | `core.Some` loop under the Signature test is one condition with `any`; the boolean-union test reads the borrowed `types()` (no clone). | 4b15f83e5* |
| index_info_to_object_computed_names_or_signature_declaration | nodebuilder_impl_p2 | 45 / 27 (+18) | 27 / 27 (0) | `core.Every`, `Filter`, `Map` were loops with flags, breaks and `continue`. The map still visits every component after a bail, as Go's does. | cbd0d04ef* |
| has_base_type | checker_p22 | 23 / 5 (+18) | 4 / 3 (+1) | Go's recursive closure `check` is `has_base_type_check` again, with Go's two tests. Perf code: `target_type_in_place` (`#[inline]`, marked `getTargetType`, 4 vs 1) and `some_base_type_has_base` (own 3, `#[inline]`). | 056ef7e56* |
| get_property_type_for_index_type | checker_p30 | 188 / 171 (+17) | 182 / 171 (+11) | Go's single `INCLUDE_UNDEFINED && !own-enum-access` test was a flag plus a nested if; `core.Map` was a loop. Left: two tagless switches, one `core.IfElse`. | a4c419c90* |
| get_union_signatures | checker_p23 | 68 / 52 (+16) | 52 / 52 (0) | `core.MapNonNil` for the this types (loop, two nested ifs) is `filter_map`. | d8ab070a3* |
| filter_type | checker_p29 | 32 / 16 (+16) | 15 / 16 (−1) | The lazy `core.Filter`/`core.Same` machinery is `filter_union_types_if_any_dropped` (own 7, `#[inline]`); the origin filter is iterator `filter`. | 3d93ca48b* |
| get_property_symbols_from_contextual_type | services | 29 / 13 (+16) | 13 / 13 (0) | `core.Filter` and both `core.MapNonNil` are iterator filter/map; the inline `core.Deduplicate` copy calls `scanner_util::deduplicate` (same order, first of each). | 47b9f9fc5* |
| is_deferred_type_reference_node | checker_p26 | 21 / 5 (+16) | 5 / 5 (0) | `core.Some` loops are `any`; `hasDefault \|\| Some` is one expression. | 9ff77a964* |
| get_possible_symbol_reference_positions | services | 29 / 14 (+15) | 16 / 14 (+2) | Our Go-byte search machinery moved out: `ReferenceSearchText::new` (3), `go_byte_before` (2), `go_byte_at` (2), all `#[inline]`; `go_offsets_to_port_offsets` (cold). Go's container default is a plain `if`. | 139e62580* |
| get_union_type_worker | checker_p28 | 75 / 60 (+15) | 66 / 60 (+6) | The unionsort1 keyed `containsType` search two loops deep is `some_named_union_contains_type` (`#[inline]`, "part of Go `getUnionTypeWorker`", summed). Left: two `core.IfElse`, two tagless switches. | 4b7e31b0c* |
| resolve_mapped_type_members | checker_p23 | 58 / 43 (+15) | 56 / 43 (+13) | `core.OrElse` on the target is `unwrap_or`. Left: three `core.IfElse` flag choices deep in Go's closure (about +15). | 1b5b78d22* |
| instantiate_type_worker | checker_p24 | 54 / 39 (+15) | 46 / 39 (+7) | Two inline instfast1 matches (detached list, in-place read of an owned list) call one `instantiate_stored_types_if_changed` (`#[inline]`, Go's `instantiateTypes` + `core.Same`). Left: else-if chain for Go's switch. | 9cdafc53a* |
| get_type_reference_serialization_kind | emit_resolver_p2 | 54 / 40 (+14) | 40 / 40 (0) | `with_checker` closure, as create_late_bound_index_signatures. | 94588a11d |
| get_contextual_type_for_yield_operand | checker_p32 | 28 / 14 (+14) | 14 / 14 (0) | Three `core.OrElse` are `get().unwrap_or`. | ae03d45e1* |
| is_empty_object_type | checker_p29 | 16 / 2 (+14) | 5 / 2 (+3) | `core.Some`/`Every` are `any`/`all`; the object case is one expression. Left: Go's switch as early returns. | 01b159e38* |
| report_implicit_any | checker_p20 | 51 / 38 (+13) | left | All residue: two `core.IfElse` and else-if chains for two Go switches. | — |
| get_type_of_assignment_pattern | services | 16 / 3 (+13) | 3 / 3 (0) | Five `core.OrElse` if/else expressions are `get().unwrap_or` (no new `or_else_type` copy). | 7b6b9ab8a* |
| serialize_return_type_for_signature | nodebuilder_impl_p2 | 45 / 33 (+12) | 34 / 33 (+1) | The pseudo type was an `Option` from the start, so its matches had dead `None` arms; it becomes `None` only where Go sets `pt = nil`. A missing map entry reads as nil, so Go's `!ok \|\| nil` is one test. | d53a00db6* |
| combine_union_or_intersection_parameters | checker_p23 | 39 / 27 (+12) | 36 / 27 (+9) | The extra rest slot is `i32::from(bool)`; `core.OrElse` to unknown is `unwrap_or`. Left: the tagless switch on the names, three `core.IfElse`. | 661e4483b* |
| get_resolved_type_parameter_default | checker_p24 | 36 / 24 (+12) | 27 / 24 (+3) | `core.FirstNonNil` was a nested loop with a break. Left: the portonly1 early return. | 405c5db23* |
| create_tuple_target_type | checker_p27 | 22 / 10 (+12) | left | All three `core.IfElse` flag choices. | — |
| get_apparent_type | checker_p24 | 17 / 5 (+12) | 15 / 5 (+10) | The cached apparent type of a mapped type is `get_apparent_type_of_mapped_type_cached` (`#[inline(always)]`). Left: all +10 is the else-if chain for Go's switch. | 5f1a21e59* |
| get_symbol_at_location | checker_p34 | 108 / 97 (+11) | left | The Rust stand-in for Go's three `fallthrough`s (extra kind test and nesting each); `#[inline(never)]` (effinline1). Splitting the arms would invent helpers Go lacks. | — |
| get_template_literal_type | checker_p32 | 40 / 29 (+11) | 35 / 29 (+6) | The tcsplit1/perffu1 reserve is `template_literal_text_capacity` (own 5, `#[inline]`); splitting the buffer into Go's `newTexts` is `template_texts_of_buffer` (new types only). Left: the else-if chain in `addSpans`, the no-marker fast path, the `sb.Len()` limit test. | 75c018e71* |
| get_type_from_array_or_tuple_type_node | checker_p26 | 31 / 20 (+11) | 20 / 20 (0) | `core.Some` and `core.Map` are iterators. | 9ff77a964* |
| get_index_type_ex | checker_p29 | 12 / 1 (+11) | left | Go's 9-case switch as early returns plus two `core.IfElse`. | — |
| get_reduced_apparent_type | checker_p24 | 11 / 0 (+11) | 1 / 0 (+1) | Both fast paths (object types; settled intersections, sentdisc1) are `get_reduced_apparent_type_cached` (own 11, `#[inline(always)]`, returns `Option`). | 5f1a21e59* |
| get_type_facts_worker (+ type_facts_of_flags, type_facts_slow) | checker_p34 | 74 / 64 (+10) | left | Hot, already split by unionfn1. The else-if chain for Go's switch, Go's `if strict { return A }; return B` as expressions, the chkfacts1 early-out. | — |
| create_access_from_symbol_chain | nodebuilder_impl_p1 | 63 / 53 (+10) | 58 / 53 (+5) | The private-name print is `go_export_name_of_private_name` (own 1); Go's parent default is a plain `if`. Left: the members test is two tests so the member lookup can borrow the name after `getMembersOfSymbol`. | 091c3faad* |
| get_binding_element_type_from_parent_type | checker_p20 | 47 / 37 (+10) | 44 / 37 (+7) | `core.OrElse` on the indexed access type is `unwrap_or`. Left: two `core.IfElse`, a borrow block in the initializer test. | 9a415d1ad* |
| get_type_arguments (+ resolve_type_arguments) | checker_p24 | 31 / 21 (+10) | 26 / 21 (+5) | `core.Map` over tuple elements was a push loop; the constvar1 overwrite record is `store_resolved_type_arguments`. Left: `core.IfElse` for the error node, the split's count return. | 1b125911f* |
| is_this_property_assignment_declaration_redundant | emit_resolver_p2 | 22 / 12 (+10) | 15 / 12 (+3) | `with_checker` closure, as above. | 94588a11d |
| is_symbol_accessible_worker | symbol_accessibility | 19 / 9 (+10) | 14 / 9 (+5) | `core.FirstNonNil` loop with a break is map/find. Left: the `core.IfElse` error node. | 048365596* |
| elaborate_never_intersection | checker_p24 | 16 / 6 (+10) | 6 / 6 (0) | Two `core.Find` flag-and-break loops are `iter().find`. | 63d03627c* |
| new_object_type | checker_p28 | 11 / 1 (+10) | left | The 8-case switch (+8) and the PERF `Kind` match in the `new_type_with` closure (+2, no-memcpy layout). | — |
| get_class_member_decorator_context_override_type | checker_p33 | 11 / 1 (+10) | 6 / 1 (+5) | The nested `core.IfElse` on isPrivate/isStatic is one match on the pair. Left: two `core.IfElse` property types. | 11c885fdb* |

`*` SHAs are the helpers' commits; the cherry-picked commits on this branch
have the same subjects (`git log --grep`). The four functions the prompt named
for their excess only: `create_union_or_intersection_property` +24 → +12,
`get_property_type_for_index_type` +17 → +11, `type_to_type_node_body` (row
`type_to_type_node`) +24 → +10; `pseudo_type_equivalent_to_type` is +9
(195 / 186), under the target line, and was not touched.

## Own-code targets

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| get_property_of_type_ex_body | checker_p21 | own 30 | port 31 / 23 (+8, wrapper included) | Not own code: the body of Go `getPropertyOfTypeEx`, whose doc named only the Rust wrapper. Marker and doc line, no code change. Left: else-if chains for Go's two switches and the propfilt1 early-out. | 932e6a7a6* |

No other function in these files is own code over 25 (before or after).

## Duplication

No block was added. `or_else_type`: helper A first added a third file-local
copy of the `core.OrElse` helper (checker_p05 and checker_p22 have one each,
other lanes' files); root replaced it with the handle's `get().unwrap_or`
in the same commit, so services.rs has none. The checker_p04/p05/p22 copies
are W2-CB's files.

## Evidence

(Filled in below as the runs finish.)
