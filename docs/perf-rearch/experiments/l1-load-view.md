# L0+L1: the per-load frozen OS view under the snapshot (LSP loads)

Lane L0+L1 of [loader v2](../design/loader-v2.md), with [Astra's review](../reviews/2026-11-04-astra-loader-v2.md)
findings 1, 2 (recovery: the reconciliation), 3 (in part) and 8, and Chad's decisions
(plan.md "LSP load view default", brief "Code quality: no old paths").
Branch `t3/per-load-frozen-loader-view`. Final source `fbb9c6671`.

## Result (umami, open `BoardCloneForm.tsx`, idle, `--profile goport`, 7 runs per side)

| side | ttfd median [range] | program update | loader (`process_all_program_files`) wall | loader stime |
|---|---|---|---|---|
| Go 3e9236361fa9 | 322.1 [296.9–382.4] | 248.1 | – | – |
| base `ba5d3f8b7` | 447.6 [389.8–533.2] | 354.0 | – | – |
| L1 `fbb9c6671` | **331.7** [274.9–415.4] | **244.8** | 189.5 | 50.9 |

The earlier window (bin `2e7ee53d5`, reconciliation still on the dispatch thread, sides from
the same build with `GOPORT_LOAD_VIEW` off/on): Go 291.4, perf head 401.4, view off 381.3
(loader 293.1, stime 151.9), view on 351.4 (loader 216.6, stime 44.7). It showed the
reconciliation (28–30 ms) delaying the first diagnostics; `9ae435b6a` moved it off the
dispatch thread.

View counts (diagnostics build, same window, medians): hit rate 0.928, worker hits 25.5k,
loader misses 1,980 (about 1.2k are the root-file stats of setup, before the workers start),
late workers 2,674, live OS calls bypassing the view 0. Reconciliation: 27.5k checks, 28.6 ms
wall on its own thread, 0 events.

## Design as built
- **V** (`frontend/vfs/loadview/mod.rs`): sharded, first answer wins, keyed by exact path.
  stat (full `FileInfo`; `stat_is_file`/`stat_is_dir` are `Common`'s FileExists and
  DirectoryExists of it), read (`Arc<str>` and xxh3, `read_file_shared`), realpath,
  accessible entries. Each entry records what the serial loader read of it (`USED_*`).
- **Placement:** `tsgo --lsp`'s host is `bundled::wrap_fs(os_view_fs())`; `ViewFs` in loader
  mode answers from the view attached on its thread. It sits under `OverlayFS.host`, so
  overlays, the snapshot caches and the config registry (referenced configs included) are
  above it. Attached around `create_program` (`load_view_scope::begin`), detached at its end.
- **Workers** (`loadview::worker_fs`): parse workers and resolve-ahead workers make the same
  OS calls into V; they read only files whose V stat is regular, and prefill the realpath of
  node_modules files (the snapshot's alias record asks for it).
- **Texts:** the cached file takes V's `Arc` and hash; the parse cache passes it on; the
  worker's parse text is the same `Arc` (`FileText::from_shared`); `take_prefetched` compares
  by pointer first.
- **Keyed loads under V** check every lookup through the snapshot's lookup cache, as first
  loads do, and adopt the package.json lookups of taken answers (also outside V): their
  lookup cache and `InfoCache` equal the serial loader's.
- **Reconciliation** (`load_view_scope::reconcile_pending`): after the watch registration
  of the snapshot the loads made, a thread re-checks every observation the loaders read
  (kind for exists calls, full `FileInfo` for stats, a fresh read's hash, realpath, entries
  with links followed again) and posts the changes back as `didChangeWatchedFiles` events.
- **L0:** `GOPORT_LOAD_TIMES` adds `goport load-phases` (CLOCK_MONOTONIC timestamps,
  `RUSAGE_THREAD` utime/stime/minflt for setup, run_queue, finish, collect), `goport
  load-phase` (publish, bind) and `goport load-view-reconcile`.
- **Diagnostics feature:** counters, bypass count, call trace, miss log, V record and replay,
  test hooks.

## Semantics
- **Stable input:** nothing observed changes during the load → byte-identical to serial.
- **Concurrent change:** the load equals the serial loader run against V (V-replay oracle).
  The reconciliation then reloads what changed between observation and registration, as its
  watch event would; a change after the checks start is found by them or by the watches.

## Proof
- Dumps (`l1-dumps.sh`, view bin vs base bin, 2–3 runs each, plus the base's serial loader):
  all first-load scenarios identical in every record; every view run equals the base's serial
  loader in every record except the auto-import snapshot variance (umami-autoimport,
  t3web-autoimport; it varies between base runs too) and one trailing snapshot in
  tspattern-fsmut (watch-event batching). Keyed scenarios differ from the base's keyed loads
  (their pre-existing lookup-cache variance) and equal its serial loader.
- V-replay oracle (`l1-replay-oracle.sh`): umami open and edit, hono, ts-pattern: replay =
  record (dump and stream), replay_miss 0, fresh serial run of the changed disk differs.
- In-process tests (`--features diagnostics`, resolveahead_test.rs `load_view`): view =
  serial (first and keyed loads), three replay-oracle cases with a mid-load change, the
  record format, and reconciliation of a deleted file, a new package, an edited file, a
  package.json edit and a negative lookup at four points of the load window, each equal to a
  fresh serial session (program, resolutions, diagnostics, watch globs), and lost without it.
- Parity full on `fbb9c6671`: 1 lost item, `hono-f12-p6/0057` inlayHint `rs-schedule`. It is
  pre-existing: the base bin gives it in 2 of 8 runs of that session (L1 1 of 4).

## fsbench (real trace of the final bin, 28,244 calls, idle)
One thread: 90–95 ms wall, utime 4 ms, stime 88–92 ms (stat 1.95 µs, read 5.2–5.6 µs,
realpath 6.3–6.8 µs). 15 threads: 10.4–11.3 ms wall, summed stime 92–93 ms.
