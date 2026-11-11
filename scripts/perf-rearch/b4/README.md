# Lane B4: the load refresh count of watch-hono-f12

H8's round-2 refill (`~/.cache/perf-rearch/parity/runs/h8r2-lsp-fill-2/watch-hono-f12`) had
one tsc-rs run (the `GOPORT_PARSE_THREADS=1` one) send 11 `workspace/diagnostic/refresh` requests
at the `load` checkpoint instead of 12 (item `0016 load server messages`, rs-schedule). Go's 8
cached runs all sent 12.

## What Go does (pin 3e9236361fa9)

- `lsp/server.go` `dispatchLoop` handles messages one at a time; a notification's handler runs
  to its end before the next message is read, and `handleRequestOrNotification` then logs
  `handled method '<m>' in <d>` (window/logMessage, when not tracing). A notification has no answer.
- `project/session.go` `DidOpenFile`: `cancelScheduledSnapshotUpdate`, then a snapshot from
  `flushChangesLocked` (file changes only: the pending ATA state is not taken), then
  `UpdateSnapshot` → `updateSnapshot`.
- `updateSnapshot`: adopts the clone, then `triggerATAForUpdatedProjects`, which starts one
  background goroutine per project with `Project.ShouldTriggerATA(snapshotID)` (no installed
  typings info yet, new files in this snapshot's program, or other typings info), then enqueues the
  adoption task (log, `updateWatches`, `publishProgramDiagnostics`, telemetry, auto-import warm).
- The ATA goroutine: `ProgressStart`, `InstallTypings` (`init` once: `npm install
  types-registry@latest`; then discovery and an `npm install` only for typings not cached),
  `ProgressFinish`; if `result.TypingsFiles` differs from the project's, it stores the result
  in `pendingATAChanges` and calls `ScheduleDiagnosticsRefresh`. Until a request's snapshot
  (`flushChanges`) applies that state, every open into the inferred project runs ATA again and
  each result differs, so each open schedules a refresh.
- `ScheduleDiagnosticsRefresh` → `scheduleDiagnosticsRefresh(DebounceDelay)` (500 ms in the LSP
  server): it cancels the scheduled refresh (`Delaying scheduled diagnostics refresh...`),
  bumps the generation and enqueues a task that waits 500 ms (or returns when cancelled), checks
  its generation, clears the cancel and calls `client.RefreshDiagnostics` (fire and forget).
  So results less than 500 ms apart send one refresh.
- Other callers: `DidChangeWatchedFiles` (an event on a relevant extension or a directory),
  `refreshDiagnosticsIfNeeded` and `refreshATAIfNeeded` (configuration changes),
  `DidChangeFile` of a content-mapped file (delay 0). `DidChangeFile` of any other file and
  `Close` cancel the scheduled refresh. The config-file path of `DidChangeWatchedFiles` uses the
  same debounce for `ScheduleSnapshotUpdate`.
- Not debounced: `refreshInlayHintsIfNeeded` and `refreshCodeLensIfNeeded` (sent on the
  configuration change itself), `publishProgramDiagnostics` (each adopted snapshot), ATA progress
  (each ATA run), and `EnqueuePublishGlobalDiagnostics` (coalesced while one is pending, but it
  publishes only what is new since the last publish).

watch-hono-f12's load in Go: 11 opens into the inferred project (snapshots 2 to 12) schedule one
refresh each, about 1.5 s apart, and the first diagnostic pull's snapshots apply the ATA state
(13) and rebuild the inferred program (14), whose ATA installs `@types/{bun,less,node,stylus,yargs}`
and schedules the 12th.

## Cause

The count depends on when the client sends its notifications, in Go and in tsc-rs alike.
lspdiff's `Script.settle` (scenario.mjs) waits for the stream to be quiet and for ATA's npm to be
idle for `ATA_IDLE_MS` (1.5 s) after the wait starts, but the server sends nothing while it handles
a didOpen. When one open takes longer than about 1.5 s, the next opens are sent while it runs and
queue behind it. Their ATA results then come milliseconds apart and fall in one debounce window.

tsc-rs ports this logic exactly. Its local timers and background jobs run on the dispatch thread
in the order they became ready (`gostd::local`), so a refresh that comes due while a handler runs
is sent before any work that the handler queues, as Go's timer goroutine would send it. tsc-rs
with `GOPORT_PARSE_THREADS=1` is just slower at the third open (the hono `src` configured project,
about 950 files: 250 ms against Go's 75 ms when idle). So it is the run most likely to reach 1.5 s
on a loaded host.

Reproduced with `starve-repro.sh` (one CPU, 6 busy loops):

| server | runs | load refreshes |
| --- | --- | --- |
| tsc-rs d46699197 | 4 | 10, 11, 11, 12 |
| Go oracle | 4 | 10, 10, 11, 11 |

The server logs show `Delaying scheduled diagnostics refresh...` after the slow open (3 to 4 s)
in every short run, Go's and tsc-rs's.

## Fix: a barrier in lspdiff (now in `vendor/lspdiff`)

`client.mjs` gets `NotificationBarrier`, which counts the notifications the client sends
(didOpen, didChange, didClose, didSave, didChangeWatchedFiles, didChangeConfiguration) and the
`handled method '<m>' in` / `error handling method '<m>':` lines each server logs for a
notification (a request's line carries its id). `LspClient.caughtUp()` waits until every sent
notification is handled. `Script.settle` (scenario.mjs) calls it first, so the quiet and npm windows
start after the server's work for the step has started. A server that died or hangs is not waited
for: its next request reports the crash or timeout as before (a tsc-rs failure, not a harness
error). `h4-selftest.mjs` has the probe client's `caughtUp` and two "b4" checks: that a scenario
wait calls the barrier before its windows, and the barrier's counting.

This lane shipped the barrier as a patch and its applier, since H8 owned `vendor/lspdiff` then. Lane
H9 (2026-11-11) applied it there (`client.mjs`, `scenario.mjs`, `selftest/h4-selftest.mjs`) and removed
both; `tsserver.mjs` (the JS referee, which can replay a scenario) has the same interface with nothing to
wait for. H9 also closed the residual race below with a progress barrier: see `docs/perf-rearch/parity.md`,
"Progress barrier (lane H9)".

## Verification (`starve-repro.sh`, 2026-11-10)

H8's harness at `b214cfb3d`, without and with the barrier. Each harness got a private Go cache
(`B4_GO_CACHE`), filled unstarved. `harness-copy.sh <ref | dir> <out>` makes such a copy (its
`parity_lsp.py` reads `B4_RS_RUNS` and `B4_GO_CACHE` and writes the server logs; lane H9). The starved runs pin the server to CPU 7, which 6 busy loops
share, so the third open takes 1.1 to 3.9 s:

| harness | server (starved) | runs | load refreshes | coalesced (`Delaying`) |
| --- | --- | --- | --- | --- |
| base | tsc-rs d46699197 | 10 | 12 ×2, 11 ×7, 10 ×1 | in every short run |
| base | Go oracle | 4 | 11 ×4 | 1 per run |
| barrier | Go oracle | 8 | 12 ×8 | none |
| barrier | tsc-rs d46699197 | 50 | 12 ×50 | none |

The unstarved Go fills sent 12 in all 16 runs. In the barrier runs every other item matched as
well (55 MATCH, 1 GO-BROKEN `end watch ids` as in H8's runs), except one run in the 50.

A residual race of the same class (the client cannot see the server's work in flight) is still
open. ATA's first install for a project calls npm twice (`types-registry`, then the typings). On a
starved CPU, the server's work between those calls (loading the registry, discovery) can take
longer than `ATA_IDLE_MS`. The npm-idle wait then ends, and the next open's ATA installs the same
typings again: one extra `@types/jsdom @types/ws` call in the load's npm list (1 of the 50 runs,
`rs-nondet`). Go would do the same, since its ATA goroutine for the next snapshot does not wait for
the first one either. Nothing on the LSP stream marks the end of an ATA request. The client turns
`workDoneProgress` off, Go logs nothing for an install that succeeded, and its progress has a
display delay of its own. So the fix needs a new signal (for example progress on, waiting for each
"Installing types" to end) or a longer idle window. Lane H9 took the first: the watch scenarios advertise
`window.workDoneProgress` and every wait lasts until each progress begun has ended (`parity.md`, "Progress
barrier (lane H9)", with its starvation table).

## The tsc-rs test

`crates/ts_goport/tests/go_baselines/project_lsp/refresh_sequence_test.rs` pins Go's calls for a
load and the file events after it, with the LSP's 500 ms debounce: one refresh per open (each with
its ATA progress), one for two back-to-back opens, none after the request that applies the ATA
state, one per relevant watch event, one for two events in one window, none for an irrelevant one,
and none when a didChange follows the event. It runs at the default parse threads and at
`GOPORT_PARSE_THREADS=1`. tsc-rs already matches Go here, so it passes on the base too.
