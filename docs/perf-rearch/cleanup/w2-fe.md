# W2-FE: frontend, build and emit, and the rest outside the checker

Lane W2-FE of wave 2 (code-quality phase). Brief: `w2-brief.md`. Base
`perf/first-principles` @ `1d0b7ff21` (code at `372112cf3`). Files: `frontend/`
(scanner, parser, compiler, module, tsoptions, tspath, vfs, json, outputpaths,
nativepath), `execute/`, `cmd/`, `emitter/`, `printer/`, `transformers/`,
`declarations/`, `sourcemap/`, `binder/`, `ast/`, `api/`, `project/`, `lsp/`,
`evaluator.rs`, `jsnum.rs`, `pprof.rs`; not the Q6 files (`program.rs`,
`program/reuse_verify.rs`, `program/ls_program.rs`, `emitter/program_emit.rs`,
`ast/synthetic*`), not `gostd/`/`locale.rs`.

Figures are cognitive complexity from `quality.sh ranking` (Rust / Go, Δ), or
rust-code-analysis on the file for the parts. "Before" is `1d0b7ff21`, "after"
the ranking at `ea95d0583`.

The lane owner did the hot frontend targets; four helper agents in their own
worktrees did the rest from a shared brief (A: json and the Go stdlib ports, B:
build and vfs, C1: printer, compiler host, module, evaluator, transformers, C2:
project, lsp, api). Their commits are cherry-picked in that order.

## Port bloat targets

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| get_normalized_absolute_path | tspath/path | 76 / 3 (+73) | 3 / 3 (0) | Go's body is `getNormalizedAbsolutePathFromNormalizedSlashes` (path.go:424); the port had inlined it. Split back out (68 + its trailing-separator fix 3, "part of Go", against Go's 72), `#[inline]`. | 63f8d8799 |
| FileLoader::resolve_imports_and_module_augmentations | compiler/file_loader | 112 / 53 (+59) | 58 / 53 (+5) | The worker prep (loadpar1), its commit (loadpar3b), the debug commit check and the last-to-first resolve-ahead (loadcost) are helpers: `take_fitting_prep` 2, `take_prep_commit` 2, `apply_prep_commit` 5, `check_prep_commit` 7 (debug builds), `resolve_module_names_ahead` 4, `take_worker_answer` 0. `ModuleNameReader` (name and mode of one module name; the three copies of the empty/source-phase test and mode lookup are one) and `resolve_module_name_for_file` (Go's resolve, error-once and trace step). A type alias drops a `type_complexity` allow. | 8d5c5a9b7 |
| Scanner::scan | scanner/scanner_p1 | 268 / 224 (+44) | 258 / 224 (+34) | The ASCII comment fast paths set the position themselves (`skip_ascii_line_comment`, `skip_ascii_multi_line_comment`, `#[inline(always)]`), so the comment cases keep Go's loop shape. Left: Go's `case '0'` stays nested in the digits arm (see "Scan"), the skip-trivia and ASCII identifier fast paths, the labeled breaks. | d53bfb119, fc4751a00 |
| Parser::reparse_hosted | parser/reparser | 205 / 173 (+32) | 162 / 173 (−11) | Go writes node fields in place; each write was a `mutate` closure inline. They are setters beside the existing `set_type`/`set_expression`: `set_type_parameters`, `set_type_alias_type`, `set_phase_modifier`, `set_object_assignment_initializer`, `set_binary_right`, `set_parameter_type_and_question` (one data change, as before), `set_heritage_clause_types`, `set_heritage_clauses`. | ec511e034 |
| Orchestrator::build_all_tasks | execute/build/orchestrator | 56 / 24 (+32) | 5 / 24 | (B) The schedule state is `TaskSchedule` (own code). Back to Go: `take_tasks` (part of `rangeTasks`), `build_or_clean_project_start` (part of `buildOrCleanProject`), `report_built_tasks` (part of `buildOrCleanOrder`). Own machinery ≤ 5 each. | 2e2630a9c |
| FilesParser::parse | compiler/files_parser | 30 / 0 (+30) | 3 / 0 | Go's `parse` is start and wait. The worker pool setup is `prefetch_pool_for_load` 13, `start_prefetch_pool` 10, `set_prefetch_redirects` 1; the worker run `parse_with_workers` 3. | a5c569256 |
| BuildTask::get_up_to_date_status | execute/build/build_task | 164 / 142 (+22) | 145 / 142 (+3) | (B) The prefetch-or-make choices are methods of `StatusParts` (own); Go's memoized root info reader is a `OnceCell`; the upstream-skip test is one let-chain again. | 34ae50cdb |
| EsDecoratorTransformer::partial_transform_class_element | transformers/estransforms/es_decorator | 62 / 47 (+15) | 51 / 47 (+4) | (C1) Three defensive `get_mut` re-lookups are `#[inline] member_info_mut` (expects the entry inserted just above). Left: the else-if chain for Go's tagless switch. | f616b358b |
| get_source_file_symbol_index | api/session_p1 | 40 / 26 (+14) | 20 / 26 | (C2) Go's recursive `addSymbol` closure is `add_symbol` (part of Go, work list). | e863f1c13 |
| Parser::reparse_unhosted | parser/reparser | 34 / 20 (+14) | 22 / 20 (+2) | The same setters. | ec511e034 |
| CompilerHostImpl::get_source_file | compiler/host | 15 / 1 (+14) | 1 / 1 | (C1) The worker-prefetch reads are `read_source_file`, `read_plain_os_source_file`, `read_other_fs_source_file` (`#[inline]`). | 54e019d6f |
| equate_string_case_insensitive | tspath/path | 14 / 0 (+14) | removed | tspath had private copies of `stringutil`'s comparers and `strings.EqualFold`; it uses `stringutil_ls`'s (see Duplication). | ec16e7021 |
| subtree_facts_of_data | ast/node | 65 / 52 (+13) | left | One match for Go's 72 `computeSubtreeFacts` methods: each arm's test sits one nesting level deeper than in its Go method. No bloat to cut. | — |
| ProjectLoadingProgress::run | lsp/progress | 50 / 37 (+13) | 40 / 37 (+3) | (C2) The port's stand-in for Go's `select` is `select_progress`, `wake_on_done`. | df426eb2b |
| ProjectReferenceParser::init_mapper_worker | compiler/project_references | 31 / 18 (+13) | 17 / 18 | (C1) Go's single test is one let-chain; `maps.Copy` is `maps_copy`. | 2e9109574 |
| compute_common_source_directory_of_filenames | outputpaths | 14 / 1 (+13) | 1 / 1 | (C1) At the pin Go's loop is `CaseSensitivity.CommonDirectoryOfFiles`: `common_directory_of_files` (14, Go 18). It keeps the port's older behaviour (see Notes). | ea46852af |
| Snapshot::clone_ (+ clone_body) | project/snapshot | 95 / 83 (+12) | 78 / 83 | (C2) Go's tagged `switch change.reason` is a `match`; the orphan program release is `release_programs_in_no_snapshot`. | b61328b39 |
| BuildTask::compile_and_emit_finish (+ start) | execute/build/build_task | 25 / 13 (+12) | 13 / 13 | (B) The early-start machinery is four own helpers; `on_written_build_info` is Go `writeFile`'s `onBuildInfoEmit` branch. | 95f01524f |
| get_node_at_position (+ split node_at) | ast/utilities_p3 | 24 / 12 (+12) | 12 / 12 | Mapping only: the cursor's descent (loadcrit1) carried Go's marker, so it was summed in. It is own code (12). | e22090e65 |
| Session::start_performance_telemetry | project/session | 19 / 7 (+12) | 4 / 7 | (C2) The ticker stand-in is `run_local_ticker`. | 4b2e3f682 |
| load_entrypoints_from_export_map (+ closure) | module/entrypoints | 92 / 81 (+11) | 70 / 81 | (C1) `if_else` for Go's two `core.IfElse`, `strings.Cut` as `split_once`, `get_or_insert_with`. | 8f2e58bef |
| new_evaluator (+ evaluate_expression) | evaluator | 40 / 29 (+11) | 32 / 29 (+3) | (C1) Go's two-value type assertions are `number_value`, `string_value` (`#[inline]`). | 7eadbcb02 |
| Printer::get_separating_line_terminator_count | printer/printer_p1 | 28 / 17 (+11) | 17 / 17 | (C1) `if_else` for Go's `core.IfElse`. Same for `get_leading_line_terminator_count` 31 → 21 (Go 21) and `get_closing_line_terminator_count` 27 → 19 (Go 19). | d9d328ef8 |
| Scanner::scan_template_and_set_token_value | scanner/scanner_p2 | 23 / 12 (+11) | 14 / 12 | `if_else` for Go's three `core.IfElse`. | afdc78c9c |
| install_npm_packages (+ async) | project/ata/ata | 16 / 5 (+11) | 5 / 5 | (C2) The `tg.Wait()` poll is `throttle_group_wait`. | 4ab60094e |
| new_type_response | api/proto | 31 / 21 (+10) | left | All +10 is the 12-arm else-if chain for Go's tagless switch. | — |
| Printer::get_leading_line_terminator_count | printer/printer_p1 | 31 / 21 (+10) | 21 / 21 | See above. | d9d328ef8 |
| DefaultResolver::resolve_module_name_worker | module/resolver_p1 | 29 / 19 (+10) | 19 / 19 | (C1) The worker cache lookup and the shared-cache publish are `take_worker_module_resolution`, `publish_module_resolution` (`#[inline]`); Go's switch is a match. | 89050a7cc |
| OsFs::chtimes | vfs/osvfs | 11 / 1 (+10) | 1 / 1 | (B) Go stdlib `os.Chtimes` is `os_chtimes`; one cfg copy of the network-path test. | 5324248e5 |
| DeclarationTransformer::transform_source_file | declarations/transform | 24 / 15 (+9) | 19 / 15 | Go's two pre-walks with the port's skip test (PERF) are `collect_cjs_exports_and_expando_members`. | 1de4f63aa |
| C2's near-targets | project/session | +8, +8, +7, +7 | | `get_snapshot` 39 → 31 (Go 31), `schedule_snapshot_update` 21 → 11 and `schedule_diagnostics_refresh` 21 → 8 (one `enqueue_debounced`), `log_cache_stats` 34 → 25, `warm_auto_import_cache` 32 → 30. | a39f6e133, 1fc505a66, 3ecc09351, ea95d0583 |

### Scan

d53bfb119 also wrote Go's `case '0'` (the hex, binary and octal prefixes) as
three guarded match arms before the digits arm, which took `scan` to 234 / 224.
icount found the cost (`--profile goport`, `icount_ab.sh`, 2 or 3 runs a side):

| Pair | query noemit-st | umami noemit-st | hono noemit-st |
|---|---:|---:|---:|
| base `1d0b7ff21` → `0b1a04c3d` (all frontend commits to then) | +0.226% | +0.132% | +0.110% |
| `0b1a04c3d` → the same with scanner_p1.rs at the base (exp1) | −0.229% | −0.128% | −0.106% |
| `a505e68cf` → the same with `case '0'` nested again (exp2) | −0.164% | −0.094% | −0.079% |

So the guarded arms cost 0.08 to 0.16% (they change the lowering of the
hot match), and the comment helpers about 0.03 to 0.06%. fc4751a00 nests the
prefixes again (258 / 224); the comment helpers stay. A cold out-of-line
helper for the three prefixed literals would take `scan` near 236 without the
guards; it is a split Go lacks, so it is left for root to decide.

## Own-code targets

| Target | File | Before | After | What | Commit |
|---|---|---|---|---|---|
| merge_symbol_ids | ast/utilities_p1 | 97 | 1 | By stage: `drop_shared_cells` 9 with `drop_losing_cells` 8 (the clock contest of one cell), `renumber_global_ids` 9, `renumber_id_blocks` 5, `WorkerSymbolIds::merge_into` 7 with `keeps_numbers` 3, `block_moves` 1, `renumber_cells` 11, `new_id` 1, `CellRenumber::block_cell_move` 2, `LineageIds::install_chunk` 4. | a5c62d4e9 |
| JsonDecoder::go_consume_value | frontend/json | 75 | 3 | (A) Keeps its own stack (no recursion); Go's parts `consume_value_step`, `consume_open`, `consume_object_name`, `consume_after_value` (≤ 8), `consume_literal_raw`, `literal_text`. | c535fb767 |
| iterate_comment_ranges | declarations/transform | 69 | removed | A second copy of the scanner's `iterateCommentRanges` with copies of the shebang helpers and a rune decoder: the transformer calls the scanner's (see Duplication). | 034ed45ae |
| base64_std_encoding_decode_string (api) | api/session_p2 | 52 | removed | Two copies of Go `StdEncoding.DecodeString`: `gostd/base64.rs` (see Duplication). | 572248ec5 |
| base64_std_encoding_decode_string (sourcemap) | sourcemap/source_mapper | 51 | removed | Same. | 572248ec5 |
| WorkerResolver::resolve | compiler/files_parser | 49 | 7 | `publish_package_scope_ahead` 3, `redirect_of_file` 1, `resolve_type_reference_directives_ahead` 8, `resolve_imports_ahead` 21, `take_prep_answer` 1, `PrepCommit::insert_ambient_module` 0. | b8e91f74a |
| pow_with | jsnum | 49 | 15 | (A) Go's leading switch is `pow_special_case` 16 and `pow_zero_base` 11; Go's repeated overflow switch is one `pow_overflow`. | 0ac97beed |
| filepath_clean (windows) | vfs/osvfs | 48 | 15 | (B) `filepath_clean_dot_dot`, `filepath_clean_element`, `LazyBuf::append`, `filepath_post_clean`. Old and new compared on 2.67 M paths. | 8f5ee2de0 |
| JsonDecoder::consume_string_raw | frontend/json | 45 | 5 | (A) By byte kind: `consume_escape`, `consume_unicode_escape` 12, `decode_surrogate_pair`, `push_ascii_run`, `consume_utf8_rune`. | ad563176b |
| Binder::get_declaration_name_of_kind | binder/binder_p1 | 43 (own) | 36 / 32 | Mapping: the body of Go `getDeclarationName` (the marker is on the wrapper). `if_else` for Go's `core.IfElse`; the private-name class note is `note_private_name_class`. | bd77478cf |
| Parser::check_js_syntax_in_js_file | parser/parser_p5 | 40 (own) | 40 + 1 / 38 | Mapping only: the body of Go `checkJSSyntax` after its JS test. | a26378b91 |
| worker_symbol_ids | ast/utilities_p1 | 39 | 0 | `take_worker_id_log` 1, `worker_id_chunks` 15, `note_cell_lineages` 9. | a5c62d4e9 |
| eval_symlinks | nativepath | 39 | 20 | (B) `next_path_component`, `last_slash_after_volume`, `back_up_dot_dot`, `walk_into_link`. | 39817d36c |
| flush_serialized_client_writes | execute/incremental/emit_files | 36 | 3 | (B) `next_serialized_round`, `flush_doomed_round`, `call_serialized_round`, `write_serialized_round`; `HeldCall`. | 382fa17a6 |
| go_path_clean | execute/tsc/compile | 35 | removed | (B) A copy of the unix `filepath_clean`; `look_path`, `go_abs` call that one. | a64ed75d7 |
| run_prefetch_worker | compiler/files_parser | 35 | 13 | `next_prefetch_job` 11, `claim_prefetch_job` 1, `parse_prefetch_job` 4, `WorkerResolverSlot` (the resolver, its failure after a panic, and the first-load end, whose `Drop` replaces the `EndAhead` guard in the same drop order). | 84a27c533 |
| filepath_clean (unix) | vfs/osvfs | 35 | 12 | (B) Same helpers. | 8f5ee2de0 |
| elf_build_id | pprof | 35 | 23 | (A) `byte_order_uint`, `hex_lower`; otherwise Go's shape (Go 33). | f042d05b9 |
| JsonDecoder::next_token | frontend/json | 33 | 1 | (A) Go's stateMachine methods: `read_literal`, `read_string`, `push_container`, `pop_object`, `pop_array` (≤ 7, `#[inline]`). | 220da1626 |
| json_append_multiline_pieces | frontend/json | 31 | 10 | (A) `json_multiline_token` 15. | 67ab66308 |
| parse_rfc3339_nano | api/callbackfs | 30 | 13 | (A) Go time's helpers: `parse_digits`, `days_in`, `parse_nanoseconds`, `parse_zone_offset`, `days_since_epoch`. | d213c3b74 |
| stack_at | frontend/json_ext | 30 | 6 | (A) `string_end`, `scalar_end`, `count_value`, `StackEntry::read_string`. | 5c566d016 |
| export_print_pack | ast/synthetic/print_pack | 30 | skipped | Lane Q6's file (`ast/synthetic*`). | — |
| dump_loader_state | compiler/file_loader | 29 | 4 | Diagnostics builds: `dump_parse_task` 5, `dump_resolver_caches` 1, `push_sorted_lines` 1. | fdcdf505a |
| FlagSet::parse_one | cmd/tsgo/main | 29 | 27 | (A) Only the `=` search is an iterator. Left at 27: a line-by-line port of Go `flag.go parseOne` (Go 35 at go1.27.1) with no Go helper boundary to split at. | b44ca984d |
| access_kind_ref | ast/node | 28 (own) | 24 / 25 | Mapping: the body of Go `accessKind`. The noderead2 one-read accesses are `unary_operator_of`, `binary_left_and_operator_token_of` (`#[inline]`). | 1e819adf3 |
| stack_pointer (json_ext) | frontend/json_ext | 27 | 1 | (A) A second copy of the decoder's `appendStackPointer`: one `append_stack_pointer` (18, Go 13). | 7b0732c07 |
| stringify_json | tsoptions/tsconfig_p2 | 27 | 9 | (A) `stringify_json_array`, `stringify_json_element` (each was written twice). | feaf9a6b8 |

## Duplication

| Block | Done | Commit |
|---|---|---|
| tspath's private `EquateStringCaseInsensitive`/`CaseSensitive`/`GetStringEqualityComparer` with its simple-fold helpers ↔ `stringutil_ls` | tspath uses `stringutil_ls`'s | ec16e7021 |
| vfsmatch `equal_fold` (with its fold helpers) ↔ `stringutil_ls::equal_fold` | one, in `stringutil_ls` (with vfsmatch's ASCII fast path); vfsmatch re-exports it for its other callers | ec16e7021 |
| declarations `iterate_comment_ranges`, `is_shebang_trivia`, `scan_shebang_trivia`, `decode_rune` ↔ scanner `comment_ranges.rs` | the scanner's; its factory is the printer's thread-local, now `scanner::with_comment_range_factory` | 034ed45ae |
| api `base64_std_encoding_decode_string` ↔ sourcemap's; api `..._encode_to_string` ↔ sourcemap generator `base64_std_encode` | `gostd/base64.rs` (new file; one line in `gostd/mod.rs`) | 572248ec5 |
| loader: three copies of the module-name read, the empty/source-phase test and the mode lookup | `ModuleNameReader::resolvable` | 8d5c5a9b7 |
| `dump_loader_state`: four sort-and-write copies | `push_sorted_lines` | fdcdf505a |
| json.rs `stack_pointer` ↔ json_ext `stack_pointer`; json_ext copies of `normalize_kind`, `is_ws`, `skip_ws` | one each in json.rs | 7b0732c07, 7dcb5e9a1 |
| compile.rs `go_path_clean` ↔ osvfs unix `filepath_clean` | osvfs's | a64ed75d7 |
| osvfs: two cfg copies of `chtimes`'s network-path test | one | 5324248e5 |
| session `schedule_snapshot_update` ↔ `schedule_diagnostics_refresh` (debounce, wait group, cancel) | `enqueue_debounced` | 1fc505a66 |
| `stringify_json` array and element writers (two each); `pow`'s repeated overflow switch; `init_mapper_worker`'s two insert loops | one each | feaf9a6b8, 0ac97beed, 2e9109574 |
| reparser: the tail of the typedef and callback cases (an exact copy once the setters were in; Go writes it twice) | `push_reparsed_js_doc_type_alias`, part of Go `reparseUnhosted` | a9d3fe82e |
| stringutil_ls `decode_rune_bytes` ↔ vfsmatch `decode_rune` (a copy once ec16e7021 gave the first the second's ASCII fast path) | vfsmatch uses stringutil_ls's | 42b1168f6 |
| es_decorator: the private-member descriptor block of the method and the accessor-field cases | left: Go repeats it; it shows as "new" only because `member_info_mut` changed its text | — |

## Left on purpose

- `subtree_facts_of_data` +13: the merged method family (see above).
- `new_type_response` +10, `partial_transform_class_element` +4: else-if chains for Go's tagless switches.
- `FlagSet::parse_one` 27: see above.
- Q6's `export_print_pack` (ast/synthetic/print_pack.rs, 30).

## Notes

- `if_else` is imported from `crate::checker` (W2-CB's place) in scanner_p2,
  binder_p1, printer_p1 and module/entrypoints. It is `pub(crate)` in
  goport_core, so ts_goport (project/) cannot use it; C2 kept `if`/`else` for two
  `core.IfElse` in `log_cache_stats`. When root moves it to `core.rs`, a `pub`
  export would let ts_goport use it.
- `common_directory_of_files` (outputpaths) keeps the port's behaviour, which
  is the shape before the pin: it normalizes against the current directory,
  has no encoded-dynamic-file-name case and returns the current directory for
  an empty common path where Go at the pin returns "". C1 left a PORT note. Not
  a change of this lane; it may be a parity gap that no tier covers.
- `member_info_mut` (es_decorator) `expect`s the member info that the function
  inserts just above; the old code skipped silently. Go dereferences it.
- The json, osvfs, tspath, vfsmatch and stringutil files build in
  `goport_util`, not `goport_core`: checks and tests need `-p goport_util`.

## Evidence

All on head `2a932c528` (every code commit of this lane), base `1d0b7ff21`.
Logs, scripts and bins under `~/.cache/perf-rearch/w2-fe/` (`final2.sh`,
`final2.log`).

- **Parity** (`parity.sh full`, release bin of `2a932c528`, base
  `base-current`): `~/.cache/perf-rearch/w2-fe/parity-full-2a932c528`.
  **PARITY OK**: 187,196 items compared, 0 lost, 0 not run, 0 new items not
  good, 0 crashes on good items, 0 newly good by a referee, 36 grandfathered.
  Tiers: cli 170 MATCH; tests 139,910 PASS, 1 FLAKY
  (`tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm`, failed then passed
  alone, as in W2-CA's run), 1,731 ignored; lsp 2,629 MATCH, 29 GO-BROKEN (in
  the base); fuzz 29,955 MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN, 1
  JS-AGREES (in the base); corpus 12,727 MATCH.
  An earlier full run on `ea95d0583` (`parity-full-ea95d0583`) had the same
  tiers but 2 NOT RUN: the gostd/base64 commit had moved two tests;
  a505e68cf puts them back under their names.
- **Tests** (`run-cargo-capped.sh test --release --workspace`, `TS_GO_REPO`
  at the pin, on `a505e68cf`; the later commits are the Scan `'0'` case, a
  reparser helper, the vfsmatch decoder and blobs, all under the parity tests
  tier above): plain 51 binaries, 2,122 passed, 0 failed, 2 ignored; with
  `ts_goport/diagnostics,ts_goport/test-hooks` 52 binaries, 2,144 passed, 0
  failed, 2 ignored.
- **Clippy** (CI's command on 1.95.0, `-D warnings`, on `2a932c528`): clean,
  plain and with the features. Quality report: repo config 0 → 0; the forced
  `clippy::all` audit 1,128 → 1,134 (`type_complexity` +4, `too_many_arguments`
  +2, `large_enum_variant` +1, `if_same_then_else` −1; all allowed lints).
- **icount, CLI** (`icount_ab.sh`, `--profile goport` builds of both sides
  made here, 3 runs per side, interleaved):

  | Cell | Base | Head | Δ | Spread b/h |
  |---|---:|---:|---:|---|
  | query noemit-st | 692,080,170 | 692,586,088 | +0.073% | 0.032/0.010% |
  | query emit-st | 728,251,072 | 728,726,069 | +0.065% | 0.010/0.008% |
  | query emit-mt | 826,340,192 | 826,894,293 | +0.067% | 0.053/0.240% |
  | umami noemit-st | 16,542,090,194 | 16,546,901,562 | +0.029% | 0.004/0.001% |
  | umami emit-st | 19,067,678,896 | 19,072,760,161 | +0.027% | 0.001/0.003% |
  | umami emit-mt | 22,386,918,104 | 22,406,021,710 | +0.085% | 0.056/0.545% |
  | hono noemit-st | 2,166,641,551 | 2,167,338,869 | +0.032% | 0.005/0.003% |
  | hono emit-st | 2,876,970,434 | 2,877,795,612 | +0.029% | 0.008/0.006% |
  | hono emit-mt | 3,735,697,980 | 3,737,093,583 | +0.037% | 0.036/0.069% |

  Every single-threaded cell is under +0.08%. Most of what is left is the
  scanner's ASCII comment helpers (about 0.03 to 0.06%, "Scan" above). The
  guarded `'0'` arms had cost +0.08 to +0.16% more; ef2bb3d24's head without
  them measured within 0.07% of base.
- **icount, LSP** (`icount_lsp_ab.sh`, umami `edit` session, 5 runs per
  side): at `ef2bb3d24` base median 9,072,149,915 (spread 1.63%), head
  8,893,681,779 (spread 5.28%), −1.97%; at `a505e68cf` +0.23% (spreads 1.09
  and 1.34%). Both inside the session's noise (worker spins); the loader
  commits change no lookup or resolution order.
- **Gating** (`quality.sh gating 2a932c528`): **GATING FAIL**, the same 11
  findings as the base (Q6's `program.rs` checker call log ×7,
  `program/reuse_verify.rs`, `dts_twin_check`, `synthetic_live_slot_count`,
  `registered_programs`). This lane adds none.
- **Quality** (`quality.sh report 2a932c528 --base 1d0b7ff21`): prod functions
  over cog 25 670 → 636, over 50 205 → 199, sum 90,628 → 89,820.
  Go-relative: port Δ ≥ 10 rows 89 → 63, Δ ≥ 25 10 → 5, port excess 5,192 →
  4,679; own > 25 136 → 110, own > 50 28 → 23, own excess 2,150 → 1,691.
  Areas at `ea95d0583`: frontend port targets 14 → 1 (Σ Δ 348 → 10; `scan`,
  now 34 again), own 14 → 0 (Σ 193 → 0); execute/build/emit port 3 → 0
  (Σ 66 → 0), own 3 → 1 (`FlagSet::parse_one` 27); other, in this lane's
  files: port 12 → 2 (`subtree_facts_of_data`, `new_type_response`), own → 0
  but Q6's `export_print_pack`. Duplication 3.48% → 3.42% (679 → 666 blocks;
  the one "new" block is es_decorator's, see Duplication). Escape hatches
  11 → 11 (switches 4 → 4).

No switch added or removed.

## Outside this lane's files

- `gostd/base64.rs` (new) and one line in `gostd/mod.rs`: the one port of
  `encoding/base64` StdEncoding for the source mapper, the generator and the
  API.
- `crate::checker::if_else` imported from scanner_p2, binder_p1, printer_p1
  and module/entrypoints (no change to checker files).
