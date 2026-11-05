# perf/first-principles: plan and log

Goal and rules: `brief.md`. Root keeps this file.

## Phase 1 (started 2026-11-03)

| Lane | Kind | Output |
|---|---|---|
| baseline | measure | `scripts/perf-rearch/bench*`, gap table vs Go, user-space samply, first profiles |
| parity | harness | `scripts/perf-rearch/parity*`: local gate vs Go (CLI, emit, LSP, tests), JS tsc cross-check |
| audit-frontend | read-only | scanner, parser, AST, factory, lib blobs |
| audit-program | read-only | program load, module resolution, file system, binder |
| audit-checker | read-only | checker types, relations, instantiation, caches, parallel checking |
| audit-emit | read-only | emitter, printer, declaration emit, source maps, output I/O |
| audit-lsp | read-only | session, snapshots, per-edit rebuild, dispatch, language service |

Phase 2: root merges the audits and the gap table into a ranked list.
An Astra (`gpt-6-astra`, high) review of that list runs before any
implementation. Implementations follow, each one measured and reviewed.

## Decisions

- 2026-11-03: upstream's code-shape parity rule and the repo's maintainer
  process (batches, `docs/typechecker-state`, remote hosts) don't apply on this
  branch (Chad). Behavior parity against Go at the pin is the bar.

## Phase 2 draft: ranked plan (root, 2026-11-03, from the five audits)

Sources are `audits/{frontend,program,checker,emit,lsp}.md`. The baseline
gap table (`baseline.md`) is still pending and may reorder this.

**Ordering principle:**
1. First, close every place where tsc-rs is slower than Go today, with exact
   and low-risk changes.
2. Then take broad, exact wins.
3. Then the large rewrites, each after its kill-or-go experiment.

**Where tsc-rs is slower than Go today:**
- the LSP `.tsx` edit (78 ms against 41 ms per update);
- LSP saturation at 120 ms/key;
- CLI JS emit (umami JS-only 0.13 s against 0.10 s; with source maps 0.15 s
  against 0.10 s; CommonJS also slower);
- possibly first global completion (pending).

The CLI check, parse, bind, d.ts emit and incremental emit are already
1.5–3× faster than Go.

### Track A: LSP edit path (behind Go)

- **A1. ReuseProgram for JSX-runtime, importHelpers and allowJs files.**
  Program audit #1, LSP audit #1.
  - **Gain:** `.tsx` update 78 → about 1 ms; ahead of Go per edit.
  - **Size:** 200–400 lines.
  - **Proof:** a verify mode comparing reuse with a full load, plus lspdiff
    and the publishDiagnostics stream.
  - **Trap:** report `SAME_FILE_NAMES` semantics to the consumers.
- **A2. Preemptible diagnostics with stackful coroutines,** plus answering
  cancelled queued requests with -32800 and priority ordering. LSP audit
  #2 and #3.
  - **Gain:** 120 ms/key p95 toward the no-diagnostics bound.
  - **Spike first** (2–3 days). It replaces #116's thread plan if it holds.
- **A3. Trust the previous load's resolutions** on overlay-only edits.
  LSP audit #5. Possibly folded into C1/C3.

### Track B: CLI emit (behind Go)

- **B1. Early emit for plain `tsc -p`.** Emit audit #1.
  - **Gain:** 5–8% of wall. **Size:** 100–250 lines, reusing the
    incremental path's rules.
- **B2. JS emit off the checker threads, with a precomputed answer table.**
  Emit audit #2.
  - **Experiment first:** compare with `--verbatimModuleSyntax`.
  - **Proof:** a dual-run mode comparing the resolver call log and the
    bytes.
- **B3. Emit phase timers,** then fused TS-stripping transforms. Emit audit
  #4.
- **B4. Skip the redundant declaration-diagnostics transform** under the
  safe conditions. Emit audit #3; measure first.

### Track E: broad exact wins, low risk

- **E1. THP for the checker arenas** (checker audit #4): about 5–8% check
  CPU here.
- **E2. Plain-load node registry lookup** (frontend #4): a one-function
  experiment, aiming at −1 to −4% of check instructions.
- **E3. Remove the +13% parse-worker overhead** (frontend #2).
- **E4. Lib snapshot v2** (frontend #5): about −10% for small projects and
  startup.
- **E5. Instantiated member tables share the target's index** (checker #5):
  2–5% of check.
- **E6. Parser cost per node** (frontend #1): profile first; −25 to −40% of
  parse.
- **E7. Smaller items:** LSP owned-node arena, synthetic reads, freeze
  copies, per-file fixed cost, the CLI source-map copy, stdout coalescing.

### Track C: loader rewrite (large architecture)

- **C1. Compact load graph with a deterministic serial replay** (program #2):
  `u32` path ids, flat edges, O(1) free. 3–4 weeks.
  - **Experiment:** a standalone replay timing from an `--explainFiles`
    graph.
- **C2. Parallel discovery on C1** (program #3): workers become
  authoritative, about 3–5k lines of speculation are deleted, the vscode
  load phase drops 0.42 → about 0.28 s, and the LSP rebuild 78 → 20–30 ms.
- **C3. Incremental LSP rebuild** (program #4, LSP #6) and overlapped `-b`
  loads (program #5).

### Track D: checker parallelism (large; needs Chad's decision)

- **D0. Experiments 1A and 1B** (checker audit): how often output depends on
  the partition, and whether an event monitor catches every divergence.
- **D1. Certified partition-free checking,** plus count-only switches
  behind the monitor and a partition policy (checker #1–3).
  - **Gain:** about 1.3–1.8× check wall on large projects.
  - **Exactness is empirical:** Chad decides after D0.
- **D2. A shared type store across checkers** (checker #8): long-term.

### Decisions for Chad

- **Certified checking (D1):** do you accept output identity proven by
  large-scale testing plus a runtime fallback, rather than by construction?
- **Persistent on-disk caches** (a parse/bind cache for node_modules
  `.d.ts`; frontend #9, program #8): writing a cache directory is a side
  effect Go doesn't have. Opt-in or not at all?
- **Effect rules:** tsc-rs runs Effect's diagnostics where Go doesn't
  (configured through `extends`). Keep as is (product behavior) or treat it
  as a parity difference?

### Proposed first wave (after Astra's review)

1. A1 (implementation).
2. B1 (implementation).
3. Spikes with faster models: E2 (one function, `icount`), E1 (THP A/B),
   and the B2 experiment.
4. The A2 coroutine spike.
5. D0 (no code) once the baseline timings are done, because it is
   CPU-heavy.

## Phase 2 after Astra's review (2026-11-03)

The review is in `reviews/2026-11-03-astra-plan.md` (go with changes). Root
accepts every finding.
- **A1** carries the reason-remap, update-kind and isolated-verify
  requirements.
- **B1** keeps the global-diagnostics boundary.
- **E2** is redesigned as an acquired registry view.
- **E1** gets a full wall/sys/RSS protocol.
- **B4** is dropped.
- **A2** waits for a remeasure after A1, and needs coroutine-local state and
  leases.
- **D1** is not an exact default; D0 is research only.
- **New items:** measure the shipped PGO+BOLT pipeline (S1), and reclaim
  emitter-only synthetic nodes per file (E8).
- **Gap claims** require successful commands, binary provenance and unloaded
  paired medians.

**First wave started:** A1 and B1 (implementation lanes). The spikes wait
until the baseline and parity lanes have released the machine.

## Chad's decisions (2026-11-03)

- **Effect rules: off by default.** The Go-parity mode skips Effect
  diagnostics; an explicit switch turns them on as an extension. Lane E0
  implements it, before the final baseline comparisons.
- **Certified fast checking (D1): default if tests pass.** Chad accepts a
  guarantee backed by large-scale testing rather than proof. D0 therefore
  becomes the gate for D1. The monitor and test design must answer Astra's
  blocker:
  - adversarial cases where the fast run skips work that would make Go hit
    a limit (cache-warming, omitted instantiations);
  - ordered-id recursion tests;
  - fresh state for the fallback, and buffered output.
  - **Bar:** zero divergences across the project corpus and the compiler
    corpus at many partitions.
- **Persistent disk caches: opt-in only,** and only after lib snapshot v2
  (E4) shows decoding beats parsing by a wide margin.

## Merged

- **2026-11-04, E0: Effect rules off by default** (`TSC_RS_EFFECT=1` or
  `--effect` to turn them on).
  - Astra: merge after fixes. Root fixed both (`5852f249f`).
  - Parity full: OK, 0 lost, 7 recovered.
  - The new base `base-current` is that run.

## Parity backlog (pre-existing differences from Go, found by lanes)

- **`--extendedDiagnostics` on main:** column padding differs on umami and
  effect; the printed `Symbols` count differs on zod (203,529 against Go's
  203,532) and on ts-pattern (off by one, and it varies between runs). Found
  by lane B1. The parity gate's extdiag items didn't catch it; check what the
  gate masks.
- **2026-11-04, baseline lane merged:** `scripts/perf-rearch/bench*`,
  `docs/perf-rearch/baseline.md`, and the user-space samply at
  `~/.local/bin/samply-user`.

## Baseline findings that change the plan (2026-11-04)

- **One thread of tsc-rs does 24–62% less work than one thread of Go**
  (user instructions, every project and mode). Wall losses come from how
  work is spread over threads and from serial phases, never from the amount
  of work.
- **New target: the VS Code CLI check.** One quiet run took 12.9 s against
  Go's 11.3 s; it has the smallest instruction margin (0.65).
  - Its 4 checkers run 16.0–19.5 s of CPU each.
  - Parse, bind and check never overlap.
  - **Levers:** D1 (more checkers, certified), a better partition (checker
    #3), and a **new candidate P1**: pipeline the phases, so checking starts
    on files whose dependencies are bound and parse and bind overlap the
    check. This needs its own audit and design note, because checker order
    and binder merges must stay Go's.
- **Wall timings are still unmeasured.** The load gate never opened between
  16:36 and 22:35, because our own lanes keep the load at 6–125. The full
  wall suite is queued (`perf-rearch-baseline-wall4`, 1.5–2.5 h, starts at
  idle).
  - **Plan:** after the current fix rounds, root holds new CPU-heavy lanes so
    the suite and the B1/A1 timing jobs (`b1-time2`) can run in one idle
    window.
- **2026-11-04, E3 spike done:** the overhead is duplicated resolution, not
  parse work (`experiments/e3-parse-overhead.md`). The prototype is held
  until an idle wall check. New candidate **E3b**: a shared package.json
  cache, about 15% of the default parse phase, to be designed with program
  audit candidate 3.
- **2026-11-04, E2 spike done:** the race-free view alone saves 0.3–0.4% (no-go); combined with thin columns, about 1% (conditional). Queued as E2b, low priority. See `experiments/e2-registry.md`.
- **Resolved: the `-b --verbose` second-run "nondeterminism"** (2026-11-04).
  It isn't a tsc-rs bug.
  - **Cause:** `systemd-timesyncd` steps this WSL machine's wall clock back
    about 1.14 s every 32 s (the kernel clock runs about 3.6% fast). A step
    between an upstream `.d.ts` write and a downstream `.tsbuildinfo` write
    inverts their mtimes.
  - **Go shares it:** 14/400 pairs rebuilt, against tsc-rs 7/400. Every
    failure, on both sides, happened during a step.
  - **Fix:** `parity_cli.py` reruns a two-run side that saw a clock step
    (`bef5f46b2`). The tools are `b-race-repro.py` and `clock-steps.py`.
  - **Timing:** wall tools use monotonic clocks (`perf_counter`,
    `performance.now`), so steps don't affect them. Durations read about
    3.6% long, the same on both sides.
- **Synthetic slots grow over an LSP session, on main too** (lane A1's
  stress test): about 160 live synthetic slots per iteration on umami
  (101.3k after 600 iterations with A1, 96.9k without). This is a memory
  growth candidate; trace where the slots come from.
- **2026-11-04, A1 merged** (`55b14f97c`): the LSP reuses the program on
  edits that keep synthetic JSX and importHelpers imports.
  - **Result:** per `.tsx` edit on umami, the program update goes from
    208–217 ms to about 6.6 ms (Go: 125–130 ms). Update plus first hover goes
    from 1.15G to 12–18M instructions.
  - **Reviews:** two rounds. The remaining Astra finding is a false-positive
    MISMATCH in the `GOPORT_VERIFY_REUSE` tool when disk diverges from the
    snapshot; it's a follow-up in the A1 lane.
  - `GOPORT_REUSE_SYNTHETIC=0` turns it off.
  - **Parity full on the merged head:** FAIL on one item,
    `cli/hono/solution/build` (rs-nondet: a `-b` up-to-date timestamp
    disagreement). It matched 6 of 6 alone on both the pre-A1 and the merged
    bin, so it's treated as an existing `-b` race under load and is being
    investigated in its own lane. base-current stays at the E0 run; A1 adds
    no gate items.
- **2026-11-04, A1 verify follow-up merged** (`b5278f0a1`): the verify child replays the server's snapshot view (file cache, directory listings, config texts) and reports ERROR when faithful reconstruction isn't possible.
- **2026-11-04, B1 merged** (`b22c2a3e6`): early emit for plain `tsc -p`.
  Transform and print run per checker right after its check and global
  read; writes are held until every check returns.
  - **Fallbacks to the barrier:** non-regular output targets, custom
    `WriteFile` callbacks, and the F1–F4 cases (for example zod).
  - **Reviews:** three Astra rounds plus a final check. Root fixed the last
    two findings (`c3921eda8`: `os_path` stat, no secondary panic).
  - **Evidence:** per-checker call logs identical. Parity full OK on
    `69a593790`, quick OK on the final.
  - **Timing still pending:** the command is in lane B1's final report,
    bin `t3-de2c82a7/target/b1/bins/`.
- **2026-11-04, parity full on the merged E0+A1+B1 head (`b22c2a3e6`): OK** (186,585 items, 0 lost). It is now base-current.
- **2026-11-04, timing window 1 queued:** `perf-window1` (`scripts/perf-rearch/window1.sh`), comparing Go, main and head `b2febcd86`, plus the B1 and A1 same-bin A/Bs. Bins are `bins/*-goport-w1` (1.95.0, `--profile goport`). Results go to `~/.cache/perf-rearch/window1/gap.md`.

## Timing window 1, first results (idle, 2026-11-04 01:43–03:16)

Bins: `bins/*-goport-w1` (1.95.0, `--profile goport`). Head is `b2febcd86`
(E0+A1+B1). Results: `~/.cache/perf-rearch/window1/gap.md`. Every output
was the same as Go's.

**Head vs Go:**
- **CLI:** every cell faster. noemit, emit and emit-js run at 0.37–0.67×
  Go's time, for example vscode check 5.33 s against 8.51 s, T3 server
  3.96 s against 10.40 s, umami 726 ms against 1.37 s.
  - The audits' "slower" cells were measured under load: the emit audit
    timed the emit phase only, and vscode's 12.9 s was a loaded run.
- **LSP:**
  - concurrent at 120 ms/key: p95 95 ms against 344 ms (0.28×; main
    1.31 s).
  - 250 and 500 ms/key: 0.64–0.67×.
  - `.tsx` probe: 7 ms against 53 ms; `.ts` probe: 3 ms against 12 ms.
- **The only remaining gap is time to first diagnostics** (open a project,
  pull one file's diagnostics): 425 ms against 289 ms, 1.47× slower, while
  using less than half Go's CPU (1.43 s against 3.50 s). This is the serial
  loader, track C (C1/C2), and the shared package.json cache (E3b) also
  applies.
- **B1's gain is small** (umami emit −3%, emit-js −4%, vscode flat).
  `cli-emit-ab` is measuring it exactly.
- **2026-11-04, D0-1B done:** a conditional go for D1
  (`experiments/d0-1b.md`). The spike is on branch
  `t3/partition-sensitivity-monitor` (`161b7bc1e`); its hooks are not
  merged, because they cost instructions even when off.
  - **Caught:** limits plus the new cold-depth shadow caught all 27 real
    divergent runs, and the adversarial cache-warming case that the limit
    class alone misses (Astra finding 8).
  - **Not certifiable as triggers:** cycles, cross-file diagnostics and id
    ties fire far too often. They never coincided with a divergence.
  - **Fallback rate:** at margin 50, 37% of large configs (the kill line is
    30%); at the hard limits, 1 of 8 (zod), which needs count and
    relation-depth shadows.
  - **Production needs:**
    - shadows for the per-statement count, relation depth and lazy links;
    - narrower cycle, cross-file and id-tie triggers;
    - fresh fallback state and buffered output;
    - exact mode for counts, traces and stealing;
    - near-zero off-mode cost (compile-time switch);
    - optionally, only splits that refine Go's.
  - **Next:** a D1 production design note, then Astra's review, then the
    implementation. Chad's bar is still zero divergences at scale.

## Timing window 1, complete (2026-11-04 01:43–05:19)

**Head is slower than Go in only 2 cells:**
- **LSP time to first diagnostics** (umami): 425 ms against 289 ms.
- **`-b` warm no-op on query:** 8 ms against 7 ms (process startup on a
  build with nothing to do).

**Everything else is 0.14–0.76× Go:**

| Area | Head vs Go |
|---|---|
| CLI check, emit, emit-js, emit-cjs | 0.37–0.67× |
| single-threaded | 0.50–0.76× |
| `-b` cold | 0.41–0.72× |
| `-b` warm | 0.23–0.70× |
| LSP concurrent | 0.28–0.67× |
| per-edit probes | 0.14–0.28× |

- **B1 on/off, same bin:** umami emit −4% (817 against 852 ms), emit-js
  −4%, emit-cjs −3.5%; hono, vscode, t3-web and ts-pattern within 0–2%.
- **THP guard off (E1):** no wall gain on zod, t3-server or vscode.
  **E1 dropped.**
- **vscode `-b` "DIFFERS" was a bench artifact:** each label builds in its
  own scratch copy, and the copy's path appears in the diagnostics. The
  outputs are identical after masking the path. `bench.py` now masks the
  run's cwd before hashing.
- **2026-11-04, Astra reviewed the loader design:** S1 rework, S3–S4 rework
  (`reviews/2026-11-04-astra-loader-design.md`).
  - **Next:** spike **S1a**, Astra's narrower first step. First-load worker
    answers go through the existing resolve-ahead acceptance path; the
    snapshot's own reads are kept; nothing speculative is adopted. Then
    measure how much of the ttfd gap it closes.
  - The LSP parity harness lane (watch registrations, two-run LSP,
    first-load cases) provides the proof.
- **2026-11-04, LSP parity harness merged** (`b00e5c646`): watch registrations, server messages and fs-event sessions; tsc-rs runs 3× per LSP session. Full gate on the head: OK, 186,611 items. base-current moved to that run.
- **2026-11-04, D1 design done** (`design/d1.md`), recommending go: exact output with refinements of Go's 4-way split cuts check wall 31–53% on large projects (vscode 0.30× Go). Astra review next.
- **2026-11-04, S1a done** (branch `t3/speed-umami-lsp-ttfd`, in Astra
  review): first-load worker answers go through `accept_ahead_answer`.
  - **ttfd on umami:** about 410 → 332 ms; Go 283–289 ms (1.18×, from
    1.46×). The loader takes 97.7–98.8% of the 9,393 keys and rejects none.
  - **Proof:** `GOPORT_LOAD_DUMP` is identical on and off in 13 scenarios;
    parity full OK.
  - **The remaining 43–49 ms is serial loader work.** Next: bind in the
    parse workers (S7, −25 to −30 ms), and skip checking or replaying calls
    the load already checked (−15 to −25 ms).
- **2026-11-04, Astra reviewed the D1 design:** overall rework; S1 go with
  changes, as an uncertified opt-in experiment
  (`reviews/2026-11-04-astra-d1-design.md`). Concrete counterexamples break
  the "colder is safer" premise: the relation budget depends on cache size,
  counter resets, `deep_true` order, tie filters, deferred-queue order and
  diagnostic collection epochs.
  - **Next:** D1-S1, a static refinement split as an opt-in. It measures the
    real wall against the default and Go-N, and how often the output differs
    from the default across the corpus (as D0-1A did).
  - Certification waits on that data.
- **2026-11-04, S1a after Astra's fixes:** exact validation at demand costs
  most of the gain. Loader CPU is 344 ms off, 318 ms on, and was 275 ms
  before the fix.
  - **ttfd:** not separable from S1a off in noisy medians; loader-time
    minimums show about 10–25 ms.
  - **Correctness:** parity full OK; protocol byte-identical in 18
    scenarios; disk-change tests fail without the fix.
  - **Lesson:** while every uncached lookup must be made again serially,
    answer-level reuse can't win. The gap needs the loader off the critical
    path.
  - **Next:**
    - Astra re-review, then merge (mainly for the S0 proof tools);
    - **loader design v2**: parallel discovery with a transactional
      snapshot, answering Astra's findings 1–7, plus binding in the parse
      workers.
- **2026-11-04, loader design v2** (`design/loader-v2.md`): half of the loader thread's time is kernel time; about 28k OS calls take 90–98 ms on one thread and 14 ms on 15. L1 puts a frozen per-load concurrent OS view V under all snapshot caches, filled by workers, with the serial loader unchanged (est. ttfd 265–300 ms against Go 283–295). L2 binds in the workers (−25 to −30 ms). Astra review next.
- **2026-11-04, Astra reviewed loader v2:** L1 and L2 go with changes, L3
  and L4 rework (`reviews/2026-11-04-astra-loader-v2.md`).
  - **Next:** L1 as an **opt-in** (`GOPORT_LOAD_VIEW=1`), with V placed under
    `OverlayFS.host` covering file-handle reads and the referenced-config
    registry, detached after the load, with exact OS-result semantics and
    the corrected measurements.
  - **The LSP default** depends on Chad's decision about the
    concurrent-change contract.

## Chad's decision (2026-11-04): LSP load view default

**Default with recovery.** L1 (`GOPORT_LOAD_VIEW`) becomes the LSP default
once a **reconciliation step** is built and tested.
- **The step:** right after watch registration, re-check the view's
  observations (including negative ones) against the disk. If anything
  changed, trigger a normal reload, as a watch event would.
- **The guarantee:** a change during the load window is never lost
  permanently.
- **Until then** L1 stays opt-in. The reconciliation step needs its own Astra
  review.
- **2026-11-04, S1a merged** (`b60c04700`): first-load worker answers go through exact validation at demand. The gain is small; the main value is the S0 proof tools (`GOPORT_LOAD_DUMP`, `GOPORT_LOAD_TIMES`, strict `dump-sections.py`, `load-dump-session.mjs`). Pre-existing: keyed loads don't adopt accepted package.json lookups into `InfoCache`, so it varies with timing (not observable); this is handed to the L1 lane.
- **2026-11-04, quality dashboard merged** (`c846e7a76`, Chad: no MI). See
  `quality.md`.
  - **Behavior switches:** main 30 → head 34 (+`TSC_RS_EFFECT`,
    `GOPORT_REUSE_SYNTHETIC`, `GOPORT_PLAIN_EARLY_EMIT`,
    `GOPORT_FIRST_LOAD_AHEAD`).
  - **Diagnostic, verification and test env names:** 7 → 22.
  - **About 44% of net production growth is verification and
    instrumentation.** Complexity and duplication are flat apart from the
    root loader and emit functions. 4 new clippy warnings.
  - **Escape-hatch rules added to the brief.**
  - **Planned cleanup lane, after timing window 2 confirms the gains:**
    - retire `GOPORT_REUSE_SYNTHETIC`, `GOPORT_PLAIN_EARLY_EMIT` and
      `GOPORT_FIRST_LOAD_AHEAD` (keep the old paths only if a measured reason
      exists);
    - move A1's `GOPORT_VERIFY_REUSE*` child hooks and the load-dump and
      phase-timer env names behind a `diagnostics` cargo feature;
    - de-duplicate the verifier copies of `trackingvfs` and `cachedvfs`;
    - split `process_all_program_files` and `create_program`;
    - fix the 4 clippy warnings.
- **2026-11-04, Chad: no old code behind switches.** "This is effectively a
  rewrite, so it doesn't make sense that we would put old code behind
  switches." This replaces the rollout-aid rule (brief: "Code quality: no old
  paths"). Go is the oracle and git holds the old code, so a switch adds
  nothing but a second path.
  - **L1:** no `GOPORT_LOAD_VIEW`. The view and its reconciliation step
    replace the LSP's first-load path in one merge, with Astra reviewing
    both. "Default with recovery" stands; there is no opt-in phase.
  - **D1-S1** is a spike: `GOPORT_CERT_SPLIT=refine` stays on its branch. If
    D1 proceeds, the certified split replaces the FENNEL split outright once
    testing shows zero divergences.
  - **The cleanup lane starts now**, in three parts, so it doesn't collide
    with running lanes:
    - **C1 (now):** remove every old-path switch outside the loader and the
      checker partition, inherited ones included (emit, program reuse, ast,
      lib snapshots, incremental, memory). Move the verify, dump and timer env
      names behind a `diagnostics` cargo feature, de-duplicate the verifier
      copies of `trackingvfs` and `cachedvfs`, and fix the 4 clippy warnings.
      Product options (`TSC_RS_EFFECT`) and tuning knobs stay.
    - **C2 (after L1 merges):** the loader switches (`GOPORT_FIRST_LOAD_AHEAD`,
      `GOPORT_RESOLVE_AHEAD`, `GOPORT_EARLY_ROOTS`, `GOPORT_JOB_MAP`,
      `GOPORT_LOAD_COMMIT`/`NAMES`/`PREP`, `GOPORT_PREFETCH_MOVE_UP`,
      `GOPORT_MAPPED_PREFETCH`), and split `process_all_program_files` and
      `create_program`.
    - **C3 (after D1 decides):** `GOPORT_FAST*`.
  - **Gate later:** once C1 to C3 land, `quality.sh` fails on any new
    behavior switch.
- **2026-11-04, C1 round 1 rejected by Astra** (`reviews/2026-11-04-astra-c1.md`).
  - Missing goldens.
  - Verifiers gated only at the env read.
  - Pure `--noEmit` incremental build info differs from Go: an existing
    divergence, which C1 locked in by deleting the Go d.ts-walk path.
  - New brief rules: keep the Go-matching path, not just the one that ships;
    gate verifiers whole.
  - **Round 2** restores Go-identical `--noEmit` build info and measures its
    cost.
- **2026-11-04, C1 round 2 done** (`0a57fcb3d`, plus `5c3037699` for results). All Astra items fixed.
  - **`--noEmit` build info:** Go's d.ts walk is now the only path; the
    version skip is deleted. 12-step `--noEmit` goldens from the pinned Go
    oracle are in git.
  - **Cost:** +5.5% instructions on warm `--noEmit` rebuilds that reach wide
    closures (85-file project and hono); query and umami are flat.
  - **Backlog W1:** a faster Go-identical d.ts walk. Add warm `--noEmit` edit
    sequences to timing window 2, against Go.
  - **Parity:** 0 lost, apart from 4 deleted skip unit tests.
  - **Nondeterminism:** 5 hono LSP `references` items vary between tsc-rs
    runs; they went to ND1.
  - Astra re-review running.
- **2026-11-04, C1 merged** (`a518e3088`).
  - Astra's re-review: ACCEPT WITH FIXES, saved in the thread. Root fixed both
    items in `d50854483`:
    - the emit phase timers are zero-sized without `diagnostics`; `nm`
      finds no `phase_times` symbols in the release binary;
    - CI runs the diagnostics and test-hooks feature tests.
  - `base-current` now points to `runs/20261104-165110-full-a49fbd8059`,
    without the 7 deleted test names.
  - Astra's W1 ideas (no measured gain yet):
    - a single-file builder-signature entry that reuses the emitter but skips
      emit scheduling;
    - hash the declaration-text slice directly when there are no declaration
      diagnostics.
- **2026-11-04, D1-S1 done** (`d1-s1-report.md`).
  - **Gain gate:** the static refinement passes the 90% gate in all 10 cells
    (median 116% of Go-N's gain).
  - **Not yet safe:** zod drops a TS2344 at r8 and r16k, because Go's warm
    checker silently degrades a type in an earlier file.
  - **D1-S2 started:** instrument every type-degrading limit or cycle event
    (diagnostics feature, spike only), check the trigger catches every
    divergence in the identity matrix, cost the fallback granularity
    (whole process vs per group), design the finding-5 diagnostic
    collection, and propose validation. Astra reviews its design.
  - C3 waits for D1's outcome.
- **2026-11-04, Chad: drop L1's recovery and match Go.**
  - Astra rejected round 2 (`reviews/2026-11-04-astra-l1-r2.md`): 4 P1 and 3 P2,
    several of them caused by the recovery itself.
  - Go has no recovery (`project/session.go` `updateWatch`). It misses a change
    between its read and the watch ack, as the base serial loader does.
  - **L1 round 3:** delete the reconciliation, keep V as the only LSP load
    path, and compare V's observation-to-registration window with the base's.
  - This replaces the "Default with recovery" decision.
- **2026-11-04, ND1 merged.** The hono `0057` inlayHint and the five
  `references` flakes were ATA racing the requests (a network `npm` call);
  Go shares the race.
  - **Harness fix:** standard sessions turn ATA off
    (`disableAutomaticTypeAcquisition`), and every LSP session runs Go 4
    times, with the run count in the cache key. Go's own references order
    varies, from `ls/crossproject.go` goroutines.
  - **LSP tier** (`runs/nd1-lsp-g4`): 0 lost, 25 recovered, 6 newly
    Go-unstable. The one DIFF, `watch-narrow-umami-f12/0012`, is in the base
    too.
  - **Perf lead for timing window 2:** in hono-f12, the first request after
    12 didOpens takes 0.65–1.05 s in tsc-rs vs 0.12 s in Go. Measure it
    after L1.
- **2026-11-04, D1-S2 done; Astra: PROCEED WITH CHANGES**
  (`reviews/2026-11-04-astra-d1-s2.md`).
  - **Blockers:**
    - the virtual-count nested resets;
    - cycle-agreement identities;
    - transactional writes for the fallback.
  - **Any unexplained divergence blocks the flip**, id-order ones included.
  - **D1-I1 started:**
    - exact-mode routing chosen before speculation;
    - production refinement without env switches;
    - a write and stream barrier, with execve fallback;
    - limit monitors with the fixed counter;
    - finding-5 monitors.
  - **Next lanes:** I2 (cycle agreement, relation bound), then the
    validation campaign, then C3 deletion and the default flip.
  - **Separate small lane later:** Go shares diagnostic pointers where tsc-rs
    clones values (`ast/misc.rs:963`), so a mutation after collection is
    visible only in Go.
- **2026-11-04, L1 round 4** (`6a766283b`): Astra's three round-3 fixes are in,
  and parity quick is OK.
  - **Window, paired V − base, with matching timestamps:** material on umami
    (p90 +78 ms) and not material on hono. The cause is the workers'
    prefetch order.
  - **Measuring Go's own window next**, with an instrumented Go build. Go is
    the reference.
    - Not material against Go: merge L1.
    - Material against Go: choose between capping the prefetch age and
      bounding prefetch distance.
  - **pkgjson-overlay `rs-nondet`** (8 items under heavy load) predates L1.
    The delayed `workspace/diagnostic/refresh` arrives after the client moves
    on. Backlog: make the harness wait for it.
- **2026-11-04, L1 merged** (`efef52257`).
  - **Go's window** (instrumented Go build, `l1-load-view.md` "Go's window
    (round 5)"), umami, all kinds, paired against Go:

    | | p90 delta |
    |---|---|
    | V − Go | +170 ms |
    | base − Go | +264 ms |

  - **The base already missed Go's window, and by more.** V moves tsc-rs
    closer to Go, so L1 merges. Hono is not material.
  - **Neither fix option** (cap the prefetch age, bound the prefetch
    distance) would meet the criterion: the estimated p90 is still +142 ms.
  - **The remaining gap is architectural.** Go makes its OS calls as it
    parses and resolves; V's workers make most of them in the first third
    of the load, and the loader then works from those answers.
  - **Backlog:** race-window parity with Go belongs to a Go-like streaming
    loader (L2-era). It is a timing property: the output for a given disk
    state is unchanged.
  - **ttfd** at the L1 head: 280 ms against Go's 288 ms.
- **2026-11-04, Chad: partition-dependent output contract** (brief, "Goal").
  - **The rule:** byte-identical with Go c4, except where Go's own output
    varies with `--checkers N` (N in {1, 2, 3, 4, 6, 8, 12, 16}). There, the
    whole run must equal some Go-N run, and tsc-rs must be deterministic.
  - **Evidence (zod `packages/zod`):** JS tsc 6.0.3 reports 0 errors and Go c4
    reports 22, including 6 TS5115 and the TS2344 at
    `assignability.test.ts(149,61)`. Go `--checkers 16` reports fewer.
  - **D1 impact:**
    - Limit triggers may stop forcing a fallback, if validation shows that
      refine outputs always land in the Go-N set.
    - The fallback rate (31% of the parity set) should drop.
    - The trigger policy per class is decided from validation data before
      merge.
  - **Harness:** the parity CLI, corpus and fuzz tiers need a GO-PARTITION
    class: on a mismatch with Go c4, run Go at every N and accept an exact
    match to any of them.
  - **D2** (shared type store) becomes more plausible, but only for designs
    whose results can be shown to equal some Go-N.
- **2026-11-04, Chad: better than Go is allowed, with a mechanical referee.**
  Root had wrongly written "not allowed"; Chad corrected it.
  - **Referees Chad chose:**
    - **JS-AGREES:** on every point where tsc-rs differs from Go, JS tsc
      6.0.3 sides with tsc-rs. This covers diagnostics, the emitted files
      that differ, and the exit code.
    - **GO-BROKEN:** Go crashes, hangs or varies with itself; tsc-rs must be
      stable.
  - **Not chosen:** a reviewed allowlist. Any other difference fails.
  - The harness lane H1 implements both alongside GO-PARTITION.
- **2026-11-05, H1 merged.** The referees (GO-BROKEN, GO-PARTITION,
  JS-AGREES) are live in the CLI, fuzz and corpus tiers
  (`parity_referees.py`, self-test 96 checks).
  - **Head full run:** PARITY OK. The 9 former fuzz GO-UNSTABLE items are now
    GO-BROKEN.
  - **zod refine N=8 is JS-AGREES, not GO-PARTITION:** it equals none of Go's
    8 outputs. Its only difference from c4 is the TS2344 that JS lacks.
  - **Backlog (better than Go):** on 9 fuzz cases tsc-rs copies Go's panics
    on purpose (`strings: negative Repeat count`, nil pointer). Giving a real
    answer there would be GO-BROKEN, which is good.
  - **Review:** Astra will review H1 and H2 together once H2 lands.
- **2026-11-05, Astra on D1-I1: ACCEPT WITH FIXES as a base for I2**
  (`reviews/2026-11-05-astra-d1-i1.md`). The branch stays unmerged, so the
  shipped default stays exact, until the campaign passes. No switch.
  - **D1-I2a** (fallback and commit): in-process fallback from the immutable
    Program if a global-state audit allows it, otherwise exec with a
    preserved snapshot. One ordered stream/file log; commit failures go
    through the normal emit errors, not a restart; aliased sinks; no env
    marker; the identity tools compare exact order and reuse the H1 referees.
  - **D1-I2b** (monitors, in parallel, disjoint files): the publication race;
    T5b insert vs reuse plus epochs; Go's real count at the t3-server site
    and a compositional reset rule; cycle agreement (`CycleDisagree`); the
    relation-budget bound.
  - **Then:** an Astra review, the validation campaign, C3, and the policy
    decision per trigger (CountBound is the first candidate).
- **2026-11-05, H2 merged.** JS tsserver 6.0.3 is the LSP referee, driven
  over its own protocol (`vendor/lspdiff/tsserver.mjs`, `referee.mjs`).
  - **Calibrated kinds (JS = Go ≥ 90% on Go-equal answers):** prepareRename,
    definition, rename, references, diagnostic, documentHighlight. The rest
    have no JS referee.
  - **LSP GO-BROKEN** covers a Go crash or timeout, and Go's 4 runs
    disagreeing (it replaces GO-UNSTABLE).
  - **pkgjson-overlay:** ATA is now off (root, `7e8a0fd0f`). LSP tier rerun:
    0 lost, 35 GO-BROKEN, 2,566 MATCH.
  - Astra reviews H1 and H2 together next.
- **2026-11-05, H3 merged** (`b1268f00c`): all 12 of Astra's gate holes are
  closed.
  - **Negative controls:** the H1 self-test has 123 checks, of which 19 fail
    on the old code; the H2 self-test has 48, of which 15 fail.
  - **New behaviour:** content-hashed cache keys, input re-hash with ERROR on
    change, new bad items fail, a quick-subset check, request-identity
    alignment, and a deterministic `ata-overlay` scenario.
  - **Grandfather list:** `parity-grandfathered.json` lists 35 known fuzz
    failures by their exact failure.
  - **Hidden real issue found:** `fuzz/1:25496` takes 84 s in tsc-rs against
    10.7 s in Go, with the same output. The old 120 s retry overwrote the
    timeout. `base-current` now points to `h3-head-efef52257-full-warm`, which
    records the timeout; **lane P1** fixes the slowness.
  - New brief rule: never write into the shared inputs.
  - An Astra re-review of H3 is running; the interim rule stays until it
    accepts.
- **2026-11-05, Astra rejected H3** (`reviews/2026-11-05-astra-h3.md`).
  - **Root's errors, reverted:** moving `base-current` to the H3 run, and the
    `fuzz/1:25496` grandfather entry (written outside `items`, so it was
    ignored anyway). The base is back at `head-efef52257-full`, and 1:25496
    is an expected LOST until P1 merges.
  - **H4 fixes the remaining 12 findings:**
    - poisoned caches from mid-run input edits;
    - a timeout hidden by the crash shortcut;
    - known-bad base items must stay present and match exactly;
    - grandfather matching on structured evidence;
    - LSP element-field association;
    - tsserver body schemas;
    - a quick coverage manifest;
    - fuzz input mutation;
    - LSP crashes after the last answer;
    - request identity with method and params;
    - ATA completion barriers and settings in the key;
    - grandfather schema validation.
- **2026-11-05, P1 merged** (`a7615f4d1`).
  - **Cause:** `fuzz/1:25496` was O(n⁴) in tsc-rs's own flow-skip key hashing
    (`flow_skip.rs` `chain_key`/`names_key` rehashed every prefix). Go is O(n³)
    on the same emit-time re-check pattern.
  - **Fix:** `ChainHash` builds every prefix key in one pass.
  - **Result:** 43.6 s → 2.2 s, against Go's 4.6 s. On projects, icount is
    within ±0.03% single-threaded.
  - **Parity:** full PARITY OK. `base-current` now points to
    `runs/p1-head-a8392a002-full`.
  - **Not counted by the compare:** `fuzz/1:5391` is a DIFF, but its base
    status is GO-UNSTABLE, so it's ignored. That gap belongs to H4 finding 3.
  - **Still timing out on both sides:** the 26 grandfathered deep-nesting
    cases. They don't share P1's cause; 1776 shows a `get_implied_constraint`
    explosion.
