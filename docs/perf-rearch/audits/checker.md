# Audit: type checker

Condensed by root from the audit lane's report on 2026-11-03. Full report:
T3 thread "perf-rearch audit: checker". Tools are in
`~/.cache/perf-rearch/scratch/audit-checker/`. Measured at load 15 to 64, so
wall numbers are noisy; the lane used CPU time, samples and paired runs.

## Facts

- **No checking case where tsc-rs is slower than Go.**
  - Per thread, the checker uses 2–3× less CPU than Go, and the profile is
    flat.

    | Group | Share of checker self time |
    |---|---|
    | instantiation | 10–12% |
    | relations | 8–10% |
    | AST accessors | ~10% |
    | members | 5–6% |
    | inference | 3.5–6% |
    | flow | 2.7–5% |
    | symbol tables | about 4% |
    | allocation | ~3% |
    | hashing | 2–3% |
    | RefCell/TLS | under 1% |

  - **Effect looks close only because tsc-rs runs the Effect rules** (38% of
    check CPU), even with `plugins: []` in an extending config. Its
    `--extendedDiagnostics` counts differ from Go's. Benchmarks must note it.
- **Remaining headroom is Go's 4-checker model.** Every checker rebuilds
  shared types:
  - duplication at 4 checkers is 1.3–2.1× the types and 1.37–1.79× the
    checker CPU of one checker;
  - the slowest checker runs 1.15–1.46× the mean;
  - on the T3 server project the critical path is 5.4 s against an ideal
    3.1 s.
- **THP guard:** it turns huge pages off on this machine. With
  `GOPORT_THP_GUARD=0`, zod's minor faults drop from 128k to 4k and CPU by
  about 6%.

## Partition and ordering analysis

**Union member order is mostly structural.** Go sorts members with
`CompareTypes` (`utilities.go:418`); type ids only break final ties.

**What depends on checker partition and check order:**
1. **Limits that depend on what is cached:** TS2589/5114/5115 instantiation
   depth and count, the conditional tail limit of 1000, relation overflow.
   Zod's divergence under `--fast` is this: TS5115 appears or disappears,
   with a TS2344 cascade.
2. **Cycle detection** (TS2456, 2502, 7022–7024, 2506, 2313): where a checker
   entered the cycle.
3. **Type-id tie-breaks:** the last fallback in `CompareTypes`, and
   `isDeeplyNestedType`, which does change relation results.
4. **Symbol-id tie-breaks,** and the length of late-bound names in node
   builder truncation.
5. **Printed counts** (`--diagnostics`, `--extendedDiagnostics`) and
   `--generateTrace` ids.

**Measured:** hono, effect and the T3 server give byte-identical output at
1, 2, 4, 8 and 16 checkers and under `--fast` with stealing. Zod differs only
in class 1.

**An exact simulation can't beat the slowest of 4 checkers.** Canonical ids
differ from Go's, and replaying the shared store into each checker's ids
means months of salsa-style dependency tracking.

**Recommended: certification.**
- Run any partition, and monitor every class 1–4 event, firing at margins
  (depth 50/100, count 1M/5M, tail 250/1000), on any cycle, and on any id
  tie-break.
- No event means the output is certified. An event stops the run and reruns
  it with Go's exact partition.
- When counts or a trace are printed, always run exact.
- **Exactness is empirical, not proven.** It needs large-scale partition
  testing and Chad's sign-off.

## Candidates (ranked)

1. **Certified partition-free checking** (a monitor plus an exact fallback).
   - **Gain:** roughly the README's `--fast` numbers with exact output, about
     1.3–1.8× check wall on large projects.
   - **Size:** 600–1,000 lines plus a harness, 3–5 weeks, mostly validation.
   - **Experiments:**
     - **1A (no code):** run the parity project set at 1, 2, 8 and 16
       checkers and `--fast` with stealing, 3 runs each, and diff against
       the default.
     - **1B (about 200 lines):** add event counters and check that every
       divergent run fired.
     - **Kill condition:** misses that margins can't fix, or more than 30% of
       large projects firing.
2. **Count-only changes behind the monitor:** `GOPORT_FAST_NEVER_ORDER`
   (mongodb test 8.9 → 1.6 s) and `GOPORT_LAZY_MEMBERS` (−4 to −15% peak
   memory). About 100 lines once #1 exists.
3. **Partition policy:** choose the checker count from declaration weight,
   import density and cores; duplication grows with N (zod types 2.9× at 16;
   the T3 server reaches 6.4 GB at 16). 10–30% over plain `--fast`.
4. **THP for checker arenas** (runtime): about 5–8% of check CPU here.
   About 100 lines.
5. **Instantiated member tables share the target's index** (exact): 6–9% of
   self time is in this area on the T3 server; 2–5% of check CPU; 1.5–2 weeks.
6. **Split checking of very large `.d.ts` files** (lib.dom without
   `skipLibCheck`) under certification: 50–100 ms on small projects.
7. **Hot/cold split of `Type`** (128 bytes, aligned to 64): 3–5%, uncertain,
   3–4 weeks.
8. **Shared type store across checkers:** removes the 1.3–2.1× duplication,
   but means months of work and exactness only through the monitor.

## Not worth it

Making a checker (under 1 ms), relation caches, RefCell/TLS, hash maps, the
node builder, moving pure helpers to threads, saving checker state across
runs.

## First three

Experiments 1A and 1B, then #1; then #4 (THP); then #2.
