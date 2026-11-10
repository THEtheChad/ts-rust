# W2-EF: Effect (`crates/ts_goport/src/effect/`)

Lane W2-EF of wave 2 (code-quality phase). Own code (a port of
Effect-TS/tsgo, no counterpart at the TypeScript-Go pin), so the limit is
cognitive complexity 25. Base `perf/first-principles` @ `f59c229c5`: 85 rows
over 25, Σ (cog − 25) = 1,312 (`quality.sh ranking`).

Cog numbers are rust-code-analysis cognitive complexity (the ranking's
metric). "After" is the largest function left from the split.

## Shared structure factored first

| Shared helper | Replaces |
|---|---|
| `typeparser/module_identifier.rs`: `import_local_name`, `unquoted_module_specifier`, `named_import_local_name` | three copies of the import walk (`find_effect_module_identifier`, `find_module_identifier`, `find_module_identifier_for_package`) and the named-import part of `layergraph::find_layer_module_name` |
| `api_stability_safety.rs`: `any_structural_type_node_child`, `conditional_type_branches`, `mapped_type_operands` | the wrapped/array/tuple/union/template/indexed-access cases of the four raw annotation walks (`type_node_mentions_unbound_parameter_scoped`, `type_node_mentions_replaced_parameter`, `type_node_mentions_infer`, p3 `type_node_mentions_type_parameter_body`) |
| `api_stability_safety.rs`: `type_containment_operands` | the shared compound cases of `contains_unbound_parameter` and `contains_replaced_parameter` |
| `rules/module_references.rs`: `top_level_module_references`, `package_module_references` | the import/export/require walk copied in `node_builtin_import`, `obsolete_match_import`, `obsolete_schema_import` |
| `piping_flow.rs`: `PipingFlowWalk::extend_or_start_flow` | three "extend the parent flow or start a new one" blocks of `compute_piping_flows` |
| `piping_flow.rs`: `TypeParser::signature_declaration_symbol` | the raw-signature → declaration → symbol chain (twice in `decompose_curried_pipeable`, once in `data_first_or_last_call`) |

## Targets

| Function | File | Before | After | What | Commit |
|---|---|---:|---:|---|---|
| `extract_layer_graph` | layergraph.rs | 133 | 12 | `LayerGraphExtractor` with one method per Go case | 5888f1fce |
| `find_layer_module_name` | layergraph.rs | 26 | <10 | shared named-import helper | f758b56b7 |
| `find_effect_module_identifier`, `find_module_identifier`, `find_module_identifier_for_package` | typeparser/module_identifier.rs | 34, 35, 35 | <10 | first two call the third; one per-import helper | f758b56b7 |
| `format_nested_layer_graph` | layergraph.rs | 33 | <20 | node subgraph and edge helpers | bb9ec0991 |
| `extract_outline_graph` | layergraph.rs | 29 | 20 (`connect_requirers_to_providers`) | step helpers | bb9ec0991 |
| `TypeParser::compute_piping_flows` | typeparser/piping_flow.rs | 95 | <20 | `PipingFlowWalk`, one method per call shape | c8eb293be |
| `decompose_curried_pipeable` | typeparser/piping_flow.rs | 26 | 23 | `signature_declaration_symbol` | 3afbfb409 |
| `build_effect_fn_transformations` | typeparser/piping_flow.rs | 26 | <20 | `effect_fn_transformation_callee_and_args` | 3afbfb409 |
| `type_node_mentions_unbound_parameter_scoped` | typeparser/api_stability_safety.rs | 79 | <25 | shared structural walk + `_body` | fe01b0564 |
| `type_node_mentions_replaced_parameter` | typeparser/api_stability_safety.rs | 63 | <25 | same | fe01b0564 |
| `type_node_mentions_infer` | typeparser/api_stability_safety.rs | 48 | <25 | same | fe01b0564 |
| `contains_unbound_parameter`, `contains_replaced_parameter` | typeparser/api_stability_safety.rs | 44, 44 | <25 | `type_containment_operands` + `_body` | fe01b0564 |
| `function_like_mentions_unbound_parameter` | typeparser/api_stability_safety.rs | 33 | <25 | `function_like_own_scope` | fe01b0564 |
| `type_node_mentions_type_parameter_body` | typeparser/api_stability_p3.rs | 39 | <25 | shared structural walk | fe01b0564 |
| `analyze_obsolete_match_import`, `analyze_obsolete_schema_import` | rules/obsolete_*_import.rs | 56, 55 | <10 | `package_module_references` | a4cb41f3c |
| `analyze_node_builtin_import` | rules/node_builtin_import.rs | 46 | <10 | `top_level_module_references` | a4cb41f3c |
| `run_any_unknown_in_error_context` | rules/any_unknown_in_error_context.rs | 70 | <10 | skip, channel and message helpers | 40fbbe3bf |
| `ApiStabilityAnalysis::optional_tagged_base` | typeparser/api_stability_inheritance.rs | 60 | 15 | per-symbol step, base/member helpers | 5c5ebbb70 |
| `run_leaking_requirements`, `parse_leaked_requirements` | rules/leaking_requirements.rs | 58, 40 | 25 | type collection, check, format and member-context helpers | b935815dd |
| `match_custom_pattern` | rules/deterministic_keys.rs | 57 | 10 | argument and annotation helpers | aeb25007b |
| `analyze_multiple_effect_provide` | rules/multiple_effect_provide.rs | 56 | <10 | `ProvideChunk`, `layer_provide_call` | 1e3645d58 |
| `TypeParser::data_first_or_last_call` | typeparser/data_first_signature.rs | 55 | 21 | argument, order and candidate helpers | 027441f5c |
| `SignatureTypeMatcher::compare_type` | typeparser/pipeable_signature_shape.rs | 55 | <25 | `_body`, structured/conditional/leaf helpers, `compare_type_pairs` | e68f4fb3b |
| `SignatureTypeMatcher::compare_anonymous_object` | typeparser/pipeable_signature_shape.rs | 29 | <25 | property and index-info helpers | e68f4fb3b |
| `check_service_dependencies` | rules/missing_effect_service_dependency.rs | 54 | 13 | layer, dependency and diagnostic helpers | 39bbed39b |


| `collect_type_literal_provenance` | typeparser/api_stability_p3.rs | 54 | <25 | per member kind | 7044c94c2 |
| `collect_anonymous_surface`, `collect_function_signatures` | typeparser/api_stability_p3.rs | 38, 29 | <25 | instantiated/declared member helpers, `merge_signature_surfaces`, `declared_signature_member` | 718d669c3 |
| `substitute`, `collect_mapped_surface` | typeparser/api_stability_p3.rs | 29, 26 | <25 | `frame_replacement`; `merge_mapped_component`, `mapped_type_declaration_nodes` | 84394b464 |
| `collect_concrete_reference_surface` | typeparser/api_stability_p2.rs | 53 | <25 | members/signatures/index helpers; `merge_public_property_surfaces` shared with p3 | aab899dbd |
| `collect_type_surface` | typeparser/api_stability_p2.rs | 38 | <25 | `enter_alias_surface`, `collect_type_kind_surface`, `merge_shallow_surfaces` | 5fa0ee778 |
| `represented_contains_type_parameter`, `contains_substituted_parameter` | typeparser/api_stability_p2.rs | 36, 34 | <10 | `type_containment_operands(_with_substitution)` | 5fa0ee778 |
| `collect_symbol_surface`, `collect_object_surface` | typeparser/api_stability_p2.rs | 31, 27 | <25 | export-assignment/declared-or-value helpers; reference object helper | af4a5c71e |
| `EffectPluginOptions::to_value` | etscore.rs | 52 | <25 | option-group inserters, `override_value` | c19b17dc0 |
| `run_strict_boolean_expressions` | rules/strict_boolean_expressions.rs | 52 | 10 | operand and type-name helpers | 03c52a5d7 |
| `check_service_property_types` | rules/non_object_effect_service_type.rs | 50 | 14 | per-option check, lazy `any_call_return_type` | bd71c24ab |
| `Tracker::collapse_piping_flow_transformations` | ls/rewriter/piping_flow.rs | 48 | <20 | step, target, unwrap and rewrite helpers | b889a2545 |
| `analyze_timeout_catch_tag_to_timeout_or_else` | rules/timeout_catch_tag_to_timeout_or_else.rs | 47 | <20 | per-step match | c546efe26 |
| `StabilityApiAllowlist::decide_uncached`, `visit` | rules/stability_api_usage.rs | 46, 35 | <11 | module name, export symbol and entry helpers; identifier/selected-signature stability | 1074d98e0 |
| `ApiStabilitySafetyScan::heritage`, `materialize_argument`, `member_annotation` | typeparser/api_stability_safety.rs | 44, 29, 26 | <25 | `heritage_entry`; `guarded_parameter_value`, `materialize_annotation_if_safe`; `property_member_annotation` | 098209a70 |
| `effect_context_walk` | typeparser/effect_context.rs | 44 | 22 | inherit / pending / generator / thunk helpers | 0f95c6ee1 |
| `analyze_race_first_with_sleep_to_timeout` | rules/race_first_with_sleep_to_timeout.rs | 42 | 12 | `race_arms` | 3be524c7f |
| `check_missing_pipeable_signatures` | rules/missing_pipeable_signature.rs | 37 | 15 | `pipeable_signature_pairs` | c5ba0ab5b |
| `create_string` | keybuilder.rs | 37 | 11 | file name, directory and join helpers | 1bb78c34c |
| `stability_of_declaration_tag` | typeparser/api_stability_p1.rs | 36 | <20 | `stability_tag_value` | 80e465781 |
| `TypeParser::extends_schema_tagged` | typeparser/extends_schema_tagged.rs | 37 | 14 | call-shape and literal helpers | ca19028da |
| `analyze_prefer_unsafe_constructor_node` | rules/prefer_unsafe_constructor.rs | 38 | 19 | effect and sibling helpers | c0c36f87b |
| `analyze_missed_pipeable_opportunity` | rules/missed_pipeable_opportunity.rs | 35 | 10 | `first_missed_pipeable_segment` | 22b2c3083 |
| `analyze_schema_struct_with_tag_node` | rules/schema_struct_with_tag.rs | 35 | 8 | match and literal helpers | 81a4f0ffa |
| `analyze_flat_map_conditional_to_filter_or_fail` | rules/flat_map_conditional_to_filter_or_fail.rs | 35 | 24 | `unassigned_simple_parameter_symbol` | 31ed37901 |

## Left (not yet done; for a continuation lane)

Cog at head, by rust-code-analysis. None is hard; the lane stopped for its
context budget, not for a risk.

| Function | File | Cog |
|---|---|---:|
| `SchemaGen::process_node`, `process_members`, `StructuralSchemaGen::process` | ls/schemagen.rs | 39, 29, 26 |
| `run_effect_schema_self_in_classes` | ls/completions/effect_schema_self_in_classes.rs | 36 |
| `parse_effect_fn_opportunity_inner` | typeparser/effect_fn_opportunity.rs | 35 |
| `analyze_catch_all_to_map_error`, `analyze_catch_to_or_else_succeed` | rules/catch_all_to_map_error.rs, rules/catch_to_or_else_succeed.rs | 34, 31 (share a catch/catchAll callee and name helper) |
| `analyze_layer_merge_all_call` | rules/layer_merge_all_with_dependencies.rs | 34 |
| `run_promise_in_effect_success` | rules/promise_in_effect_success.rs | 34 |
| `run_write_tag_class_accessors` | ls/refactors/write_tag_class_accessors.rs | 33 |
| `reconstruct_piping_flow` | typeparser/reconstruct.rs | 31 |
| `run_schema_literal_non_finite` (and its `visit`) | rules/schema_literal_non_finite.rs | 30, 29 |
| `analyze_unsafe_effect_type_assertion` | rules/unsafe_effect_type_assertion.rs | 30 |
| `effect_fn_build_replacement` | ls/fixables/effect_fn_opportunity.rs | 29 |
| `run_service_not_as_class_fix` | ls/fixables/service_not_as_class.rs | 29 |
| `format_layer_hover` | ls/hover.rs | 28 |
| `try_convert_to_datafirst` | ls/refactors/pipeable_to_datafirst.rs | 28 |
| `check_extends_native_error` | rules/extends_native_error.rs | 28 |
| `analyze_run_of_exit_to_run_exit` | rules/run_of_exit_to_run_exit.rs | 28 |
| `evaluate_constant_expression` | typeparser/constant_evaluation.rs | 28 |
| `run_prefer_succeed_some_or_none_fix` | ls/fixables/prefer_succeed_some_or_none.rs | 26 |
| `remove_exit_transformation` | ls/fixables/run_of_exit_to_run_exit.rs | 26 |
| `run_global_timers` | rules/global_timers.rs | 26 |
| `analyze_match_effect_to_match` | rules/match_effect_to_match.rs | 26 |

## Notes

- No behaviour change was intended anywhere. Two edge cases moved from a
  panic to Go's answer: `unquoted_module_specifier` (module_identifier.rs)
  slices bytes lossily like Go and `find_layer_module_name` did, where
  `find_module_identifier*` sliced a `str` (a panic on a module specifier
  whose last byte is inside a multi-byte character, which a parsed string
  literal cannot produce).
- Every split keeps the order of checker calls (type reads, signature
  resolution) of the original, including short-circuits: lazy `any` over
  signatures and operands, never an eager collect of resolved types.
- New file: `rules/module_references.rs` (and its `pub mod` line in
  `rules/mod.rs`). No other file outside `effect/` changed.
- No switch added or removed.

## Evidence

- Effect area by `quality.sh` (own code > 25): 85 rows / Σ excess 1,312 at
  `f59c229c5` → 24 rows / 124.
- Tests: workspace `test --release` plain 2,121 passed, with
  `diagnostics,test-hooks` 2,143 passed, 0 failed; `effect_cases`
  1,060/1,060 (also at the final commit).
- Clippy 1.95.0 `-D warnings` (CI command), plain and with the features: ok.
- Parity full: PARITY OK, 0 lost
  (`~/.cache/perf-rearch/parity/runs/20261109-191432-full-c8b54ec71b`).
- icount `--profile goport`, base `f59c229c5`: all 9 cells within ±0.07%.
  Effect on (`TSC_RS_EFFECT=1`, projects/effect `packages/effect`,
  `--singleThreaded --noEmit`): 26.601 G → 26.601 G (−0.002%), same stdout.
