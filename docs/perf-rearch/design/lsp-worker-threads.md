# LSP worker threads: design

Design lane, 2026-11-12. Read-only: no source change, no build, no benchmark. Base: fork `main`
`1b6aba1a8`. Port paths are relative to `crates/ts_goport/src/`. Go paths are relative to
`~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc/internal/`.

Inputs:
- the profile `docs/perf-rearch/experiments/lsp-t3code-profile.md` on `perf/lsp-t3code-profile`
  (`e385ab538`), called "the profile" below;
- the earlier design `docs/lsp-concurrency.md` on `perf/lsp-concurrency` (`931fad409`), called
  "phase 1";
- the state notes `lspconc1-note-2026-10-26`, `lspyield1-dropped-2026-10-26`,
  `lspdiag1-dropped-2026-10-26`, `lspwait1-dropped-2026-10-04`, `followups15-decision-2026-10-03`,
  `lspperf1-not-taken-2026-09-30`, `resolveahead1-decision-2026-10-02`, `lsgaps3b-2026-10-07`,
  `apisess1-decisions-2026-10-01`.

Markers: **M** = measured by the profile or a note (cited), **E** = my estimate from those parts,
**G** = a guess I could not check without running code.

## 0. Recommendation

Use **slot threads**: one thread per (program version, checker-pool slot), following Go's
checker pool (1 diagnostics slot, 3 query slots). This is option (b), built on the
search-thread machinery of option (a). The dispatch thread keeps the session, snapshots, sync
parts, pool bookkeeping and background tasks. It applies Go's slot rules and hands the async
part of a request to the thread that owns that slot's checker. The thread sends the answer
itself, then posts a continuation back (`gostd::local::post_later`). Under one-at-a-time
traffic (what the parity gate sends) every request gets the checker Go gives it, so answers
stay byte-identical. Under load, each answer is one Go could give. Do not make the
language-service types `Send` wholesale (option c).

**First milestone: pull diagnostics on the slot-0 thread.** Only diagnostics use slot 0, so
this one move takes one checker with one request kind and leaves every other checker's
history as it is. It removes the ~39 ms that tokens, hints and other-file diagnostics wait
behind the edited file's diagnostics in pulls=all (74–75 → ~35 ms, E), and moves the 780 ms
diagnostic pulls off the dispatch thread on `ChatView.tsx`. It needs four things:
- a frozen `Send` view of the snapshot file system for the checker's probes (the blocker that
  dropped lspdiag1);
- symbol-id sync at job boundaries;
- the continuation;
- a measured seed cost.

**The query slots can only move together.** Every query kind shares slot 1 in Go's sequential
behavior, so the next step is a series of behavior-neutral refactors (providers generic over
`ProgramView`, a `Send` auto-import view). The flip of slots 1–3 to threads comes after all of
them. That flip is what brings tokens and hints to ~5–12 ms (E, the profile). It is 10k+ lines
(E). Decide on it with M1's numbers.

## 1. Go's model at the pin

### 1.1 Goroutines

- **Server** (`lsp/server.go:895–912`): an `errgroup` runs `dispatchLoop` and `writeLoop`, and
  a plain goroutine runs `readLoop`.
- **Per request with an async part:** one bare goroutine
  (`go func() { doAsyncWork(); removeRequest() }()`, `server.go:1054`). There is no pool and no
  limit.
  - The sync part runs inline on the dispatch loop (`server.go:1050`), for each kind of handler
    in `handlerMap` (`server.go:1263–1333`).
  - `registerLanguageServiceDocumentRequestHandler` (`:1378`): the sync part is
    `session.GetLanguageService`, which flushes pending changes and may clone the snapshot. The
    async part is the provider, then `EnqueuePublishGlobalDiagnostics`, a `ctx.Err()` check and
    `sendResult`.
  - `registerLanguageServiceWithAutoImportsRequestHandler` (`:1405`, completion and
    codeAction): the async part may call back into the session
    (`GetLanguageServiceWithAutoImports`, which clones the request's own snapshot).
  - `registerMultiProjectReferenceRequestHandler` (`:1440`, references and implementation):
    the orchestrator calls into the session, and `ls/crossproject.go:79` searches projects in a
    `WorkGroup` of goroutines.
  - `registerRequestHandler` and `registerNotificationHandler` have no async part. These are
    rename, call hierarchy in/out, workspace/symbol, completionItem/resolve, codeLens/resolve,
    willRenameFiles, and every notification.
- **Background tasks:** every `backgroundQueue.Enqueue` is its own goroutine
  (`project/background/queue.go:20–40`). That covers snapshot logging, watch updates,
  publishDiagnostics, ATA, global-diagnostics publish and snapshot adoption.
- **Auto-import warm:** a goroutine started from `UpdateSnapshot` (`project/session.go:2077`).
  Its registry build makes its own pool of checkers (the profile: "32 created checkers").
- **Timers:** checker-pool idle cleanup (`time.AfterFunc`, `checkerpool.go:373`) and the debounced
  snapshot and diagnostics refreshes.

### 1.2 Locks and atomics

| What | Guard | Where |
|---|---|---|
| request and outgoing queues | channel state machine (`dynamicQueue`), FIFO | `lsp/dynamic_queue.go` |
| pending client requests (cancel lookup) | `pendingClientRequestsMu` | `server.go:991, 1016` |
| current snapshot pointer | `snapshotMu sync.RWMutex` | `session.go:137` |
| snapshot updates (flush, clone) | `snapshotUpdateMu sync.Mutex`: one clone at a time | `session.go:138`, `getSnapshot` `:1055` |
| pending file changes, ATA changes, warm cancel, refresh timers | one mutex each | `session.go:144–180` |
| global-diagnostics publish coalescing | `atomic.Bool` | `session.go:196, 1999` |
| snapshot lifetime | `refCount atomic.Int32`; `ref`, `tryRef`, `Deref`; `dispose` at 0 | `snapshot.go:35, 731–764` |
| program lifetime | `programCounter.mu`; `Deref` to 0 discards the pool | `programcounter.go`, `snapshot.go:764` |
| `Program` | immutable after creation, plus `sync.Once` lazies | `compiler/program.go:71–141` |
| checker pool | `mu`, `diagSem` (1), `querySem` (3), `persistentSem` (1) | `checkerpool.go:41–82` |
| checker | none. One holder at a time, by the pool's `heldBy` | `checkerpool.go:226–265, 319` |
| node and symbol ids | process atomics; an id is stored on the node or symbol with a CAS (a lost CAS burns an id) | `ast/utilities.go:17–45` |

### 1.3 What a request holds while it runs

- **A document request** holds a `*ls.LanguageService`: the program pointer and the snapshot as
  host. It takes **no snapshot ref** (`GetLanguageService`, `callerRef=false`, `session.go:1145`).
  "Language services continue to work even after their backing snapshot has been disposed"
  (`session.go:1282`): the GC keeps them alive.
- **Completion and codeAction** take a ref (`WithLanguageServiceAndSnapshot`, `:1284`) and drop
  it when the async part ends. They need the snapshot for the auto-import retry.
- **Checkers are taken and released per projection or per phase, not per request**
  (`GetTypeCheckerForFile` and its `done`).
  - Slot rules: diagnostics take slot 0 (`getDiagnosticsChecker`, `:194`, via
    `CheckerLifetimeDiagnostics` set in `handleDocumentDiagnostic`, `server.go:1891`).
  - A query takes, in order: request affinity, the file's associated slot if idle, the lowest
    idle slot, else a new checker in the first empty slot (`:226–287`).
  - A full semaphore blocks the goroutine. A checker that saw a cancel is disposed on release
    (`:319–356`).

### 1.4 Ordering guarantees

- The sync parts run in arrival order. So does every `didChange` before the next request's
  snapshot.
- **Concurrent async parts have no order.** The write loop sends answers in completion order.
- Background tasks have no order among themselves or against answers.
- Snapshot clones are serialized (`snapshotUpdateMu`).
- That is all Go promises.

### 1.5 Cancellation and snapshot updates

- **Cancels.** `readLoop` handles `$/cancelRequest` at once (`server.go:978–981`).
  `cancelRequest` (`:991`) cancels the request's context only if the dispatch loop has
  registered the request. A cancel for a queued request is dropped, but Go's queue is short,
  because sync parts are short.
- **Async work stops cooperatively.** The LS loops check `ctx`. The checker checks it per
  top-level statement and sets `wasCanceled` (`checker/checker.go:2294`). The pool then
  disposes that checker.
- **Snapshot updates.** A `didChange` only records a pending change. The next request's sync
  part flushes it and clones (`getSnapshot`). An async part that is still running keeps its old
  language service and program and answers from the old snapshot. Nothing invalidates it; only
  a cancel stops it.
- **Disposal.** When the last snapshot ref goes, `dispose` derefs the programs. A program with
  no refs left gets `checkerPool.Discard()` (its idle timer stops), and the GC frees it later
  (`snapshot.go:764–790`).

### 1.6 What makes Go's answers repeatable

- **Nothing makes Go repeatable under concurrency.**
  - The query checker a request gets depends on which slots are busy.
  - A checker's answers depend on what it checked before: type ids order union members, for
    example.
  - Symbol ids race between goroutines.
- **Under one-at-a-time traffic the choice is fixed:** diagnostics on slot 0, every query on
  slot 1 (phase 1, §4).
- **That gives the port's rule:** for sequential traffic, make Go's exact checker choice and
  feed each checker the same history; under concurrency, make a choice Go could make.

## 2. Which request kinds move, which stay

| Kind | Checker | Moves to | Why |
|---|---|---|---|
| `textDocument/diagnostic` | slot 0 only | **slot-0 thread, M1** | Go's dedicated checker. No other kind shares it, so it moves alone. Biggest queue blocker (38–42 ms own work, 780 ms on `ChatView.tsx`). |
| semanticTokens full/range, inlayHint, hover, definition, typeDefinition, sourceDefinition, signatureHelp, documentHighlight, multiDocumentHighlight, codeLens, prepareCallHierarchy, prepareRename, linkedEditingRange | query slot | query-slot threads, **M4** (after M2, M3) | All share slot 1 in sequential Go. They must move together (§3.6). |
| completion, codeAction | query slot + auto-import registry | query-slot threads, M4 (after M3) | They need a `Send` auto-import view. The `ERR_NEEDS_AUTO_IMPORTS` retry becomes a round trip to the dispatch thread. |
| documentSymbol, foldingRange, selectionRange, formatting (3 kinds), onAutoInsert | none | can move at any time, no gain | Their own work is small (the profile shows no wait from them). |
| references, implementation | default project on slot 1; other projects on search threads | stays as today until M4. The default project's search then joins the query slot. | The orchestrator calls the session. |
| rename, call hierarchy in/out, workspace/symbol, completionItem/resolve, codeLens/resolve, willRenameFiles, projectInfo, API session, profiling | various | **stay on the dispatch thread** | Go runs them synchronously in the dispatch loop too. |
| didOpen, didChange, didClose, didSave, didChangeWatchedFiles, didChangeConfiguration, initialize, shutdown, exit, setTrace | none | **stay** | They mutate session state, and Go handles them in the dispatch loop. |
| Sync parts (flush and snapshot clone, 4.3 ms per edit) | none | **stay** | Go serializes them too (`snapshotUpdateMu`). |
| Background tasks, timers, client requests (`registerCapability`), ATA | none | **stay** | The oracle compares where publishDiagnostics land (`lsp/server.rs:55–67`). |
| Auto-import warm | its own checkers | stays (idle work) until M3 provides a `Send` registry | See §5. |

## 3. State sharing

### 3.1 What a worker needs and how it gets it

| Need | Today | On a slot thread |
|---|---|---|
| Program version (`prog()`, `VersionTables`, AST store, lib blobs) | `&'static GoProgram` and `Arc`/`OnceLock`/`Mutex` tables; lib blobs static | As is: `ls_program::enter_version` (`program/ls_program.rs:329`). Already thread-safe: the CLI's checker threads read the same data. |
| Program reads of the LS (`NewProgram`: file by name, default-lib test, JSX and helper specifiers) | `Rc<NewProgram>` with `OnceCell` and `Rc<dyn Resolver>` | `ProgramView` (`ls/program_view.rs`) backed by a plain-data copy (`SearchProgramData`, `ls/search_thread.rs:444`), grown one provider at a time |
| Checker | `Rc<RefCell<Checker>>` in a thread-local registry (`ls_program.rs:134–175`) | Made and kept on its slot thread, never crosses |
| Thread-local factory state (synthetic nodes, lazy JSDoc, node and symbol ids) | per thread | `WorkerSeed` (`program.rs:4169`) at spawn, plus a **symbol-id delta sync** per job (§4) |
| Checker probes of the file system (`file_exists`, package.json, ancestor directory) | dispatch: the snapshot's FS through `FRONTENDS`. A worker reads `HostFsCache`, then the **OS FS** (`program/go_frontend.rs:157–172, 790–840`; `modulespecifiers/host.rs:20–60, 210–260`) | **A frozen `Send` snapshot-FS view** per version (§3.6), or `host_queries` to the dispatch thread as the fallback (`program/host_queries.rs`) |
| Host reads of the LS (file text, line maps, `Converters`) | `Rc<dyn Host>` = the snapshot; `Rc<Converters>` | `WorkerHost`-style: program files from the AST store, line maps computed per thread, other files from the FS view |
| Request context, params, preferences, capabilities | `Context` (`Send + Sync`), `Rc<RequestMessage>`, plain structs | `Context` cloned. Params decoded on dispatch and moved by value; preferences cloned. |
| Answer | `lsproto` values (`AnyValue: Send`) | `ServerShared::send_result` (`Arc`) from the thread |
| Logger | `Arc<Logger>` | As is |
| Auto-import registry (M3 only) | `Rc` over dirty maps | A frozen `Send` view per snapshot. Go's registry is immutable per snapshot apart from a `SyncMap` specifier cache (phase 1, §6.4). |
| Session callbacks (global-diagnostics publish, auto-import retry, telemetry) | `Rc<Session>` | A continuation on the dispatch thread through `post_later` (`gostd/local.rs:241`) |

### 3.2 Option (a): extend the search-thread pattern to query requests

The pattern: one long-lived thread per program version, seeded with `WorkerSeed`. It keeps one
checker and drops it after 30 s idle. Jobs and results are plain data, and rare reads go to the
dispatch thread (`HostQuery`). It ends at `release_search_thread`.

- **What changes:** the job kinds grow (`SearchJob` becomes a general job), `SearchView` grows,
  and providers become generic over `ProgramView`. Files: `ls/search_thread.rs`,
  `ls/program_view.rs`, each provider, and `lsp/server.rs`.
- **Single-request cost:** a seed per version (O(size of the dispatch thread's synthetic arena
  and id tables); **G**, never measured on a long session), a thread spawn (~30–80 µs, G), and
  a channel hop each way (~10 µs, G).
- **Memory:** one copy of the seed per live version, and one checker. Short-lived threads land
  in other jemalloc arenas, so the next version's checker does not reuse the freed pages (see
  freecheck1/2 and heapafter1).
- **Parity risk:** **one checker per version breaks Go's slot model.**
  - Its `HostQuery` round trip blocks when the dispatch thread runs a 35 ms hover. Today the
    dispatch thread answers only while it waits for its own search.
  - Diagnostics would share a checker with queries, or queries with each other, depending on
    the order: neither is Go's.
- **Verdict:** good machinery, wrong unit.

### 3.3 Option (b): a per-program pool of slot threads, as Go's checker pool

The checker of each slot lives on its own thread: slot 0 diagnostics, slots 1–3 queries.

- **How it works:**
  - The dispatch thread keeps the pool's bookkeeping (`held_by`, file and request associations,
    `last_released`, the idle timer; `project/checkerpool.rs`).
  - It picks the slot with Go's rules when it hands a request off. "Idle" means no job queued
    or running on that thread.
  - The counting semaphore whose full case is `unreachable!` (`checkerpool.rs:40–75`) becomes
    a parked request that starts when a slot frees.
- **What changes:**
  - `lsp/server.rs`: hand-off in the three LS handler kinds (`:3062`, `:3108`), and the
    continuation.
  - `project/checkerpool.rs`: remote slots, split release, dispose on cancel.
  - `program/ls_program.rs`: registry, checker id, the diagnostics functions over a version.
  - `ls/search_thread.rs`, generalized into a slot-thread module, and the providers.
  - `ast/utilities_p1.rs`: id sync.
  - The FS view (`project/snapshotfs.rs` or a new module, `program/go_frontend.rs`,
    `modulespecifiers/host.rs`).
  - `crates/ts_goport/PORTING.md` "Threads".
- **Single-request cost:** as in (a), per slot thread used, plus a pool decision (cheap). The
  diagnostics checker creation (~3 ms, M) and the edited-file check leave the dispatch thread.
- **Memory:**
  - Up to 4 checker threads per live program version, but Go makes the same 4 checkers.
  - The seed copy per thread is the new cost (G).
  - Pin each slot's threads to one claimed jemalloc arena (`set_jemalloc_thread_arena`, as
    `start_checkers` does at `program.rs:4402–4440`), so a new version's checker reuses the old
    version's pages.
- **Parity risk:** lowest of the three, because it is Go's model: same slot, same per-slot
  FIFO history. The remaining risks are ids (§4) and FS answers (§3.6).

### 3.4 Option (c): make the language-service types `Send` wholesale

| Area | `Rc` | `RefCell` | files | lines |
|---|---|---|---|---|
| `checker/` | 611 | 323 | 79 | 124k |
| `project/` | 1,263 | 337 | 45 | 25k |
| `ls/` | 591 | 159 | 74 | 67k |
| `frontend/` | 731 | 107 | 96 | 86k |

These counts come from `grep -o`. `Rc<dyn Fs>` appears in 50 files. Thread-local state sits in
`ast/` (18 sites) and `program/` (16): synthetic arena, ids, lazy JSDoc, `CHECKER_ARENA`.

- **What changes:** either a `Send` checker or `Arc`/`Mutex` across the LS layer, plus movable
  thread-local arenas.
- **Single-request cost:** atomic refcounts and locks on relater, node-builder and binder hot
  paths that the CLI shares. A guess of +1–5% instructions on CLI cells (G), which conflicts
  with "≤ Go on every cell".
- **Memory:** neutral.
- **Parity risk:** high. It touches the checker's hot paths and arenas. Most Rust-only
  single-thread notes (~280 PORT blocks, phase 1 §6.6) would revert.
- **What it buys:** no latency that (b) does not, since Go also serializes sync parts and keeps
  one checker per holder.
- **Verdict: rejected.** This agrees with phase 1 §6.1 and lspconc1 option (a).

### 3.5 Comparison

| | (a) search pattern | (b) slot threads | (c) wholesale `Send` |
|---|---|---|---|
| Go's checker choice | no | **yes** | yes |
| Lines (E) | similar to (b) | M1 1.0–1.6k; all stages 10–16k | 30k+ (G) |
| CLI hot-path cost | none | none | likely >0 (G) |
| Dispatch-thread cost per edit | seed (G) | seed per used slot (G); minus checker creation and the diagnostic check | none |
| Memory | +seed copy | +seed copy per used slot; arena-pinned | ≈0 |
| Parity risk | medium (slot model) | low–medium (ids, FS) | high |

### 3.6 The recommended shape in detail

- **A slot thread** (generalize `ls/search_thread.rs`):
  - Spawned lazily for (version, slot) at the first job.
  - Seeded with `bind_source_files` + `WorkerSeed::take` on the dispatch thread, as
    `spawn_search_thread` does (`search_thread.rs:338–355`).
  - Pinned to the slot's jemalloc arena.
  - Its jobs run FIFO, so per-slot history equals the arrival order.
  - It ends when the program is released: drop the sender, and the thread exits after its
    queue.
  - On wasm (one thread) the executor runs the job inline on the dispatch thread. This is a
    `cfg(target_family = "wasm")` target split as in `program.rs:4402`, not a switch.
- **The job:**
  - Built in the sync part, after `run_pending()`, so tasks that the sync part queued (snapshot
    logging, watch updates, publishDiagnostics) still run before the job starts
    (`lsp/server.rs:2247–2258`).
  - It carries the decoded params, `Context`, preferences, position encoding, project id, the
    `Arc` program data and the FS view, the symbol-id delta, and a `Post` handle.
- **On the slot thread:**
  - The job makes a `LanguageService<SlotView>` over the thread's checker.
  - It runs the provider, sends the answer with `ServerShared::send_result` (or the cancel
    error), and logs `handled method …` through `Arc<Logger>`.
  - Then it fills a shared result cell (global diagnostics merged at release for slot 0,
    `was_canceled`, the id delta, a panic payload if any) and drops the `Post` handle.
- **The continuation** runs on the dispatch thread in the next `run_pending` (`gostd/local.rs`).
  - It does the pool release: Go's `createRelease` (`checkerpool.go:319`), and dispose after a
    cancel (dispose becomes "the thread drops its checker").
  - It merges ids, calls `enqueue_publish_global_diagnostics`, does `remove_request`, and
    re-raises a panic through `recover_guard`'s Go text.
  - The post wakes the loop through the existing waker (`QueuedRequest::Wake`). `Wake` counts
    as a queued message (`lsp/server.rs:1991`), so a running auto-import warm yields to it.
- **The FS view.** Lane lspdiag1 found that the semantic check reaches `program::file_exists`.
  On the dispatch thread that reads the snapshot FS, with editor overlays. On a worker it reads
  `HostFsCache`, then the OS FS: wrong for unsaved buffers. Two ways to fix it:
  - **(i) A frozen `Send` view per snapshot** (my recommendation):
    - Overlays: path → `Arc<str>` + kind, as `FileText` already is (lspbig1, note
      `followups17b-lspbig1-decision-2026-10-03`), plus the snapshot's cached disk answers.
    - Behind it the OS FS, memoized per version in the existing `HostFsCache`, so the first
      answer stays for the version.
    - It serves `file_exists`, `directory_exists`, `read_file` and realpath for the module
      specifier host and the checker. The package.json lookups read through it too, instead of
      `osvfs_fs()` (`modulespecifiers/host.rs:228`).
  - **(ii) `host_queries` to the dispatch thread** (exists for the API `--callbacks` build). It is
    correct but blocks the worker while the dispatch thread runs a request. Use it only for the
    rare probes that (i) does not cover.
  - M0 counts the probes per edit to size (i) (§6).
- **Bookkeeping invariants:**
  - A checker never crosses threads.
  - No synthetic `Node` crosses threads. Only positions, URIs, file roots of pinned versions and
    plain data may cross.
  - Slot choice happens on the dispatch thread only.
  - The dispatch thread never blocks on a slot thread, except at shutdown. While it waits
    there, it serves host queries.

## 4. Determinism and parity

| Source of difference | Where Go stands | How the design keeps the port right |
|---|---|---|
| **Which checker answers** | Fixed under sequential traffic (slot 0 / slot 1); varies under load | Go's rules, applied at hand-off. Per-slot FIFO keeps each checker's history in arrival order. **Query kinds move together** (M4): one kind on another checker would change slot 1's history for the rest (e.g. type ids, then union order in hover and hint text). |
| **Symbol ids** (late-bound `__@k@<id>`, node-builder truncation, `compareSymbols`; `crates/ts_goport/PORTING.md:1088–1110`) | Process atomics; the id is on the symbol, so every checker sees it; concurrent first uses race | Today every LS checker is on the dispatch thread and shares one table (`ast/utilities_p1.rs:16–40`), which equals Go for sequential traffic. A slot thread's seeded table would drift: ids that the dispatch thread gives after the seed are not seen there, and the reverse. **Fix: delta sync at job boundaries.** The job carries the global and block counters and the lineage cells set since the thread's last sync. The result carries the thread's own. The continuation merges them with the CLI carry rules (`carry_symbol_ids`, "first id wins", `PORTING.md:1142–1175`). Sequential: exact. Concurrent: a Go race outcome. |
| **Checker id** | Atomic `nextCheckerID` | Identity only (search threads already use their own range, `search_thread.rs:39`). Make it one process atomic. |
| **Answer order on the wire** | Completion order | Concurrent answers reorder, as Go's do. The gate's sessions await each answer (`vendor/lspdiff/session.mjs:123`), so the gate sees no change. |
| **publishDiagnostics vs. the answer** | Background goroutine races `sendResult`; usually after | The worker enqueues the answer, then posts. The dispatch thread enqueues the publish in the continuation, so the publish comes after the answer, as today. Under load the next request can be dispatched before the continuation runs (G: needs a descheduled worker). Go races the same way. |
| **Sync-part tasks before async work** | Usually done before the answer | Unchanged: `run_pending()` runs before the hand-off. |
| **FS answers** | The snapshot FS | The frozen view, or a host query (§3.6). Never the OS FS for an LS program. |
| **Interned names** | n/a | The interner is already process-wide (`core.rs:480`). Name-id order depends on timing in the CLI already, and CLI output is deterministic, so no output reads it (A). Keep it that way. |
| **Thread-local caches** (keyword completions, direct-import maps, lazy JSDoc, emit resolver) | Shared `SyncMap`s | Per thread; same content. Lazy JSDoc nodes get thread-local handles; node ids reach no output (`PORTING.md:1088`). |
| **Cancel disposal** | A checker that saw the cancel is disposed (fresh checker next) | Run the job like Go: **no pre-check before the provider**. The checker sees `ctx` where Go's does, so `was_canceled`, and with it the next checker's freshness, matches Go. (`run_search`'s early skip, `search_thread.rs:394`, is right for a search but not here.) |
| **Auto-import warm timing (class D1)** | The warm races requests on a goroutine; results differ run to run (notes `lspfix1-followups9-decisions-2026-10-02`, `lswarm1c-2026-10-07`) | The dispatch thread now idles while a slot runs, so the warm starts and finishes more often, which is closer to Go. Expect movement on items in the D1 class; refills may be needed. |

**Go's nondeterminism and the gate's tolerance** (`docs/perf-rearch/parity.md`):
- Completion items and `WorkspaceEdit` files are compared as sets.
- An answer is GO-BROKEN when Go's runs or accumulated variant records disagree ("Accumulated
  Go evidence"). Hono and query sessions run Go 8 times, because of the `crossproject.go`
  goroutines.
- Watch registration counters are masked, and progress is not compared (lane H9).
- Pull-diagnostic order has no JS referee.
- **tsc-rs must never vary between its own runs:** repeated tsc-rs runs give the rs-nondet
  check.

So the sequential gate checks "Go's exact choice" and needs no harness change. It does not
exercise concurrency. For that, add one check (a harness change resets Go evidence, so batch it
with another refill). Run the bench's concurrent mode 5×, record the answer of every
non-cancelled diagnostic pull with its document version, and require:
- equal answers per (uri, version) across runs;
- equal to the sequential answer at that version.

Slot 0 is FIFO, so this holds exactly for diagnostics. For queries (M4) it holds only per slot
history, so the M4 check compares against a sequential replay with the same slot assignment
(phase 1 §6.5).

## 5. Cancellation, snapshot release, freeing, idle work

- **Cancellation.** Assume the queued-cancel lane has landed.
  - With hand-off, a request leaves the dispatch queue after its short sync part, so a cancel
    finds it in `pending_client_requests` and cancels its `Context` (`Send + Sync`) on the slot
    thread. This is Go's path.
  - The lspwait1 objection (answering -32800 where Go answers a result) goes away for moved
    kinds, because they are registered as early as Go's.
  - A job waiting in a slot's queue runs when its turn comes and stops at its first `ctx` check,
    as Go's goroutine does after `diagSem`.
- **Snapshot and program release.**
  - `ls_program::release_program` (Go `programCounter.Deref`) also closes the version's slot
    senders, as `release_search_thread` does (`search_thread.rs:283`).
  - Queued jobs still run, matching Go: a running request keeps its old language service.
  - The thread exits after its queue. Its checker, synthetic nodes and line maps are freed on
    that thread.
  - Release still waits for program guards and holds on the dispatch side (`ls_program.rs:438–
    454`). A job holds the version through its `Arc` data and `enter_version`, not through an
    `Rc`.
- **Freeing.**
  - followups15c measured ~16,000 boxes of the old checker freed on the dispatch thread before
    the first answer (hono import-dependency).
  - lspperf1 deferred frees and was not taken: +RSS, and slower back-to-back.
  - With slot threads the slot-0 checker dies on its own thread, in parallel, so that cost
    leaves the dispatch thread for diagnostics at M1, and for queries at M4.
  - The risk is memory reuse: freecheck1/2 found that freeing at once lets the next check reuse
    pages. Pinning a slot's threads to one jemalloc arena keeps the reuse, because the dying
    thread frees into the arena the next version's thread allocates from.
  - `drop_after_pause` stays for dispatch-owned state (programs, query checkers until M4).
- **Idle work (auto-import warm).**
  - `run_idle_work` (`lsp/server.rs:2278`) starts idle jobs when no message waits. While a slot
    job runs, the dispatch thread can now run the warm.
  - The warm builds its registry with checkers of its own, not slot 0, so the two do not
    conflict.
  - A slot-job continuation wakes the loop, and the warm yields to it like any message.
  - It still contends for CPU with the slot thread on another core. The profile's 50 ms quiet
    rule (`IDLE_QUIET_PERIOD`) is a separate fix (profile item 5).
  - Moving the warm itself to a worker needs M3's `Send` registry.

## 6. Plan

Every merge replaces the inline path for its kinds in the same change: no env var, no fallback
switch (brief, "Replace, don't switch"). Phase 1 proposed `TSC_RS_LSP_THREADS`; this design
drops it. Each step runs the full parity gate, icount on the editor cells it touches, and
`quality.sh report` and `gating`.

### M0: measurements before code (spike branch, never merged; 1–2 lane-days)

- **`WorkerSeed::take` + spawn per program version**, on a 10-minute T3 Code pulls=all session
  and on hono and query.
  - Time on the dispatch thread, bytes copied (synthetic slots, datas, node ids, lineage ids,
    lazy JSDoc).
  - **Kill or redirect:** p50 > 2 ms or p95 > 5 ms means M1 uses long-lived slot threads with a
    delta seed (M1b below), not per-version threads.
- **FS probes per edit** of a slot-0 check on a worker: count `file_exists`, package.json and
  ancestor-directory calls that miss `HostFsCache`. They size the frozen view against the host
  query fallback.
- **Symbol-id traffic per job:** the number of lineage cells set per job, and how many global
  ids the dispatch thread gives between two diagnostic pulls. This sizes the delta sync.
- Use the diagnostics-module convention (`quality.md` §9) so the spike's counters are gated.

### M1: diagnostics on slot-0 threads (10–15 lane-days; 1.0–1.6k lines incl. tests, E)

- **Scope:**
  - the slot-thread module (generalized from `search_thread.rs`);
  - hand-off and continuation in `register_language_service_document_request_handler` for the
    diagnostics kind only;
  - slot 0 made remote in `checkerpool.rs`, with a parked wait instead of `unreachable!` for
    `diag_sem`;
  - the diagnostics functions (`ls/diagnostics.rs`, `ls_program` semantic, suggestion and
    declaration diagnostics) over a version;
  - `to_lsp_diagnostics` with thread-side `Converters`;
  - the flake-logging emit on the slot thread: the emit uses the program's checkers, so it
    cannot stay inline once slot 0 lives on a thread (open question 3);
  - id delta sync, the process-wide checker id, and the frozen FS view (or host queries, after
    M0);
  - `PORTING.md` "Threads" and the `server.rs` module doc.
- **Files (about 12):**
  - `lsp/server.rs`, `project/checkerpool.rs`, `program/ls_program.rs`, `ls/diagnostics.rs`
  - `ls/search_thread.rs` (to the slot-thread module), `ls/program_view.rs`
  - `ast/utilities_p1.rs`, `program.rs`, `program/go_frontend.rs`, `modulespecifiers/host.rs`
  - `project/snapshotfs.rs` or a new FS-view module, `program/host_queries.rs`
  - `crates/ts_goport/PORTING.md`
- **Sizing:** lspdiag1 estimated 845–1,270 lines for the diagnostics worker plus the publish
  barrier; I add ~200–300 for the id sync (E).
- **Measure:**
  - `lspdiff bench` pulls=all and pulls=no-diagnostics on T3 Code: the profile's files and
    harness, 5 runs, the brief's load rule, stable builds of both sides.
  - Latency p50/p95 per kind. Expected: tokens 74 → ~35 ms, hints 75 → ~36, other-file
    diagnostics 72 → ~33, edited-file diagnostics ≈ today ± seed cost, hover and completion
    unchanged (all E).
  - On `ChatView.tsx`: whether the queue drains with the cancel lane in place.
  - Dispatch-thread CPU per edit, RSS high-water mark on the editor benches (query core, hono,
    T3 Code), and the concurrency check of §4.
- **Kill criteria:**
  - tokens/hints p50 gain < 20 ms in pulls=all;
  - PARITY FAIL that survives three rounds (AGENTS "Rounds");
  - any rs-nondet in 20 repeated LSP-tier runs;
  - RSS high-water mark > +10% on any editor bench after arena pinning;
  - or the edited-file diagnostic p50 slower by > 3 ms.

### M1b: only if M0 redirects (5–8 lane-days)

- One long-lived thread per slot index for the process, not per version.
- At each job it takes a **delta seed**: the synthetic nodes, lazy JSDoc and ids that the
  dispatch thread made since its last job.
- It switches versions with `enter_version`, and frees a released version's state as the
  dispatch thread does (lsmem3).
- This keeps the jemalloc arena warm without pinning, but adds a delta-seed mechanism that does
  not exist today.

### M2: query providers generic over `ProgramView`, still on the dispatch thread (15–25 lane-days; 3.5–6k lines, E from phase 1 stages 2–3)

- **Behavior-neutral refactors**, each its own merge: semantic tokens and inlay hints (one
  merge), then hover, the definition family, signature help, document highlights, code lens,
  prepare rename and linked editing.
- `get_type_checker_for_file` becomes a `ProgramView` method.
- Generic code can change inlining, so watch icount (brief: fix any cost over +0.3%).
- **No latency gain** until M4.
- **Kill:** if, after M1, the remaining queue wait behind dispatch-resident queries (hover,
  completion) is < 10 ms p50 for tokens and hints, stop here. The profile's single-thread
  stopgap (serve cheap pulls first when several are queued) then covers the rest. Its answers
  are ones Go can give, because in Go a fast goroutine can release slot 1 before a slow one
  takes it.

### M3: completion and code actions (30–50 lane-days; 4–7k lines, E from phase 1 stage 4)

- A frozen `Send` auto-import view per snapshot.
- The module-specifier host and resolver behind the FS view.
- `ERR_NEEDS_AUTO_IMPORTS` as a round trip to the dispatch thread (Go calls the session from
  the goroutine).
- This is also what lets the warm move off the dispatch thread.
- The profile's completion gap (78 vs 45 ms own work, 22 ms of it in the fix sort) is a
  separate single-thread fix and should land first.

### M4: flip query slots 1–3 to threads (8–12 lane-days; 1–1.5k lines, E)

- Go's query rules at hand-off: affinity, file, lowest idle, new.
- A parked wait on a full `query_sem`, dispose on cancel, 30 s idle drop.
- Requests that hold two checkers at once (nil-file `GetTypeChecker` inside references, rename
  and call hierarchy) stay on the dispatch thread.
- **Measure:** tokens and hints expected ~5–12 ms against Go's 17 (the profile, E); hover and
  completion unchanged.
- **Gate:** the M4 concurrency check against a sequential replay with the same slot assignment.

## 7. Risks and open questions, ranked

1. **Seed cost per version (G).**
   - Every keystroke makes a version, and per-version slot threads copy the dispatch thread's
     synthetic arena and id tables (`ast/synthetic.rs:1627`, `ast/utilities_p1.rs:417`). Neither
     has been measured on a long session.
   - If it is milliseconds, it lands on the dispatch thread on every edit.
   - Handled by M0 and M1b.
2. **FS answers off the dispatch thread.** The bug class that dropped lspdiag1: an unsaved
   buffer changes a diagnostic's import-extension suggestion or a declaration diagnostic's
   module specifier. The frozen view must be built from the same snapshot as the program.
   - Negative control: a session with an overlay-only file whose existence decides a TS2307 or
     TS2834 suggestion. The `unsaved-ts-pattern` session covers part of it.
3. **Id drift between threads.** Without the delta sync, diagnostics with late-bound names or
   near truncation limits can differ from Go and from today. The CLI idcarry1 cases show these
   are real (`PORTING.md:1130–1140`). Add an LSP test with Go's `b4only` shape.
4. **Schedule.** The query side needs M2+M3+M4, 10k+ lines (E), before tokens and hints reach
   Go's numbers. M1 alone halves their wait. Decide M2 on M1's data, not now.
5. **Memory.** Up to 4 checker threads per live version, plus the seed copies. RSS HWM on hono
   paced typing was sensitive before (lspperf1: +35 MiB was enough to reject). Arena pinning is
   the mitigation (A, from freecheck1/2 and heapafter1).
6. **A synthetic `Node` crossing threads.** `Node` is `Send` at the type level, so a mistake
   names a wrong node silently. Add a thread tag check in a gated `_verify` module before M2
   (phase 1 risk 2).
7. **Ordering races under load:** publishDiagnostics against answers, and the continuation
   against the next request. Go has the same races, and the sequential gate is unaffected (G
   that no watch scenario depends on it; the watch checkpoints wait for quiet).
8. **Warm race shift (class D1).** More idle dispatch time changes when the warm finishes, so
   expect Go-variant refills on auto-import items.
9. **Deadlock.** A slot thread asks a host query while the dispatch thread waits on the slot (at
   shutdown or in the API wait path). Rule: the dispatch thread waits on slot threads only
   through a serving wait (`host_queries::recv_serving`).
10. **Code shape.** Providers generic over `ProgramView` drift from Go's shape, which makes pin
    bumps less mechanical. Keep the trait thin and name its methods after Go's `Program`
    methods (phase 1 risk 7).

**Open questions:**
- (1) Does the "end of a run" phase tracking (`PORTING.md:1866–1890`) need a new phase for slot
  jobs? Go's `Run` returns without waiting for goroutines, so slot threads may let the port
  match that better.
- (2) Telemetry and flake logging read `Server` cells: confirm they all fit in the
  continuation.
- (3) Does `ls_program::emit` with the diagnostics lifetime take slot 0 in the port, as Go's
  `Program.Emit` does through `GetTypeCheckerForFile`? This decides where flake-logging emit
  must run.
- (4) The per-version `HostFsCache`: is it shared between a version and its reuse clone, or
  cold after each edit? This sizes M0's probe counts.
- (5) Is the profile's other-file diagnostic pull in another program (its own slot 0, fully
  parallel) or in the edited file's program (queued behind it on slot 0, as Go's `diagSem`
  does)? The M1 estimate for other-file pulls assumes the former (G).
