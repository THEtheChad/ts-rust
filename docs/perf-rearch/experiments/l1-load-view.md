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

## Round 2 (Astra's L1 review, `docs/perf-rearch/reviews/2026-11-04-astra-l1.md`)

| finding | fix | commit |
|---|---|---|
| 1. replacement events cancel | one `Transition` per path (merged over its checks); `Replaced` is Deleted then Created, in that order (no derived `Ord`, no sort of events) | `caaf8a994` |
| 2. FIFO hang | reader: `Fs::read_regular_file_shared` opens with `O_NONBLOCK`, `fstat`s the open file, reads only a regular file (`NotRegular` is a replaced path); shutdown: `Session::close` cancels the job (no `TaskHold`), its checks stop, its result is dropped | `4e60f7a4b`, `caaf8a994` |
| 3. failure = no change | `Outcome::Failed`; views leave the `Reconciler` only on a final result; one retry, then every observation of the views is reported possibly changed (the session's flood-of-watch-events path) | `caaf8a994` |
| 4. diagnostic state ships | `GOPORT_LOAD_TIMES` (S0 line, L0 phases, publish/bind) in `load_times.rs` as on/off modules (C1's pattern), reconcile timing/counts (`JobDiag`), `Slot::by_worker`, test hooks: all under `diagnostics`; one `diagnostics` feature after C1 | `caaf8a994`, `0de56c592` |
| 5. unbounded threads | one process-wide pool (cores, at most 8 threads); per session one job in flight; later loads' views wait and go into the next job | `caaf8a994` |

Tests (`--features diagnostics`, which CI and `parity_tests.py` build with since C1; they need
Linux and `mkfifo`): repointed node_modules symlink (TS2322, Go's answer), realpath-only change,
file→directory, directory→file, each at four points and equal to a fresh serial session; FIFO
before the watch registration (no hang, bounded close); close during a slow reconciliation (no
wait, no delivery); one and two injected failures; twelve loads on a slow file system (bounded,
nothing lost). goport_core unit tests of the transition order. A Created-before-Deleted mutant
fails the symlink and file→directory tests.

`nm` of a plain `--profile goport` build (`0de56c592`): 40,028 symbols, 0 of the diagnostics
items (load_times, LoadTimes, PhaseTimer, ThreadUsage, monotonic_ms, JobDiag, ViewDiag, the
hooks, loadview/load_view_scope `diag`, note_os_call, ViewCounts), 45 of the production
reconciliation; no `goport load-*` or `GOPORT_LOAD_*` string; `GOPORT_LOAD_TIMES=1` prints
nothing. The diagnostics build has 124 matches.

ttfd after round 2 (umami, idle, `--profile goport` with 1.95.0, 7 runs per side, 0 excluded;
manifest `~/.cache/perf-rearch/l1/ttfd-0de56c592/manifest.jsonl`): Go 306.1 [274.1–407.9],
base `09ca99ee2` 406.6 [383.7–555.6], L1 `0de56c592` **287.4** [276.8–344.3]; program update
246.5 / 365.9 / 243.6. Diagnostics build (counts only): loader 199.4 ms (stime 44.5), hit rate
0.930, reconciliation 28.4 ms on the pool, 0 events. Parity full on `0de56c592` against the C1
base: PARITY OK (0 lost of 187,119); the 20 L1 tests ran in its tests tier.
