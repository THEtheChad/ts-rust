# L0+L1: the per-load frozen OS view under the snapshot (LSP loads)

Lane L0+L1 of [loader v2](../design/loader-v2.md), after three rounds of Astra's review
([v2 design](../reviews/2026-11-04-astra-loader-v2.md), [round 1](../reviews/2026-11-04-astra-l1.md),
[round 2](../reviews/2026-11-04-astra-l1-r2.md)) and Chad's decision of 2026-11-04 (plan.md: drop
L1's recovery and match Go). Branch `t3/per-load-frozen-loader-view`.

## What it is
- **V** (`frontend/vfs/loadview/mod.rs`): one per program load of the language server, sharded,
  first answer wins, keyed by the exact path string. It holds exactly what `osvfs::OsFs` returns:
  stat (full `FileInfo`; `FileExists` and `DirectoryExists` are `vfs::stat_is_file` and
  `stat_is_dir` of it, as `Common` does), read (the decoded text as `Arc<str>` with its xxh3,
  `Fs::read_file_shared`), realpath, accessible entries (order and symlink set).
- **Placement:** `tsgo --lsp`'s host is `bundled::wrap_fs(loadview::os_view_fs())`. V sits under
  `OverlayFS.host`, so overlays, every snapshot cache and the config registry are above it, and
  the serial loader makes every call it made before; only a call that would reach the OS reaches
  V. Other hosts (tests' map file systems, the API's client file system, the CLI) have no view.
- **Only path:** no switch; V replaced the live OS path of LSP loads.
- **Workers:** the parse workers and the resolve-ahead workers fill V through the same OS calls
  (`loadview::worker_fs`); a worker reads a file only when V's stat shows a regular file, and
  prefills the realpath of node_modules files (the snapshot's alias record asks for it).
- **Texts:** the cached file takes V's `Arc` and hash (no copy, no rehash), the parse cache passes
  it on, the worker's parse text is the same allocation, and `take_prefetched` compares by
  pointer first.
- **Lifetime:** `load_view_scope::begin` attaches V around `create_program`; at the end of the
  load, before any later load or auto-import work, the scope, the parse pool and the
  resolve-ahead job let go of it (round 4). A worker still inside a resolution of the job
  holds its own reference until that returns; the view is freed with the last reference.
  The ended-job queue is bounded (`MAX_ENDED` = 4 before frees go ahead of new jobs).
- **Keyed loads** under V check every lookup through the snapshot's lookup cache, as first loads
  do, and adopt the package.json lookups of the answers they take into the loader's `InfoCache`:
  their lookup cache and `InfoCache` equal the serial loader's.
- **Diagnostics** (cargo feature `diagnostics`, never shipped): `GOPORT_LOAD_TIMES` (load timers
  as on/off modules), V's counters, the live-OS bypass count, the observation log
  (`GOPORT_LOAD_OBS_LOG`), the call trace, the V-replay oracle (record and replay) and the test
  hooks.

## Semantics (as Go)
- **Stable disk:** with nothing that the load observes changing during it, the load is
  byte-identical to the serial loader.
- **A change during the load window:** the load equals the serial loader run against V (the
  V-replay oracle). As in Go, which registers its watchers after the load
  (`project/session.go` `updateWatch`), a change between the observation and the client's watch
  acknowledgement is seen when a later watch event reports its path; the base serial loader
  behaves the same. Rounds 1 and 2 added a reconciliation after the watch registration; Astra's
  round-2 review found that it caused new races (stale events cancelling client events, a FIFO
  read on the dispatch thread, directory consumers not invalidated), and Chad chose Go's
  semantics. It is deleted (round 3: 1,521 lines out of `crates/`, `reconcile.rs` 552 and
  `pool.rs` 77 among them).

## Observation-to-watch window (round 3)
From when a load got a path's OS answer to the end of the watch registration that follows it
(every `registerCapability` answered). View: the time each loader-demanded entry of V was first
observed (by a worker or the loader). Base: the base serial loader's first OS call per path on
the loading thread (`422caac19` with `scripts/perf-rearch/loader/l1-window-base-spike.patch`,
never merged). Both logs are written after the load. Idle, 3 runs per side,
`l1-window-run.mjs` + `l1-window.py`, results in `~/.cache/perf-rearch/l1/window/results.txt`.

| umami, ms | load span | load end → watch | reads p10 / p50 / p90 / max | all kinds p50 / p90 / max |
|---|---|---|---|---|
| view r1 | 257.2 | 27.3 | 207.0 / 231.9 / 261.6 / 274.7 | 237.5 / 268.3 / 283.7 |
| view r2 | 242.9 | 29.1 | 193.9 / 220.4 / 249.4 / 262.6 | 226.9 / 257.0 / 271.2 |
| view r3 | 255.0 | 80.5 | 255.2 / 282.8 / 311.6 / 325.3 | 290.0 / 319.8 / 334.3 |
| base r1 | 367.8 | 26.2 | 127.3 / 207.9 / 329.1 / 373.0 | 229.2 / 352.7 / 393.2 |
| base r2 | 379.0 | 24.5 | 128.7 / 211.6 / 335.7 / 382.4 | 233.5 / 358.7 / 402.8 |
| base r3 | 372.6 | 26.1 | 125.8 / 206.4 / 328.5 / 379.4 | 227.8 / 357.4 / 398.1 |

5,006 reads and 27,500 observations on both sides. The view observes early (25k of them by
workers) but its load ends about 120 ms sooner, so its windows are narrower: the median read
window is 10–75 ms longer than the base's, the p90 and max are 60–110 ms shorter. Hono's first
load (292 reads, 1,390 observations, 30–37 ms span) has windows of 65–125 ms on both sides,
dominated by the load-end-to-watch delay (44–93 ms, varying from run to run on both).

## Memory (umami RSS after the first load, release builds, idle)
`l1-rss.mjs`, VmRSS at 1, 5 and 12 s after the first diagnostics (steady): base `422caac19`
542–550 MiB (4 runs), head 529–533 MiB (4 runs), round 2 (views kept until their
reconciliation) 547 MiB (2 runs). Results: `~/.cache/perf-rearch/l1/rss/umami.txt`.

## ttfd (umami, idle, `--profile goport` with 1.95.0, 7 runs per side, 0 excluded)
Manifest `~/.cache/perf-rearch/l1/ttfd-117ee423d/manifest.jsonl`: Go 288.4 [270.7–308.2], base
`422caac19` 402.7 [387.7–477.2], L1 `117ee423d` **280.0** [266.8–293.1]; program update 230.5 /
351.4 / 239.5.

## Proof
- **Dumps** (`l1-dumps.sh`, round 1 and 2): every first-load scenario identical to the base in
  every record; every view run equals the base's serial loader except the auto-import snapshot
  variance and tspattern-fsmut's trailing snapshot.
- **V-replay oracle** (`l1-replay-oracle.sh`): replay = record on umami (open, edit), hono and
  ts-pattern with 8 changed source files; a fresh serial run differs.
- **Tests** (`--features diagnostics`, go_baselines `resolveahead_test::load_view`): first and
  keyed loads equal the serial loader (lookup cache included), three replay-oracle cases with a
  mid-load change, the record format; and the load window as Go: a file deleted, a file edited
  and a node_modules symlink repointed after the load and before the watch registration stay
  unseen with and without the view until their watch events, then match a fresh serial session
  (TS2307, TS2322); a file deleted after a worker observed it behaves the same under the view.
- **Parity full** against base-current: see the lane report.

## fsbench (real trace of a first load, round 1)
28,244 calls, one thread 90–95 ms wall (stime 88–92, utime 4); 15 threads 10.4–11.3 ms.
