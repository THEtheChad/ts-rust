# W2-EF: Effect (`crates/ts_goport/src/effect/`)

Lane W2-EF of wave 2 (code-quality phase). Own code (a port of
Effect-TS/tsgo, no counterpart at the TypeScript-Go pin), so the limit is
cognitive complexity 25. Base `perf/first-principles` @ `f59c229c5` (rebased in round 2 onto `1d0b7ff21`): 85 rows
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
| `extract_layer_graph` | layergraph.rs | 133 | 12 | `LayerGraphExtractor` with one method per Go case | 58bd40610 |
| `find_layer_module_name` | layergraph.rs | 26 | <10 | shared named-import helper | c6d4edcb0 |
| `find_effect_module_identifier`, `find_module_identifier`, `find_module_identifier_for_package` | typeparser/module_identifier.rs | 34, 35, 35 | <10 | first two call the third; one per-import helper | c6d4edcb0 |
| `format_nested_layer_graph` | layergraph.rs | 33 | <20 | node subgraph and edge helpers | 7bd7bb69a |
| `extract_outline_graph` | layergraph.rs | 29 | 20 (`connect_requirers_to_providers`) | step helpers | 7bd7bb69a |
| `TypeParser::compute_piping_flows` | typeparser/piping_flow.rs | 95 | <20 | `PipingFlowWalk`, one method per call shape | 2d1bd53b7 |
| `decompose_curried_pipeable` | typeparser/piping_flow.rs | 26 | 23 | `signature_declaration_symbol` | ed44d6558 |
| `build_effect_fn_transformations` | typeparser/piping_flow.rs | 26 | <20 | `effect_fn_transformation_callee_and_args` | ed44d6558 |
| `type_node_mentions_unbound_parameter_scoped` | typeparser/api_stability_safety.rs | 79 | <25 | shared structural walk + `_body` | 28cb549f6 |
| `type_node_mentions_replaced_parameter` | typeparser/api_stability_safety.rs | 63 | <25 | same | 28cb549f6 |
| `type_node_mentions_infer` | typeparser/api_stability_safety.rs | 48 | <25 | same | 28cb549f6 |
| `contains_unbound_parameter`, `contains_replaced_parameter` | typeparser/api_stability_safety.rs | 44, 44 | <25 | `type_containment_operands` + `_body` | 28cb549f6 |
| `function_like_mentions_unbound_parameter` | typeparser/api_stability_safety.rs | 33 | <25 | `function_like_own_scope` | 28cb549f6 |
| `type_node_mentions_type_parameter_body` | typeparser/api_stability_p3.rs | 39 | <25 | shared structural walk | 28cb549f6 |
| `analyze_obsolete_match_import`, `analyze_obsolete_schema_import` | rules/obsolete_*_import.rs | 56, 55 | <10 | `package_module_references` | 7fc3911b9 |
| `analyze_node_builtin_import` | rules/node_builtin_import.rs | 46 | <10 | `top_level_module_references` | 7fc3911b9 |
| `run_any_unknown_in_error_context` | rules/any_unknown_in_error_context.rs | 70 | <10 | skip, channel and message helpers | 928677cd1 |
| `ApiStabilityAnalysis::optional_tagged_base` | typeparser/api_stability_inheritance.rs | 60 | 15 | per-symbol step, base/member helpers | 8f141a35b |
| `run_leaking_requirements`, `parse_leaked_requirements` | rules/leaking_requirements.rs | 58, 40 | 25 | type collection, check, format and member-context helpers | 45295bb23 |
| `match_custom_pattern` | rules/deterministic_keys.rs | 57 | 10 | argument and annotation helpers | 0f6925e95 |
| `analyze_multiple_effect_provide` | rules/multiple_effect_provide.rs | 56 | <10 | `ProvideChunk`, `layer_provide_call` | 712aaeb30 |
| `TypeParser::data_first_or_last_call` | typeparser/data_first_signature.rs | 55 | 21 | argument, order and candidate helpers | 7a309d9b8 |
| `SignatureTypeMatcher::compare_type` | typeparser/pipeable_signature_shape.rs | 55 | <25 | `_body`, structured/conditional/leaf helpers, `compare_type_pairs` | 10be12230 |
| `SignatureTypeMatcher::compare_anonymous_object` | typeparser/pipeable_signature_shape.rs | 29 | <25 | property and index-info helpers | 10be12230 |
| `check_service_dependencies` | rules/missing_effect_service_dependency.rs | 54 | 13 | layer, dependency and diagnostic helpers | 151e390ac |


| `collect_type_literal_provenance` | typeparser/api_stability_p3.rs | 54 | <25 | per member kind | 866f40087 |
| `collect_anonymous_surface`, `collect_function_signatures` | typeparser/api_stability_p3.rs | 38, 29 | <25 | instantiated/declared member helpers, `merge_signature_surfaces`, `declared_signature_member` | 67cbffe91 |
| `substitute`, `collect_mapped_surface` | typeparser/api_stability_p3.rs | 29, 26 | <25 | `frame_replacement`; `merge_mapped_component`, `mapped_type_declaration_nodes` | 1f381b1cc |
| `collect_concrete_reference_surface` | typeparser/api_stability_p2.rs | 53 | <25 | members/signatures/index helpers; `merge_public_property_surfaces` shared with p3 | f5ed50c33 |
| `collect_type_surface` | typeparser/api_stability_p2.rs | 38 | <25 | `enter_alias_surface`, `collect_type_kind_surface`, `merge_shallow_surfaces` | 1b8bd08e6 |
| `represented_contains_type_parameter`, `contains_substituted_parameter` | typeparser/api_stability_p2.rs | 36, 34 | <10 | `type_containment_operands(_with_substitution)` | 1b8bd08e6 |
| `collect_symbol_surface`, `collect_object_surface` | typeparser/api_stability_p2.rs | 31, 27 | <25 | export-assignment/declared-or-value helpers; reference object helper | 040740e3f |
| `EffectPluginOptions::to_value` | etscore.rs | 52 | <25 | option-group inserters, `override_value` | 1ff65626c |
| `run_strict_boolean_expressions` | rules/strict_boolean_expressions.rs | 52 | 10 | operand and type-name helpers | 65ca2835b |
| `check_service_property_types` | rules/non_object_effect_service_type.rs | 50 | 14 | per-option check, lazy `any_call_return_type` | 02552821a |
| `Tracker::collapse_piping_flow_transformations` | ls/rewriter/piping_flow.rs | 48 | <20 | step, target, unwrap and rewrite helpers | a76a0a242 |
| `analyze_timeout_catch_tag_to_timeout_or_else` | rules/timeout_catch_tag_to_timeout_or_else.rs | 47 | <20 | per-step match | 2358b2263 |
| `StabilityApiAllowlist::decide_uncached`, `visit` | rules/stability_api_usage.rs | 46, 35 | <11 | module name, export symbol and entry helpers; identifier/selected-signature stability | c244668f5 |
| `ApiStabilitySafetyScan::heritage`, `materialize_argument`, `member_annotation` | typeparser/api_stability_safety.rs | 44, 29, 26 | <25 | `heritage_entry`; `guarded_parameter_value`, `materialize_annotation_if_safe`; `property_member_annotation` | 15fababc6 |
| `effect_context_walk` | typeparser/effect_context.rs | 44 | 22 | inherit / pending / generator / thunk helpers | 4bba8d8cd |
| `analyze_race_first_with_sleep_to_timeout` | rules/race_first_with_sleep_to_timeout.rs | 42 | 12 | `race_arms` | 76e784b65 |
| `check_missing_pipeable_signatures` | rules/missing_pipeable_signature.rs | 37 | 15 | `pipeable_signature_pairs` | 9e9a5fa16 |
| `create_string` | keybuilder.rs | 37 | 11 | file name, directory and join helpers | d7e6be944 |
| `stability_of_declaration_tag` | typeparser/api_stability_p1.rs | 36 | <20 | `stability_tag_value` | 196f7eff8 |
| `TypeParser::extends_schema_tagged` | typeparser/extends_schema_tagged.rs | 37 | 14 | call-shape and literal helpers | 9d633531b |
| `analyze_prefer_unsafe_constructor_node` | rules/prefer_unsafe_constructor.rs | 38 | 19 | effect and sibling helpers | f88b40a7c |
| `analyze_missed_pipeable_opportunity` | rules/missed_pipeable_opportunity.rs | 35 | 10 | `first_missed_pipeable_segment` | 55e2e78df |
| `analyze_schema_struct_with_tag_node` | rules/schema_struct_with_tag.rs | 35 | 8 | match and literal helpers | b62ae7008 |
| `analyze_flat_map_conditional_to_filter_or_fail` | rules/flat_map_conditional_to_filter_or_fail.rs | 35 | 24 | `unassigned_simple_parameter_symbol` | 4f9769c29 |

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

- **One intended behaviour change** (Effect extension only; found by
  Astra's round-1 review). `unquoted_module_specifier`
  (typeparser/module_identifier.rs) slices the specifier's bytes lossily,
  as the Go reference and the old `find_layer_module_name` did. The old
  `find_effect_module_identifier`, `find_module_identifier` and
  `find_module_identifier_for_package` sliced a `str`, which panicked when
  the specifier text ends inside a multi-byte character. A parsed string
  literal does produce that: an unterminated specifier
  (`import { X } from "é`, `import * as E from "é`, `import "é`), also
  when it comes from a UTF-16 file or from invalid UTF-8 (decoded to a
  multi-byte character). With `TSC_RS_EFFECT=1`, `textDocument/codeAction`
  on such a file answered `-32603` (a panic) and now answers the Effect
  refactors. Plain tsgo never runs these lookups. The 1,060 diagnostic
  cases don't reach this path; `tests/effect_lsp_module_specifier.rs` pins
  the five cases (it fails 5 of 6 with the old slicing).
- Apart from that, no behaviour change was intended. Astra's round-1 probes
  (31 CLI probes, 27 code-action requests) matched base.
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
