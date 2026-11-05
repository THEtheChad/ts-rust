# L1 report (lane `t3/per-load-frozen-loader-view`, head fbb9c6671, base ba5d3f8b7)

The lane's full write-up is `docs/perf-rearch/experiments/l1-load-view.md`, on
the lane branch.

- **Design:**
  - V is a per-load frozen OS view under `OverlayFS.host`, and the only LSP
    load path; `GOPORT_LOAD_VIEW` was added and removed inside the lane.
  - Reconciliation runs on a `goport-reconcile` thread after watch
    registration: it re-checks only what the loader read and replays changes
    via didChangeWatchedFiles.
  - Keyed loads adopt package.json lookups into `InfoCache` (everywhere).
  - Diagnostics, the replay oracle and the counters are behind the
    `diagnostics` feature.
- **Commits:**
  - `52570b717` core
  - `05a055488` and `9ae435b6a` reconciliation
  - `abdd39592` and `c8e424f98` keyed loads
  - `48ad574cf` V is the only path
  - `fbb9c6671` proof tools
- **ttfd** (umami idle, goport, 7 interleaved rounds, medians):

  | | ms |
  |---|---|
  | Go | 322.1 |
  | base | 447.6 |
  | L1 | 331.7 |

  - L1 is +9.6 ms over Go, inside Go's spread, so not yet a "≤ Go" claim.
  - Loader 189.5 ms.
  - Reconciliation 28.6 ms off-thread, 27.5k checks.
  - Manifest: `~/.cache/perf-rearch/l1/ttfd-fbb9c6671/manifest.jsonl`.
- **Evidence:**
  - 11 dump scenarios identical to base; V runs equal the base's serial loader.
  - Replay oracle exact (0 misses).
  - 37/37 diagnostics-feature tests, including reconciliation mutations at
    4 timings × 5 kinds.
  - Parity full PARITY OK twice while V was opt-in (on and off).
  - The final full run failed only `lsp/hono-f12-p6/0057` inlayHint
    `rs-schedule`. The base bin reproduces it (2/8), so it is not caused by L1.
- **Residual risks:**
  - The config walk is outside V.
  - Reconciliation re-reads about 26 MB per first load.
  - Reconciliation events arrive after the first diagnostics.
  - The L1 tests need `--features diagnostics`.
- **Quality:**
  - Switches 46 → 46; 5 new diagnostics-only names.
  - Cognitive complexity >25 flat (716).
  - Duplication 3.65%.
  - `panic!` +3.

## Round 2 (head `dc7f562d0`; code at `0de56c592`)
- **All 5 Astra findings fixed** (`4e60f7a4b`, `caaf8a994`, `0de56c592`):
  - One ordered transition per path (a replacement is Delete→Create).
  - Reads use `O_NONBLOCK` plus fstat, and only regular files are read.
  - `Session::close` cancels without waiting.
  - A failed job gets an explicit Failed result: one retry, then
    possibly-changed invalidation.
  - Diagnostics are gated whole, with no-op `off` modules.
  - Reconciliation runs on a process-wide pool (at most 8 threads), with one
    job in flight per session.
- **New tests:** symlink repoint, realpath-only, file↔dir, FIFO, close during
  reconcile, injected failures, 12 loads on a slow FS. A Create-before-Delete
  mutant fails them.
- **`nm`:** 0 diagnostic symbols in the plain goport build.
- **Parity:** full PARITY OK against the C1 base (0 lost of 187,119).
- **ttfd** (umami, idle, 7 runs; manifest
  `~/.cache/perf-rearch/l1/ttfd-0de56c592/manifest.jsonl`):

  | | median |
  |---|---|
  | Go | 306.1 ms |
  | perf head | 406.6 ms |
  | L1 | 287.4 ms |

- **Quality:** shipped escape hatches 41 → 40, complexity flat, duplication
  3.64%, `panic!` +4.

## Round 3 (head `fb62e1799`, code at `117ee423d`): reconciliation dropped, Go's watch window
- **Deleted:** 1,521 lines (`reconcile.rs`, `pool.rs`, the non-blocking reader,
  the session reconcile state and its tests); 323 added (window tests and a
  diagnostics-only observation log). V is freed at the end of the load.
- **Window, observation to watch registration** (umami, 3 runs, ms):

  | | p50 | p90 | max |
  |---|---|---|---|
  | V | 220–283 | 249–312 | 263–325 |
  | base serial | 206–212 | 329–336 | 373–382 |

  The window is not materially larger.
- **RSS** (umami): base 542–550 MiB, head 529–533 MiB.
- **Parity:** full PARITY OK, 0 lost of 187,119, 25 recovered.
- **ttfd** (umami idle, 7 runs; manifest
  `~/.cache/perf-rearch/l1/ttfd-117ee423d/manifest.jsonl`):

  | | median |
  |---|---|
  | Go | 288.4 ms |
  | base | 402.7 ms |
  | L1 | **280.0 ms** |

- **Quality:**
  - shipped escape hatches 41 → 40;
  - production lines +1,730 net;
  - functions above cognitive 25: flat;
  - `nm`: 0 diagnostic symbols.
