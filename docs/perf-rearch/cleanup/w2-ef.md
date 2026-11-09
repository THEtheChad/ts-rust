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
| `find_layer_module_name` | layergraph.rs | 26 | <10 | shared named-import helper | (module ids) |
| `find_effect_module_identifier`, `find_module_identifier`, `find_module_identifier_for_package` | typeparser/module_identifier.rs | 34, 35, 35 | <10 | first two call the third; one per-import helper | (module ids) |
| `format_nested_layer_graph` | layergraph.rs | 33 | <20 | node subgraph and edge helpers | bb9ec0991 |
| `extract_outline_graph` | layergraph.rs | 29 | 20 (`connect_requirers_to_providers`) | step helpers | bb9ec0991 |
| `TypeParser::compute_piping_flows` | typeparser/piping_flow.rs | 95 | <20 | `PipingFlowWalk`, one method per call shape | (piping) |
| `decompose_curried_pipeable` | typeparser/piping_flow.rs | 26 | 23 | `signature_declaration_symbol` | (piping 2) |
| `build_effect_fn_transformations` | typeparser/piping_flow.rs | 26 | <20 | `effect_fn_transformation_callee_and_args` | (piping 2) |
| `type_node_mentions_unbound_parameter_scoped` | typeparser/api_stability_safety.rs | 79 | <25 | shared structural walk + `_body` | fe01b0564 |
| `type_node_mentions_replaced_parameter` | typeparser/api_stability_safety.rs | 63 | <25 | same | fe01b0564 |
| `type_node_mentions_infer` | typeparser/api_stability_safety.rs | 48 | <25 | same | fe01b0564 |
| `contains_unbound_parameter`, `contains_replaced_parameter` | typeparser/api_stability_safety.rs | 44, 44 | <25 | `type_containment_operands` + `_body` | fe01b0564 |
| `function_like_mentions_unbound_parameter` | typeparser/api_stability_safety.rs | 33 | <25 | `function_like_own_scope` | fe01b0564 |
| `type_node_mentions_type_parameter_body` | typeparser/api_stability_p3.rs | 39 | <25 | shared structural walk | fe01b0564 |
| `analyze_obsolete_match_import`, `analyze_obsolete_schema_import` | rules/obsolete_*_import.rs | 56, 55 | <10 | `package_module_references` | (module refs) |
| `analyze_node_builtin_import` | rules/node_builtin_import.rs | 46 | <10 | `top_level_module_references` | (module refs) |
| `run_any_unknown_in_error_context` | rules/any_unknown_in_error_context.rs | 70 | <10 | skip, channel and message helpers | 9c2326787 |
| `ApiStabilityAnalysis::optional_tagged_base` | typeparser/api_stability_inheritance.rs | 60 | 15 | per-symbol step, base/member helpers | 970cca02e |
| `run_leaking_requirements`, `parse_leaked_requirements` | rules/leaking_requirements.rs | 58, 40 | 25 | type collection, check, format and member-context helpers | (leaking) |
| `match_custom_pattern` | rules/deterministic_keys.rs | 57 | 10 | argument and annotation helpers | (det keys) |
| `analyze_multiple_effect_provide` | rules/multiple_effect_provide.rs | 56 | <10 | `ProvideChunk`, `layer_provide_call` | (provide) |
| `TypeParser::data_first_or_last_call` | typeparser/data_first_signature.rs | 55 | 21 | argument, order and candidate helpers | (data first) |
| `SignatureTypeMatcher::compare_type` | typeparser/pipeable_signature_shape.rs | 55 | <25 | `_body`, structured/conditional/leaf helpers, `compare_type_pairs` | (shape) |
| `SignatureTypeMatcher::compare_anonymous_object` | typeparser/pipeable_signature_shape.rs | 29 | <25 | property and index-info helpers | (shape) |
| `check_service_dependencies` | rules/missing_effect_service_dependency.rs | 54 | 13 | layer, dependency and diagnostic helpers | (service deps) |

Commit hashes in parentheses are filled in at the end (`git log`).

## Left (not yet done)

Everything else over 25 in the ranking at the base; see the final report.
