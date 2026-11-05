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

## Observation-to-watch window (round 4, replaces round 3's)
From when a load started the OS call that answered a path to the end of the watch registration
that follows the load (every `registerCapability` answered). Both sides take the time before
the OS call and write their logs after the registration. View: each loader-demanded entry of
V (first OS call, by a worker or the loader; diagnostics build `cdeba80d3`). Base: the base
serial loader's first OS call per path on the loading thread (`0f2570052` with
`scripts/perf-rearch/loader/l1-window-base-spike.patch`, never merged). Idle, 5 runs per side,
interleaved; `l1-window-run.mjs`, then `l1-window.py pair` over the paths both observed
(results: `~/.cache/perf-rearch/l1/window2/`).

**Criterion (stated before the measurement):** material if the paired p90 delta (view − base)
is over +50 ms, or if more than 5% of the paths are over +100 ms.

| paired delta, ms | n | p50 | p90 | p99 | max | > +50 | > +100 |
|---|---|---|---|---|---|---|---|
| umami, reads | 25,030 | +16.6 | **+83.8** | +115.8 | +183.6 | 7,045 | 1,051 (4.2%) |
| umami, all kinds | 137,500 | −4.3 | **+78.2** | +109.3 | +183.6 | 31,857 | 4,808 (3.5%) |
| hono, reads | 1,460 | −4.4 | +10.5 | +12.4 | +15.6 | 0 | 0 |
| hono, all kinds | 6,950 | −12.6 | −0.9 | +11.9 | +15.6 | 0 | 0 |

**Verdict: material on umami** (paired p90 +78 ms over all kinds, +84 ms for reads, both above
+50; the share over +100 ms is 3.5% and 4.2%, under 5%). **Not material on hono** (a 30–40 ms
first load). Per run, umami's load span is 239–267 ms with the view against 361–379 ms for
the base, and the load end to watch 12–15 ms on both.

**Why: worker prefetch order.** The paths whose window grows are the ones that the serial
loader reaches late but the workers observe early. By the base loader's read offset in its
load: 0–50 ms, delta p50 −117; 100–150, −52; 200–250, +41 (p90 +95); 250–300, +74 (p90
+104). All 31,857 paths over +50 ms were observed by a worker, at a median 77 ms into the
view's load, where the base read them at a median 268 ms. The view ends about 120 ms sooner,
but its workers read the late part of the serial order much earlier than that. This is the
window against the base serial loader; Go reads in parallel parse tasks too, so its own
window is not the serial one (not measured here).

**Fix options (not implemented; Chad decides):**
1. **Measure Go's window first.** The bar is Go's behavior. A Go oracle build with the same
   probe (time before each `vfs` call, log after `updateWatch`) would show whether Go's
   parallel parse tasks already observe as early as V's workers. If so, no change is needed.
2. **Bound the workers' lead at demand.** When the loader demands an entry that a worker
   observed more than T ms earlier (T = 50 ms), the loader makes the OS call again and V
   keeps the new answer. That caps the window's growth at about T, but it puts those OS calls
   back on the loader thread: the paths read after 150 ms in the base order are about half of
   all observations, so it costs about half of the view's loader gain.
3. **Pace the workers to the loader's order.** Workers take parse jobs in the serial loader's
   expected order, within K files of its demand cursor. That keeps observation times close to
   the demand times without loader OS calls, at the cost of less parallel prefetch (needs a
   measurement of the loss).

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
