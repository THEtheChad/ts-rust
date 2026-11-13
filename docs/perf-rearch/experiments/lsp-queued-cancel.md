# A cancel that reaches a queued request (lane lsp-queued-cancel)

Implementation lane, 2026-11-12, AMD MS-A2 (WSL2, 32 threads), branch `perf/lsp-queued-cancel` on fork
main `1b6aba1a8` with `perf/lsp-t3code-profile` merged (its harness fixes). Motivation:
[lsp-t3code-profile.md](lsp-t3code-profile.md), "On T3 Code's own files": typing in `ChatView.tsx`, the
fork's queue never drains because it runs requests that the editor cancelled while they waited.

## Summary

- **The change** (`crates/ts_goport/src/lsp/server.rs`): a `$/cancelRequest` for a request that still waits
  in the port's request queue now reaches it, the way Go's would reach a request its dispatch loop has
  taken. The request runs with an already-cancelled context and answers what Go's handler answers then:
  -32800 at the wrapper's `ctx.Err()` check. While the dispatch thread runs a handler's **sync part**,
  Go's dispatch loop would also be busy and the request would sit in Go's queue, so the cancel is
  dropped, as Go drops it.
- **Per request kind:** every Go request wrapper checks `ctx.Err()` before it sends a result, so no kind
  answers a result *because it lacks a check*. Go answers a result for a cancelled request only when the
  cancel misses it: the request is still in Go's queue behind a sync part, or it has already finished.
  The probe below shows this for 10 kinds on ChatView and BranchPicker.
- **ChatView.tsx (476 KB) typing, 120 ms/key, all pulls:** the queue drains now.
  - Completion p50 12.6 s → 0.53 s (Go 0.14 s).
  - Hover 21.3 s → 0.51 s (Go 4 ms).
  - Edited-file diagnostic pulls answered as cancelled: 13 → 447 of 492 (Go 449).
  - A bench run takes 47 s instead of 194 s (Go 36 s).
- **Other T3 Code files and umami:** no change outside noise (tables below).
- **Where the fork still differs from Go:** Go runs the requests behind a slow one at the same time, so a
  fast request (hover, documentSymbol, definition, completion) often finishes before the editor's
  cancel and Go answers its result. In the fork the same request is queued behind the slow one, so the
  cancel reaches it and it answers -32800. In the other direction, Go's sync part on a `.tsx` edit is
  longer (Go rebuilds the program, the fork reuses it), so a cancel 30 ms after the request finds Go still
  in a sync part (cancel dropped, result) but the fork already past it (-32800). Each answer is one Go
  gives for some timing; only worker threads could match the timing itself.
- **Parity:** the full gate against `base-current` (`c2close-3733fe44b-full`) gives **PARITY OK**, with 0 lost. Run dir: `~/.cache/perf-rearch/parity/runs/20261112-211909-full-98d7a01752`.

## How Go does it (pin 3e9236361fa9, `internal/lsp/server.go`)

Order of effects for one request with an id:

1. **Read loop** (`readLoop`, 920): reads a message. A `$/cancelRequest` is handled right there
   (978-981: `cancelRequest(params.Id)`) and never queued. Every other request goes to
   `requestQueue.Put` (983).
2. **`cancelRequest`** (991): under `pendingClientRequestsMu`, if the id is in `pendingClientRequests` it
   calls that request's `cancel()` and deletes the entry. Otherwise it does nothing. A cancel for a request
   still in `requestQueue`, already finished, or unknown is dropped.
3. **Dispatch loop** (`dispatchLoop`, 1005): `requestQueue.Get`, then at once
   `context.WithCancel` and the entry in `pendingClientRequests` (1016-1024). Then it runs
   `handleRequestOrNotification` (1181). That runs the handler's **sync part**: unmarshal params, and for
   language-service kinds `session.GetLanguageService` / `WithLanguageServiceAndSnapshot` /
   `getLanguageServiceAndCrossProjectOrchestrator`, which includes the snapshot update after an edit.
   The sync part returns the **async part** as a closure.
4. If there is an async part, the loop starts it with `go func()` (1054) and takes the next request at
   once. So in Go a request waits in `requestQueue` only while the dispatch loop runs a sync part.
5. **The async part** runs on its goroutine. Each wrapper checks `ctx.Err()` after the handler returns
   and before `sendResult`:
   - `registerLanguageServiceDocumentRequestHandler` (1378, check at 1397): hover, definition,
     typeDefinition, signatureHelp, formatting (3 kinds), documentSymbol, documentHighlight,
     selectionRange, inlayHint, codeLens, codeAction, prepareCallHierarchy, foldingRange,
     prepareRename, linkedEditingRange, diagnostic, semanticTokens full/range, onAutoInsert.
   - `registerLanguageServiceWithAutoImportsRequestHandler` (1405, checks at 1420 and 1431): completion,
     codeAction.
   - `registerMultiProjectReferenceRequestHandler` (1440, check at 1460): references, implementation,
     VS references.
   - `registerRequestHandler` (1354, check at 1371): no async part; the whole handler runs in the sync
     part, then the check. Rename, workspace/symbol, completionItem/resolve, codeLens/resolve, call
     hierarchy incoming/outgoing, willRenameFiles, shutdown, the custom requests.
   A cancelled context gives `context.Canceled`, which `handleError` (1027) answers as
   `ErrorCodeRequestCancelled` (-32800). A handler error that is not a cancel is answered as that error.
6. **`removeRequest`** (1041) deletes the entry and calls `cancel()` after the answer.
7. A request cancelled before it starts and one cancelled during its sync part get the same answer: the
   sync part runs to its end either way (its own context checks can end it early with the same -32800),
   and the async part answers -32800 at its check.
8. The work a cancelled request still does is the handler's work up to its next context check. In
   diagnostics, `checker` stops at each statement; `getDeclarationDiagnosticsForFile`
   (`compiler/program.go:1655`) has no check and runs to the end.

So, per kind: a cancel that reaches a request gives -32800 for every kind. A result means the cancel
missed it: it came while Go's dispatch loop was busy with a sync part ahead of it, or after the answer.
The earlier lane `lspwait1` (dropped 2026-10-04) saw results for hover, documentSymbol, references,
workspace/symbol and rename "3 of 3". That was the second case: those requests finish in Go before the
cancel arrives. It was not a missing check.

## Design

- **Where Go's model and the port's differ:** the port's dispatch thread runs the sync part and the async
  part of a request back to back (PORTING.md, "Threads"; `server.rs` module comment), so the requests
  behind a running request wait in `request_queue` for its whole run. Go's would wait only for its sync
  part.
- **The emulation** (`ServerShared::cancel_request`, `Server::dispatch_request`):
  - `dispatch_in_sync_part: AtomicBool` is true while the dispatch thread runs
    `handle_request_or_notification` (the sync part). It is saved and restored around it, so a nested
    dispatch (an API call's wait serving LSP messages) keeps the outer value.
  - On the reader thread, under the `pending_client_requests` lock:
    - a registered request is cancelled, as before (Go's path);
    - otherwise, if the dispatch thread is outside a sync part and the id is in `request_queue`, the id
      goes into `canceled_queued_requests` (a set, read and written only under that lock);
    - otherwise the cancel is dropped (Go: queued behind a sync part, finished, or unknown).
  - In `dispatch_request`, at registration, under the same lock: if the id is in the set, it is removed,
    the new context is cancelled, and no pending entry is made. That is what Go's `cancelRequest` leaves
    behind. The request then runs its sync part and its async part with the cancelled context, so the
    answer comes from the same wrapper checks as in Go.
- **Races:** the reader holds the pending lock while it checks the queue and inserts, and the dispatch
  thread takes that lock to register. So a cancel either finds the request queued (and registration sees
  the mark) or registered (and cancels it). If the dispatch thread has taken the request from the queue
  but not yet registered it, the cancel is dropped. Go has the same window between `Get` and the map
  insert.
- **Threads:** only `Send` data crosses: the id set and the flag live in `ServerShared` (`Arc`, `Sync`).
- **What it does not change:** answers for requests that nobody cancels, the order of answers, when
  background tasks run, and which work a cancelled request still does (the same as a request cancelled
  while it runs, which the port already handled).
- **No switch:** the old behaviour (drop every cancel for a queued request) is gone. No env var, feature or
  option was added.

## Tests

`crates/ts_goport/tests/go_baselines/project_lsp/lsp_server_cancel_test.rs`, in-process server
(`lsptestutil`). The client holds the dispatch thread on one of its own replies, so each case is
deterministic:

| test | hold | expected | base `1b6aba1a8` | this branch |
|---|---|---|---|---|
| `a_request_canceled_while_queued_outside_a_sync_part_answers_request_cancelled` | the watch `client/registerCapability` that didOpen's sync part queued (a background task, outside any sync part) | 9 kinds queued and cancelled (hover, documentSymbol, references, rename, workspace/symbol, completion, diagnostic, semanticTokens/full, definition): -32800 each. One queued hover that is not cancelled: result. Then cancels for the finished ids and for an unknown id change nothing: one request of each kind answers its result | FAIL (hover answered a result) | ok |
| `a_cancel_while_the_dispatch_thread_runs_a_sync_part_is_dropped` | `workspace/configuration` in the sync part of `initialized` | the same 9 kinds queued and cancelled: result each (Go drops these cancels). Late and unknown cancels: no effect | ok | ok |
| `a_request_canceled_while_it_runs_answers_request_cancelled` | the watch registration that the hover's own snapshot update queued (a.ts not open), between the hover's sync and async parts | the running hover: -32800. A documentSymbol queued behind it and cancelled: -32800. Late and unknown cancels: no effect | FAIL (queued documentSymbol answered a result; the running hover was cancelled, as before) | ok |

Run: `scripts/run-cargo-capped.sh test -p ts_goport --test go_baselines -- lsp_server_cancel` (3 passed).
The parity gate's tests tier (below) builds this checkout and runs the whole suite: 140,040 PASS,
1,731 ignored.

## LSP comparison with Go: `scripts/lspdiff/probe-cancel.mjs`

The parity gate's LSP sessions keep one request in flight and send no cancels, so they do not cover this.
Adding a session there would reset its Go evidence, and the gate's harness is root's. So the comparison is
a separate probe. It runs the same messages against each server and records the answer per request kind,
3 times per kind (`~/.cache/perf-rearch/lsp-queued-cancel/probe/{ChatView,BranchPicker}.json`). C =
-32800, R = result.

- **held-sync** (one-file fixture, the dispatch loop held in `initialized`'s sync part): R for all 10 kinds,
  on all three servers.
- **after** (cancel after the answer, plus an unknown id; then a hover): R, and the next hover R, for all
  kinds on all servers.
- **in-sync** (didChange, the request, its cancel 2 ms later: it lands in the request's sync part, the
  snapshot update):

| file | go | base | new |
|---|---|---|---|
| ChatView.tsx | C on 30 of 30 | C 30/30 | C 30/30 |
| BranchPicker.tsx | C 29/30 (inlayHint R once) | C 29/30 (hover R once) | C 30/30 |

- **behind-D** (didChange, an uncancelled diagnostic pull, the request, its cancel D ms later):

| file | D | kind | go | base | new |
|---|---|---|---|---|---|
| ChatView | 30 | rename, workspaceSymbol | R3 | R3 | R3 |
| ChatView | 30 | the other 8 kinds | R3 | R3 | C3 |
| ChatView | 300 | diagnostic, semanticTokens, inlayHint | C3 | R3 | C3 |
| ChatView | 300 | hover, documentSymbol, references, rename, workspaceSymbol, definition, completion | R3 | R3 | C3 |
| BranchPicker | 30 | all 10 | R3 | R3 | C3 |
| BranchPicker | 300 | references, rename | C3 | C3 | C3 |
| BranchPicker | 300 | the other 8 | R3 | R3 | R3 |

- **Reading behind-D:**
  - At 30 ms Go is still in the pull's **sync part**: its snapshot update rebuilds the `.tsx` program,
    42+ ms on BranchPicker and longer on ChatView. So the request is in Go's queue, Go drops the cancel
    and answers the result. The fork's sync part (program reused) ended after 4–14 ms, so the fork counts
    the request as taken.
  - At 300 ms on ChatView, Go has run the request beside the pull. The slow kinds were still running and
    answer -32800; the fast ones had answered. The fork has every kind queued behind the pull's 0.8 s of
    work, so all answer -32800. Base answers every one, after the pull.
  - These are the timing differences named in the summary. On ChatView rename and workspace/symbol at
    30 ms the fork itself was still in the pull's sync part, and both agree with Go.

## Timing

**Method.**
- Binaries: base `1b6aba1a8` and new `6845fb0d3` (the lane's compiler commit; later commits change only
  scripts and docs), both stable release builds made by this lane (`TS_CARGO_NIGHTLY=0
  TS_CARGO_INCREMENTAL=0 RUSTUP_TOOLCHAIN=1.95.0 run-cargo-capped.sh build --release -p ts_goport --bin
  tsgo`). They are in `~/.cache/perf-rearch/lsp-queued-cancel/bins/`. Go is
  `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9`.
- Every bench ran under the heavy-run rule, one job at a time.
- **T3 Code** (`scripts/perf-rearch/lsp-queued-cancel/t3bench.sh`):
  - `lspdiff.mjs bench --runs 1 --interval 120 --pulls all --modes concurrent --trace`, 6 files: the
    edited one plus the 5 others of the profile lane's E runs.
  - 3 runs per side and cell. Sides are interleaved run by run, with the order rotating.
  - Each run starts at 1-minute load < 1.5. Load before was 0.35–1.49 for all 36 runs. Load after was
    ≤ 1.4 for every fork run, and 3.5–6.6 for Go on the `.tsx` cells, which is Go's own parallel
    rebuilds.
  - Latencies count answered requests and are pooled over the 3 runs. "run medians" is the per-run p50.
  - Raw data: `~/.cache/perf-rearch/lsp-queued-cancel/t3bench/`, table by `t3bench-agg.mjs`.
- **umami:** `scripts/perf-rearch/bench.sh lsp --scenarios concurrent --runs 3`, labels go, base, new.
  The tool waits for load, interleaves and repeats runs. Data: `~/.cache/perf-rearch/lsp-queued-cancel/umami/`.

### T3 Code, 120 ms/key, pulls=all (p50 / p95 in ms unless marked s; answered and cancelled are totals over 3 runs)

#### apps/web/src/components/ChatView.tsx (476 KB)

| kind | go p50 / p95 | base p50 / p95 | new p50 / p95 | answered / cancelled: go, base, new |
|---|---|---|---|---|
| completion | 136.9 / 602.7 | 12.60 s / 27.42 s | 530.1 / 2.31 s | 59/1, 60/0, 38/22 |
| hover | 4.3 / 156.1 | 21.28 s / 38.80 s | 508.3 / 2.83 s | 63/0, 63/0, 48/15 |
| diagnostic edited | 2.85 s / 5.66 s | 21.31 s / 39.88 s | 2.37 s / 4.98 s | 43/449, 479/13, 45/447 |
| diagnostic other | 106.5 / 419.2 | 20.98 s / 38.59 s | 434.2 / 3.36 s | 589/26, 615/0, 238/377 |
| semanticTokens | 1.82 s / 2.31 s | 21.91 s / 39.47 s | 2.31 s / 6.58 s | 12/111, 122/1, 17/106 |
| inlayHint | 1.74 s / 2.24 s | 22.02 s / 39.58 s | 2.42 s / 6.68 s | 12/111, 123/0, 17/106 |

#### apps/web/src/components/BranchPicker.tsx (10 KB)

| kind | go p50 / p95 | base p50 / p95 | new p50 / p95 | answered / cancelled: go, base, new |
|---|---|---|---|---|
| completion | 99.0 / 410.6 | 21.1 / 178.2 | 21.7 / 100.0 | 60/0, 60/0, 60/0 |
| hover | 1.6 / 53.4 | 8.6 / 59.4 | 8.0 / 60.2 | 63/0, 63/0, 63/0 |
| diagnostic edited | 180.3 / 332.5 | 69.9 / 196.8 | 69.7 / 88.3 | 15/477, 484/8, 469/23 |
| diagnostic other | 61.3 / 307.4 | 72.5 / 134.3 | 71.6 / 118.3 | 615/0, 615/0, 615/0 |
| semanticTokens | 104.4 / 185.7 | 94.7 / 148.5 | 94.3 / 138.1 | 123/0, 123/0, 123/0 |
| inlayHint | 91.7 / 168.1 | 95.5 / 149.2 | 95.5 / 139.0 | 123/0, 123/0, 123/0 |

#### apps/server/src/bootstrap.ts (6.8 KB)

| kind | go p50 / p95 | base p50 / p95 | new p50 / p95 | answered / cancelled: go, base, new |
|---|---|---|---|---|
| completion | 14.0 / 74.9 | 9.3 / 63.8 | 9.5 / 61.6 | 60/0, 60/0, 60/0 |
| hover | 1.9 / 7.2 | 3.7 / 14.4 | 3.7 / 12.3 | 63/0, 63/0, 63/0 |
| diagnostic edited | 24.7 / 32.7 | 18.2 / 39.5 | 18.2 / 28.2 | 492/0, 490/2, 483/9 |
| diagnostic other | 7.1 / 9.3 | 18.8 / 40.2 | 18.8 / 38.8 | 615/0, 615/0, 615/0 |
| semanticTokens | 23.4 / 33.7 | 29.6 / 49.9 | 30.1 / 47.8 | 123/0, 123/0, 123/0 |
| inlayHint | 23.6 / 33.6 | 31.4 / 51.0 | 32.0 / 49.0 | 123/0, 123/0, 123/0 |

#### default: .repos/alchemy-effect/.../CloudFront/KeyValueStore.ts

| kind | go p50 / p95 | base p50 / p95 | new p50 / p95 | answered / cancelled: go, base, new |
|---|---|---|---|---|
| completion | 18.0 / 84.6 | 12.0 / 140.7 | 12.2 / 136.6 | 60/0, 60/0, 60/0 |
| hover | 61.6 / 102.2 | 43.9 / 49.1 | 44.4 / 48.5 | 63/0, 63/0, 63/0 |
| diagnostic edited | 67.8 / 79.0 | 47.9 / 83.5 | 48.4 / 82.1 | 484/8, 480/12, 472/20 |
| diagnostic other | 14.4 / 75.8 | 78.4 / 93.5 | 79.6 / 120.3 | 615/0, 615/0, 615/0 |
| semanticTokens | 18.5 / 20.9 | 81.0 / 97.3 | 82.2 / 121.9 | 123/0, 123/0, 123/0 |
| inlayHint | 17.9 / 20.0 | 82.0 / 97.8 | 83.2 / 122.6 | 123/0, 123/0, 123/0 |

- The full table with per-run medians is in `t3bench/table.md`.
  - On BranchPicker, bootstrap.ts and the default file, the fork's per-run medians agree within a few
    percent.
  - The ChatView rows spread more: new completion 477–657 ms, hover 462–553 ms, tokens 1.55–2.78 s. That
    is the queue's dynamics.
  - Go's `.tsx` completions spread from 57 to 165 ms.
- **ChatView:**
  - The fork's queue drains. Cancelled counts now follow Go's: 447 edited-file pulls (Go 449), and 106 of
    123 tokens and hints pulls (Go 111).
  - The fork answers fewer completions and hovers than Go (38 of 60 and 48 of 63) and more "other"
    diagnostic pulls get cancelled (377, Go 26). These are the requests that Go finishes beside a slow
    one before the cancel comes (see the probe).
  - Remaining gap to Go: completion p50 530 vs 137 ms, hover 508 ms vs 4 ms. It is queue wait behind the
    edited-file pulls that still run (~0.9 s each) and behind the cost of cancelled ones (next section).
- **BranchPicker, bootstrap.ts and the default file:** base and new agree within noise on every p50.
  BranchPicker's edited-diagnostic p95 drops from 197 to 88 ms, and its completion p95 from 178 to 100 ms,
  because a few queued pulls are now dropped (23 cancelled against base's 8). Go is slower than the fork
  on BranchPicker's edited diagnostics (180 ms) and cancels 477 of them.

### umami (`bench.sh lsp --scenarios concurrent`), per-kind medians of the 3 runs' p50 and p95

The bench's cell value (completion p95, `results.md`): 120 ms/key go 300, base 76, new 76; 250 ms/key
go 126, base 77, new 78; 500 ms/key go 127, base 81, new 79. Go's 120 ms runs ended at load 2.2–5.2
(its own threads; flagged "(load)").

| interval | kind | side | n | cancelled | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| 120 | completion | go | 20 | 0 | 69.8 | 299.9 |
| 120 | completion | base | 20 | 0 | 16.7 | 76.0 |
| 120 | completion | new | 20 | 0 | 17.0 | 75.9 |
| 120 | hover | go | 21 | 0 | 63.3 | 78.3 |
| 120 | hover | base | 21 | 0 | 10.2 | 16.9 |
| 120 | hover | new | 21 | 0 | 10.0 | 17.0 |
| 120 | diagnostic | go | 353 | 16 | 111.4 | 170.5 |
| 120 | diagnostic | base | 369 | 0 | 30.9 | 58.0 |
| 120 | diagnostic | new | 366 | 3 | 30.9 | 40.5 |
| 120 | semanticTokens | go | 41 | 0 | 73.5 | 94.7 |
| 120 | semanticTokens | base | 41 | 0 | 44.8 | 69.6 |
| 120 | semanticTokens | new | 41 | 0 | 44.6 | 51.5 |
| 120 | inlayHint | go | 41 | 0 | 94.1 | 109.6 |
| 120 | inlayHint | base | 41 | 0 | 51.4 | 76.6 |
| 120 | inlayHint | new | 41 | 0 | 51.6 | 58.0 |
| 250 | completion | go | 20 | 0 | 73.3 | 126.2 |
| 250 | completion | base | 20 | 0 | 16.9 | 77.2 |
| 250 | completion | new | 20 | 0 | 17.0 | 77.8 |
| 250 | hover | go | 21 | 0 | 64.5 | 79.4 |
| 250 | hover | base | 21 | 0 | 10.2 | 18.9 |
| 250 | hover | new | 21 | 0 | 10.2 | 17.4 |
| 250 | diagnostic | go | 574 | 0 | 97.9 | 115.7 |
| 250 | diagnostic | base | 574 | 0 | 31.9 | 55.2 |
| 250 | diagnostic | new | 573 | 1 | 32.0 | 54.8 |
| 250 | semanticTokens | go | 82 | 0 | 74.1 | 86.2 |
| 250 | semanticTokens | base | 82 | 0 | 45.0 | 68.4 |
| 250 | semanticTokens | new | 82 | 0 | 45.3 | 68.9 |
| 250 | inlayHint | go | 82 | 0 | 86.1 | 99.8 |
| 250 | inlayHint | base | 82 | 0 | 51.8 | 75.9 |
| 250 | inlayHint | new | 82 | 0 | 52.1 | 76.1 |
| 500 | completion | go | 20 | 0 | 79.4 | 127.4 |
| 500 | completion | base | 20 | 0 | 17.6 | 80.7 |
| 500 | completion | new | 20 | 0 | 17.3 | 79.1 |
| 500 | hover | go | 21 | 0 | 56.5 | 75.2 |
| 500 | hover | base | 21 | 0 | 10.3 | 17.9 |
| 500 | hover | new | 21 | 0 | 10.4 | 17.7 |
| 500 | diagnostic | go | 984 | 0 | 99.7 | 115.6 |
| 500 | diagnostic | base | 984 | 0 | 32.6 | 43.7 |
| 500 | diagnostic | new | 984 | 0 | 32.4 | 42.9 |
| 500 | semanticTokens | go | 164 | 0 | 67.1 | 86.3 |
| 500 | semanticTokens | base | 164 | 0 | 45.7 | 53.5 |
| 500 | semanticTokens | new | 164 | 0 | 45.7 | 53.5 |
| 500 | inlayHint | go | 164 | 0 | 80.4 | 98.2 |
| 500 | inlayHint | base | 164 | 0 | 52.7 | 60.9 |
| 500 | inlayHint | new | 164 | 0 | 52.7 | 61.0 |

- No regression. At 120 ms/key the p95 of diagnostics, tokens and hints improves (58 → 41, 70 → 52,
  77 → 58 ms), because 3 queued pulls are now dropped. Every other cell is equal within 1 ms.

### What a cancelled request still costs (ChatView, new, one run)

- From the servers' own `error handling method … in D` lines for the requests answered -32800
  (`new-r1` trace):
  - 291 diagnostic pulls: p50 47 ms, max 551 ms, 11.9 s in total. Each is the first request after an
    edit, so it pays the snapshot update (~13 ms). It also pays the diagnostic work that has no context
    check: declaration diagnostics, as in Go's `getDeclarationDiagnosticsForFile`.
  - 35 semanticTokens: p50 2.8 ms, 2.6 s in total.
  - 9 completions: 0.6 s in total. 35 inlay hints and 7 hovers: under 0.1 s in total.
- That cancelled work is now most of the fork's remaining queue wait on ChatView: the "other" column of
  `trace-split.mjs`, 400–600 ms. Go does the same work on goroutines; its cancelled pulls log 308 ms
  p50.
- **Follow-up, not in this lane:** stop the cancelled pull's no-check diagnostic work early, which is
  allowed because it does not change the answer. Or run requests on workers.

## Parity gate

`scripts/perf-rearch/parity.sh full ~/.cache/perf-rearch/lsp-queued-cancel/bins/tsgo-new-6845fb0d3 --base
~/.cache/perf-rearch/parity/base-current`, under the heavy-run rule. Run dir `~/.cache/perf-rearch/parity/runs/20261112-211909-full-98d7a01752`.

| tier | result |
|---|---|
| cli | MATCH 179 |
| tests | PASS 140,040, IGNORED 1,731 |
| lsp | MATCH 3,316, GO-BROKEN 29 |
| api | MATCH 73 |
| fuzz | MATCH 29,955, DIFF 36 (all grandfathered: Go timeouts on deep nesting, Go crashes reproduced on purpose, the known fuzz/1:5391 trace line), GO-BROKEN 8, JS-AGREES 1 |
| corpus | MATCH 12,727 |

- Compare: 188,092 items, 186,361 of them base items that a full run must have. 0 lost, 0 not run,
  0 known-bad not as pinned, 0 new items not good, 0 crashes on good items. 36 grandfathered, 0 stale
  grandfather entries.
- 0 items newly good by a referee (GO-BROKEN 0, GO-PARTITION 0, JS-AGREES 0). The GO-BROKEN and
  JS-AGREES items above are the base's own.
- **PARITY OK.**

The gate's LSP sessions send no `$/cancelRequest`, so this proves that requests nobody cancels answer as
before. The cancel answers themselves are covered by the tests and the probe above.

Quality (`quality.sh report HEAD --base perf/first-principles`):
- Prod churn +73/−12 in one file.
- Escape hatches 11 → 11 (switches 4 → 4).
- Functions with cognitive complexity >25: 541 → 541. One new function (`is_queued`, cog 1);
  `cancel_request` goes from cog 1 to 3 (Go's: 1).
- Duplication 3.16% → 3.16%. Hygiene: no change.
- Clippy (repo config) 0 → 0; `--force-warn clippy::all` 1,132 → 1,132.

`quality.sh gating HEAD`: **GATING OK**. No allow entry was added.

## Files and commits

- `crates/ts_goport/src/lsp/server.rs`: `ServerShared::{canceled_queued_requests, dispatch_in_sync_part,
  is_queued}`, `cancel_request`, `dispatch_request`, module comment.
- `crates/ts_goport/tests/go_baselines/project_lsp/lsp_server_cancel_test.rs` (new), `mod.rs`.
- `scripts/lspdiff/probe-cancel.mjs` (new): the probe above.
- `scripts/perf-rearch/lsp-queued-cancel/`: `heavy.sh` (heavy-run wrapper), `t3bench.sh`,
  `t3bench-agg.mjs`.
- Switches removed: none (none existed). Switches added: none.
