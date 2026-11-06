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
