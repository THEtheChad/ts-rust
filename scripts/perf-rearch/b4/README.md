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

## Fix: a barrier in lspdiff (`lspdiff-barrier.patch`)

`client.mjs` counts the notifications it sends (didOpen, didChange, didClose, didSave,
didChangeWatchedFiles, didChangeConfiguration) and the `handled method '<m>'` lines each server
logs. `caughtUp()` waits until every sent one is handled. `Script.settle` calls it first, so the
quiet and npm windows start after the server's work for the step has started. A server that
died or hangs is not waited for, and its next request reports the crash or timeout as before.
The patch is against H8's `95006241b` (H8 owns `vendor/lspdiff`). It changes `SESSION_SOURCES`,
so it resets the Go evidence of every watch scenario: a Go fill is needed when it is applied.
