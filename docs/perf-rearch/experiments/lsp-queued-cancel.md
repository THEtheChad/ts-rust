# A cancel that reaches a queued request (lane lsp-queued-cancel)

Implementation lane, 2026-11-12, AMD MS-A2 (WSL2, 32 threads), branch `perf/lsp-queued-cancel` on fork
main `1b6aba1a8` with `perf/lsp-t3code-profile` merged (its harness fixes). Motivation:
[lsp-t3code-profile.md](lsp-t3code-profile.md), "On T3 Code's own files": typing in `ChatView.tsx`, the
fork's queue never drains because it runs requests that the editor cancelled while they waited.

## Summary

- **The contract is approximate, by an owner-approved fidelity exception** (Chad, 2026-11-13;
  `docs/perf-rearch/brief.md`, "Fidelity exceptions"). Text, as written into the brief:

  > **Queued-cancel outcomes behind a sync part that may wait.**
  > - **Scope:** a `$/cancelRequest` for request R arrives while R is still in the port's request
  >   queue (the port is backlogged). A message queued ahead of R, or the one running, belongs to a
  >   sync-part family that may or may not wait in Go: `initialized` (configuration request and
  >   registration), `custom/setContentMapperContributions` (registration, or its mutex), `shutdown`
  >   (background cleanup), snapshot-lock waits during an API `APIUpdate`, or content-mapper process
  >   or lifecycle waits.
  > - **In scope,** the port may answer RequestCancelled where Go answers the result, or the reverse.
  > - **Client preconditions:** the port has a backlog when the barrier arrives, and either Go's
  >   answer depends on whether the client answered Go's request (which the port has not sent yet)
  >   before it cancelled, or the barrier does not wait in Go after all.
  > - **Why it cannot be exact on one thread:** Go takes R as soon as every sync part ahead of it
  >   ends; the port runs them only after the request in front, so it must decide before running the
  >   barrier.
  > - **Pinned by:** the tests in `lsp_server_cancel_test.rs` and `lsp_server_apisession_test.rs`,
  >   and the probe's `barrier` case.

  Outside that scope, answers must match Go.
- **Round 3** (Astra FAIL on `6efeb5783`). Fixed:
  - **Finding 1:** the API barrier is Go's `APIUpdate`, not any API callback. An independent
    `createSnapshot` blocks nothing.
  - **Finding 2:** inside an `APIUpdate`:
    - a message whose Go sync part takes the snapshot locks is a barrier, didChange included (an
      explicit list since the fix-up);
    - a message the port already served out of order stays a barrier until the `APIUpdate` ends;
    - `setContentMapperContributions` without dynamic sync still counts.
  - **Finding 3:** dequeue, the published sync-part state and the classification of a cancel are one
    transition under the request queue's lock.
  - **Finding 5:** every cancel is acknowledged before a hold is released, and pause points replace
    sleeps.
  - **Findings 4 and 6:** the code's barrier list matches the documented families below (family 5 is
    not classified), each with its Go lines and the direction of possible mismatch. Claims are stated
    as this approximate contract; the Go-feasibility argument is in "Bounded fix-up after round 3".
  - Each round-2 bug has a test that fails on round 2's code (table in "Tests").
- **Round 2** (Astra FAIL on `6845fb0d3`): round 1 kept a cancel whenever the dispatch thread was outside
  a sync part, which missed barriers queued ahead of the request (the reviewer's
  `setContentMapperContributions` counterexample). Round 2 decided per request but modelled the API
  barrier as "any API callback" and classified without a common lock; round 3 fixes both.
- **The change** (`crates/ts_goport/src/lsp/server.rs`): a `$/cancelRequest` for a request that still waits
  in the port's request queue is kept when Go's dispatch loop would have taken that request by then. The
  request then runs with an already-cancelled context and answers what Go's handler answers:
  -32800 at the wrapper's `ctx.Err()` check, in every case but the two exceptions below. Otherwise
  (Go would still have it queued) the cancel is dropped, as Go drops it.
- **Per request kind:** every Go request wrapper checks `ctx.Err()` before it sends a result, so no kind
  answers a result *because it lacks a check*. A cancelled request answers a result in three cases:
  - the cancel missed it: still in Go's queue, or already answered;
  - it came in the short window after the wrapper's last check and before `sendResult` (Go
    server.go:1397-1400; the port's `register_language_service_document_request_handler`, the same
    order);
  - the request got a content-mapper fallback answer, which is sent from the sync part's error path
    without a context check (Go server.go:1192-1196, `contentMapperFallbackResponse`; the port's
    `handle_request_or_notification`, the same).
  The probe below shows the first case for 10 kinds on ChatView and BranchPicker.
- **ChatView.tsx (476 KB) typing, 120 ms/key, all pulls** (round 1's idle numbers; round 3 below in
  "Timing"): the queue drains now.
  - Completion p50 12.6 s → 0.53 s (Go 0.14 s).
  - Hover 21.3 s → 0.51 s (Go 4 ms).
  - Edited-file diagnostic pulls answered as cancelled: 13 → 447 of 492 (Go 449).
  - A bench run takes 48 s instead of 194 s (Go 36 s).
- **Other T3 Code files and umami:** no change outside noise (tables below).
- **What still differs from Go outside the exception: timing only.**
  - With no barrier involved, Go runs the requests behind a slow one at the same time, so a fast request
    (hover, documentSymbol, definition, completion) often finishes before the editor's cancel arrives,
    and Go answers its result. The fork has the same request queued behind the slow one, so the cancel
    reaches it and it answers -32800.
  - In the other direction, Go's sync part on a `.tsx` edit is longer (Go rebuilds the program, the
    fork reuses it). So a cancel 30 ms after a request can find Go still in a sync part (cancel dropped,
    result) but the fork already past it (-32800).
  - In both, the fork's answer is the one Go gives when the cancel arrives at a different moment
    relative to Go's own progress. It depends on speed, not on message order.
  - The written exception does not name this case. I report it for root's take/drop decision.
- **Parity (fix-up, `045e30717`):** **PARITY OK**, 0 lost
  (`~/.cache/perf-rearch/parity/runs/20261113-040634-full-1e9fe4049d`).
- **Parity (round 3):** the full gate against `base-current` (`c2close-3733fe44b-full`) gives **PARITY OK**,
  with 0 lost. Run dir: `~/.cache/perf-rearch/parity/runs/20261113-024238-full-2a9b6d31a7`, bin
  `tsgo-r3-774f6e4e4`. The quick gate on the final `58bec2a12` (doc and type-alias edits only) is also
  OK.

## Bounded fix-up after round 3 (root decision: TAKE)

Round-3 Astra review on `1d1ef7787`. Root took the branch on condition of a bounded fix-up with no design
change. All four findings were fixed within design (a).

- **Finding 1, sync-handler order (exact Go ordering, not part of the exception).**
  - A queued request whose Go handler and answer run entirely in the sync part holds the requests
    queued behind it (`answers_in_sync_part`, `ANSWERS_IN_SYNC_PART` in `server.rs`). These are the
    `registerRequestHandler` kinds (lsp/server.go:1354-1376):
    - initialize :1269, shutdown :1271, willRenameFiles :1282, rename :1312;
    - callHierarchy incoming :1315 and outgoing :1316;
    - workspace/symbol :1318, completionItem/resolve :1319, codeLens/resolve :1320;
    - runGC, saveHeapProfile, saveAllocProfile, startCPUProfile, stopCPUProfile :1325-1329;
    - initializeAPISession :1331, projectInfo :1332, setContentMapperContributions :1333.
  - Go registers the request behind one of them only after it answered (:1041-1062).
  - The running one was already covered: the port's `register_request_handler` sends the result
    inside the handler, within the flagged sync part (`dispatch_request` clears `sync_part` only after
    `handle_request_or_notification` returns).
- **Finding 2, the APIUpdate list.** `blocks_on_api_update` is an explicit list
  (`TAKES_SNAPSHOT_LOCK`, the Go path per entry; the family 4 row above), with false as the default.
  The changed messages:
  - didChangeConfiguration (no snapshot lock, null settings or not: `Configure` takes `configureMu`
    and `userConfigRWMu` only, session.go:286-311);
  - the profiling requests;
  - initializeAPISession;
  - unknown methods.
  - **A conflict between the requested negative tests:** the profiling requests are sync handlers, so
    finding 1's rule makes them hold the requests behind them anyway, with or without an APIUpdate.
    Go also answers runGC before it takes the next request. So the profiling negative test checks the
    list directly (`queued_cancel_blocks_on_api_update("custom/runGC") == false`). Its behavioural half
    expects the hover behind runGC to keep its result, by the sync-handler order.
- **Finding 3:** `lsp_server_cancel_test` is behind `#[cfg(feature = "test-hooks")]` on its `mod`
  line. Its `cancel` and kept-count helpers have no no-op variant any more. The phase-sensitive API
  tests were already gated, and the suite compiles without the feature, with no warnings.
- **Finding 4:** claims are reworded to what the argument below shows.

### Go feasibility: every outcome the port produces is one Go can produce, outside the exception

- **Claim.** Take any message sequence and any positions of the `$/cancelRequest`s in it, with no
  exception-family message involved (families 1–5; family 4 includes the API's `api_update_blocked`).
  The port's outcome for every request is one that Go produces under some timing. The outcome is
  "RequestCancelled" or "its answer".
  - Not every Go outcome is produced: the port picks one. The answers that do not depend on a cancel
    are the same code in both: results, handler errors, content-mapper fallbacks, unknown-method errors.
- **The Go schedule.** Go's dispatch loop handles messages in order. A message M is "taken" (registered,
  if it has an id) when every message ahead of it has finished its sync part. Choose:
  - **(s1)** for every message that answers in its async part, and for every notification: a sync part
    of zero length;
  - **(s2)** for every sync handler H (`ANSWERS_IN_SYNC_PART`): a sync part, and with it H's answer,
    that ends exactly when the port's sync part of H ends;
  - **(s3)** an async part, per request, of a length chosen below;
  - **(s4)** every cancel arrives at Go at the position where the port read it.
- **A cancel is a no-op for both** when the request is unknown or already answered in the port: Go has
  answered it too, because Go answers no later than the port under this schedule.
- **A cancel for a registered request lands in both.** It reaches a request the port runs, in its sync
  part, the run_pending between the parts, or its async part.
  - Go took R no later than the port did: every sync handler ahead of R ended in the port before the
    port took R, and by (s2) in Go too.
  - Choose R's Go run long enough to be running at the cancel, so it lands and both answer -32800 at
    the same check.
- **A cancel the port keeps for queued R.** The port keeps it only when it is not in a sync part and no
  sync handler is queued ahead of R. Then every sync handler ahead of R has ended in the port, hence in
  Go (s2), and everything else ahead is zero-length (s1). So Go took R before the cancel, and choosing
  R running at that point makes the cancel land. If R is itself a sync handler, its Go sync part runs
  until the port's ends (s2), which is after the cancel, because the port has not started R.
- **A cancel the port drops for queued R** (R then answers its result, unless another cancel for R
  lands later, which is consistent with Go as above).
  - **(a) A sync handler H is queued ahead of R, or running in the port.** By (s2), H has not ended in
    Go, so Go has not taken R and drops the cancel. ✓
  - **(b) The port is in the sync part of an async-kind message or a notification M.** Under (s1), Go
    took R long ago. Choose R's Go run to have answered before the cancel arrives, so Go's cancel is
    a no-op and R answers its result in both. ✓
    - This needs R's Go answer to come before the cancel position. That holds, because R is taken in
      Go no later than the latest sync-handler end ahead of it, which is before the cancel by (a).
    - If R is itself a sync handler: R ahead of later requests with kept cancels would contradict R
      being answered early. But a request behind R cannot have its cancel kept while R is queued or
      running (rule (a)). After the port finished R, by (s2) Go's R also ended, so letting Go's R end
      earlier than the port's only makes more of Go's takes earlier. The port drops in that window,
      which (b) explains by answers before the cancels.
  - **(c) The cancel comes between the port's dequeue of R and R's registration** (the dequeue sets
    `sync_part`): the same as (b) with M = R. Go has the identical `Get`-to-register window as well.
- **Why the old rule failed this argument.** Without (a) for sync handlers, the reviewer's sequence
  needs B taken before A ends in Go, since B's cancel was kept while A was queued. It also needs A
  still running at A's later cancel, since A's cancel was kept. That contradicts Go's order. With (a),
  B's cancel is dropped and the schedule exists.
- **Where the argument stops** (the exception):
  - a message of families 1–4 ahead of R, or one taken during an APIUpdate: its Go duration depends on
    a client reply or an API connection that the port's order cannot reproduce;
  - family 5 (process waits).
  - Round 1's timing notes (Go answering fast requests before the editor's cancel) are case (b) and
    the "answered before the cancel" choices above. They are Go-feasible outcomes, not exceptions.
- **I found no case outside the exception** where the rule cannot be matched within design (a).

### Tests of the fix-up

| test | round 1 | round 2 | round 3 | fix-up |
|---|---|---|---|---|
| `a_sync_handler_ahead_drops_the_cancel_of_the_request_behind_it` (reverse order, async pause) | FAIL | FAIL | **FAIL** | ok |
| `a_cancel_behind_a_canceled_sync_handler_is_dropped` (forward order) | FAIL | FAIL | **FAIL** | ok |
| `a_request_canceled_while_queued_outside_a_sync_part_answers_request_cancelled` (now expects workspace/symbol, queued behind rename, to keep its result) | FAIL | FAIL | **FAIL** | ok |
| `api_update_does_not_count_messages_that_take_no_snapshot_lock` (null-settings configuration, unknown method) | ok | FAIL | **FAIL** | ok |
| `api_update_lock_list_is_explicit` (list check; runGC holds the hover by sync-handler order) | FAIL | FAIL | **FAIL** | ok |
| the other 13 cancel and API tests | as in the round-3 table | | ok | ok |

- Rounds 1–3 were built with the current test hooks (`hooks-variant.py`; raw logs
  `logs/variants4.log`, `logs/variants4b.log`).
- 18 of 18 pass on the fix-up, 10 stress runs in a row.

### Parity and quality of the fix-up (`045e30717`)

- **Full gate:** `parity.sh full ~/.cache/perf-rearch/lsp-queued-cancel/bins/tsgo-r4-045e30717 --base
  ~/.cache/perf-rearch/parity/base-current`. Run dir
  `~/.cache/perf-rearch/parity/runs/20261113-040634-full-1e9fe4049d`.

  | tier | result |
  |---|---|
  | cli | MATCH 179 |
  | tests | PASS 140,052 |
  | lsp | MATCH 3,316, GO-BROKEN 29 |
  | api | MATCH 73 |
  | fuzz | as base: 36 grandfathered DIFF, GO-BROKEN 8, JS-AGREES 1 |
  | corpus | MATCH 12,727 |

  0 lost, 0 new items not good. **PARITY OK.**
- `quality.sh gating HEAD`: **GATING OK**.
- **No T3 Code re-bench:** the dispatch hot path is unchanged. The fix-up changes only the reader
  thread's classification, which compares queued methods against two short constant lists.

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

So, per kind: a cancel that reaches a request gives -32800 for every kind, with two exceptions. One is a
cancel that lands between the wrapper's last check and `sendResult` (1397-1400). The other is a
content-mapper fallback answer (1192-1196), which never checks. Otherwise a result means the cancel missed
the request: it came while Go's dispatch loop had not yet taken it, or after the answer.
The earlier lane `lspwait1` (dropped 2026-10-04) saw results for hover, documentSymbol, references,
workspace/symbol and rename "3 of 3". That was the second case: those requests finish in Go before the
cancel arrives. It was not a missing check.

## Go's take condition, and the sync-part families that may wait (rounds 2–3)

- **The condition.** Go's dispatch loop is one goroutine: `Get`, register, sync part, `go` the async part,
  then the next `Get` (server.go:1005-1063). A cancel for request R reaches it (`cancelRequest`, 991)
  when every message ahead of R in Go's queue has finished its sync part.
- **Ordinary sync parts are work.** Unmarshalling, the snapshot clone, the program update, loading a
  project and checker-free LS setup are CPU work. Go finishes them well before an editor's cancel unless
  the dispatch loop is in one right then.
- **The families of sync parts that may wait outside the loop**, which are the fidelity exception's
  scope. "Classified" means the port drops a cancel for a request queued behind such a message
  (`ServerShared::sync_part_may_wait`).
  - The code classifies families 1–4 below, and family 4 only through the explicit list
    `TAKES_SNAPSHOT_LOCK`.
  - It does not classify family 5.
  - Separately, and not as part of the exception, it holds requests behind a queued sync handler (Go's
    exact order; see "Bounded fix-up after round 3").

| # | family | Go (pin 3e9236361fa9) | the port | possible mismatch |
|---|---|---|---|---|
| 1 | `initialized` | workspace/configuration (server.go:1791 → :854, when the client has the capability, :839-840); config watch registration, always (:1797) | classified, always | Go took R because the client answered before cancelling: Go -32800, port result |
| 2 | `custom/setContentMapperContributions` | with RunExternalCode (session.go:344): `updateContentMapperRegistrations` (:359 → :1592) takes `contentMapperRegistrationMu` (:1601), held also by a snapshot update's background task while it calls the client (:1438); if the extensions changed and the client registers sync dynamically (server.go:460), it (un)registers and waits (:1616 → server.go:459, :499, :731) | classified with RunExternalCode and dynamic text document sync | unchanged extensions and no background registration in flight, or the client answered first: Go -32800, port result |
| 3 | `shutdown` | `session.Close` (server.go:1833 → session.go:1710) → `backgroundQueue.Close` (:1722) waits for every running background task (background/queue.go:51-56), among them watch (un)registrations (session.go:1526, :1575) and content-mapper registrations (:1438) | classified, always | no background task waiting in Go: Go -32800, port result (needs a request after shutdown) |
| 4 | snapshot-lock waits during an API `APIUpdate` | `getCurrentLanguageServerSnapshot` and session close (api/session.go:1817, :5217) → `APIUpdate` holds `snapshotUpdateMu` (project/api.go:19) and, while it clones, `snapshotMu` (session.go:1384). Waiting (the explicit list `TAKES_SNAPSHOT_LOCK` in `server.rs`, Go path per entry): every language-service request through `getSnapshot` (:1060; the document, auto-import and multi-project wrappers, server.go:1378, :1405, :1440), rename, codeLens/resolve, call hierarchy, completionItem/resolve, workspace/symbol, willRenameFiles, projectInfo (session.go:1145, :1153, :1180, :1229, :1243), `DidOpenFile` (:322), `SetContentMapperContributions` with RunExternalCode (:348), `DidChangeFile` and `DidChangeWatchedFiles` via `Snapshot()` (:393, :403, :423, :1046), `Close` (:1724). Everything else does not wait: `DidCloseFile` (:362), `DidSaveFile` (:410), `$/setTrace`, `custom/setLogVerbosity`, `exit` (server.go:1875, :1882, :1837), `workspace/didChangeConfiguration` (:1841, `Configure` session.go:286), the profiling requests (:2467-2506), `initializeAPISession` (:1331), unknown methods (:1224). `createSnapshot` takes neither lock (api/session.go:1440 → snapshothost.go:111, :137) | classified only between `api_update`'s start and end (`Session::api_update_hook`): any queued waiting message ahead of R, and any waiting message the port already served in that update (`api_update_blocked`) | the API message's start differs: Go starts `APIUpdate` on its own goroutine when the message arrives, the port when the dispatch thread reaches it. A cancel in between is judged against the port's order. Either direction |
| 5 | content-mapper process or lifecycle waits | the snapshot clone spawns and talks to mapper processes with RunExternalCode (contentmapper host); ATA npm calls | not classified (treated as work; the port runs the same work in the same sync part) | Go still waits on the process when the cancel comes: Go result, port -32800 |

- **Not waits, and treated as work:**
  - `WaitForBackgroundTasks` (session.go:1503) is test-only.
  - `RefreshDiagnostics` / `RefreshInlayHints` / `RefreshCodeLens` (server.go:742-791) are
    fire-and-forget.
  - `snapshotUpdateMu` held by the scheduled snapshot update or the idle cache clean (session.go:620,
    :684) is CPU work.

## Design (round 3)

- **One published model of Go's dispatch loop** (`GoDispatchModel` in `ServerShared`), changed by the
  dispatch thread only while it holds the request queue's lock, and read by the reader thread under
  that lock together with the queued items:
  - `sync_part`:
    - set at the moment a message leaves the queue: in `DynamicQueue::get_with` for the dispatch loop,
      and in `wait_during_api_call`'s `with_items` for nested dispatch;
    - cleared under the lock when `handle_request_or_notification` returns;
    - this covers notifications too, which have no registration.
  - `api_update_depth`: counted in and out by `project::Session::api_update`, through
    `Session::api_update_hook` → `ServerShared::api_update`, under the lock. Go's `APIUpdate` holds
    the snapshot locks for exactly that span.
  - `api_update_blocked`: set when, during an `APIUpdate`, a message whose Go sync part takes those
    locks leaves the queue, for example a didChange the port serves in the API wait. It is cleared
    when the last `APIUpdate` ends.
- **Classification** (`go_took_queued_request`, under `pending_client_requests` and then the queue
  lock). Keep the cancel iff all of these hold:
  - `sync_part` and `api_update_blocked` are false;
  - the id is in the queue;
  - no queued message ahead of it is a sync handler (`answers_in_sync_part`, added in the fix-up), or
    satisfies `sync_part_may_wait`: families 1–3, or family 4 while `api_update_depth > 0`, combined
    without an early return.
  Kept ids go into `canceled_queued_requests`; `dispatch_request` cancels the request when it registers
  it, as in rounds 1–2.
- **What the shared lock rules out** (finding 3): a reader that saw "outside a sync part" and then a
  queue the barrier had already left. The dequeue and the flag are now one step.
  - A stale read of `sync_part = true` just after a sync part ended only drops a cancel, which is Go's
    answer for a cancel a moment earlier.
- **Rejected alternative (b)** (run sync parts on arrival and schedule the async parts as separate
  jobs), as in round 2:
  - On one thread a sync part still waits for the async part in front of it, so the cancels this lane
    exists for would still be lost.
  - Making it exact needs re-entrancy into the session (the `lspwait1` failure) or worker threads (the
    `Rc` language-service state).
  - It would also change answer order, `gostd::local` task timing and where `publishDiagnostics`
    lands.
- **Removed:** round 2's `api_calls_waiting` counter on `ApiSessionConn` (finding 1) and the
  `dispatch_in_sync_part` flag (now `GoDispatchModel::sync_part`).
- **Outside the exception** (must match Go; it does wherever the families do not apply):
  - a cancel while the port is idle or runs another request's async part or a background task;
  - a cancel during the request's own sync or async part;
  - a cancel after the answer;
  - a cancel for an unknown id;
  - id reuse.
  Round 1's timing notes (Go finishing fast requests beside a slow one before the editor's cancel)
  are about *when* a cancel arrives, not about which answer follows it.
- **Threads:** the model is atomics in `ServerShared`, written under the queue's mutex; the session
  hook holds a `Weak<ServerShared>`.
- **No switch:** no env var, feature or option.
  - The test hooks (`ServerShared::cancel_hooks: test_hooks::CancelHooks` with the handled-cancel count
    and the "dequeued" / "async" pause points; `queued_cancel_count`; `queued_cancel_in_sync_part`)
    are behind `#[cfg(feature = "test-hooks")]`: the field, its 3 call sites and the module.
  - That is the pattern of `execute/watchmanager`'s `test_hooks::ShutdownFaults`.

## Tests (round 3)

`crates/ts_goport/tests/go_baselines/project_lsp/lsp_server_cancel_test.rs` and
`lsp_server_apisession_test.rs`, in-process server (`lsptestutil`, which exposes the server's
`Arc<ServerShared>`).
- **Phases are proven, not slept** (finding 5).
  - With `test-hooks`, `cancel()` returns only after the reader thread has handled the cancel
    (`CancelHooks::cancel_handled`), so a hold released next cannot overtake it.
  - A pause point stops the dispatch thread at a named phase, the test acts, then releases it:
    - "dequeued": the message left the queue, before its registration and sync part;
    - "async": before the async part.
  - The kept-cancel count (`queued_cancel_count`) is asserted at each step.
- **`assert_late_cancels_dropped`:**
  - it cancels every finished id and an unknown id (999);
  - it sends one request per kind that **reuses** those ids, and each must answer its result;
  - then 0 cancels may be kept.
- **Round 1 and round 2 against the same tests.** `scripts/perf-rearch/lsp-queued-cancel/hooks-variant.py`
  adds round 3's test hooks (the same pause points and handled count) to round 1's `server.rs`
  (`6845fb0d3`) and round 2's (`a0a82419a`). The 14 tests then run against each. Raw log:
  `~/.cache/perf-rearch/lsp-queued-cancel/logs/variants.log`.

| test | phase / hold | expected (Go) | round 1 | round 2 | round 3 |
|---|---|---|---|---|---|
| `a_request_canceled_while_queued_outside_a_sync_part_answers_request_cancelled` | didOpen's watch registration (background task) held | 9 kinds queued, cancelled: 9 kept, -32800 each; an uncancelled hover: result; reused ids: results | ok | ok | ok |
| `a_cancel_while_the_dispatch_thread_runs_a_sync_part_is_dropped` | `initialized`'s workspace/configuration held | the 9 kinds: result each | ok | ok | ok |
| `a_request_canceled_while_it_runs_answers_request_cancelled` | the hover's own watch registration held (between its sync and async parts) | running hover -32800; queued documentSymbol: 1 kept, -32800 | ok | ok | ok |
| `a_cancel_behind_a_sync_part_that_waits_for_the_client_is_dropped` (round-1 counterexample) | didOpen's watch registration held; setContentMapperContributions, hover, cancel (acked) | hover: result; content-mapper extensions registered | **FAIL** | ok | ok |
| `a_shutdown_canceled_while_queued_answers_request_cancelled` | watch registration held | shutdown: 1 kept, -32800 (server.go:1371); exit ends the server | ok | ok | ok |
| `a_request_canceled_at_the_start_of_its_async_part_answers_request_cancelled` | pause "async" | -32800 by the pending map; 0 kept | ok | ok | ok |
| `a_cancel_right_after_the_dispatch_thread_took_the_message_ahead_is_dropped` (finding 3) | pause "dequeued" on the first hover | second hover's cancel dropped (0 kept); both hovers: result | **FAIL** | **FAIL** | ok |
| `independent_api_snapshot_callback_keeps_every_cancel` (finding 1) | API `createSnapshot` held at its resolver callback | both hovers kept (2), both -32800 | ok | **FAIL** | ok |
| `api_update_callback_keeps_the_cancel_of_the_first_waiting_request_only` (finding 1) | API `getCurrentLanguageServerSnapshot` (APIUpdate) held at its callback | first hover kept, -32800; second dropped, result | **FAIL** | ok | ok |
| `api_update_counts_a_served_did_change_as_a_barrier` (finding 2) | APIUpdate held; didChange served in the wait (log line and sync-part end awaited); then hover, cancel | hover's cancel dropped; result | **FAIL** | **FAIL** | ok |
| `api_update_counts_set_content_mapper_contributions_as_a_barrier` (finding 2) | RunExternalCode, no dynamic sync; APIUpdate held; setContentMapperContributions queued, hover, cancel | hover's cancel dropped; result | **FAIL** | **FAIL** | ok |
| 3 existing apisession tests (`api_session_keeps_lsp_served`, `api_callback_wait_serves_shutdown_and_exit_behind_other_messages`, `api_session_without_client_frees_its_pipe_on_shutdown`) | | unchanged | ok | ok | ok |

- **Which tests already passed on earlier rounds:**
  - ordinary cancel, sync hold, cancel while running, shutdown, async start, and the 3 existing API
    tests pass on rounds 1–2 too: they pin Go behaviour that round 1 already had;
  - the independent-`createSnapshot` test passes on round 1, which had no API rule;
  - every round-2 bug (findings 1, 2, 3) fails on round 2's code, and every round-1 bug fails on
    round 1's.
- Run: `scripts/run-cargo-capped.sh test -p ts_goport --features test-hooks --test go_baselines --
  lsp_server_cancel lsp_server_apisession`: 14 passed.
- **Stress:** 10 runs in a row, 14 of 14 each.
- The parity gate's tests tier runs the whole suite.

## LSP comparison with Go: `scripts/lspdiff/probe-cancel.mjs`

The parity gate's LSP sessions keep one request in flight and send no cancels, so they do not cover this.
Adding a session there would reset its Go evidence, and the gate's harness is root's. So the comparison is
a separate probe. It runs the same messages against each server and records the answer per request kind,
3 times per kind (`~/.cache/perf-rearch/lsp-queued-cancel/probe/{ChatView,BranchPicker}.json`). C =
-32800, R = result.

- **barrier** (round 2: the reviewer's counterexample against the pinned Go oracle).
  - Setup: a one-file fixture with a tsconfig content mapper; RunExternalCode; dynamic watch and text
    document sync registration.
  - The client holds every server request from didOpen's watch registration on. It sends
    setContentMapperContributions, a hover and the hover's cancel, then answers the held requests
    300 ms later. Data: `probe/barrier.json`.

| barrier | go | base `1b6aba1a8` | round 1 `6845fb0d3` | round 2 `a0a82419a` | round 3 `774f6e4e4` |
|---|---|---|---|---|---|
| setContentMapperContributions | result | result | result | result | result |
| hover (cancelled) | **result** | result | **-32800** | **result** | **result** |
| content-mapper extensions registered | yes | yes | yes | yes | yes |

(Round 3: `probe/barrier-r3.json`.) The barrier case is in the exception's family 2. The probe pins the
port's answer for the client order the reviewer chose: the client holds every server request until
after the cancel.

- **held-sync** (one-file fixture, the dispatch loop held in `initialized`'s sync part): R for all 10 kinds,
  on all servers, round 2 included (`probe/barrier.json` has the round-2 run).
- **after** (cancel after the answer, plus an unknown id; then a hover): R, and the next hover R, for all
  kinds on all servers.
- **in-sync** (didChange, the request, its cancel 2 ms later). These rows are **timing samples**:
  - nothing holds the server, so they do not prove where the cancel lands;
  - it usually lands in the request's own sync part (the snapshot update);
  - the deterministic cases are the tests above.
  - The round-1 binary ran these; round 2 changes nothing on this path (no barrier message is queued).

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

### Round 3: ChatView typing, 120 ms/key, pulls=all

- **Binaries:** stable release builds (`TS_CARGO_NIGHTLY=0 TS_CARGO_INCREMENTAL=0
  RUSTUP_TOOLCHAIN=1.95.0`): base `1b6aba1a8`, round 1 `6845fb0d3`, round 3 `774f6e4e4`; Go oracle.
- **Method:** `t3bench.sh`, every run started at 1-minute load < 1.5, sides interleaved with the order
  rotating. Another project's headless Chrome renderer used about 2 cores during all of these runs.
- **Bench 1** (`t3bench-r3/`, 3 runs per side: go, base, round 3; 2026-11-13 02:17–02:42).
  - Load before each run 1.07–1.49. Load after was ≤ 2.4 for the fork, and 6.9–17.3 for Go, which is
    Go's own rebuild threads plus the renderer. Go's run 0 is disturbed (its run medians spread to
    712 ms).

| cell | kind | side | answered | cancelled | p50 ms | p95 ms | run medians |
|---|---|---|---|---|---|---|---|
| chatview | completion | base | 60 | 0 | 12.62 s | 28.80 s | 12.45 s, 12.57 s, 12.74 s |
| chatview | completion | go | 55 | 5 | 176.4 | 1.77 s | 133.0, 146.9, 712.5 |
| chatview | completion | r3 | 32 | 28 | 829.5 | 3.38 s | 614.7, 784.9, 1.10 s |
| chatview | hover | base | 63 | 0 | 21.68 s | 39.73 s | 21.68 s, 21.68 s, 22.51 s |
| chatview | hover | go | 63 | 0 | 38.7 | 3.05 s | 8.2, 10.0, 742.4 |
| chatview | hover | r3 | 34 | 29 | 829.6 | 5.09 s | 505.7, 880.6, 928.8 |
| chatview | diagnostic edited | base | 479 | 13 | 21.80 s | 40.83 s | 21.53 s, 21.80 s, 21.94 s |
| chatview | diagnostic edited | go | 165 | 327 | 7.76 s | 11.85 s | 2.97 s, 4.33 s, 8.79 s |
| chatview | diagnostic edited | r3 | 58 | 434 | 2.98 s | 7.03 s | 2.61 s, 2.87 s, 3.30 s |
| chatview | diagnostic other | base | 615 | 0 | 21.52 s | 39.93 s | 20.67 s, 21.52 s, 22.08 s |
| chatview | diagnostic other | go | 577 | 38 | 180.9 | 6.67 s | 96.4, 139.6, 911.4 |
| chatview | diagnostic other | r3 | 175 | 440 | 802.0 | 5.15 s | 555.5, 562.9, 1.01 s |
| chatview | semanticTokens | base | 122 | 1 | 22.48 s | 40.85 s | 21.64 s, 22.48 s, 23.08 s |
| chatview | semanticTokens | go | 34 | 89 | 7.93 s | 11.75 s | 1.82 s, 1.91 s, 8.68 s |
| chatview | semanticTokens | r3 | 21 | 102 | 3.61 s | 6.22 s | 2.45 s, 3.30 s, 3.79 s |
| chatview | inlayHint | base | 123 | 0 | 22.59 s | 40.97 s | 21.75 s, 22.59 s, 23.19 s |
| chatview | inlayHint | go | 34 | 89 | 7.54 s | 11.59 s | 1.75 s, 1.84 s, 8.56 s |
| chatview | inlayHint | r3 | 21 | 102 | 3.72 s | 6.36 s | 2.57 s, 3.42 s, 3.91 s |

- **Bench 2: round 1 against round 3 in one session**, to separate round 3 from the machine
  (`t3bench-ab/`, 6 runs per side).

| cell | kind | side | answered | cancelled | p50 ms | p95 ms | run medians |
|---|---|---|---|---|---|---|---|
| chatview | completion | r1 | 74 | 46 | 569.9 | 3.07 s | 464.4, 485.0, 542.4, 560.0, 845.9, 936.5 |
| chatview | completion | r3 | 63 | 57 | 1.07 s | 3.65 s | 639.2, 673.4, 887.7, 940.2, 1.05 s, 1.08 s |
| chatview | hover | r1 | 84 | 42 | 521.0 | 3.83 s | 342.4, 429.4, 430.7, 486.3, 555.9, 977.6 |
| chatview | hover | r3 | 65 | 61 | 850.9 | 4.93 s | 489.3, 563.5, 780.7, 840.7, 850.9, 1.12 s |
| chatview | diagnostic edited | r1 | 101 | 883 | 2.78 s | 6.40 s | 2.24 s, 2.49 s, 2.49 s, 2.57 s, 3.08 s, 3.62 s |
| chatview | diagnostic edited | r3 | 118 | 866 | 3.04 s | 6.40 s | 2.30 s, 2.58 s, 2.61 s, 2.98 s, 3.15 s, 3.19 s |
| chatview | diagnostic other | r1 | 468 | 762 | 469.6 | 5.38 s | 411.9, 427.8, 463.3, 468.5, 543.6, 1.04 s |
| chatview | diagnostic other | r3 | 339 | 891 | 885.7 | 5.00 s | 558.5, 704.5, 744.4, 1.10 s, 1.31 s, 1.75 s |
| chatview | semanticTokens | r1 | 42 | 204 | 2.77 s | 6.71 s | 1.60 s, 1.76 s, 2.27 s, 2.75 s, 3.23 s, 4.53 s |
| chatview | semanticTokens | r3 | 43 | 203 | 2.97 s | 6.08 s | 1.86 s, 2.76 s, 2.90 s, 2.97 s, 3.33 s, 4.12 s |
| chatview | inlayHint | r1 | 42 | 204 | 2.89 s | 6.82 s | 1.71 s, 1.88 s, 2.38 s, 2.86 s, 3.35 s, 4.63 s |
| chatview | inlayHint | r3 | 43 | 203 | 3.08 s | 6.18 s | 1.96 s, 2.89 s, 3.00 s, 3.08 s, 3.44 s, 4.23 s |

  - Pooled p50s were higher for round 3 here, and walls had medians of 48.5 s (round 1) and 50.9 s
    (round 3). The servers' own "handled" times per request are the same (edited diagnostic own work
    884 vs 877 ms; tokens 922 vs 914 ms).
  - The difference came from round 3 answering a few more edited-file pulls in full: per run 18–21
    against round 1's 13–23. That is the saturated queue's sensitivity, so I ran a third session.
- **Bench 3: round 1, round 3, and round 3 with the sync flag set at the handler start** (round 1's
  spot; the only hot-path difference between the two) (`t3bench-ab3/`, 4 runs per side).

| cell | kind | side | answered | cancelled | p50 ms | p95 ms | run medians |
|---|---|---|---|---|---|---|---|
| chatview | completion | r1 | 41 | 39 | 658.3 | 3.39 s | 556.8, 715.4, 1.06 s, 1.13 s |
| chatview | completion | r3 | 42 | 38 | 666.1 | 2.98 s | 643.8, 646.7, 666.1, 1.86 s |
| chatview | completion | r3late | 41 | 39 | 618.9 | 4.62 s | 553.0, 591.7, 618.9, 947.4 |
| chatview | hover | r1 | 51 | 33 | 707.0 | 5.48 s | 444.3, 624.7, 850.1, 864.7 |
| chatview | hover | r3 | 46 | 38 | 749.3 | 4.37 s | 529.6, 678.7, 749.3, 1.67 s |
| chatview | hover | r3late | 44 | 40 | 528.8 | 4.42 s | 364.6, 528.8, 532.7, 853.0 |
| chatview | diagnostic edited | r1 | 79 | 577 | 2.94 s | 6.78 s | 2.25 s, 2.46 s, 2.99 s, 3.07 s |
| chatview | diagnostic edited | r3 | 74 | 582 | 2.97 s | 6.24 s | 2.37 s, 2.74 s, 2.97 s, 3.11 s |
| chatview | diagnostic edited | r3late | 73 | 583 | 2.67 s | 6.31 s | 1.81 s, 2.52 s, 2.84 s, 2.86 s |
| chatview | diagnostic other | r1 | 258 | 562 | 489.5 | 5.54 s | 445.1, 692.5, 933.7, 1.05 s |
| chatview | diagnostic other | r3 | 245 | 575 | 695.2 | 5.25 s | 556.3, 562.8, 569.1, 925.7 |
| chatview | diagnostic other | r3late | 230 | 590 | 481.3 | 4.52 s | 426.4, 617.8, 1.19 s, 1.36 s |
| chatview | semanticTokens | r1 | 25 | 139 | 3.17 s | 7.21 s | 2.35 s, 2.77 s, 2.85 s, 3.93 s |
| chatview | semanticTokens | r3 | 29 | 135 | 2.83 s | 7.16 s | 2.66 s, 2.68 s, 3.03 s, 3.35 s |
| chatview | semanticTokens | r3late | 23 | 141 | 2.89 s | 7.24 s | 2.36 s, 2.89 s, 3.28 s, 3.56 s |
| chatview | inlayHint | r1 | 25 | 139 | 3.28 s | 7.32 s | 2.46 s, 2.89 s, 2.96 s, 4.04 s |
| chatview | inlayHint | r3 | 29 | 135 | 2.94 s | 7.27 s | 2.76 s, 2.79 s, 3.15 s, 3.46 s |
| chatview | inlayHint | r3late | 23 | 141 | 3.02 s | 7.35 s | 2.47 s, 3.02 s, 3.40 s, 3.67 s |

  - Run walls: round 1 48.0–53.7 s, round 3 48.2–51.6 s, the variant 44.0–52.7 s.
  - Edited-file pulls answered per run: round 1 16–22, round 3 16–21, the variant 12–22.
- **Reading:**
  - Round 3 and round 1 are equal within the run-to-run spread, which is large on ChatView: round 1's
    own completion p50 is 530 ms in the idle round-1 bench and 570–750 ms here.
  - The behaviour is the same in this bench: no barrier messages occur, and the per-request work is
    identical.
  - Against base (fork-before) the drained queue stands: completion p50 12.6 s → 0.6–1.1 s, hover
    21.7 s → 0.5–0.9 s, and 434 of 492 edited-file pulls cancelled (Go 327 in bench 1; Go's run 0 was
    disturbed).
  - Against Go, the fork is still behind on completion and hover latency. That is the single dispatch
    thread (see "What a cancelled request still costs").

### Round 2: why the T3 Code bench was not re-run
- **The dispatch hot path is the same as round 1.** `dispatch_request`'s registration and its sync-part
  flag did not change. Only the reader thread's `cancel_request` changed, and it already walked the
  queue in round 1; it now also compares each queued message's method.
- **No barrier occurs in the typing bench.** It sends no `initialized`, `shutdown`,
  `setContentMapperContributions` or API traffic during typing, so round 2 decides every cancel the way
  round 1 did.
- **No valid timing was possible.** From about 22:00 on 2026-11-12 until this report, another project
  on the machine kept the 1-minute load at 25–80, far from the brief's start condition of < 1.5. I
  started a run (`t3bench-r2`), and it waited about 30 minutes without starting, so I stopped it.
- The numbers below are round 1's (`6845fb0d3`), taken on an idle machine.

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

### Round 3

`scripts/perf-rearch/parity.sh full ~/.cache/perf-rearch/lsp-queued-cancel/bins/tsgo-r3-774f6e4e4 --base
~/.cache/perf-rearch/parity/base-current`, under the heavy-run rule. Run dir
`~/.cache/perf-rearch/parity/runs/20261113-024238-full-2a9b6d31a7`.

| tier | result |
|---|---|
| cli | MATCH 179 |
| tests | PASS 140,048, IGNORED 1,731 (the 14 cancel tests included, with `test-hooks`) |
| lsp | MATCH 3,316, GO-BROKEN 29 |
| api | MATCH 73 |
| fuzz | MATCH 29,955, DIFF 36 (all grandfathered), GO-BROKEN 8, JS-AGREES 1 |
| corpus | MATCH 12,727 |

- Compare: 188,092 items (186,361 base items). 0 lost, 0 not run, 0 new items not good, 0 crashes on
  good items, 36 grandfathered, 0 newly good by a referee. **PARITY OK.**
- The gate's LSP sessions send no cancels and no API traffic mixed with LSP. They show that requests
  nobody cancels answer as before, outside the exception and inside it.

### Round 2

`scripts/perf-rearch/parity.sh full ~/.cache/perf-rearch/lsp-queued-cancel/bins/tsgo-r2-a0a82419a --base
~/.cache/perf-rearch/parity/base-current`, under the heavy-run rule, at load 35–55 (another project's
builds). Run dir `~/.cache/perf-rearch/parity/runs/20261112-235845-full-758c06a015`.

| tier | result |
|---|---|
| cli | MATCH 179 |
| tests | PASS 140,044, IGNORED 1,731 (the new tests included, with `test-hooks`) |
| lsp | MATCH 3,316, GO-BROKEN 29 |
| api | MATCH 73 |
| fuzz | MATCH 29,955, DIFF 36 (all grandfathered), GO-BROKEN 8, JS-AGREES 1 |
| corpus | MATCH 12,727 |

- Compare: 188,092 items (186,361 base items). 0 lost, 0 not run, 0 new items not good, 0 crashes on
  good items, 36 grandfathered, 0 newly good by a referee. **PARITY OK.**
- **An earlier round-2 run gave PARITY FAIL**: `20261112-224026-full-6448457a83`, bin
  `tsgo-r2-7104d6cd7`, at load 50–80. It had two items:
  - **lost:** `cli/umami/root/incr`, ERROR ClockStepped ("the wall clock stepped during each of 6
    attempts"). This is the machine's timesyncd step every 32 s; at that load each attempt outlasted
    32 s. It is not a behaviour difference, and it was MATCH in the run above.
  - **FLAKY:** the new API test, a test-side race fixed in `91a234b29` and `a0a82419a` (see Tests).
  - Its lsp, api, fuzz and corpus tiers were the same as in the run above.

### Round 1

Run dir `~/.cache/perf-rearch/parity/runs/20261112-211909-full-98d7a01752` (bin `tsgo-new-6845fb0d3`):
cli MATCH 179, tests PASS 140,040, lsp MATCH 3,316 + GO-BROKEN 29, api 73, fuzz and corpus as above.
0 lost. **PARITY OK.**

The gate's LSP sessions send no `$/cancelRequest`, so this proves that requests nobody cancels answer as
before. The cancel answers themselves are covered by the tests and the probe above.

### Quality (round 3, `quality.sh report HEAD --base perf/first-principles` at `58bec2a12`)

- **Prod churn** +365/−15 in 4 files: `server.rs`, `dynamic_queue.rs`, `project/api.rs`,
  `project/session.rs`.
- **Escape hatches** 11 → 11 (switches 4 → 4).
- **Cognitive complexity:** functions over 25: 541 → 541. The largest new functions are
  `go_took_queued_request` (12), `sync_part_may_wait` (5) and `blocks_on_api_update` (5).
- **Against Go:**
  - `Session::api_update` 5 → 8 (the scope guard);
  - `handle_initialized` 22 → 24 (the hook installation);
  - `cancel_request` 1 → 2 (Go's: 1).
- **Duplication** 3.16% → 3.16%. **Hygiene:** no change.
- **Clippy** (repo config) 0 → 0 and `--force-warn clippy::all` 1,132 → 1,132.
  - The report at `774f6e4e4` showed 2 `doc_lazy_continuation` warnings and 1 `type_complexity`;
    `58bec2a12` fixes them (a doc blank line and the `ApiUpdateHook` alias).
- `quality.sh gating HEAD` (`58bec2a12`): **GATING OK**. `test_hooks` stays gated whole and no allow
  entry was added.
- **Parity on `58bec2a12`:**
  - the full gate ran on `774f6e4e4`; `58bec2a12` changes only a doc comment and a type alias;
  - a quick gate on a build of `58bec2a12`: **PARITY OK**, 0 lost
    (`~/.cache/perf-rearch/parity/runs/20261113-034209-quick-9cb47e020a`).

### Quality (round 2, `quality.sh report HEAD --base perf/first-principles` at `a0a82419a`)

- **Prod churn** +170/−16 in `server.rs`. The test-hooks accessors are counted as prod.
- **Escape hatches** 11 → 11 (switches 4 → 4).
- **Cognitive complexity:** functions over 25: 541 → 541. 5 new functions, the largest
  `go_took_queued_request` (cog 10) and `sync_part_waits_for_client` (cog 5). `cancel_request` cog
  1 → 2 (Go's: 1).
- **Duplication** 3.16% → 3.16%. **Hygiene:** no change.
- **Clippy** (repo config) 0 → 0; `--force-warn clippy::all` 1,132 → 1,132.
- `quality.sh gating HEAD` (`a0a82419a`): **GATING OK**. The new `test_hooks` module is gated whole by
  `#[cfg(feature = "test-hooks")]`; no allow entry was added.

## Files and commits

- `crates/ts_goport/src/lsp/server.rs`:
  - `GoDispatchModel` (`sync_part`, `api_update_depth`, `api_update_blocked`, `took`, `api_update`);
  - `ServerShared::{canceled_queued_requests, go_dispatch, go_took_queued_request,
    sync_part_may_wait, blocks_on_api_update, run_external_code, api_update}`;
  - `cancel_request`, `dispatch_next` (`get_with`), `dispatch_request` (sync-part end),
    `wait_during_api_call` (marks a served message);
  - the session hook installation;
  - the `test_hooks` module (`CancelHooks`, `queued_cancel_count`, `queued_cancel_in_sync_part`);
  - the module comment.
- `crates/ts_goport/src/lsp/dynamic_queue.rs`: `get_with`.
- `crates/ts_goport/src/project/{api.rs, session.rs}`: `Session::api_update_hook`
  (`ApiUpdateHook`) and the `api_update` scope guard.
- Tests: `project_lsp/lsp_server_cancel_test.rs` (new), `lsp_server_apisession_test.rs` (4 tests,
  helpers, `start_server_with`), `lsptestutil.rs` (`LspClient::shared`), `mod.rs`.
- `docs/perf-rearch/brief.md`: the "Fidelity exceptions" entry.
- `scripts/lspdiff/probe-cancel.mjs`: the probe (`barrier` case, `--no-project`).
- `scripts/perf-rearch/lsp-queued-cancel/`: `heavy.sh`, `t3bench.sh`, `t3bench-agg.mjs`,
  `hooks-variant.py`.
- Switches removed: none (none existed). Switches added: none.
