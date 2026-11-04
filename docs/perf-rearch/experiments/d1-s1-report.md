# D1-S1 report (spike branch `t3/1441d85b`, head 07ac2d5ae; never merged as a switch)

The full write-up is `docs/perf-rearch/experiments/d1-s1.md` on the spike
branch. The bins are `~/.cache/perf-rearch/bins/tsgo-d1s1-a17219bf7-goport`
and `tsgo-d1s1base-50f195257-goport`.

## Design
- A static refinement of Go's 4 groups into N sub-checkers. Each group gets
  k_i sub-checkers by largest remainder on checked weight, and is split
  inside by fast_mode `cost_split`.
- Sub-checkers take their Go checker's symbol ids; N=4 output is
  byte-identical.

## Gain gate
The gain gate is refine's best (c4 − X) over Go-N's best, for N in 8/12/16.

- **Cost inner split: passes all 10 cells.** Minimum 94% (vscode noemit),
  median 116%.
- **Contiguous inner split: fails on umami (83%).** Drop it.

| Project | Go | c4 | best refine |
|---|---|---|---|
| vscode noemit | 8.59 s | 5.83 s | 2.76 s (r16k) |
| t3-server noemit | 10.30 s | 4.07 s | 2.55 s |
| umami noemit | 1.28 s | 0.695 s | 0.428 s |

## Identity (66 projects × 3 runs, 1,719 corpus cases)
- **Go-N** (c8/c12/c16): 3 output diffs each, all on zod.
- **r8, r16k:** 3 diffs (zod, −1 TS2344).
- **r12, r16, r8k, r12k:** 0 diffs.
- **Corpus:** 0 diffs.
- **Determinism:** no nondeterminism.

**The zod mechanism:** in Go's group-0 checker, earlier work silently
degrades `z.input<…>` to `{count: never}`, with no limit diagnostic at the
site, and that is what yields TS2344 at
`v4/mini/tests/assignability.test.ts(149,61)`. A colder sub-checker computes
`{count: string}` and drops that diagnostic. Colder is not safer.

## Verdict
- The gain is worth building.
- Static refinement is not safe as is: certification needs a trigger on any
  type-degrading limit or circularity hit in any sub-checker of a group, plus
  a fallback.
- Add zod's case as a fixture.

## C3 deletion list (if the certified split replaces fast mode)
- **`fast_mode.rs`:**
  - delete `enabled`, `STATE`, `part`, `steal`, `own_arenas`,
    `checker_count`, `MAX_CHECKERS`, `FILES_PER_CHECKER`,
    `cost_split_enabled`, the `GOPORT_FAST`/`_STEAL`/`_SPLIT`/`_ARENAS`
    envs, and the `--fast` handling in `take_flag`;
  - keep `blank_flag` and `cost_split`, together with `GOPORT_FAST_SPLIT_DEP`
    (or move it).
- **`program.rs`:**
  - the fast `checker_count` branch and `fast_cost_split`;
  - `StealQueues`, `steal_file_weights`, `start_stealing_group_do`,
    `stealing_check` and `VersionTables::steal_pass_done`;
  - the FENNEL call in `create_checkers`.
- **Elsewhere:**
  - the fast checks in `ls_program.rs:1190 start_checker_associations`;
  - `stealing_check` in `program_emit.rs:972`;
  - the `own_arenas` call in `jemalloc_layout.rs ArenaClaims::claim`;
  - the `--fast` mentions in `goport_build.rs`.
- **If lazy members and the never order go too:**
  - `checker/lazy_members.rs` (1,333 lines);
  - the `lazy_members` checks in `checker_p21`, `p30`, `p34` and
    `relater_p1`;
  - `fast_never_order` in `checker_p24`;
  - the matching fields in `checker_p01`.
