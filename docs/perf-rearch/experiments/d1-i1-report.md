# D1-I1 report (branch `t3/certified-checker-refinement`, head 00b333163)

The handoff is `docs/perf-rearch/design/d1-impl.md` on the branch. Bins are
`~/.cache/perf-rearch/bins/tsgo-d1i1-*`; data is in
`~/.cache/perf-rearch/scratch/d1i1/`.

## What it does
- **Routing before any checker work.** Exact for watch, `-b`, incremental and
  composite, diagnostics and counts, traces and pprof, `--singleThreaded`, an
  explicit `--checkers`, `--listFilesOnly`, `--noCheck`, fast and lazy
  members, `--effect`, existing output, a non-regular config, and
  LSP/API/wasm/non-Linux.
- **Split size:** N = max(4, min(16, cores, checked/8)), largest remainder,
  cost split.
- **Held output:**
  - every stdout and stderr write in one ordered, stream-tagged list;
  - every emit file write held;
  - commit order: files first, then the streams;
  - a failed write at commit falls back to an exact rerun.
- **Fallback:** `execve /proc/self/exe` with the same argv, cwd and env, plus
  `GOPORT_PROTOCOL_EXACT_RERUN=<pid>` (the protocol marker). Panics also
  fall back.
- **Monitors:** limits, a virtual count that resets only at top-level frames
  and statement checks, an end bound, and the T5a/T5b/T5b′ global-variant
  checks. The fallback policy table is `certified::monitor::POLICY`; every
  class falls back for now.

## Evidence
- Parity full PARITY OK at `1d9cd1dcc` (`20261104-224817-full-8d8c03418b`).
- **Identity:** 66/66 items equal to Go c4 directly. 7 fall back: zod,
  react-hook-form, ts-pattern tests emit (ConstraintCut), and t3-server
  (virtual count).
- **Fired runs without fallback** (78 runs, 23 fired):

  | class | fired | equal | JS-AGREES | neither |
  |---|---|---|---|---|
  | ReportedLimit | 6 | 4 | 2 | 0 |
  | SilentLimit | 19 | 17 | 2 | 0 |
  | CountBound | 6 | 6 | 0 | 0 |
  | Collection | 6 | 4 | 2 | 0 |

- **Wall, noemit:**

  | project | Go | c4 | certified | vs c4 |
  |---|---|---|---|---|
  | vscode | 8.64 s | 5.61 s | 2.78 s | −50% |
  | umami | 1.32 s | 0.708 s | 0.478 s | −32% |
  | zod | | | | +13% (falls back) |
  | **t3-server** | | 3.83 s | **6.72 s** | **+75%** (late fallback) |

- **icount:** monitor overhead is within fat-LTO noise.

## Left to do
1. **The t3-server fallback.** One expression-bodied arrow reaches a virtual
   count of 5.32M. Proposed fix: scope the resets to memoized computations.
2. Cycle agreement.
3. The relation-budget bound.
4. The empirical residue.
5. The validation campaign.
6. The C3 deletion.
7. A memory cap and physical-core count in the N policy.
8. The shared-diagnostic-pointer parity fix.

## D1-I2a (branch `t3/certified-checker-fallback`, head 85b6f0420; design `design/d1-i2a.md`)
- **In-process fallback.** The program is loaded once; check, emit and report
  run as one attempt. On a fire or panic before the commit, the attempt
  unwinds and the pool is drained (`abandon_checker_pool`), then the attempt
  runs again exactly with a fresh c4 pool. No exec, no marker, no re-read.
  - **Global-state audit:** nothing found that can't be reset. The pool entry,
    `file_associations`, the declaration-diagnostics cache, the certified
    state and the unported registry are reset; a fingerprint assert backs it.
- **Ordered commit.** `certify` comes before the first file write. Order:
  held stream pieces, then files through the normal emit error path (TS5033),
  then the report. The aliased-stdout repro is byte-identical to Go.
- **Findings 1, 2, 3, 6 and 8 fixed.** The identity tools compare exact order,
  check determinism first, use the referees, run from a manifest, and have a
  forced-fallback side.
- **Identity:** 66/66 exact against Go c4; forced fallback 66/66. Without
  fallback, 27 fired: all identical, or JS-AGREES (zod N=8, needs review).
- **Parity full:** 1 lost, `lsp/pkgjson-overlay/0007` (the ATA flake; the
  branch predates the ATA-off fix). The LSP-only rerun lost 0. The 9
  GO-BROKEN items are the base's GO-UNSTABLE fuzz cases.
- **Wall:** the gated bench never ran (load 25–77). Indicative only:
  t3-server 18.0 → 16.2 s against I1.

## D1-I2b (branch `t3/certified-checker-cycle-bounds`, head f53c97e78; design `design/d1-i2b.md`)
- **Publication race fixed:** publish before the pending count drops, with a
  stall test.
- **T5b:** `add` reports insert vs reuse, with epochs.
- **t3-server:**
  - Go's real count at `app.test.ts:1451` is 2.62M; the I1 virtual count was
    5.32M, an overestimate.
  - New compositional rule: entry-reset memos take max(saved, end), and fresh
    files reset where Go does.
  - The virtual peak is now 4.15M (17% margin), with 0 bound violations
    against Go's real peaks. t3-server commits.
- **Cycle agreement** (`cert_cycles.rs`): `CycleDisagree` and
  `CycleAmbiguous`. vscode commits (the `reportRelationError` temporary type
  parameter is skipped). Gap: the "resolved normally elsewhere" case has no
  end-to-end test.
- **Relation-budget bound:** 0 fires; the closest margin is 2.9% (umami).
- **Identity:** 66/66 against Go c4. 15 commit, 5 fall back (ConstraintCut ×4,
  InstantiationDepth ×1).
- **icount:** the exact path is +0.03–0.06%, within LTO noise.
- **Wall:** not measured (load 19–70).
- **Integration lane D1-I2 started:** I2a + I2b + perf head.

## D1-I2 integrated (branch `t3/6fb1d54b`, head 6ed200011, code 5addb1178)
- **Merges:** I2a, I2b, H2, H3 and P1.
- **Publication:** inside the job, before the pending count drops. The stall
  test fails on the old order and passes on the new one.
- **State reset:** `MAP.stats` and `MAP.cycles` are cleared on abandon, and the
  fingerprint check covers them.
- **Parity full:** OK, 0 lost (`20261105-080045-full-193886eef5`). The 37
  referee-only items were already referee-only in the base; none of them goes
  through D1.
- **Identity:** 66/66 exact on every comparison (manifest `e207424842b1d334`).
  15 commit, 5 fall back (all ConstraintCut). With fallbacks disabled, 5 runs
  fire and every raw output still equals Go.
- **Wall** (gated, goport, without P1):

  | project | Go | c4 | I2 | I2 vs Go |
  |---|---|---|---|---|
  | vscode | 9.04 s | 5.76 s | 3.08 s | 0.34× |
  | t3-server | 11.11 s | 3.85 s | 3.06 s | 0.28× |
  | umami | 1.33 s | 0.716 s | 0.491 s | 0.37× |
  | hono | 0.286 s | 0.123 s | 0.093 s | 0.32× |
  | zod (falls back) | 1.01 s | 0.461 s | 0.555 s | 0.55× |

  - **Open:** umami is +3.8% against I1, with equal instructions. Suspects are
    the cycle-check round and the fresh-file pass.
