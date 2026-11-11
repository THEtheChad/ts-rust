# A1: Go `int` width, nil against empty, and integers through `f64` (read-only audit, 2026-11-11)

**Scope:** base `perf/first-principles` at `87d406014` against Go `3e9236361fa9`. B6 round 2 (not merged) was read only to see what it already covers. Nothing was built or run, so every behaviour below is traced from the source, and "inferred" marks the ones not traced to the end.

## Method
- **The generated `lsproto` layer.** Every Go struct field in `lsp_generated.go` (1,162 matched) was compared with its Rust field.
  - All integer widths match: `int32`→`i32` 15, `uint32`→`u32` 18, `float64`→`f64` 44, and 30 enum newtypes. Go `lsproto` has no plain `int`.
  - **The omit rules hold:**
    - slices with no omit tag (63) are required on both sides;
    - the 50 pointer-to-slice and 648 other pointer fields are `Option`;
    - the 2 maps write `{}` on both sides.
  - The only collapse is the 25 `Resolved*` capability slices, which carry `omitzero` and only reach a log line (B2).
- **The API.** All 509 fields of `api/proto.go` against `api/proto.rs`, including the omit mode, plus every conversion or `== nil` check of a request parameter in `session.go`.
- **Everything else, by grep:** casts from `f64` to an integer, `as i32|u32|usize` on API and LSP fields, `LspAny::Number`, `marshal_float`, `strconv.Atoi|ParseInt|ParseUint`, `.(float64)`, `flag.Int`, every `omitzero` slice or map outside `lsproto`, and the port's own notes.
- **Excluded:**
  - in-file positions and lengths (Go narrows them to `int32` too);
  - checker, binder and AST internals;
  - numbers in source text;
  - values that are floats in Go as well;
  - sites already correct: `--maxNodeModuleJsDepth`, `--checkers` and `--builders`, semver, `Content-Length`, the source map decoder, the JSON-RPC id, the API node handle, the `getCompletionsAtPosition` position and hover verbosity;
  - what B6 round 2 covers: `maximumHoverLength`, the formatter sizes, `IndentStyle`, preferences written through `f64`, and the `MarshalJSONTo` slices. Recheck these at B6's merge, along with `printer/text_writer.rs:126` `GetColumn` as `i32`.

## Part A: integer width
| # | Rust site | Go | Reachable | Go / Rust | Input | Rank |
|---|---|---|---|---|---|---|
| A1 | `api/proto.rs:2767` `max_response_bytes_per_page: i32`; `session_p1.rs:2275` | `proto.go:862` `int`; `session.go:1285-1296` | API `batchRequests` | Go returns one page; Rust's `int32` decoder fails the whole batch. | `maxResponseBytesPerPage: 4294967296` or `9007199254740991` | **1** |
| A2 | `parsing_helpers.rs:662` `from_i32(*f as i32)` | `parsinghelpers.go:348-353` `T(value.(float64))`; amd64 gives `0x80000000` when out of range | API raw compiler options (`createPrograms`, `reconfigureSnapshotProgram`, `transpile`); tsbuildinfo options | `target`, `module`, `jsx`, `moduleResolution`, `moduleDetection` or `newLine` ≥ 2^31: Go gets `i32::MIN`, Rust saturates to `i32::MAX`. Emit differs (inferred). | `target: 2147483648` | **3** |
| A3 | `session_p2.rs:1236` `params.position as i32` (`formatNodeForInsertion`) | `session.go:4167` `int(params.Position)` | API `uint32` position | Go treats it as past EOF and succeeds (inferred). Rust gets a negative line and panics at `format/util.rs:80`. | `position: 2147483648` | 4 |
| A3b | `session_p1.rs:1451, 4365, 4492, 4834, 4884` (the same cast) | `session.go:819, 2684, 2757, 3030, 3059` | API | Probably no visible difference (unverified). | same | 9 |
| A4 | `proto.rs:3927` `index: i32`; `:4262` `position: i32` | `proto.go:1396`, `:1553` `int` | API | Go: "invalid resolution index", or the request runs. Rust: a decode error. | `4294967296` | 6 |
| A5 | `build_info.rs:86-89` `unmarshal_i32` wraps (`:208, :457, :570/610/671, :938-943, :997-999, :1556, :1572, :1229/1236, :449/453`) | `buildInfo.go:19-20, :209-211`: ids and `pos`/`end` are `int`; `code`, `category`, `mode` and `kind` are `int32` | CLI `--incremental`/`-b` with an edited or corrupt `.tsbuildinfo` | Ids: Go keeps the value and panics on lookup; Rust wraps and uses the wrong file. `int32` fields: Go rejects the build info and builds fresh; Rust wraps and reuses the cache. | `changeFileSet:[4294967297]`; diagnostic `code: 4294969296` | 5 |
| A5b | `build_info.rs:1359` `*id as i32` saturates | `buildInfo.go:409-412` | `.tsbuildinfo` `emitSignatures` | Both panic, with different numbers in the text. | `[4294967297.0,["s"]]` | 8 |
| A6 | `effect/etscore.rs:388-393` `*f as i64` saturates (`:489, :496, :592, :852, :876`) | Effect's Go `int(f)` (unverified) | tsconfig plugin options | `1e19`: Go `i64::MIN`, Rust `i64::MAX` | `pipeableMinArgCount: 1e19` | 7 |
| A7 | `cmd/tsgo/lsp.rs:121-122` `client_process_id as i32` | `cmd/tsc/lsp.go:81-101` `int` | `--lsp -clientProcessId` | 2^31: Go starts a watchdog that exits after 5 s (inferred). Rust's value wraps negative and it starts no watchdog. | `-clientProcessId=2147483648` | 10 |
| A8 | `ls/lsconv/converters.rs:832-842` | `converters.go:366-377` | LSP position line 0xFFFFFFFF | Both panic, with different text in the `InternalError` reply. | hover at `line: 4294967295` | 11 |
| A9 | `ls/autoimport/fix.rs:357` | `fix.go:74, :173` | `completionItem/resolve` data echoed back by the client | `importIndex: -1`: both panic, with different text. | edited resolve data | 12 |

## Part B: nil against empty
| # | Rust site | Go | Reachable | Go / Rust | Input | Rank |
|---|---|---|---|---|---|---|
| B1 | `frontend/core_ext.rs:10-11` `TypeAcquisition.include/exclude: Vec`; `options_json.rs:93-120`; `api/proto.rs:3233-3237` | `options_generated.go:855-856` `[]string` with `omitzero`; `ParseStringArray` keeps `[]` | API `createSnapshot` (`parsedCommandLine`), `parseConfigFile`, `parseCommandLine` | Go writes `"typeAcquisition":{"include":[]}`; Rust leaves the key out. | tsconfig `"typeAcquisition":{"include":[],"exclude":[]}` | **2** |
| B1b | `ata/discovertypings.rs:82` `!include.is_empty()` | `discovertypings.go:46` `Include != nil` | LSP ATA | Only a log line differs. | same | 13 |
| B2 | `json_ext.rs:125-130` `IsZero for Vec`; `resolved_capabilities.rs` | 25 `Resolved*` slices with `omitzero`; `server.go:1567-1571` | LSP `initialize` | Only the "Resolved client capabilities" log line differs. | `hover.contentFormat: []` | 14 |
| B3 | `tsoptions/parsed_options.rs:13-27`; `execute/watcher.rs:1318-1322` | `parsedoptions.go:31-38` nil checks | `--watch` config recheck | No config edit found that changes only whether a list is nil (unverified). | — | 15 |
| B4 | `snapshot_to_build_info.rs:20-22, 242` | `snapshottobuildinfo.go:202` | tsbuildinfo | Likely unreachable. | — | 16 |

## Part C: integers through `f64`
Only `userpreferences.rs:1872-1889` writes an integer through `f64`; B6 round 2 fixes it. Every other integer site writes an integer token, as Go does:
- build info's `marshal_int`;
- `tsconfig_p2.rs:644-646`, `show_config.rs:500-509` and tracing `Arg::Int`;
- the API `MarshalerTo` for `i32/i64/u32/u64`.

## Ranked fixes and pins
1. **A1:** make the field and `paginate_batch_responses` `i64`.
   - **Pin:** a unit test decoding `4294967296` and `9007199254740991`. The API harness also needs a `{method, params}` request form to add an item `api/batch-page-cap-int64`.
2. **B1:** make `include`/`exclude` `Option<Vec<String>>`, marshal with `is_some`, compare with `slices.Equal` semantics, and use `if let Some` in ATA.
   - **Pin:** item `api/typeacq-empty-include` (works with the harness as it is), plus a unit test of `TypeAcquisitionJSON`.
3. **A2:** add a Go amd64 `float64→int32` helper beside B6's `gostd/conv.rs` helper (NaN or out of range gives `i32::MIN`), used at `parsing_helpers.rs:662`.
   - **Pin:** item `api/raw-options-target-int32`, plus a unit test.
4. **A3:** carry the position as `i64`, or reuse the past-the-end handling at `session_p2.rs:2886-2905`.
   - **Pin:** a session test. The harness needs the request form from 1 for a parity item.
5. **A5:** make the ids and `pos`/`end` `i64`; decode Go's `int32` fields with a range check, so an error makes the build info unreadable.
   - **Pin:** cli items with an edited `.tsbuildinfo` fixture.
6. **A6, A7, A4, A8, A9:**
   - A6: the shared amd64 helper;
   - A7: compare as `i64`, and truncate only at `kill`;
   - A4: `i64`;
   - A8/A9: a `go_index`-style helper that panics with Go's text.
   - **Pins:** unit tests, or LSP probes for A8/A9.

**Root cause:** every finding comes from one of two places.
- **Hand-written `i32` for a Go `int`,** in code next to the generated layer: API parameters and the build-info reader.
- **A `Vec` standing in for a Go slice that is read through `omitzero` or `== nil`.**

The generated layers matched Go mechanically.
