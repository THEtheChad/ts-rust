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
