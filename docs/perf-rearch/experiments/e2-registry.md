# E2 spike: AST registry reads without a data race (2026-11-04)

Lane E2 (Opus 5.5). Branch `t3/safe-ast-reads`, commit `ce84ab924`
(not merged). Scratch data: `~/.cache/perf-rearch/safe-ast-reads/`.

## Design

- New `goport_cell::SettledArray` and `SettledView` (the only unsafe code).
  After `publish_file_stores` sets its entries, `settle()` raises a global
  settled end with a Release compare-exchange, after Acquire-checking each
  cell.
- Each reader thread keeps a view length that rises only through an Acquire
  load of that end. Ids below the length are read with plain loads; any
  other id takes today's Acquire path.
- So the happens-before argument doesn't depend on how a thread came to hold
  an id (Astra finding 6).
- **Coverage:** ThreadSanitizer and Miri (8 seeds) pass on a 4-publisher and
  4-reader race test. Weakening the Release or the Acquire is caught. The
  ts_goport store tests were not run under the tools.

## Results (user instructions, fat LTO, stale-blob no-op baseline)

| | umami single | hono single |
|---|---|---|
| the race-free view alone | −0.33% | −0.41% |
| the view plus no column bounds checks (unsound, measurement only) | −1.06% | −1.24% |

- **What actually costs:** the Acquire load is a plain `mov` on x86-64, so
  stopping LLVM from merging lookups isn't the cost; the dependent loads and
  bounds checks are.
- **Verdict:** the view alone is no-go. The view plus single-length thin
  columns (one bounds check, node column inside the 64-byte entry) is a
  conditional go. It must reach ≥1% with fresh blobs on both sides, and the
  same column change on today's Acquire read must be measured as the
  alternative.
- **Priority:** low, about 1%. Queued as E2b behind the larger items.
