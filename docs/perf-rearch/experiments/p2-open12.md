# P2: hono open12 (12 didOpens, then the first diagnostic pull)

Lane P2, branch `t3/56ab9e90` on `perf/first-principles` @ `65c2c2625` (compiler source equal to
W2's head `64014edb4`; the commits between touch only the parity harness). W2's `open12.mjs`,
`window2.sh` and `bench.py` changes are cherry-picked (`206958171`).

## W2's record (`~/.cache/perf-rearch/window2/run/open12/runs.jsonl`)
first_ms per run: go 301 / 109 / 120; main 119 / 623 / 138; head 198 / 91 / 536. The 536 ms in the
brief is head's worst run; head's best (91 ms) beats Go's best. The two slowest tsc-rs runs and Go's
slowest each came first after a long load wait (load 0.0 to 0.16 at start). So the first step is a
real reproduction with more runs.

## Notes (in time order)

### 1. Client-side timeline (2026-11-05, machine loaded 10–19, not gated: shape only)
`p2/open12-timeline.mjs` logs every server message against the first didOpen. Both servers log
`handled method 'textDocument/didOpen' in X`, so the per-open cost is visible on both sides.

- Both servers handle the 12 opens one after another (Go: `Session.DidOpenFile` holds
  `snapshotUpdateMu` and calls `UpdateSnapshot` synchronously, session.go:318; tsc-rs
  `Session::did_open_file`, project/session.rs:466, the same). The first diagnostic pull answers
  1–2 ms after the last open on both. **first_ms = the sum of four loading opens + ~2 ms.**
- The four opens that load (the other eight take 0.3–0.8 ms):

| open | project loaded | files | Go ms (2 runs) | tsc-rs head ms (2 runs) |
|---|---|---|---|---|
| 1 `benchmarks/routers/src/bench.mts` | benchmarks/routers/tsconfig.json | 108 | 35.7 / 36.1 | 18.5 / 17.3 |
| 2 `runtime-tests/deno/ssg.test.tsx` | inferred | 138 | 17.5 / 19.2 | 15.6 / 17.1 |
| 3 `src/adapter/.../handler.test.ts` | tsconfig.spec.json | 846 | 49.2 / 53.8 | 45.8 / 46.3 |
| 4 `src/client/fetch-result-please.ts` | tsconfig.build.json (composite) | 352 | 8.8 / 17.8 | 20.1 / 25.9 |
| first_ms | | | 119.5 / 134.2 | 105.6 / 111.8 |

  Warm, tsc-rs is already faster than Go in this session. The only open where Go is faster is the
  build load (all its files are in the parse cache from the spec load).
- Hono's solution `tsconfig.json` (`files: []`, 9 references) puts build (composite, `src/` minus
  tests) and spec (not composite, `src/` with tests) on one BFS level. Open 3 (a test file): build
  fails its composite include check without a load, spec loads. Open 4 (a source file): spec is
  already loaded, build loads. So the two big loads fall in **different opens**, and Go's parallel
  BFS level does not overlap them either.

### 2. Why W2 saw 536 ms: the first run after a load wait starts cold
W2's slow runs are exactly the first run after each long wait for load < 1.5: go run 1 (after
34 min: 301 ms), main run 2 (after 5.5 min: 623 ms), head run 3 (after 1 min: 536 ms). The
`.wslconfig` on this machine sets `autoMemoryReclaim=gradual` ("hand idle page cache back to
Windows"): when the CPU goes idle, WSL drops page cache. Only 78 of 805 MiB of hono's tree was
resident when checked. `p2/pagecache.py evict` (posix_fadvise DONTNEED on hono's files and the
server binary; reads only) reproduces the state:

| (ungated, load 6–9) | Go | tsc-rs head |
|---|---|---|
| cold (evicted) | 309 / 236 | 286 / 262 |
| warm | 125 / 116 | 280 (all four opens 2.5× slower: contention) / 115 |

Cold, open 3 (the spec load, 846 files plus node_modules) takes 153–226 ms in Go and 181–195 ms
in tsc-rs. Cold is comparable on both; neither shows a tsc-rs-only stall. Gated runs follow.
