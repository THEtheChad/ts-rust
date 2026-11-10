# B3: pre-pin ports (read-only audit, 2026-11-10)

Lane B3 checks for Rust functions that still have the shape of an older
typescript-go. Q0 reported that 2,099 `// Go:` markers name nothing at the pin
(`quality.md` §7). Astra's W2-FE finding 2 is the bug this lane looks for:
`common_directory_of_files` has its pre-pin shape.

- **Source:** `b8505f3ea` (`perf/first-principles` when the lane started).
  Since then, `perf/first-principles` has gained only Q8 (`core.rs` helpers),
  which touches none of the code below.
- **Go:** pin `3e9236361fa9`, `~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc`.
  Only that one commit is on disk. There is no older typescript-go checkout or
  history under `~/.explore/repos/` or anywhere else on the box, so I could not
  diff the pins. Two things stand in for that diff:
  - Rust markers record many renames themselves, with notes such as
    "(at 673a5f17d713; removed by ts#64159)".
  - The pre-pin shape can be seen in the code. The Go body at the pin calls a
    ts#64159 rooted-path API; the Rust body still calls the string helper that
    came before it.
- **Binaries:** Rust `~/.cache/perf-rearch/w2-fe/tsgo-head-2a932c528-goport`
  (the W2-FE head, the same path code as `b8505f3ea`). Go oracle
  `~/.local/bin/tsgo-oracle-3e9236361fa9`.
- **Scratch:** `~/.cache/perf-rearch/scratch/b3/` (case files, outputs and
  classification JSON).

## Tools (committed with this report)

- `scripts/perf-rearch/b3_markers.py unresolved [<rev>]`
  - Lists each unresolved marker, using gorel's own mapping: 2,097 at
    `b8505f3ea`, against Q0's 2,099 at `e658d4d29`.
  - For each, it gives scope hints: an own directory; a Go file that is not at
    the pin; whether the name exists in the named file, elsewhere at the pin, or
    as a Go closure.
- `scripts/perf-rearch/b3_markers.py calls [<rev>]`
  - For each of the 12,803 Rust functions placed on one Go function, it gives
    the callee sets on both sides and the Go callees that Rust lacks.
  - It also checks the dynamic-path API:
    - `go_dyn`: Go calls a ts#64159 API that is dynamic-aware.
    - `rust_dyn`: Rust calls a helper that is dynamic-aware.
    - `rust_legacy`: Rust calls a string helper that does not handle dynamic
      names.
  - **Astra's bug shape** is a row with `go_dyn` and `rust_legacy` and without
    `rust_dyn`. The check finds `compute_common_source_directory_of_filenames`
    with no prior knowledge of it.
- `scripts/perf-rearch/b3_api_probe.py <tsgo> <case.json> [--insensitive]`
  (removed by lane H7: `parity_api.py probe <tsgo> <scenario>` replaces it,
  and the cases below are scenarios of the gate's api tier, `parity.md`
  "api: API sessions")
  - Ran one synthetic-program API probe:
    `tsgo --api --async [--useCaseSensitiveFileNames=false]`, then
    `initialize`, `createSnapshot` and `emitToString`.
  - It printed the output file names and the diagnostics, so the Go and Rust
    outputs diff directly.

## 1. The 2,097 non-resolving markers, by class

The task's five classes do not fit 86% of these rows. Those markers name Go
outside the pin on purpose, so they get a class of their own, (x).

| Class | Count | What it is |
|---|---:|---|
| **(x)** names Go outside the pin, by design | **1,811** | Effect's Go plugin (`effect/`, 1,093); Go std in `gostd/` (403); Go std / x/text / encoding/json/v2 named from other files (`locale.rs` 122, `frontend/json.rs`, `pprof.rs`, the flag and syscall ports, …: 315). Not stale ports. |
| **(a)** exists at the pin, unchanged in shape | **178** | 86 `ast/fields.rs` markers name Go struct **fields** of `ast_generated.go`. 59 name Go `var`s, constants or map literals: semver regexps, `PseudoType*`, `escapedCharsMap`, `codeFixProviders`, the `NewES20xxTransformer` vars, `ModuleKindToModuleResolutionKind`. 21 name a named Go closure or `.funcN`. 12 are prose markers for a part of a Go function. gorel indexes only `func`s, which is why these do not resolve. |
| **(b)** renamed or moved | **54** | 46 already carry a note such as "(at 673a5f17d713; ts#64159 renames it X)". The other 8: `ModuleResolutionKind::string` and `JsxEmit::string` (now in `core/options_generated.go`), `requestFileSystem.toPath` and `UseCaseSensitiveFileNames`, `api/session useCaseSensitiveFileNames`, `aliasResolver.UseCaseSensitiveFileNames`, and the resolver's `resolvePathForModule` and `dynamicDirectoryCandidate`. |
| **(c)** exists at the pin with a materially different body | **0 among these markers** | See §2. The stale bodies found sit behind markers that **do** resolve (Astra's function among them), or in code with no marker at all. Body comparison of the (b) and (d) rows found no further stale body. |
| **(d)** gone at the pin | **42** | 36 are annotated "removed by ts#64159 / ts#64519 / ts#64554 / ts#64649 / tsgo#4471". Also: `callbackModuleResolver.{GetPackageScopeForPath,PackageJsonCacheEntries,ResolvePackageDirectory}` (ts#64519) and `optionsType`, `optionsHaveChanges` and `ForEachCompilerOptionValue` (ts#64457, now generated). |
| **(e)** wrong or ambiguous marker | **12** | Prose taken as a marker ("promoted from the embedded `position`", "the `select` of start and finish", "wg.RunAndWait()", …). One is wrong: `CompilerOptions::deep_equal` is marked `Clone`. |

### Notes on the classes

- **How the counts were made:** the (x), (a), (b) and (d) counts are
  mechanical: scope, lookup in the Go file, annotations. I assigned the last
  48 rows by hand. `final.json` in scratch holds the class of every row.
- **What (a) assumes:** the class says the function exists; it does not say the
  function is unchanged. I compared 10 (a) rows with their Go counterparts and
  all 10 match: `codeFixProviders`, the semver regexps (including the RE2
  `(?i)` folding), `ModuleKindToModuleResolutionKind`, `newlineNormalizer`,
  `PseudoType*` and `embeddedContents`.
- **Most of the (b) and (d) rows are ts#64159:** 64 of the 96, including
  `UseCaseSensitiveFileNames` → `CaseSensitivity`, `GetCurrentDirectory` →
  `BaseDirectory`, `toPath` → `PathKey`, and `ComparePaths*`. Bump D ported
  that change "by behaviour" and kept the old Rust function names. Most of
  those ports are correct. Where they are not, the gap is in **callers**: the
  call site still uses the pre-pin string helper (§2).

## 2. Confirmed differences

### D1. `compute_common_source_directory_of_filenames` (Astra W2-FE finding 2, lane B2)

- **Where:** `frontend/outputpaths.rs:515`. The marker resolves to Go
  `outputpaths/commonsourcedirectory.go:8`. The `calls` check flags the
  function: Go calls `CaseSensitivity.CommonDirectoryOfFiles`, Rust calls
  `get_canonical_file_name`.
- **Go** (`rooted_path.go:627`): when either name is an encoded dynamic name, Go
  compares case-sensitively and drops the trailing separator of the root
  component.
- **Rust:** compares with the host's case setting and has no dynamic branch.
- **Reproduced:** `cases/c0-commondir.json`, which is Astra's case.
  - Go: `virtualout/A/x.*` and `virtualout/a/y.*`.
  - Rust: `virtualout/x.*` and `^/~ts-uri~/mem/auth/a/y.*`.
- **Smaller shape differences, no output change seen:**
  - Rust resolves file names against the current directory
    (`get_normalized_path_components`); Go's names are already rooted.
  - Rust adds a trailing separator; Go's `RootedDirectoryPath` has none.

### D2. The emit-side program takes its case sensitivity from the OS, not from the program's filesystem (new)

- **Where:** `program/go_frontend.rs:1018` `build_program`:
  `let use_case_sensitive_file_names = osvfs_fs().use_case_sensitive_file_names();`.
  - The same pattern appears at `program/go_frontend.rs:735`
    (`publish_parsed_files`) and `execute/incremental/checker_access.rs:128`.
- **Why it matters:** this value becomes `ProgramState.use_case_sensitive_file_names`.
  Everything that reads `program::use_case_sensitive_file_names()` uses it: the
  emit host, `json_file_may_be_emitted`, `get_source_file` (`to_path`), and the
  declaration emit host.
- **Go:** `emitHost.CaseSensitivity()` is the program host's filesystem
  setting. The `NewProgram` side of Rust gets this right, so common-directory
  computation already sees the right value.
- **Result:** on an API session whose filesystem case setting differs from the
  OS, the emit paths of the frontend and of the emitter disagree. On Linux that
  is `--useCaseSensitiveFileNames=false`; on macOS or Windows it is `=true`.
- **No marker named this:** it is own code from the program-state split. The
  dynamic-path check led to it: the dynamic case below matched Go only because
  of this bug.

#### Reproduction (Linux, Go oracle versus W2-FE head)

```sh
P=scripts/perf-rearch/b3_api_probe.py; S=~/.cache/perf-rearch/scratch/b3
python3 $P ~/.local/bin/tsgo-oracle-3e9236361fa9           $S/cases/c2-flag-disk.json --insensitive > go.json
python3 $P ~/.cache/perf-rearch/w2-fe/tsgo-head-2a932c528-goport $S/cases/c2-flag-disk.json --insensitive > rs.json
diff go.json rs.json
```

Since lane H7 the same case is the api scenario `case-mixed-dirs-insensitive`:

```sh
P=scripts/perf-rearch/parity_api.py
diff <(python3 $P probe ~/.local/bin/tsgo-oracle-3e9236361fa9 case-mixed-dirs-insensitive) \
     <(python3 $P probe <tsgo> case-mixed-dirs-insensitive)
```

**`c2-flag-disk`:**

- **Input:** an in-memory full filesystem with `<d>/A/x.ts` and `<d>/a/y.ts`.
  Root files are those two plus a `noLib` globals file; options are
  `{noLib, outDir: <d>/out}`.
- **Go:** `out/x.js` and `out/y.js`.
- **Rust:** `out/x.js` and **`a/y.js`**, beside its source.

| Case | Change from c2 | Go | Rust |
|---|---|---|---|
| `c2b-rootdir` | `rootDir: <d>/A` | `out/y.js` | `a/y.js` |
| `c2d-decl` | `declaration: true` | `out/y.js`, `out/y.d.ts` | `a/y.js`, `a/y.d.ts` |
| `c2c-reversed` | root files in the other order | `out/x.js` | `A/x.js` |
| `c1b-json-case-disk` | `<d>/src/a.ts` imports `./data.json`; `rootDir: <d>/src`, `outDir: <d>/SRC`, `resolveJsonModule`, module 99, moduleResolution 100 | no `data.json` output: it would overwrite its input | emits `SRC/data.json` over `src/data.json` |

In `c2b-rootdir`, `rootDir` fixes the common directory, so only the emit-side
case setting is left to cause the difference. That isolates D2.

**Scope:**

- API sessions whose `useCaseSensitiveFileNames` differs from the OS default.
  The API client option and `--useCaseSensitiveFileNames` set this.
- The CLI and the LSP are not affected: their host filesystem is the OS
  filesystem.
- On macOS and Windows the bug runs the other way, with `=true`.

### Probed, no difference

| Probe | What it covers | Result |
|---|---|---|
| `c1-json-case` | Dynamic JSON, `outDir` differing only in case. Sensitive and insensitive runs. | Same. On Linux, D2 hides the §3 S1 and S2 sites; see S1. |
| `c3-dyn-resolve` | Dynamic module resolution: an extension-like directory `./lib.v2`, `./dir/`, `./c.js` → `c.ts`, `../src/sub/d`, a space in a directory name, `%20` in a directory name. Declarations and `outDir`. | Same diagnostics and emit. This covers `resolve_dynamic_logical_path`, `dynamic_directory_candidate` and `resolve_path_for_module` (class b/d). |
| `cli1` | CLI `tsgo -p sub --mapRoot maps --sourceRoot srcroot`, with the config directory different from the current directory. JSON import, declaration maps. | Same. This covers `EmitHost::get_current_directory`, where Go uses `BaseDirectory` (rule R1). |
| ts#64457 port | Generated `CompilerOptionsAffect*` and `ForEachCompilerOptionAffectingBuildInfo` against Rust's `options_have_changes` / `for_each_compiler_option_value` with the `affects_*` decl flags. | The sets are equal: semantic 36, declaration path 4, emit 29, build info 67. The 67 build-info options come in the same order. No difference. |

## 3. Ranked list: (c) and (d) rows, and stale bodies behind resolving markers

**How the list is ranked:**

- **Risk** is how likely the item is to change observable output, weighed by
  how reachable it is: CLI ahead of API and LSP, disk ahead of dynamic names.
- **Status:** **C** is confirmed, **R** is a difference found by reading,
  **N** means none was found, and **U** means it was not verified.
- **"dyn":** Go at the pin calls a `CaseSensitivity`-receiver API that compares
  encoded dynamic names (`^/~ts-uri~/…`) case-sensitively, with the root
  separator canonicalized. The Rust code calls `compare_paths`,
  `get_canonical_file_name` or `contains_path`, which do neither. These
  differences appear only on a case-insensitive host with dynamic names that
  differ only in case. On Linux that needs `--useCaseSensitiveFileNames=false`,
  and D2 then masks the emit sites.

| # | Rust | Go at the pin | Status | Behavioural difference | Input that shows it |
|---|---|---|---|---|---|
| 1 | `program/go_frontend.rs:1018` `build_program` (+ `:735`) | `emitHost.CaseSensitivity()` | **C** (D2) | Emit-side case setting is the OS's, not the host filesystem's | §2, `c2-flag-disk` |
| 2 | `frontend/outputpaths.rs:515` `compute_common_source_directory_of_filenames` | `CaseSensitivity.CommonDirectoryOfFiles` | **C** (D1) | dyn: folds case for dynamic names | `c0-commondir` |
| 3 | `frontend/compiler/program_lookup.rs:958` `source_file_may_be_emitted` (`:1038`) | `emitter.go:532` `CompareFilePaths` | R | dyn: on an insensitive host, a JSON file under a dynamic root, with `outDir` equal to the common directory except in case: Go emits, Rust skips | `c1-json-case` on macOS or Windows, or on Linux once D2 is fixed |
| 4 | `program.rs:3889` `json_file_may_be_emitted` (the second port of `sourceFileMayBeEmitted`) | same | R | same as #3, on the emit-host path | same |
| 5 | `frontend/outputpaths.rs:205` `get_output_js_file_name` | `outputpaths.go:111` `CompareFilePaths` | R | dyn: the check that a JSON output would not overwrite its input | same |
| 6 | `ls/lsutil/userpreferences.rs` `type_parsers`, `config_path_parsers`, `collect_field_infos`, `set_field_from_value`, `serialize_field`, …; `UserPreferences::marshal_json_to` (cog 12 against generated cog 138) | removed by ts#64554 (`userpreferences_generated.go`) | U | The reflective pre-ts#64554 parser is kept. Generated code may differ for wrong-typed values, unknown enum strings and nested `js/ts` keys. Those decide LSP preferences, and through them completions, inlay hints and organize-imports answers. | LSP `workspace/didChangeConfiguration` with `"includeInlayParameterNameHints": 1`, `"quotePreference": "bogus"`, and a nested `typescript.preferences`; then compare `textDocument/inlayHint` and completion answers |
| 7 | `frontend/compiler/project_references.rs:601` `get_source_to_dts_if_symlink` | removed by ts#64519 (the builder's `resolveSymlink`, `projectreferencefilemapper.go:197`) | U | Pre-split shape: when the symlink maps are filled, and in what order. Affects source ↔ d.ts redirects in symlinked monorepos with references. | pnpm-style monorepo: `packages/a` (composite) and `packages/b` referencing `a` through `node_modules/a → ../packages/a`; `tsc -b` and `--explainFiles` |
| 8 | `modulespecifiers/util.rs:17` `compare_paths_by_redirect` | `CompareFilePaths` | R | dyn: the order of candidate module paths | Two dynamic redirect targets differing only in case, on an insensitive host; then auto-import or d.ts specifier |
| 9 | `modulespecifiers/util.rs:315` `package_json_paths_are_equal` | `CaseSensitivity.ComparePaths` | R | dyn | as #8 |
| 10 | `frontend/compiler/file_loader.rs:1762` `get_source_file_from_reference` (`:1787`) | `p.caseSensitivity.PathKey` | R | dyn: Rust lowercases a dynamic name before the extension check. `/// <reference path="x.TS"/>` under a dynamic root on an insensitive host: Go reports the unsupported extension, Rust accepts it. | dynamic `x.TS` plus a reference |
| 11 | `frontend/module/resolver_p2.rs:159` `get_original_and_resolved_file_name` | `CompareFilePaths` | R | dyn: realpath differing only in case; low, since dynamic files rarely have a realpath | — |
| 12 | `frontend/tsoptions/tsconfig_p2.rs:705` (`:988`), `:2029` (`:2038`) | `caseSensitivity.PathKey` | R | dyn: wildcard and file-name keys lowercase dynamic names | API config project on a dynamic filesystem with `include` matching names that differ only in case |
| 13 | `frontend/vfs/vfsmatch.rs:1118` `GlobVisitor::visit` (`:1132`) | `v.caseSensitivity.PathKey(realPath)` | R | dyn: the visited-directory key | as #12 |
| 14 | `project/overlayfs.rs:661` `OverlayFS::get_accessible_entries` (`:677`) | `fs.caseSensitivity.PathKey` | R | dyn: LSP overlay directory listing for untitled or virtual documents | LSP `untitled:` documents differing only in case, on macOS |
| 15 | `frontend/tsoptions/wildcard.rs:115` `to_canonical_key` (2 callers) | removed by ts#64159 (`PathKey`) | R | dyn, as #12 | as #12 |
| 16 | `frontend/tspath/path.rs:1046` `get_canonical_file_name` (28 callers), `compare_paths` (25), `contains_path` (26), `get_relative_path_*` (21), `convert_to_relative_path` (9), `trim_file_path_prefix` (5) | pre-pin string helpers (`path.go`). The pin keeps several, not dynamic-aware; their Go callers moved to `CaseSensitivity.*`. | systemic | The source of #3–#15. Each caller has to be checked against its Go call site. `contains_path`'s own comment says no Go caller outside tests uses it. | — |
| 17 | `frontend/outputpaths.rs:376`, `:419` `get_source_file_path_in_new_dir(_worker)` | `GetSourceFileNameInNewDir` (ts#64159) | N | Ported: `relative_path_within_directory` handles dynamic names | c0 and c2 diffs come from #1 and #2 |
| 18 | `checker/emit_resolver_p1.rs:105` `get_emit_resolver` | removed by ts#64649 (`NewEmitResolver`) | U, low | The declaration tracker reaches the emit resolver through the checker. ts#64649 makes it per-emit. A difference would need emit-resolver state shared between two emits. | two `emitToString` calls with different `declaration` options on one snapshot |
| 19 | `frontend/module/staticresolver.rs:254-269`, `api/module_resolution.rs:186-205` | removed by ts#64519 | N | Kept as trait shims; the Go methods are gone, so nothing at the pin calls them | — |
| 20 | `frontend/tsoptions/decls.rs:1657`, `:1695` `options_have_changes`, `for_each_compiler_option_value` | removed by ts#64457 | N | Sets and order equal (§2) | — |
| 21 | `frontend/module/resolver_p1.rs:116`, `:184`, `:98` | removed or renamed by ts#64159 | N | `c3-dyn-resolve` | — |
| 22 | `program.rs:5664…5749` `EmitHost::get_current_directory` | `BaseDirectory` (rule R1) | N | `cli1`. Go uses `BaseDirectory` only for `mapRoot` resolution and the JSON common directory; those options arrive absolute. | — |
| 23 | `execute/watcher.rs:596` `compare_paths_options`, `execute/watchmanager/watchmanager.rs:751` `DirWatchSet::canonical` | `caseSensitivity` / `PathKey` (ts#64159) | U, low | Watch-mode keys for dynamic names, which watch never sees | — |
| 24 | `frontend/tsoptions/parsed_command_line.rs:882` `file_names_by_path` | `FilePaths` (ts#64159) | U, low | The key form of root file names | — |
| 25 | `lsp/lsproto/lsp.rs:423` `bool_to_int` (177 generated callers) | removed by tsgo#4471 (reflective `marshalUnion`) | U, low | Per-type generated marshal kept. Covered by the LSP oracle batteries. | — |
| 26 | `sourceFileMayBeEmitted` ported twice (#3, #4); `GetOutputPathsFor` twice (`outputpaths.rs:79`, `program.rs:4034`) | — | drift risk | Two copies of the same Go logic: a fix to one (F2) must reach both | — |
| 27 | `Program.GetSymlinkCache` ported 4 times (`program_lookup.rs:465`, `modulespecifiers/host.rs:322`, `program.rs:3302`, `go_frontend.rs:311`) | `program.go:2343` | drift risk | — | — |
| 28 | `modulespecifiers/` private copies: `packagejson.rs` (`GetVersionPaths`, `GetPaths`, `initObjectKind`, `InfoCacheEntry::get_directory` still pre-ts#64159), `tspath_extension.rs`, `deps.rs` (`TryParsePatterns`, `GetConditions`, `MangleScopedPackageName`, `FindBestPatternMatch`) | — | drift risk | 285 Go functions have Rust ports in more than one file family (`dups.txt`); these are the ones on output paths | — |
| 29 | Dead pre-pin helpers (no non-test callers): `matches_exclude`, `resolve_tripleslash_reference`, `compare_paths_case_sensitive`, `compare_paths_case_insensitive`, `starts_with_directory` | removed by ts#64159 | N | Dead code | — |
| 30 | `CompilerOptions::deep_equal` marked `Clone` | (e) | N | A wrong marker only | — |

### Step 3: unmarked functions placed by name

- **Sample:** the 119 rows that the name matcher placed (`high`) and that have
  a callee set, sorted by the share of Go callees missing in Rust.
- **Findings:**
  - The top rows are delegating shims (`DefaultResolver::resolve_module_name`
    → `resolve_module_name_worker`) or Go `json.v2` marshal methods.
  - One sits in the ts#64554 area: `UserPreferences::marshal_json_to` (#6).
  - No unmarked function with a pre-pin body turned up. D2 is the exception, but
    it has no Go counterpart.
- **Callee-set check over all 12,803 rows:**
  - The median miss ratio is 0.25, and 1,199 rows miss at least 50% of their
    Go callees.
  - That signal is noisy. The Rust `Node` enum accessors, `match` instead of
    `As*()`, and split helpers all raise it. It is useful only together with a
    specific API change, which is how the dynamic-path rows were found.

## 4. Fix plan, by area

### F1. frontend/program: D2 (highest priority, small)

- **Fix:** take the case setting from the program's host filesystem, not from
  `osvfs_fs()`:
  - `build_program` at `go_frontend.rs:1018`: use `np.use_case_sensitive_file_names()`;
  - `publish_parsed_files` at `:735`: use the program's filesystem;
  - `checker_access.rs:128`, if it is production code.
- **Regression cases:**
  - Add `c2-flag-disk`, `c2b-rootdir` and `c1b-json-case-disk` as API parity
    cases, run with `--useCaseSensitiveFileNames=false`.
  - Before adding them, check that the parity harness can pass that flag.
- **Order:** do F1 before F2. D2 hides the F2 differences on Linux, so F2's
  tests cannot be written until F1 lands.

### F2. tspath and outputpaths: the dynamic-aware call sites (lane B2 extended)

1. Port `CaseSensitivity.CommonDirectoryOfFiles` (D1, B2's scope).
2. Replace `compare_paths` with `compare_rooted_text` where Go at the pin calls
   `CaseSensitivity.CompareFilePaths`, `ComparePaths` or `CompareFileNameStems`:
   - `outputpaths.rs:217`;
   - `program_lookup.rs:1038`;
   - `program.rs` `json_file_may_be_emitted`;
   - `resolver_p2.rs:166`;
   - `modulespecifiers/util.rs:33` and `:326`.
3. Replace `get_canonical_file_name` with the `PathKey` form (`to_path` on a
   rooted name, which keeps dynamic case) where Go calls
   `caseSensitivity.PathKey`:
   - `file_loader.rs:1787`;
   - `tsconfig_p2.rs:988` and `:2038`;
   - `vfsmatch.rs:1132`;
   - `overlayfs.rs:677`;
   - `wildcard.rs` `to_canonical_key`.
4. Then rename or retire the legacy helpers:
   - one `PathKey` helper in place of `get_canonical_file_name`;
   - delete the dead ones (#29).
   - The aim is that a new caller cannot pick the pre-pin form by accident.
5. Tests: the `c1-json-case` and #10 shapes on an insensitive host. On Linux these work
   only after F1, with `--useCaseSensitiveFileNames=false`.
6. Keep `b3_markers.py calls` as a report: a row with `go_dyn` and
   `rust_legacy` but no `rust_dyn` should be justified or fixed.

### F3. LSP preferences: ts#64554 (#6)

- **Verify:** compare `ls/lsutil/userpreferences.rs` with
  `userpreferences_generated.go` at the pin:
  - field set;
  - the config path of each field;
  - parse of wrong-typed values;
  - serialization order and omission.
- **Test:** add an LSP parity battery that sends odd configuration values.

### F4. Project references: ts#64519 (#7)

- **Verify:** compare `ProjectReferenceFileMapper` against the pin's builder
  (`resolveSymlink`, `realpathDtsToSource`): when the maps fill, and in what
  order.
- **Test:** add a symlinked-monorepo `-b` parity project.

### F5. Duplicate ports (#26–#28)

- **Fold the duplicates to one port per Go function:** `sourceFileMayBeEmitted`,
  `GetOutputPathsFor`, `GetSymlinkCache`, and the `modulespecifiers/` copies of
  packagejson, tspath and module helpers.
- **Why:** each duplicate is where the next pin bump can leave a copy stale.

### F6. Markers (no output change; makes Q0 and B3 cheaper next time)

- **Update the 96 (b) and (d) markers** to the pin's names. Remove the dead
  pre-pin functions, or re-mark them as own code.
- **Fix the 12 (e) markers.**
- **Teach gorel about Go fields and vars** (gocog could emit them), so that
  the 145 (a) rows resolve.

## 5. Limits

- **No older Go on disk.** "Unchanged since the pre-pin version" could not be
  tested directly. The (c) judgement rests on the code at the pin and on the
  ts#64159 API split.
- **(a) bodies were spot-checked, not compared exhaustively** (10 of 178).
- **The R items in §3 come from reading.** On Linux they can only be run
  through the API case flag, and D2 then masks most of them. Confirming them
  needs F1 first, or a macOS or Windows host.
- **Not covered:** the checker, emitter and LS bodies behind resolving markers,
  beyond the callee-set scan. Q0's calibration (76% exact cognitive match on
  12,516 rows) suggests those ports track the pin well; this audit did not
  check them one by one.
