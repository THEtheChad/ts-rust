# Loader design: parallel discovery, serial replay, one shared resolution layer (C1/C2/C3, LSP #5, E3b)

**The whole ttfd gap is in the LSP's first program load.** On umami it takes 384 ms against Go's 234 ms. The diagnostics request itself is already faster than Go: 20 ms against 35 ms.

**Cause:** on that first load the serial loader re-resolves all 9,393 module keys itself, even though the parse workers have just resolved the same keys. The workers' answers are shared with the loader only on plain-OS-filesystem hosts (`file_loader.rs:362-369`). Resolve-ahead only helps when there are keys from a previous load, and a first load has none.

**Stage S1 fixes this alone.** It reuses the CLI's existing answer-sharing path for the snapshot host's first load. Estimated result: ttfd about 240–280 ms against Go's 289 ms.

Every wall number below was taken after `perf-window1` ended (DONE at about 05:25). Runs started at 1-minute load 1.35–1.5, except where noted. I did not edit or commit anything in either checkout. Each number is marked **[measured]** or **[est.]**.

---

## 1. Where the 425 ms goes (umami, open `BoardCloneForm.tsx`, pull diagnostics)

**Method.**
- **Wall:** a copy of `lsp-ttfd.mjs` that also records the servers' own log lines (`scripts/perf-rearch/loader/ttfd-logs.mjs`, phases from `scripts/perf-rearch/loader/phases.py`). 5 interleaved runs per side, each starting at load < 1.5.
- **Binaries:** `tsgo-head-b2febcd86-goport-w1` and the Go LSP oracle.
- **Split:** `samply-user` at 4 kHz on `tsgo-main-91f3d021c-prof` (same loader code as head) and on the Go oracle. Per-thread spans come from `scripts/perf-rearch/loader/loader_thread.py`.
- **Scaling:** samply adds about 6% to tsc-rs (program update 408 ms profiled against 384 ms). Profiled ms are scaled by 0.94 below.

### Top-level phases [measured: medians of 5 runs; range in brackets]

| Phase | tsc-rs | Go |
|---|---|---|
| spawn + initialize | 7 ms | 7 ms |
| didOpen: find and parse tsconfig | 5 | 9 |
| didOpen: **program update** (load + bind) | **384** [369–445] | **234** [219–288] |
| didOpen: rest (cleanup, auto-import registry, snapshot) | 13 | 16 |
| diagnostic request (checker 3 ms, check 20 ms, response) | 20 | 35 |
| **ttfd** | **427** [410–491] | **294** [278–395] |
| process CPU | 1.44 s | 3.38 s |

The window-1 medians were 425 ms against 289 ms. Gap: +150 ms in the program update, −15 ms in the diagnostic, −4 ms in config.

### The tsc-rs program update [measured, samply; scaled ms]

| Step | Wall | Notes |
|---|---|---|
| setup: root tasks (FileExists for each root), worker pool start | 9 | lib snapshot decode, about 10 ms of CPU, runs off the critical path |
| **serial loader `FilesParser::run_queue`** | **314** | one thread, 100% busy (322 ms of CPU profiled) |
| · loader's own module resolution (`resolve_module_name`, 9,393 keys) | 150 | the workers already resolved them (below) |
| · snapshot read and hash (`OverlayFS::get_file_by_path`, `cache_source_file`) | 36 | the workers read the same texts |
| · parse-cache acquire, `take_prefetched` (text compare, adopt) | 27 | |
| · package scopes (`package_scope_ahead`, 1,110 scopes) | 18 | |
| · `ImportNames::of` (keys kept for the next resolve-ahead) | 17 | |
| · `start` (queue, prefetch requests), `add_sub_task` | 25 | |
| · other (task Rc graph, strings, hashing) | about 40 | |
| worker join | 5 | |
| `get_processed_files` walk | 9 | Go's walk: about 7–13 |
| `publish_file_stores` and the rest | 9 | |
| bind (`bind_all`, 20 threads, 206 ms of CPU) | 33 | Go binds inside its parse tasks |

**The 15 parse workers** [measured]:
- They used 1.1 s of CPU: 0.61 s parsing and 0.47 s resolving through `queue_references`, which the loader then discards.
- They were done about 95 ms after the loader started, and the loader ran for another ~220 ms alone.
- `GOPORT_PREFETCH_STATS` agrees: the loader took 4,384 worker parses, but **0 worker metadata and 0 preps**.
- Resolve-ahead reported `keys: 0, new_keys: 9393, new_scopes: 1110`.
- The parse-free stage after the workers finish is serial work only.

**The CLI on the same project** (`--listFilesOnly`, profiled) [measured]:
- The loader takes 4,260 worker metadata and 3,737 preps (31,509 names, 19 resolved by the loader itself).
- `run_queue` takes 94 ms of CPU, about 21 µs per file. It ends about 20 ms after the workers.
- Program creation through bind takes about 194 ms.

This is the evidence that S1 can work on its own.

**Go** [measured, but samply slows Go about 2.2×]: Go's program update is one parallel phase. Parse, resolve and **bind** run together in the goroutine of each parse task: the LSP parse cache parses and binds (`project/parsecache.go:74-83`). After that comes the serial `getProcessedFiles` walk (13 ms profiled) and `NewChecker` (4 ms).

**CLI cold load, vscode** [measured, profiled]:
- `process_all_program_files` spans 42→462 ms, which is the 0.42 s parse phase.
- The workers are saturated (about 14 cores) from 100 to 425 ms. The loader is busy 241 ms of its 323 ms span, so at 15 workers it already waits.
- The serial floor on the main thread is about 312 ms: 49 ms of root `get_source_file_from_reference`, 241 ms of loader, 26 ms of collect. That floor is why going from 15 to 24 workers doesn't help.
- Bind is a separate 160 ms parallel phase after the load.

---

## 2. How Go loads a program (pin 3e9236361fa9, `tsc/internal`)

### Entry point: `compiler/fileloader.go:167` `processAllProgramFiles`

Order of effects:
1. **:179–193** Build the loader. The `filesParser` gets `wg = NewWorkGroup(singleThreaded)` and `maxDepth = maxNodeModuleJsDepth`.
2. **:194** `addProjectReferenceTasks`: referenced configs are parsed on their own work group, before the resolver exists.
3. **:202–206** One resolver for the whole load: `CreateModuleResolver` or `module.NewResolver`. Its `ResolutionData` holds one `packagejson.InfoCache` (`module/cache.go:63-84`).
4. **:210–212** Root tasks, serially, in `Config.FileNames()` order. Each `getSourceFileFromReference` (:683) calls `FileExists` on the host. A failure makes a `failedLookup` task carrying a diagnostic.
5. **:213–228** Lib root tasks: the default lib, or `compilerOptions.lib` order. They go through `pathForLibFile` (:970: SyncMap caches, plus the `libReplacement` resolution stored in `pathForLibFileResolutions`).
6. **:230–232** One automatic-type-directive task, `baseDir/__inferred type names__.ts`.
7. **:234** `filesParser.parse` = `start(rootTasks, 0)` + `wg.RunAndWait()`.
8. **:236** `getProcessedFiles`, `resolver.GetResolutionData()`, `moduleResolutionError`.

### Work group: `core/workgroup.go`

- Parallel mode starts one goroutine per `Queue` (`wg.Go`, :34–43).
- Single-threaded mode is a LIFO stack (it pops the last queued function first). **tsc-rs's serial order is exactly Go's single-threaded order.**

### `filesParser.start` (`filesparser.go:273`)

For each task, in list order:
- `taskDataByPath.LoadOrStore(path, …)` runs **synchronously in `start`** (:279), then the closure is queued (:284).
- The closure (:285–332) holds `data.mu` for its whole body:
  1. A later arrival either finds its casing (`loadedTask = existing`) or adds it, and then `startSubtasks = data.startedSubTasks`.
  2. The first non-empty `packageId` is copied into the data (:299–302).
  3. `currentDepth = depth (+1 if increaseDepth)`. If it lowers `lowestDepth`, the subtasks restart (:304–312, ts#64632).
  4. **Elision returns after the depth was recorded:** `elideOnDepth && currentDepth > maxDepth` (:314).
  5. Every casing in `data.tasks` that isn't loaded yet gets `load()`ed **under the lock**. A redirect forces its subtasks. Subtasks start when `loadSubTasks && (lowered || !startedSubTasks)` (:318–331).

### `parseTask.load` (`filesparser.go:59`)

Order:
1. `loaded = true`. An automatic-type-directive task resolves the type names and returns (:198). A `failedLookup` task returns.
2. `getParseFileRedirect` (project reference output ↔ source) can turn the task into a redirect task (:187).
3. Extension check (a processing diagnostic, then return).
4. `totalFileCount++`.
5. Lib files get CommonJS metadata. Other files get `loadSourceFileMetaData` (fileloader.go:383): the package scope is looked up through the **same** resolution data, **before** the parse.
6. `parseSourceFile` (:413): the host's `GetSourceFile`, or a content mapper. In the LSP this is `compilerhost.go:95`: `sourceFS.GetFileByPath` (tracked), then `parseCache.Acquire`, which parses and binds.
7. Subtasks, in this fixed order:
   1. triple-slash path references (:728, `FileExists`);
   2. type reference directives (:770);
   3. lib reference directives;
   4. `resolveImportsAndModuleAugmentations` (:826): synthetic `tslib` first, then the synthetic JSX runtime specifier (both through `createSyntheticImport` under `factoryMu`, :960), then imports, then string-literal module augmentations. Each name goes through `resolver.ResolveModuleName`, and the first error is kept with `moduleResolutionErrorOnce` (:900–905);
   5. content-mapper supplemental files.

### Determinism without file ids

Go's loader assigns no ids. Node and symbol ids are lazy, so binding in parallel needs no global order. Order comes entirely from the walk after `RunAndWait`.

**`getProcessedFiles`** (:337, `collectFiles` :386–552) walks the task tree serially from `rootTasks`, depth first, per task:
- add the include reason, except for redirects and the auto-types task (:388–397, `addIncludeReason` :594);
- substitute `loadedTask` and skip tasks that weren't loaded;
- `seen[data]`: **the first casing in walk order wins**. Later casings go to `duplicateSourceFiles` once per casing, plus casing diagnostics (:404–436);
- an ignore-case collision check on case-sensitive file systems (:438–445);
- flush traces, type-reference traces first (:447–452);
- package dedup: the first file per `packageId` in walk order wins, and the others become `redirectsFile` (:454–488);
- **recurse into the subtasks before appending the file** (post-order, :490);
- processing diagnostics, missing files, lib or non-lib lists, the maps, and `lowestDepth > 0` → `sourceFilesFoundSearchingNodeModules` (:514–548).

Then:
- `sortLibs` (a stable sort by lib priority, :553);
- `libs ++ files`;
- lib-replacement resolutions in **sorted key order**, with their traces (:560–570).

### What stays racy in Go itself (order-dependent there)

- Which casing is stored first (`LoadOrStore`), and `data.tasks` map iteration. This affects only load order, not output.
- `packageId` propagation: the first non-empty value per path in lock order. All non-empty values are equal in practice.
- **The elided-first case:** if a path's first arrival is elided and a later non-elided arrival doesn't lower the depth, the file loads but its subtasks never start (:304–331).
- The content-mapper failure cap of 5: a mutex-guarded counter in load order (fileloader.go:636–677).
- `moduleResolutionErrorOnce`. The default resolver never returns an error (`module/resolver.go:467-470`), so only custom resolvers can set it.
- `--traceResolution` package.json wording ("according to earlier cached lookups" or not), the known diff `fuzz/1:23080`.
- The `seenFiles` value: `Store` keeps the last writer's casing (`project/snapshotfs.go:806`).

### Resolution caches

- **Module cache:** `module/cache.go:12-29`, a SyncMap keyed by `{containingDirectory, moduleName, mode, redirectConfigName}`. `Set` is `LoadOrStore`, so the first writer wins.
- **Type-reference cache:** :31–49, `Store`.
- **Paths patterns:** :51–61.
- With `traceResolution`, cache **reads** are skipped so traces are complete, but `Set` still runs (`resolver.go:422`, :477).
- **package.json InfoCache:** `packagejson/cache.go:219-262`, a SyncMap PathKey → entry with `LoadOrStore`. It also stores entries for missing package.json files with their `DirectoryExists` result (`resolver.go:1985` `getPackageJsonInfo`).
- The cache outlives the load in `Program.resolutionData` (`program.go:323`, cloned for `UpdateProgram` at :428).
- `.tsbuildinfo` `packageJsons` and `missingPackageJsons` are built from **every** cache entry, realpath'd, **sorted and deduplicated** (`execute/incremental/program.go:453-481`).
- **LSP tracking:** `sourceFS` keeps `seenFiles` (SyncMap) and `missingDirectories` (SyncSet) as unions (`snapshotfs.go:775-855`).

**Why these sets don't depend on order:** a memoized entry is created only by making its FS calls. So the union of calls over any run order of the same loads is the same. That holds whenever the set of loads is the same.

---

## 3. Design for tsc-rs

### 3.1 Shape

Three roles, each authoritative over one thing:

| Role | Authoritative over | How |
|---|---|---|
| **Discovery** (parallel, all workers) | the **work**: which files are read, parsed and resolved, and every FS call | Each worker runs Go's `start` closure and `load()` exactly, against one shared concurrent resolution layer. Nothing is speculative. |
| **Replay** (one thread, µs per file) | the **order** | Runs today's serial LIFO state machine (`files_parser.rs:707-963`) over integer records. Its output is the load sequence, the per-path state and the loaded set. |
| **Commit + walk** (one thread) | the **program** | Assigns store ids, creates synthetic nodes and copies loader-global facts in replay order. Then the unchanged walk (`files_parser.rs:1181`) builds `ProcessedFiles`. |

If the replay's loaded set, started set or depths differ from discovery's, or a feature with order-dependent side effects is on, the load runs on the **serial engine**: today's loader, kept and renamed. Fallback always starts from fresh caches and tracking sinks.

### 3.2 Compact load graph (C1)

Ids:
- `PathId(u32)` and `NameId(u32)` come from one concurrent interner. It is process-wide in the CLI and `-b`, and session-wide in the LSP, so ids stay stable across versions.
- On a case-sensitive FS, a name and its path key are the same string, so they get the same id.

```rust
struct Arrival { name: NameId, path: PathId, reason: u32 /* kind:4 | index:26 | flags:2 */, pkg: u32 /* interned PackageId, 0 = none */ } // 16 bytes
// flags: increase_depth, elide_on_depth; record kinds (lib, auto-types, supplemental, failed-lookup, redirect) live on FileRec
struct FileRec { name: NameId, path: PathId, edges: Range<u32>, file: u32 /* detached store / parse slot */,
                 meta: u32, resolutions: Range<u32>, diags: Range<u32>, traces: Range<u32>, synth: u8, redirect: u32 }
struct PathState { lowest_depth: i32, started: bool, pkg: u32, casings: SmallVec<[u32; 1]> }  // dense by PathId
struct LoadGraph { roots: Vec<Arrival>, recs: Vec<FileRec>, edges: Vec<Arrival>, paths: Vec<PathState>, … }  // POD only
```

- A record's edge order is Go's subtask order (§2).
- The owner of an edge is implicit, so an include reason is built in the walk as `(kind, owner's path, index, owner's synthetic node)`.
- This replaces the `Rc<RefCell<ParseTask>>` graph (`files_parser.rs:22-69`), `ParseTaskData` (:459) and `ParseTaskLinks` (:415–441).

### 3.3 Parallel discovery (C2)

**Work items** are `(Arrival, depth)`. Each worker keeps a LIFO deque for locality and steals from the others. Per-path state lives in a dense table with a small lock per path, the equivalent of Go's `data.mu`. Each arrival runs Go's closure verbatim (casing, `packageId`, lowering, elision, loading unloaded casings, starting subtasks), with `load()` inside the lock as in Go. Lock contention is per path only.

**`load()` is authoritative**, in Go's order:
1. redirect;
2. extension check;
3. metadata from the shared package.json cache, **before** the parse. This replaces the guessed `ExternalModuleIndicatorOptions` and the `fits` checks (`files_parser.rs:832-870`, :3966, :2154);
4. read through the host's Sync view;
5. parse into a detached store with a provisional id;
6. LSP: parse-cache acquire with the full key;
7. triple-slash references, type references and lib references;
8. synthetic import **names and modes** without creating nodes (`SyntheticImports::of` already does this);
9. imports and augmentations through the shared resolver;
10. emit `Arrival`s.

Each `(path, casing)` that Go's algorithm loads is loaded exactly once.

**Deleted by C2:**
- the prefetch, prep, commit, take and fit machinery: `run_prefetch_worker` :4035, `WorkerResolver` :4238, `take_prefetched` :5738, `PrefetchQueue`, `JobMap`;
- `SharedResolutionCache` (`module/cache.rs:695`);
- the worker-log adoption (`file_loader.rs:2204`, :2236);
- the first-load half of resolve-ahead;
- S1's adapter.

### 3.4 One concurrent resolution layer (C2 + E3b + program audit #6)

- **Module and type-reference caches:** sharded maps keyed by `{dir: PathId, name: NameId, mode: u8, redirect: u16}` → `Arc<ResolvedModule>`, insert-if-absent like Go's `LoadOrStore`. Hashing u32 ids instead of strings addresses program audit #6 (72 ms of string hashing per LSP edit).
- **package.json:** entry membership is a sharded `PathId(dir) → Arc<InfoCacheEntry>` (exists, directory exists, contents). Contents come from a **process-wide content cache keyed by `(path, text hash)`**, parsed once (E3b).
  - Membership stays per load. That keeps the `packageJsons`/`missingPackageJsons` sets exact, so only the parse is shared.
  - E3 measured 685 package.json parses with no workers, 1,771 at the default count, about 0.77 G instructions.
- **Stats:** the host's lookup cache, made concurrent: `SharedStatCache` in the CLI, `BuildStatCache` in `-b`, the snapshot view in the LSP.
- **LSP tracking:** concurrent `PathId` sets for seen files and missing directories.
- **Effect attribution** (needed only for C3): each memo entry records the `PathId`s it read.

The `Rc` → `Arc` work is in `DefaultResolver` (`module/resolver_p1.rs:388`), `Caches` (`module/cache.rs:277`) and `InfoCache` (`packagejson.rs:1147`). E3 counted about 73 sites in 13 files.

### 3.5 Replay and commit (C1)

**Replay:**
- The replay is `start` + `run_queued` over `LoadGraph`: a `Vec` stack, `PathState` indexed by `PathId`, casings in insertion order, `packageId` set by the first non-empty arrival in run order. "`load(rec)`" means "append `rec` to the load sequence and push its edges".
- **[est.]** About 10–30 ns per arrival: umami (4.4k records, about 20k arrivals) under 1 ms, vscode 1–3 ms. S3 measures this on real graphs.
- **Check:** discovery's `(loaded set, started_sub_tasks per record, lowest_depth per path)` must equal the replay's. Any difference sends the load to the serial engine.

**Commit, in replay load order:**
- `adopt_detached_store` gives the next real id (`ast/store.rs:4413`). Make the JSDoc cache id-free, keyed by slot, so adoption is O(1) instead of a rehash.
- Synthetic `tslib` and JSX nodes are created (`file_loader.rs:2247`).
- Counts are summed.

Then the **existing walk** (`files_parser.rs:965-1600`) runs on the graph. It is serial, as Go's is. Program maps are still built in walk order, so their iteration order doesn't change.

### 3.6 Fallback to the serial engine (v1)

The serial engine handles:
- `singleThreaded`, wasm, and `GOPORT_LOADER=serial`;
- `traceResolution` (Go itself bypasses the cache, and the wording is order-dependent);
- a custom module resolver (the API's);
- content mappers configured;
- a case-insensitive FS (macOS and Windows; v1 only, see §5);
- a replay mismatch.

**On fallback:** new resolver and package.json membership, new LSP tracking sets, parse-cache acquires from discovery released, `-b` lookups from discovery dropped. A `GOPORT_LOADER_STATS` counter records fallbacks, on stderr or in the LSP log only.

### 3.7 How each order-dependent output stays identical

| Output | Today (serial) | New design | Why it's identical |
|---|---|---|---|
| **File ids / store ids** | LIFO load order (`files_parser.rs:1820-1827`) | assigned at commit in **replay** order | The replay runs the same state machine on the same records, and `load()` results don't depend on order (pure memos over the FS view). S3 proves sequence equality on every corpus graph. |
| **Synthetic specifier identity** (A1's concern) | created in `load()`, in serial order | created at commit, in replay order; reasons point to the owner's node | same creation sequence, same node per file |
| **File order** | walk | unchanged walk over the graph | depends only on root order and per-record edge order, which is Go's subtask order |
| **Include reasons** | Rc per edge, appended in walk order | built from `Arrival.reason` plus the owner in walk order | same walk, same data |
| **Casing first kept** | walk `seen` (:1227–1231); dupes once per casing | unchanged walk | Which casings get *loaded* is checked against the replay. On a case-sensitive FS there is only one casing per path. |
| **Depth restarts, `maxNodeModuleJsDepth` elision** | LIFO run | discovery computes Go's fixed point; replay recomputes the serial state | equality of loaded, started and depth sets; else fallback (covers the elided-first case) |
| **`packageId` per path; dedup** | first non-empty in run order; dedup in walk | replay computes the first; walk unchanged | same |
| **First module-resolution error** | serial first (`file_loader.rs:2342`) | custom resolver ⇒ serial engine | the default resolver has none (Go `resolver.go:467`) |
| **Content-mapper cap of 5** | serial counter (`file_loader.rs:1376`, :1415) | content mappers ⇒ serial engine (v1) | unchanged path. Later: count in replay order, stub files over the cap |
| **`packageJsons` / `missingPackageJsons`** | loader cache plus adopted closures of worker answers | one membership map written only by authoritative lookups; sorted at buildinfo time | Same set of loads, so the same union of memo-creating calls (§2). No speculation exists to filter out. |
| **Negative lookups** (failed lookup and affecting locations; missing package.json entries; `DirectoryExists=false`) | per answer; loader tracking | fields of answers (pure); concurrent sets of authoritative calls | union argument: same loads ⇒ same calls |
| **Discovery side effects** (LSP seen files, missing dirs, parse-cache refcounts; `-b` stat cache) | loader or replayed worker logs | written directly by authoritative calls; one acquire per loaded casing; fallback discards | same loads; fallback starts clean |
| **Traces** | serial | serial engine | Go's own wording races |
| **Hash-map iteration** | program maps built in walk order | same; concurrent caches never iterated except package.json, which is sorted | rule plus shuffle-seed test (S4) |

### 3.8 O(1) free

`LoadGraph` is plain-old-data `Vec`s, so dropping it frees about 8 allocations. Destructor-owning values (files, `Arc<ResolvedModule>`, diagnostics) are owned by the program's `Vec`s, not by the graph. No `ParseTaskLinks` pass and no `drop_later` (`files_parser.rs:415-441`). This removes the 27 ms per-rebuild free that the LSP audit measured, and the forget-the-loader trick (`file_loader.rs:549`).

### 3.9 Reusing unchanged files' records across LSP versions (C3 + LSP #5)

- The project keeps `Arc<LoadGraph>` and the memo layer with effect attribution.
- A new snapshot comes with a change set Δ: overlay edits, opens and closes, watch events, package.json edits. A config change forces a full reload.
- **A record is valid** if its parse-cache key is unchanged and no memo entry in its resolution closure read a path in Δ. A `PathId → entries` index is built on the first edit.
- **The new load** walks from the roots. A valid record is reused in O(1); an invalid one gets `load()` again, in parallel. Then replay and commit as usual.
- **Tracking sets** are the union of the loaded records' closures, computed with a bitset over `PathId`s.
- **LSP #5** is the special case where Δ holds only content edits of existing non-package.json files: only the edited files reload.
- **Synthetic nodes** in reused records are remapped by A1's rule (Astra finding 1).

---

## 4. Staged plan

| Stage | What | Size [est.] | ttfd (umami) | CLI cold load | Parity proof | Kill condition |
|---|---|---|---|---|---|---|
| **S0** | Loader phase timers (`GOPORT_LOAD_TIMES`) and a canonical **load dump** (`GOPORT_LOAD_DUMP`: arrivals, serial order, loaded/started sets, depths, seen files, missing dirs, package.json membership, stat lookups, acquires) | 250–400 lines | 0 | 0 | output byte-identical (the dump is a separate file) | – |
| **S1 — closes ttfd** | Snapshot-host first load takes the CLI path (`file_loader.rs:362-380`) through a host `worker_view()` (details below) | 600–1,000 lines | **427 → ~240–280 ms [est.]**: loader CPU 322 → ~110–140 profiled (CLI does 94), program update → ~190–220 | 0 | S0 dump with S1 on and off on umami, ts-pattern, hono, query, t3-web; lspdiff tier; new watch-registration lspdiff variant; open-file and second-project cases | ttfd median ≥ 289 ms at idle (5 runs), or exactness needs per-call validation costing > 40 ms |
| **S2 (E3b)** | One process-wide package.json **content** cache; membership per resolver unchanged | 300–600 lines | −3 to −8 ms (worker CPU) | parse-phase instructions about −15% (E3: 0.77 G of 5.0 G); vscode wall −20 to −40 ms (worker-bound) | incr/`-b` tiers (`packageJsons`); icount A/B | < 0.3 G saved on umami parse-only |
| **S3 (C1a)** | `LoadGraph` + replay as a **shadow** of the serial engine, asserting the same sequence and state | 1.5–2k lines | 0 | 0 | this stage is the proof: run on the corpus (12,727), fuzz (30k), projects and LSP sessions | replay > 5 ms per 10k files, or a mismatch not traced to a known case |
| **S4 (C2)** | Authoritative parallel discovery, replay commit, fallback; delete the speculation machinery and S1's adapter; parallel root FileExists | 4–6k lines changed, 3–5k deleted | −20 to −40 ms more → ~200–230 | umami −20 ms (tail); vscode main-thread floor 312 → ~70 ms, load 0.42 → ~0.30 s, check about −2 to −3%; import-changing LSP rebuild 78 → 20–30 ms | `GOPORT_VERIFY_LOADER` (serial engine in a child process, digest compare) on corpus, fuzz, projects, LSP; `GOPORT_PARSE_THREADS` 1/2/4/32 × `GOPORT_DISCOVERY_SHUFFLE` seeds; fallback counter 0 on real projects; parity full | vscode load not ≥ 20% better than after S2; any real-project fallback; a digest mismatch not fixed in 2 rounds |
| **S5 (C1b)** | O(1) free, compact collect, lazy reasons | 0.8–1.5k lines | −3 to −5 ms | vscode collect 26 → ~10 ms | parity full | < 10 ms total on vscode |
| **S6 (C3 + LSP #5)** | Reuse records across versions | 1.5–2.5k lines | 0 | 0 | per-edit verify (fresh load in a child) in lspdiff and the stress tool; watch tests | invalidation > 5 ms per edit, or any digest mismatch |
| **S7** (optional) | Bind in the discovery workers, as Go's LSP parse cache does | separate note | −25 to −30 ms (bind 33 ms overlapped) | vscode −100 to −160 ms (160 ms bind phase) | lineage order (`program.rs:1868`) and private-name ids (`give_private_name_class_ids`) need their own design | – |

**S1's `worker_view()` capability gives the loader:**
- **(a)** the overlay paths and directories. Answers that touch them aren't shared; the loader resolves those keys itself.
- **(b)** a check against lookups the snapshot already caches. Answers that contradict them are rejected.
- **(c)** at `end_load`, every taken answer's lookups seed the snapshot's lookup cache **and** its seen-file and missing-dir sets. This is the pattern `-b`'s `BuildStatCache::end_load` already uses (`files_parser.rs:4954`).
- **(d)** worker texts and hashes seed the snapshot's disk-file cache, so the loader doesn't read and hash again.
- **(e)** worker metadata and preps are taken (`take_prefetched_meta` :2261, `FilePrep::fits` :2154).

**S1 notes:**
- S1 is speculation that S4 deletes later: about 600–1,000 throwaway lines. It's the cheapest way to close ttfd now, and nearly all of it reuses existing paths.
- After S1, the loader overlaps the workers as it does in the CLI. Expected: setup 9 + max(workers ~95, loader ~120) + tail ~20 + collect 9 + publish 9 + bind 33 ≈ 190–220 ms program update.

---

## 5. Risks, and what the parity gate does and doesn't cover

### Risks

- **Observability of file ids.** `Node` derives `Ord` and `Hash` with the file id in its high bits (`core.rs:127`). Any sort or iteration keyed by `Node` depends on id order. The design keeps today's ids exactly, through the replay. An optional id-permutation run under parity full would show whether ids are observable at all.
- **Map iteration order** of anything built during discovery. Rule from §3.7 plus the shuffle-seed test.
- **The Sync snapshot view (S4)** is a broad `Rc` → `Arc` refactor of the snapshot FS (`project/snapshotfs.rs:1366` `SourceFS`, the overlay and disk caches), plus the resolver's `Rc`s. Atomics cost a little. Measure with icount.
- **Unnoticed disk changes (C3).** Trusting old records is Go-equivalent only for lookups that Go's snapshot also caches across snapshots. Existence checks Go would ask the OS again must be checked again. This needs a Go read of `snapshotfs.go` caching rules before S6.
- **Case-insensitive file systems** fall back in v1, so macOS and Windows get no S4 gain until per-record casing logs are added.
- **Fallback frequency** must stay at 0 on real projects. The counter is the kill signal.
- **S1 is throwaway code.**
- **The shared machine.** Wall claims need idle paired runs; my Go medians had bursts up to 395 ms.

### What the gate covers

- **CLI tier:** listfiles; explainFiles and traceResolution (30% of fuzz cases); extdiag file counts; incr `.tsbuildinfo` including `packageJsons`; `-b` twice.
- **Corpus:** 12,727 cases through the real CLI on Linux.
- **TestLocal** (in process, case-insensitive vfs cases).
- **LSP:** answers on umami, ts-pattern and hono.
- **Nondeterminism:** `rs-nondet` (tsc-rs run twice) on CLI and fuzz.

### What it misses

1. **LSP watch registrations and invalidation.** lspdiff's client has no dynamic watch registration ("file watching: disabled" in both logs), so seen files and missing dirs are never compared. This is the main exactness risk for S1, S4 and S6. Add an lspdiff mode that advertises `didChangeWatchedFiles` dynamic registration and compares the registered globs with Go's.
2. **The LSP tier runs once.** There's no `rs-nondet` for LSP. Add a two-run mode for umami.
3. **First-load-specific situations:** an open file not on disk; an edited package.json overlay; a second project's first load after the session cached lookups. These need S1's own tests.
4. **Case-insensitive real file systems, content mappers, custom resolvers.** They go to the fallback, but the gate can't tell whether the new engine was used. The `GOPORT_LOADER_STATS` counter must be part of the proof.
5. **The elided-first and casing-load interplay.** Add synthetic graphs that assert fallback and serial-identical output.
6. **TestLocal may run single-threaded** and so exercise only the serial engine. Force the new engine in one test-local shard.
7. **ttfd and LSP timing** are outside the gate; `bench.sh lsp --scenarios ttfd` is the measure.

---

## Artifacts

Tools, in `scripts/perf-rearch/loader/`:
- `ttfd-logs.mjs`: ttfd plus the server's log lines and stderr, timestamped from spawn. `node scripts/perf-rearch/loader/ttfd-logs.mjs <server> -- <project dir> [--file REL]` (the script adds `--lsp --stdio`).
- `phases.py`: extracts config, program update, didOpen, diagnostic and ttfd from `ttfd-logs.mjs` JSON.
- `loader_thread.py`: per-thread spans and the call tree under an anchor function for the loader thread (`--marker` picks the thread, e.g. `getProcessedFiles` for Go), built on `samply_top.py`.
- `samply-wrap.sh`: runs a server under samply for `ttfd-logs.mjs` (`SAMPLY_BIN`, `SAMPLY_OUT`, optional `SAMPLY_RATE`); pass its absolute path as the server.

Data, in `~/.cache/perf-rearch/scratch/design-loader/`:
- Profiles: `ttfd-rs.json.gz`, `ttfd-go.json.gz`, `cli-list-rs.json.gz` (umami CLI), `cli-vscode-list.json.gz`, each with its `.syms.json`.
- Run JSONs: `g-{go,rs}-{1..5}.json`, `full-*.json`, `stats-rs.json`.
