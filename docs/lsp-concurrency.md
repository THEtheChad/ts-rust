# LSP concurrency, phase 1: harness, benchmark, design

Goal: make the tsc-rs language server handle requests concurrently the way
Go's does, without changing any answer. This phase builds the measuring tools
and checks the design. It adds no server code.

Pin: microsoft/TypeScript 3e9236361fa9. Go paths are relative to
`tsc/internal` of that checkout. Rust paths are relative to
`crates/ts_goport/src`.

## Summary

- **Go oracle language server** at the pin:
  `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9` (`--lsp --stdio`).
- **Differential harness** (`scripts/lspdiff/`, Node, no dependencies):
  1,355 answers over 3 projects and 14 request kinds. **All 1,346 answers on
  which Go agrees with itself are byte-identical in tsc-rs.** The other 9
  are auto-import completions where two Go runs pick different module
  specifiers.
- **Load benchmark, on an idle machine (section 7, which supersedes the
  phase 1 numbers in section 3).**
  - On a mid-size Next.js app (umami), tsc-rs keeps up at 250 and 500 ms
    per key: completion and hover p95 are 1.3 to 1.6 times Go's.
  - At 120 ms per key (a fast typist), it falls behind. Completion p95 is
    3 times Go's (1.0 s against 332 ms) and hover p95 16 times (1.3 s
    against 83 ms). Every request queues behind the diagnostics pull sent
    on each key.
  - Without the diagnostics pulls, tsc-rs at 120 ms/key is back within 1.4
    times Go. Stage 1 targets exactly this.
  - Phase 1's numbers (section 3: 4 to 14 times Go at 250–500 ms/key, and
    89 against 1,903 cancelled pulls) were taken at load 23 to 83 and are
    mostly load.
- **Design verdict.** The target design holds, with four amendments (6.1):
  - choose the checker slot on the dispatch thread;
  - give slot threads a per-version `Send` view instead of a shared
    snapshot;
  - make threads per (program version, slot);
  - share the id counters across threads.

  A `Send`/`Sync` language-service layer is not worth it.
- **Staged plan** (6.7): 1 (diagnostics on slot-0 threads, 1.2–1.8k lines,
  2–3 weeks) is the next step. 2 (semantic tokens, inlay hints), 3 (other
  single-file queries) and 4 (completion, code actions; the largest) wait
  for measurements after stage 1. 1a (cancel queued requests) was tried and
  dropped; S (the per-edit update) was profiled (section 7).

## 1. Tools

### Go language server at the pin

`cmd/tsc/main.go:23` sends `--lsp` to `runLSP` (`cmd/tsc/lsp.go:21`), which
only supports `--stdio`. It is the same binary as the CLI oracle. Built
without touching the pinned checkout:

```sh
cd ~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc
flock /tmp/ts-rust-k12-heavy.lock env GOWORK=off CGO_ENABLED=0 \
  go build -trimpath -o ~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 ./cmd/tsc
~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 --lsp --stdio
```

tsc-rs starts the same way: `tsgo --lsp --stdio` (`bin/tsgo.rs:964`).

### Differential harness: `scripts/lspdiff/`

Node, no dependencies. Six files:

| File | Role |
|---|---|
| `client.mjs` | JSON-RPC over stdio with Content-Length framing; the `initialize` params. Inlay hints and auto-import completions are turned on through `initializationOptions.userPreferences`, so neither server asks `workspace/configuration`. |
| `project.mjs` | Picks files (spread over the sorted file list) and positions (identifiers and member accesses, outside comments and strings) the same way for every server. |
| `session.mjs` | The scripted session, every request awaited before the next. |
| `bench.mjs` | The load benchmark (section 3). |
| `lspdiff.mjs` | The command line. |
| `probe-edit.mjs` | Splits the cost of one edit into the snapshot update, a repeated request, and the edited file's diagnostics (section 3). |

```sh
node scripts/lspdiff/lspdiff.mjs diff  --project /tmp/lspdiff/projects/umami --files 6 --positions 4 \
  --rs /path/to/tsc-rs/target/release/tsgo
node scripts/lspdiff/lspdiff.mjs bench --project /tmp/lspdiff/projects/umami --files 6 --runs 3 \
  --rs /path/to/tsc-rs/target/release/tsgo [--pulls all|no-diagnostics|none] [--only go|rs]
```

`--go` defaults to `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9`; `--rs`
defaults to `$TSC_RS_BIN`, then `target/release/tsgo`. Output goes to
`/tmp/lspdiff/out/<project>-<mode>/` (`go.jsonl`, `go2.jsonl`, `rs.jsonl`,
`mismatches.json`, `go-unstable.json`, `summary.json`). `--reuse` diffs the
saved answers again without running the servers. The servers never run at
the same time.

The session, per open file: `textDocument/diagnostic`,
`semanticTokens/full`, `inlayHint` (whole file), `documentSymbol`,
`foldingRange`; at each picked identifier `hover`, `definition`,
`typeDefinition`, `references`, `documentHighlight`, `signatureHelp`,
`codeAction` (empty context), and `prepareRename` + `rename` at the first;
`completion` after each picked member access (trigger `.`); `codeAction`
with `only: ["quickfix"]` on the first three errors. Then it types
`console.log(Math.max(1, 2).toFixed(1));` at the end of the first file one
character per `didChange`, with a `completion` after each character, pulls
diagnostics, hovers, deletes the text and pulls diagnostics again.

Normalization, only for what has to differ:
- the project root in paths becomes `<root>` (the same directory is used for
  both servers, so this only matters when comparing saved runs);
- completion items are compared as a set (sorted by their JSON). Go itself
  does not keep their order stable: on the one-file `heavy` project, two Go
  runs gave a different item order in 25 of 43 completions. Editors sort by
  `sortText`;
- the files of a `WorkspaceEdit` (`changes`) are sorted. In Go `changes` is
  a `map` (`ls/rename.go:138-146`), and it is written in map iteration order,
  which changes from run to run.

The harness runs Go twice. An answer on which the two Go runs disagree,
after normalization, is counted as **Go-unstable**, not as a mismatch.

## 2. Differential results

tsc-rs: release build of `main` at 91f3d021c
(`/home/agent/personal/ts-rust-litmus/target/release/tsgo`). Go: the binary
above. Projects, copied or cloned under `/tmp/lspdiff/projects/`:

| Project | What | Files opened | Answers | Equal | Differ | Go-unstable |
|---|---|---|---|---|---|---|
| `heavy` | copy of `/tmp/dcc/heavy` (the old tsbench-heavy is gone): one file importing effect, zod, drizzle, hono, react-query, `skipLibCheck: false` | 1 | 83 | 83 | 0 | 0 |
| `ts-pattern` | gvergnaud/ts-pattern f11be73, type-heavy library; the picked test files fall into the inferred project | 8 | 429 | 429 | 0 | 0 |
| `umami` | umami-software/umami 559e3c6, Next.js app, 1,375 TS files, `pnpm install --frozen-lockfile --ignore-scripts` | 6 | 269 | 266 | 0 | 3 |
| `umami` (larger session: 10 other files, 6 positions each) | same | 10 | 574 | 568 | 0 | 6 |
| **Total** | | | **1,355** | **1,346** | **0** | **9** |

**Every answer on which Go agrees with itself is byte-identical in tsc-rs**
(after the normalization above). That covers all 14 request kinds the
session sends, including the typing sequence.

All 9 Go-unstable answers are global completions on umami. When a component
is exported both from its own module and from the barrel `src/index.ts`, Go
picks either module specifier for the auto-import item (for example
`TeamContext` from `@/index` or from `@/app/(main)/teams/TeamProvider`). The
pick changes from one Go run to the next. tsc-rs's answer equals neither Go
run. All but 2 to 5 of its items (out of 7,197 to 28,955) occur in one of
the two Go runs. The others use the other specifier of the pair, which
may just be a pick these two Go runs didn't happen to make. More Go runs
would settle whether tsc-rs's pick is always a possible Go outcome.

Before the normalization, Go-vs-Go also differed on completion item order
(25 of 43 on `heavy`) and on rename's file order (1 on umami).

## 3. Concurrent-load benchmark

`lspdiff.mjs bench` drives one server at a time. It opens N files, pulls
their diagnostics once and hovers once (warm-up, not measured), then types
the same line as the diff session, character by character, at the end of
the first file, `--interval` ms per key (default 120, a fast typist). The
line is typed four times per run, and deleted between rounds after
everything has answered. It sends what VS Code sends:

- every key: `didChange`, plus a diagnostics pull for the edited file;
- a completion at each trigger point: after `.`, and at the first letter of a
  word. VS Code filters the list on its own while the rest of the word is
  typed. That gives 5 completions per round;
- every 500 ms of typing (every key at 500 ms/key): diagnostics pulls for
  the other open files, plus semantic tokens and inlay hints for the edited
  file;
- every 8 keys: a hover in another open file.

A new request of a kind cancels the previous one of that kind if it is still
in flight, as an editor does.

**Concurrent** mode sends on the timer and never waits. **One-at-a-time**
mode sends the same requests in the same order and waits for each answer
before sending the next, so it measures each request's own cost with no
queueing. `--pulls no-diagnostics` drops every diagnostics pull, which gives
an upper bound for stage 1 (diagnostics off the dispatch thread). `--pulls
none` also drops semantic tokens and inlay hints, which leaves completions
and hovers alone.

**Caveat: the machine was shared.** Three other agents were building on the
same 16-core box, with a load average of 23 to 83 during the runs, so the
absolute numbers are noisy and inflated. Both servers ran under the same
conditions, one after the other: three runs each, six at 500 ms/key.

**Superseded by section 7.** On an idle machine the gap is much smaller:
tsc-rs keeps up at 250 and 500 ms/key and falls behind only at 120 ms/key.
Most of what the tables below show at 250 and 500 ms/key is load, not the
servers. They are kept as the record of phase 1.

### Results

Raw samples are in `/tmp/lspdiff/out*/` (`samples.json`). In the tables
below, p50 and p95 are pooled over the runs. "Per run" is the range of the
per-run p95, which shows how noisy the box was. Latencies count answered
requests only; cancelled requests are counted in the last column.

**One at a time.** Each request's own cost, with no queueing:

| Request | umami Go p50 / p95 | umami tsc-rs p50 / p95 | ts-pattern Go p50 / p95 | ts-pattern tsc-rs p50 / p95 |
|---|---|---|---|---|
| completion | 288 ms / 611 ms | 354 ms / 1.1 s | 5 ms / 27 ms | 6 ms / 18 ms |
| hover | 228 ms / 366 ms | 257 ms / 640 ms | 1 ms / 3 ms | 1 ms / 3 ms |
| diagnostic | 19 ms / 447 ms | 31 ms / 539 ms | 1 ms / 31 ms | 1 ms / 27 ms |
| semanticTokens | 35 ms / 69 ms | 30 ms / 70 ms | 23 ms / 34 ms | 19 ms / 27 ms |
| inlayHint | 48 ms / 96 ms | 20 ms / 55 ms | 2 ms / 5 ms | 2 ms / 3 ms |

**Small project under load** (ts-pattern, 120 ms/key, all pulls, 3 runs).
Serial dispatch costs nothing measurable here, because every request takes
milliseconds:

| ts-pattern, concurrent | Go p50 / p95 | tsc-rs p50 / p95 |
|---|---|---|
| completion | 11 ms / 38 ms | 8 ms / 28 ms |
| hover | 3 ms / 14 ms | 3 ms / 11 ms |

**Mid-size app under load: completion** (umami, concurrent; 3 runs per
row, 6 for 500 ms):

| completion (concurrent) | Go p50 | Go p95 | tsc-rs p50 | tsc-rs p95 | tsc-rs p95 per run | answered (cancelled) Go / tsc-rs |
|---|---|---|---|---|---|---|
| 120 ms/key, all pulls | 4.7 s | 7.0 s | 7.3 s | 18.7 s | 14.5 s–27.0 s | 48 (12) / 50 (10) |
| 250 ms/key, all pulls | 802 ms | 1.6 s | 3.4 s | 10.7 s | 7.8 s–10.8 s | 49 (11) / 59 (1) |
| 500 ms/key, all pulls | 347 ms | 1.8 s | 1.2 s | 7.4 s | 2.5 s–65.0 s | 120 (0) / 115 (5) |
| 120 ms/key, no diagnostics | 399 ms | 2.2 s | 1.0 s | 3.8 s | 2.1 s–4.6 s | 35 (25) / 44 (16) |
| 250 ms/key, no diagnostics | 370 ms | 1.6 s | 359 ms | 7.1 s | 788 ms–10.9 s | 55 (5) / 58 (2) |
| 500 ms/key, no diagnostics | 378 ms | 2.0 s | 626 ms | 3.2 s | 1.4 s–7.0 s | 120 (0) / 116 (4) |
| 120 ms/key, no pulls | 264 ms | 527 ms | 450 ms | 1.2 s | 764 ms–1.7 s | 48 (12) / 30 (30) |

**Mid-size app under load: hover:**

| hover (concurrent) | Go p50 | Go p95 | tsc-rs p50 | tsc-rs p95 | tsc-rs p95 per run |
|---|---|---|---|---|---|
| 120 ms/key, all pulls | 5.4 s | 9.5 s | 10.8 s | 25.1 s | 19.3 s–32.5 s |
| 250 ms/key, all pulls | 610 ms | 1.9 s | 5.4 s | 13.7 s | 9.6 s–14.9 s |
| 500 ms/key, all pulls | 254 ms | 597 ms | 1.8 s | 8.5 s | 3.2 s–65.6 s |
| 120 ms/key, no diagnostics | 293 ms | 639 ms | 1.1 s | 4.4 s | 2.1 s–5.5 s |
| 250 ms/key, no diagnostics | 251 ms | 654 ms | 259 ms | 8.0 s | 557 ms–12.2 s |
| 500 ms/key, no diagnostics | 263 ms | 797 ms | 433 ms | 3.9 s | 1.0 s–7.8 s |
| 120 ms/key, no pulls | 235 ms | 454 ms | 402 ms | 697 ms | 622 ms–1.7 s |

**Cancellation.** At 500 ms/key with all pulls, Go answered 1,903 of 5,904
diagnostics pulls with RequestCancelled; tsc-rs answered 89 of 5,904 that
way. The client cancels every superseded pull still in flight, and in
tsc-rs more pulls are in flight, not fewer. In Go the cancel reaches a
request that is already running and stops it. In tsc-rs
the cancel nearly always arrives while the request is still queued, so it is
dropped (`lsp/server.rs:93-96`) and the stale pull runs to the end.

**Where an edit's time goes** (`scripts/lspdiff/probe-edit.mjs`, umami, three
probes of 15 edits each; medians):

| After a one-character edit | Go | tsc-rs |
|---|---|---|
| first request (pays the snapshot/program update) | 214–237 ms | 376–411 ms |
| the same request again | 0.6–0.7 ms | 0.9–7.4 ms |
| the edited file's diagnostics | 193–232 ms | 65–74 ms |

On ts-pattern the update costs 2 ms in both servers. In Go the update is the
synchronous half of the first request after the edit
(`session.GetLanguageService` flushes the pending changes), so it runs on
the dispatch loop: **Go serializes it too**. The diagnostics check after it
runs on a goroutine. In tsc-rs both run on the dispatch thread.

### What the numbers say

1. **Answering one request at a time, tsc-rs is on par with Go.** Its
   semantic tokens and inlay hints are faster; its completion and hover p95
   are higher on umami. On a small project, serial dispatch costs nothing.
2. **On a mid-size app at typing speed, serial dispatch is expensive.** At
   250–500 ms/key, tsc-rs's completion and hover p95 is 4 to 14 times Go's.
   Its p50 is 3.5 to 9 times Go's. Some tsc-rs runs snowball: once the queue
   grows faster than it drains, latencies reach a minute (the 65 s run).
   At 120 ms/key both servers saturate, and tsc-rs is 1.5 to 2.7 times
   worse.
3. **Diagnostics are the largest part of it.** Without the diagnostics pulls,
   tsc-rs's completion p50 at 250 ms/key drops from 3.4 s to 359 ms, equal
   to Go's. At 120 ms/key it drops from 7.3 s to 1.0 s. The tail stays above
   Go's (p95 3.2–7.1 s against 1.6–2.2 s), because semantic tokens, inlay
   hints, completions and the snapshot update still queue on one thread. This
   is the upper bound for stage 1.
4. **The snapshot update per edit is a ceiling that concurrency won't lift.**
   Go serializes it as well, and tsc-rs's is 1.6 to 1.9 times Go's on
   umami. With every request perfectly concurrent, tsc-rs's dispatch thread
   would still be busy about 390 ms per edit against Go's 225 ms. At 250
   ms/key that is already saturated. So "concurrent like Go" also needs this
   update made cheaper (see 6.7, track S).
5. **Stale work isn't cancelled**: 89 cancelled diagnostics pulls in tsc-rs
   against 1,903 in Go, out of the same 5,904. tsc-rs runs the rest of
   them to the end.

## 4. How Go does it

**Dispatch** (`lsp/server.go`). `readLoop` (920) handles `$/cancelRequest`
itself (978-981) and queues everything else. `dispatchLoop` (1005) takes one
message at a time, makes a cancellable context carrying the request id
(1016-1024), runs the handler's synchronous part inline (1050) and, if it
returns `doAsyncWork`, runs that on a bare goroutine (1053-1059): no pool, no
limit. `writeLoop` (1066) writes in completion order, so answers can be
reordered.

Handler kinds (`handlerMap`, 1263-1333):

| Registration | Sync part (dispatch loop) | Async part (goroutine) | Methods |
|---|---|---|---|
| `registerNotificationHandler` (1337) | all | - | didOpen, didChange, didClose, didSave, watched files, configuration |
| `registerRequestHandler` (1354) | all, including the answer | - | rename, callHierarchy in/out, workspace/symbol, completionItem/resolve, codeLens/resolve, willRenameFiles, shutdown |
| `registerLanguageServiceDocumentRequestHandler` (1378) | `session.GetLanguageService(ctx, uri)` (1384): flush pending changes, maybe a new snapshot | provider, `EnqueuePublishGlobalDiagnostics`, `ctx.Err()` check, answer (1388-1400) | diagnostic, hover, definition, typeDefinition, signatureHelp, formatting, documentSymbol, documentHighlight, selectionRange, inlayHint, codeLens, foldingRange, prepareRename, linkedEditingRange, semanticTokens full/range, ... |
| `registerLanguageServiceWithAutoImportsRequestHandler` (1405) | `WithLanguageServiceAndSnapshot` (takes a snapshot ref) | provider; on `ErrNeedsAutoImports`, `GetLanguageServiceWithAutoImports` over the request's own snapshot, once | completion, codeAction |
| `registerMultiProjectReferenceRequestHandler` (1440) | default language service and project list | provider; the orchestrator calls back into the session | references, implementation |

Snapshot updates are serialized (`snapshotUpdateMu`,
`project/session.go:138`, taken at 322). A snapshot is immutable once made
(`project/snapshot.go:31-59`). A request that is running when a `didChange`
arrives keeps its old language service and answers from the old snapshot;
nothing is invalidated, only `$/cancelRequest` stops it.

**Checker pool** (`project/checkerpool.go`). One pool per `Program`, a new
pool for every program (`compiler/program.go:452-468`). `MaxCheckers` 4
(85-89): slot 0 is the diagnostics checker, slots 1-3 query checkers. One
mutex plus three channel semaphores (`diagSem` 1, `querySem` 3,
`persistentSem` 1). Selection for a query (`getQueryChecker`, 226-265):
request affinity, then the file's associated checker if idle, then the
lowest idle existing checker, else a new one in the first empty slot, and
only on that last path the file association is (over)written (261-263).
Diagnostics always take slot 0 (`getDiagnosticsChecker`, 194-221, via the
`CheckerLifetimeDiagnostics` context set in `handleDocumentDiagnostic`,
`lsp/server.go:1891`). A request takes and releases checkers several times
(per projection or per phase), never for the whole request. A checker that
saw a cancellation is disposed on release (323-326). Idle checkers are
disposed after 30 s (373-433).

**What makes answers identical.** Nothing, under concurrency: which checker a
query gets depends on which slots are busy at that moment, and a checker's
answers depend on what it has checked before (type ids order union members,
`checker/checker.go:25575-25579`, 27249). Under a strictly sequential
workload the choice is fixed: diagnostics always on slot 0, every query on
slot 1 (the lowest idle slot; slots 2-3 are never made). The exception is a
background task, such as the auto-import warm, holding a query checker while
a request runs. That is the
behaviour the oracle and this harness see, and the one a port must
reproduce exactly. Under load the target is "an outcome Go could have
produced".

**Fan-out inside a request** is limited to cross-project search
(`ls/crossproject.go:79, 234`); single-file diagnostics are not parallel
(`compiler/program.go:723-735`).

## 5. How tsc-rs does it today

- One dispatch thread owns the session, snapshots, projects, programs, pools,
  checkers and language services (`PORTING.md` "Threads"). Reader and writer
  threads exist (`lsp/server.rs` `read_loop` 1901, `write_loop` 2339); the
  writer side (`ServerShared`, an `Arc`) can be called from any thread.
- The handler table keeps Go's split: a handler returns
  `Option<AsyncWork>` (`lsp/server.rs:2519`,
  `Box<dyn FnOnce() -> Result<(), GoError>>`, not `Send`), and
  `dispatch_request` runs it inline (`lsp/server.rs:2242-2256`, "Go runs the
  async work on a goroutine; it runs here, on the dispatch thread, before the
  next message"). **That call is the seam** where Go jumps to a goroutine.
- Cancellation is Go's (`lsp/server.rs:93-96`): `$/cancelRequest` is handled
  on the reader thread and cancels the running request's context; a cancel
  for a queued request is dropped. Because everything after a slow request
  is queued, in tsc-rs most stale requests are queued, not running, so their
  cancels are dropped and they run to the end. Go has almost nothing queued.
- The pool (`project/checkerpool.rs`) has Go's selection rules, but its
  semaphores are counters whose full case is `unreachable!` (1-11, 40-75),
  and checkers are `Rc<RefCell<Checker>>` reached through a thread-local
  registry (`program/ls_program.rs:134-146`, `PROGRAM_CHECKERS`,
  `NEXT_CHECKER_ID`).
- Work groups are always serial (`frontend/core_workgroup.rs:30`).
- The checker reads its program through `prog()` (`core.rs:4843-4933`), a
  per-thread current `&'static GoProgram` whose data (`VersionTables`,
  `program.rs:957`) is `Arc`/`OnceLock`/`Mutex` only. The CLI already runs
  each checker on its own thread from a `WorkerSeed`
  (`program.rs:4153-4207`, `start_checkers` 4455). So does the LSP's
  cross-project search (`ls/search_thread.rs`): one seeded thread per
  program version, a checker made on that thread, plain data in and out,
  file system reads proxied to the dispatch thread (`HostQuery`), results
  committed in a fixed order.

## 6. Design spike

### 6.1 Verdict

**The target design holds, with four amendments.** Checkers stay on their
threads, session and snapshot updates stay on the dispatch thread, and the
async half of a request runs on the thread of the checker slot that Go's
rules give it. The checker and the CLI hot path need no change. But:

1. **Choose the slot on the dispatch thread, before the async half starts,
   and pin the whole async half to it.** Go picks lazily inside the
   goroutine, sometimes several times per request. The port can apply the
   same rules in the serial loop: diagnostics take slot 0; a query takes
   request affinity, then the file's slot if idle, then the lowest idle
   slot, then a new one. "Idle" means no job queued or running on that
   thread. Under one-at-a-time traffic this gives exactly Go's choice:
   slot 0 for diagnostics and slot 1 for every query, which is what the
   harness and the oracle check. Under load it gives one of the choices Go
   could have made.
2. **There is no thread-safe snapshot to read.** Only the checker's side is
   thread-safe today: `GoProgram`, `VersionTables` and the AST store. The
   language-service layer reads `NewProgram` (`Rc`), the snapshot as
   `ls::Host` (`Rc`, with lazily filled `RefCell`/`OnceCell` caches) and
   `Converters` (`Rc`). Don't make those `Sync`. Give each slot thread a
   per-version `Send` view, as the search thread does (`SearchProgramData`,
   `ProgramView`, `WorkerHost`, `HostQuery`), and grow that view one
   provider at a time.
3. **Make threads per (program version, slot), not per slot for the
   process.** Like Go, the port makes a new pool for every program, and
   typing makes a new program per keystroke. Spawn a slot thread lazily,
   seed it after bind, and end it when its program is released
   (`ls::release_search_thread` is the model). Reusing threads across
   versions saves only the spawn, not the seed copy, and leaves file-version
   pins (`ast/file_version.rs:457`) behind.
4. **Share the id counters across threads, as Go does.** Symbol ids reach
   output (PORTING.md "Threads": late-bound names `__@k@<id>`, node-builder
   truncation, `compareSymbols`). Today every LSP checker runs on the
   dispatch thread and shares one counter, which matches Go's single global
   atomic for sequential traffic. Seeded threads count their own ids
   (`ast::id_seed`), so slot-0 diagnostics could see different ids than Go.
   Slot threads need a process-wide atomic for global symbol ids and the
   checker id (`NEXT_CHECKER_ID`, `program/ls_program.rs:138`, is Go's atomic
   `nextCheckerID`). That is the CLI's problem in reverse: the CLI emulates
   a shared counter with `new_pool_checker` (`program.rs:4347`).

**The alternative, a `Send`/`Sync` language-service layer like Go, is not
worth it.** It needs one of two things:
- A `Send` checker: about 480 `Rc` and 320 `RefCell` in `checker/`, and the
  thread-local arenas (synthetic nodes `ast/synthetic.rs:1155`, synthetic
  flows `checker/extras.rs:12`, `CHECKER_ARENA` `checker/types.rs:392`, ids)
  made movable, with atomic refcounts on hot relater and node-builder paths.
- Or `Arc`/`Mutex` across the LS layer: `project/` alone has about 1,700
  `Rc`/`RefCell` sites, `Rc<dyn vfs::Fs>` is used in 46 files, and about
  280 PORT blocks would be reverted.

Either is weeks of work with a high risk to parity and performance, and it
buys no latency that the target design doesn't.

### 6.2 What the async half touches

Traced for the document handlers (`register_language_service_document_request_handler`,
`lsp/server.rs:3057-3100`) and completion/codeAction (`:3103-3170`):

| State | Type today | Read on a slot thread? |
|---|---|---|
| `Rc<Server>` (telemetry `Cell`, logger) | not `Send` | Only by a few handlers, e.g. `handle_document_diagnostic` (`:4032`) reads flake logging and telemetry. Read these in the sync half. |
| `ServerShared` (queues, pending requests) | `Arc`, `Send + Sync` | Yes, as is: `send_result`, `pending_client_requests`. |
| Session | `Rc`, `RefCell` | No. The async half calls it three times: `enqueue_publish_global_diagnostics` (`:3087`), the auto-import retry `get_language_service_with_auto_imports` (`:3135`), and the cross-project orchestrator. Each becomes a message back to the dispatch thread. |
| Snapshot as `ls::Host` | `Rc`; `read_files` `RefCell`, line maps `OnceCell` | Through a thread-side host: program files from the AST store, overlay text as `Arc<str>`, the rest by `HostQuery`. |
| `NewProgram` | `Rc`; `OnceCell`, `LazyValue`, `Rc<dyn Resolver>` | Through `ProgramView`, grown per provider. `go_frontend()` (`program.rs:1450`) panics off the loading thread. |
| `GoProgram`, `VersionTables`, AST store | `&'static`, `Arc`, `OnceLock`, `Mutex` | Yes. `WorkerSeed::install` and `ls_program::enter_version` make it current (`prog()`, `core.rs:4933`). |
| Checker pool | `Rc<CheckerPool>`, `Cell`/`RefCell` | Bookkeeping stays on the dispatch thread (6.3). |
| Checker | `Rc<RefCell<Checker>>` in thread-local `PROGRAM_CHECKERS` (`program/ls_program.rs:144`) | Made and kept on its slot thread. |
| `Converters`, line maps | `Rc` | Rebuilt per thread from the file text, as `WorkerHost` does. |
| Factory nodes, cached tokens, synthetic arena | thread-local | Per thread, copied at seed. Correct only if **no synthetic `Node` crosses threads**. `Node` is `Send` at the type level (`core.rs:128`), so the compiler won't catch a mistake. Only positions, URIs, plain data and parsed nodes of pinned versions may cross. |
| User preferences, client capabilities, `Context` | plain struct, `Arc`, `Arc` | Clone or send them. A cancel reaches a slot thread through `Context`. |
| lsproto results | `Send`, enforced (`AnyValue: Send`, `frontend/json_ext.rs:39`) | The slot thread can send the answer itself. |

The diagnostics path is the easy case. `ls::get_all_diagnostics`
(`ls/diagnostics.rs:11-42`) calls `ls_program::get_semantic_diagnostics` and
`get_suggestion_diagnostics`, whose per-file bodies are the CLI's
(`get_semantic_diagnostics_with_checker` and friends, `program/ls_program.rs:1537`
comment). They read the program through `prog()`, and `core::Diagnostic`
is already `Send`
(`core.rs:3912`, "diagnostics cross the checker threads"). Syntactic
diagnostics, `skip_type_checking`, and the conversion to lsproto
(`to_lsp_diagnostics`, which needs `Converters`) can stay on the dispatch
thread.

### 6.3 Checker pool and cancellation on threads

- **Pool bookkeeping stays on the dispatch thread, as it is now** (`held_by`,
  file and request associations, `last_released`, the idle timer). Only the
  checker moves. A slot is held while its thread has the request's job.
- **When every query slot is busy, Go blocks** on `querySem`. The port parks
  the request on the dispatch side and starts it when a completion frees a
  slot, picking the slot with Go's rules at that moment. The `unreachable!`
  counters (`project/checkerpool.rs:40-75`) become that wait.
- **Release** (`create_release`, `project/checkerpool.rs:498`) splits in two.
  The thread reports `was_canceled` and slot 0's global diagnostics with the
  result. The dispatch thread runs Go's bookkeeping. "Dispose" (after a
  cancel, or 30 s idle) becomes a message telling the thread to drop its
  checker. The next job then makes a fresh one, as Go does.
- **Nested acquisitions within one request** land on the same slot through
  request affinity, and on the slot thread the checker is already there.
  Requests that Go would spread over two slots at once, such as nil-file
  `GetTypeChecker(ctx)` while holding a slot (references, rename, call
  hierarchy), stay on the dispatch thread until they are analysed.
- **Cancellation already works across threads.** `$/cancelRequest` is handled
  on the reader thread, `Context` is `Arc`-based, and the checker polls it at
  every statement. A job that is cancelled before it starts answers -32800
  without running. Handing the async half off immediately also fixes a
  hidden cost of today's design. Go drops cancels for queued requests too
  (`lsp/server.go:991-999`), but in Go almost nothing is queued. In tsc-rs
  every request behind a slow one is queued, so its cancel is dropped and it
  runs to the end anyway.
- **The answer can leave from the slot thread** (`ServerShared::send_result`).
  The dispatch thread must still be told, so that it can update the pool,
  finish `remove_request`, and run `enqueue_publish_global_diagnostics`. The
  loop already has a cross-thread wake: `gostd::local::set_waker` →
  `QueuedRequest::Wake` (`lsp/server.rs:2113-2120`). Add a
  `QueuedRequest::Done(job, Box<dyn Any + Send>)` beside it, and keep the
  non-`Send` continuation in a dispatch-thread map keyed by job.

### 6.4 What must become `Send`/`Sync`

- **Stage 1 (diagnostics): no existing type.** New `Send` types: the job
  (`&'static GoProgram`, file `Node`, `Context`, flags) and the result
  (`Vec<Diagnostic>`, global diagnostics, `was_canceled`). `AsyncWork` gets
  a handed-off variant. The checker id and global symbol id counters become
  process-wide atomics.
- **Later stages:**
  - `ProgramView` grows `get_type_checker_for_file`, then the
    `NewProgram` reads each provider needs.
  - A thread-side `ls::Host`.
  - `lsproto` params are sent by value (the server wraps them in
    `Rc<RequestMessage>` today).
  - For completion and code actions, a frozen `Send` view of the auto-import
    registry. Go's registry is immutable per snapshot apart from a `SyncMap`
    specifier cache (`ls/autoimport/registry.go:327-343`); the port's is
    `Rc` over dirty maps.

### 6.5 Output identity

Go's file→checker association does not make answers identical under load.
It makes them repeatable for sequential traffic (section 4). The port keeps
that property if it:
- applies Go's selection rules at dispatch time, with Go's notion of idle
  (including checkers held by background work such as the auto-import warm);
- makes a fresh pool per program;
- disposes a checker after a cancel and after 30 s idle;
- never shares a checker between diagnostics and queries;
- shares id counters across threads (6.1, item 4).

**The harness's serial session stays the parity gate.** For traffic under
load, a weaker check is possible: an answer must equal Go's for the slot the
port chose. Run the same request sequentially against a fresh server whose
query slot history matches.

### 6.6 The single-thread PORT notes

A one-line grep for "dispatch thread", "serial", "thread-local", "not Send"
and similar in PORT comments under lsp, ls, project, frontend, program and
api gives 83 hits. The brief's figure of 69 came from a narrower pattern. A
multi-line scan of PORT blocks finds 279 that use thread-related words.
Under the target design:

- **Must change (about 15):**
  - `lsp/server.rs` module doc (3, 93-96) and 2249 ("runs here, on the
    dispatch thread").
  - `project/checkerpool.rs` 1-11, 40-75 and 261 (counters, nested
    acquisition).
  - `program/ls_program.rs` 35 and 134-146 (thread-local registry,
    `NEXT_CHECKER_ID`), and 620, 670, 1314, 1357 (the `RefCell` borrow as
    the checker lock).
  - The PORTING.md "Threads" section ("It runs every request").
  - The `ls/search_thread.rs` header, which becomes the general slot-thread
    module.
- **Stay true (the rest):**
  - Session, snapshot building, `projectcollectionbuilder`, ATA, the
    background queue, the auto-import warm, `core_workgroup`, `core_bfs` and
    the file loader. Go serializes snapshot updates too, and the port keeps
    those on the dispatch thread.
  - The "Go runs a WorkGroup, we run serially" notes in `ls_program`
    (1403/1427/1574/1619/1903) could fan out to slot threads. But in the
    LSP they run over one file, so there is nothing to gain.

### 6.7 Staged plan

Sizes are lines of Rust including tests. Durations assume one engineer
working against this harness plus the maintainer's oracle.

| Stage | What | Size | Time |
|---|---|---|---|
| 0 | Harness and benchmark (this phase) | done | - |
| 1a | Answer a request cancelled while queued with -32800 without running it | 100-200 | days |
| 1 | Pull diagnostics on slot-0 threads | 1,200-1,800 | 2-3 weeks |
| 2 | Semantic tokens and inlay hints on query-slot threads | 1,500-2,500 | 2-3 weeks |
| 3 | hover, definition, typeDefinition, signatureHelp, documentHighlight; references and implementation for the default project | 2,000-3,500 | 3-4 weeks |
| 4 | completion and codeAction | 4,000-7,000 | 6-10 weeks |
| 5 | Remove the inline fallbacks (except wasm); update PORTING.md | small | - |
| S | Parallel track: make the per-edit snapshot update cheaper (section 3, point 4) | unknown until profiled | - |

**Order (revised after section 7).** Stage 1 is the only stage the idle-machine
numbers support. At 120 ms/key it removes most of the gap; at 250 and 500
ms/key nothing queues. Decide on stages 2 to 4 from measurements taken after
stage 1. 1a was built and measured (section 7.4) and is dropped, because it is
not Go-shaped and only helps once requests already queue. S was profiled
separately (section 7.3).

*Original order (phase 1):* do 1a and S first, since they are cheap or
independent and both raise the ceiling. Then 1, which is the largest single
win measured (section 3, point 3). Then 2, because semantic tokens and inlay
hints are the next pulls that queue on every edit. Stages 3 and 4 matter once
a client sends several queries at once, or completions pile up behind each
other.

**Track S.** On umami the first request after an edit costs tsc-rs 376–411
ms against Go's 214–237 ms. That work stays serial in any design. There was
no profiler on this machine (no `perf`), and tsc-rs's `--pprofDir` writes
profiles without samples (`pprof.rs`). One candidate to check first: Go
frees the old program's checkers and nodes in its garbage collector,
concurrently. tsc-rs frees a released program's checkers and synthetic
nodes on the dispatch thread "at once", unless the client has paused
(`lsp/server.rs:83-91`, `gostd::local::drop_after_pause`). Under fast typing it never pauses. The
lower diagnostics cost in tsc-rs (65–74 ms against 193–232 ms) suggests
that some checking work also moved into the update; the profile should
show which.

**Stage 1a (not Go-shaped, output-safe).** The reader thread
already sees every `$/cancelRequest`. It can also record the ids of queued
requests, so the dispatch loop answers them with `RequestCancelled` without
running them. Go would give the same answer for a request cancelled while
it runs, so every answer is still one Go could give. This only helps when
the client cancels, which VS Code does for superseded diagnostics, semantic
token and inlay hint pulls. It does nothing for the first wait behind a
running diagnostic check.

**Stage 1: the smallest step that takes diagnostics off the dispatch
thread.**
- **Dispatch.** `AsyncWork` can hand off. Add a `QueuedRequest::Done` message
  and a continuation map on the dispatch thread, and defer `remove_request`
  and `enqueue_publish_global_diagnostics` to the continuation
  (`lsp/server.rs:2184-2256`, `:3057-3100`).
- **Slot-0 thread per program version.** Generalize `ls/search_thread.rs`:
  `bind_source_files`, `WorkerSeed::take`, a thread with the Go stack size,
  idle checker drop, and an end at `release_program`.
- **`textDocument/diagnostic`.** Syntactic diagnostics, `skip_type_checking`
  and the conversion stay on the dispatch thread. Semantic and suggestion
  diagnostics for the file and its supplemental files run on the thread.
  Declaration diagnostics move too when `emitDeclarations` is on (the CLI
  already has a per-thread emit resolver, `program.rs:5511`). Until then,
  fall back to inline. Flake logging (which emits) stays inline.
- **Pool.** Slot 0 becomes remote, with a split release, the global
  diagnostics merge, and dispose on cancel (`project/checkerpool.rs:306`,
  `:498`).
- **Ids.** The checker id and global symbol id counters become process-wide
  atomics for LSP slot threads.
- **Gate and acceptance.** Put it behind an env var
  (`TSC_RS_LSP_THREADS=diagnostics`) until the harness shows 100% on the
  Go-stable answers and the maintainer's oracle agrees.
- **Effect.** Pulls never delay completions or hovers on the dispatch thread
  again, though they still share CPU. The measured upper bound on an idle
  machine (section 7): at 120 ms/key, completion p95 drops from 1.0 s to
  186 ms (Go: 135 ms), and hover p95 from 1.3 s to 107 ms (Go: 73 ms). It is
  optimistic, because without a pull on every key the program update is also
  paid less often. (Phase 1, under load: at 250 ms/key completion p50 3.4 s
  to 359 ms, section 3.)

**No proof of concept in this phase.** A minimal one needs all of stage 1's
parts at once: the handed-off `AsyncWork`, the slot-0 thread, the remote
release, and the shared id counters. Without the counters its diagnostics
are not trustworthy against the oracle. That is more than a spike. A
release build of `tsgo` on the shared, loaded machine also takes too long
per iteration. The `--pulls no-diagnostics` runs measure the same upper
bound with the existing binaries.

**Stage 2.** Choose query slots at dispatch time with Go's rules (6.3).
Build a `WorkerHost` and `Converters` on the thread. Make `semantictokens.rs`
(800 lines) and `inlay_hints.rs` (1,265) generic over `ProgramView`. They
call the program only through `get_type_checker_for_file`.

**Stage 3.** The same for the other single-checker providers. `hover.rs` is
partly generic already (19 `impl<P: ProgramView>` blocks exist, against 77
non-generic `impl LanguageService` blocks). `definition.rs`,
`signaturehelp.rs` and `documenthighlights.rs` each read the program in one
or two places. Folding ranges, document symbols and selection ranges need no
checker and can stay on the dispatch thread.

**Stage 4.** Completions (9.9k lines), code actions (4.9k) and the
auto-import code (9.2k) need:
- a frozen `Send` auto-import view;
- the resolver and module-specifier host behind `ProgramView` or
  `HostQuery`;
- the auto-import retry (`ERR_NEEDS_AUTO_IMPORTS`) as a round trip to the
  dispatch thread.

This is the largest and riskiest stage. It is also the one that matters most
for completion latency under load, because completions then stop waiting
behind each other and behind hovers.

**Stays on the dispatch thread** (Go runs these synchronously too): rename,
call hierarchy in/out, workspace/symbol, completionItem/resolve,
codeLens/resolve, willRenameFiles, all notifications and snapshot updates.
The cross-project search keeps its own threads.

### 6.8 Risks

1. **Id parity across threads** (6.1, item 4). If global symbol ids are not
   shared, diagnostic texts with late-bound names, or near a truncation
   limit, differ from Go. Atomics put a contended counter on a path the CLI
   keeps thread-local. Measure it.
2. **A synthetic `Node` crossing threads goes unnoticed.** `Node(u64)` is
   `Send`, so a handle from the dispatch thread names a different node or a
   missing slot on a slot thread. Add a debug-build check (thread tag in
   synthetic handles) before stage 2.
3. **Unsaved buffers.** `WORKER_FS` (`program/go_frontend.rs:168`) reads the
   OS file system when `host_fs_cache` misses, not the overlay. Audit every
   checker path that can miss before any request runs off-thread.
4. **Memory and seed cost.** Each slot thread copies the dispatch thread's
   synthetic arena, ids and lazy JSDoc at seed time, and keeps its own line
   maps, token caches and name tables. Typing makes a version per keystroke,
   so a large arena makes every diagnostics pull pay for the copy. Measure
   `WorkerSeed::take` on a long-lived session before stage 1 lands.
5. **Under load the result is not deterministic**, as in Go. The serial
   session stays the gate; the weaker per-slot check (6.5) still needs
   building.
6. **Lifetimes.** File-version pins and program-release accounting are per
   thread. A program release must wait for, or hand off to, its slot
   threads, as it does for search threads.
7. **Code shape.** Making providers generic over `ProgramView` moves LS code
   away from Go's shape, which makes upstream bumps less mechanical. The
   search thread already set this pattern; keep the generic surface as thin
   as possible (trait methods named after the Go `Program` methods).
8. **wasm** has one thread (`program.rs:4532`), so the inline path stays
   there.

## 7. Re-baseline on an idle machine (2026-11-03)

Phase 1 ran on a shared box at load 23 to 83 (section 3). This section
repeats the benchmark on an idle machine and measures the per-edit update.
Its numbers supersede section 3's.

### 7.1 Method

- **Machine:** AMD Ryzen 9 9955HX (16 cores, 32 threads), 47 GB, otherwise
  idle.
- **Builds:** tsc-rs `main` @ `91f3d021c` built with `--profile goport`, and
  the Go oracle at the pin.
- **Benchmark:** `lspdiff.mjs bench` on umami @ `559e3c6`, 6 files,
  concurrent mode. Each run is a separate call with `--only go|rs --runs 1`,
  so the servers alternate run by run.
- **Load gate:** a run starts only when the 1-minute load average is under
  1.5, and the load before and after each run is recorded.
- **Exclusion:** one tsc-rs run at 500 ms/key ended at load 4.9 after an
  outside CPU burst. Its completion p95 was 258 ms against 189–196 ms in the
  other runs, so it was replaced by a fourth run and left out.
- **Per-edit probe:** `probe-edit.mjs` from the stage S branch
  (`perf/lsp-edit-cost`), with `--edits 20 --cpu --logs`. There are 3 probes
  per server for each edited file, with the same load gate. "Program update"
  is each server's own `Program update for … completed in …` log line, and
  both servers log the same span.

### 7.2 Load benchmark

p50 / p95 pooled over 3 runs per row. Latencies count answered requests only.

| umami, concurrent | completion | hover | diagnostics pull |
|---|---|---|---|
| 500 ms/key, tsc-rs | 104 / 196 ms | 97 / 107 ms | 118 / 131 ms |
| 500 ms/key, Go | 78 / 150 ms | 56 / 74 ms | 96 / 113 ms |
| 250 ms/key, tsc-rs | 104 / 190 ms | 97 / 117 ms | 118 / 147 ms |
| 250 ms/key, Go | 68 / 148 ms | 63 / 73 ms | 97 / 112 ms |
| 120 ms/key, tsc-rs | 530 ms / 1.0 s | 702 ms / 1.3 s | 716 ms / 1.3 s |
| 120 ms/key, Go | 69 / 332 ms | 56 / 83 ms | 109 / 174 ms |
| 250 ms/key, no diagnostics pulls, tsc-rs | 99 / 185 ms | 94 / 99 ms | - |
| 250 ms/key, no diagnostics pulls, Go | 71 / 147 ms | 56 / 74 ms | - |
| 120 ms/key, no diagnostics pulls, tsc-rs | 99 / 186 ms | 94 / 107 ms | - |
| 120 ms/key, no diagnostics pulls, Go | 74 / 135 ms | 60 / 73 ms | - |

Per-run completion p95 varies by less than 10% within a row, apart from the
excluded run, and so does hover p95 at 250 and 500 ms/key. At 120 ms/key,
tsc-rs's per-run p95 is 842 ms to 1.0 s for completion and 1.1 to 1.4 s for
hover.

- **At 250 and 500 ms/key nothing queues.** tsc-rs is 1.3 to 1.6 times Go's
  p95, which is about its one-request cost. Dropping the diagnostics pulls
  changes nothing.
- **At 120 ms/key the dispatch thread saturates.** Every kind of request
  waits behind the per-key diagnostics pull, whose own p50 rises from 118 ms
  to 716 ms. tsc-rs answers 10 of 60 completions as cancelled, because they
  were superseded while still queued. Go cancels none.
- **Without the diagnostics pulls, tsc-rs at 120 ms/key is back within 1.4
  times Go.** This is stage 1's upper bound. It is optimistic: without a pull
  on every key, the program update in 7.3 is paid only by the next request,
  not on every key.
- **Phase 1 was mostly load.** At 250 ms/key it measured tsc-rs completion p95
  at 10.7 s against Go's 1.6 s; idle, it is 190 ms against 148 ms.

### 7.3 Per-edit update (track S)

Medians. The first request after an edit pays the program update.

| umami, one-character edit | tsc-rs | Go |
|---|---|---|
| `.tsx` edit (`BoardCloneForm.tsx`): program update | 78 ms (75–104) | 41 ms (33–81) |
| `.tsx` edit: first request after it | 82–85 ms | 47–58 ms |
| `.tsx` edit: CPU per edit, all threads | 255–261 ms (dispatch 111–114, `goport-resolve` 144–146) | 1,418–1,466 ms |
| `.ts` edit (`useFunnelQuery.ts`): program update | 0.75 ms | 3.8 ms |
| `.ts` edit: first request after it | 2.7–3.1 ms | 9.5–10.3 ms |

- **A `.ts` edit reuses the program, and tsc-rs is faster than Go there.**
- **A `.tsx` edit rebuilds the program in both servers.** Go's
  `ReuseProgram` refuses files that need a JSX runtime import
  (`compiler/program.go:399-406`), and the port copies that. Go spends about
  5 times the CPU, spread over all cores, and finishes in about half the wall
  time. tsc-rs's loader is serial ("contract 10"), with module resolution on
  worker threads.
- **What this means for stage 1.** At 120 ms/key on a `.tsx` file, the
  rebuild alone keeps tsc-rs's dispatch thread busy for about 65% of each
  key interval (Go: about 35%). Stage 1 should keep up at that speed, with
  little headroom. Somewhat faster typing would saturate the thread on the
  rebuild alone, in any dispatch design. A `ReuseProgram` that handles the
  JSX runtime import would bring a `.tsx` edit to roughly a `.ts` edit's
  cost in both servers. See `docs/lsp-edit-cost.md` on `perf/lsp-edit-cost`.
- **Phase 1 measured the first request after an edit at 376–411 ms against
  Go's 214–237 ms** (section 3). That too was mostly load.

### 7.4 Stage 1a, tried and dropped

Stage 1a was built on its own branch (`fix/lsp-cancel-queued`): it answers a
request that the client cancelled while it was queued with -32800, without
running it. It was measured only at load 7 to 24, where it cut completion
p95 at 250 ms/key by about a third. It is dropped for two reasons:
- **It is not Go-shaped.** Go has no queued-cancel path, and when Go's
  dispatch loop is busy, Go answers normally where 1a answers -32800.
- **It only helps once requests already queue.** On an idle machine that
  happens only at 120 ms/key, and stage 1 removes the queue itself.

### 7.5 Plan changes

- **Stage 1 is the next step and the only one these numbers support.**
- **Stages 2 to 4 wait for measurements after stage 1.**
- **The `.tsx` rebuild is the next ceiling.** It is best raised with
  microsoft/TypeScript, since the fix belongs in Go's `ReuseProgram` first.
