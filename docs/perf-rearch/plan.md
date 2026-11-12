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
- **2026-11-05, H4 done** (gate round 2; branch not merged yet). All 12 H3
  findings are fixed.
  - **New mechanisms:**
    - read-only rsync snapshots watched with inotify;
    - staged caches;
    - a structured grandfather file with a regen tool;
    - a quick manifest read from the accepted ref;
    - a mandatory LSP `session end` item;
    - request identity by method and params;
    - ATA barriers.
  - **Results:** full has 1 lost, `lsp/hono-f12-p6/0476`. That is Go's
    references order race: all 4 of Go's new runs agreed by chance. Quick is
    OK.
  - **Root decision: accumulated Go evidence.** Every variant the pinned Go
    has produced on identical inputs and settings is legitimate, and the merge
    is monotone. Multi-project LSP sessions get 8 Go runs. H4 is implementing
    both.
  - **`fuzz/1:5391`** stays pinned as a known output DIFF.
  - **Backlog (compiler):** on `--traceResolution`, tsc-rs always prints
    "does not exist." where Go's default prints "does not exist according to
    earlier cached lookups.". The port may be missing a cached-lookup path.
- **2026-11-05, H4 merged.** Full gate: PARITY OK, 0 lost
  (`runs/h4-p1-a8392a002-full-3`), now `base-current`.
  - **Accumulated Go evidence:** an answer-variant record per key material.
    The merge is monotone; base logs are imported only when their key matches.
  - **Go runs:** 8 on the multi-project sessions (hono and query).
  - **`fuzz/1:5391`:** pinned as a known output DIFF.
  - **Interim rule:** stays until Astra accepts the gate.
- **2026-11-05, H4 round 3 merged.** Astra's six H4 findings are fixed.
  - **Fixes:**
    - the watcher stops, joins and drains before it answers;
    - leftover LSP stdout fails the session;
    - only clean, verified Go runs add evidence, with provenance per variant;
    - the H3 imports are removed;
    - the record schema is strict;
    - the normalizer fingerprint is complete;
    - inotify construction failures no longer leak.
  - **Result:** 0476 is still GO-BROKEN on 16 fresh verified Go runs.
  - **Full gate:** OK, 0 lost (`runs/h4r3-a8392a002-full`), now
    `base-current`.
  - **Next:** a focused Astra recheck, then the interim rule is dropped.
- **2026-11-05, gate accepted.** Astra's focused recheck closed findings 1
  and 3–6.
  - **Root fixed the last two gaps** (`22c967e4f`):
    - **No watch, no run:** a fuzz/corpus run whose inotify watch can't be
      built now fails closed as a harness ERROR.
    - **Real EOF:** an LSP session is unclean unless stdout really reaches
      EOF.
  - **Gate after the fixes:**
    - The full run (`runs/recheck-a8392a002-full`) lost only
      `watch-umami-f12/0012`. The record had been reset by the `client.mjs`
      edit.
    - After two `--go-refill` fills, the LSP tier lost 0
      (`runs/recheck-a8392a002-lsp`).
  - **The interim rule is dropped.** The brief now says to add refills after
    any lspdiff edit.
- **2026-11-05/06: WSL VM power-offs.** Clean shutdowns started from the
  Windows side cancelled D1-I3 and W2. D1-I3 was resumed from its worktree,
  and W2's resumable unit `w2-resume.service` is running again.
- **W2 partial results** (in `~/.cache/perf-rearch/window2/run/gap.md`):
  - CLI emit and emit-js: 0.47–0.61× Go.
  - Warm incremental `--noEmit` after an edit: 0.50–0.58× Go.
  - **hono 12-open first request: 0.536 s against Go's 0.120 s (4.5×
    slower) with less CPU.** Lane P2 is investigating. Most likely the opens
    or the project loads run serially.
- **2026-11-06, P2 merged: the open12 gap is not real** (`experiments/p2-open12.md`).
  - **Cause:** each slow W2 run was the first run 7–33 s after a VM boot.
  - **Gated and warm:** tsc-rs 106.7 ms against Go's 118.4 ms (0.90×); umami
    ttfd 292 ms against Go's 302 ms.
  - **Tooling fix:** `bench.py` waits 300 s after a VM boot and records the
    uptime.
  - **Candidate, not needed for "≤ Go":** a parallel BFS level of project
    loads (Go `projectcollectionbuilder.go:1086`) would save 30–40 ms when
    opening `src/index.ts`. It would need an LS threading redesign.
  - **Host-cold disk:** one session lost about 35 ms on the spec load. Not
    reproducible on demand.
- **2026-11-06, D1-I3 done** (branch `t3/checker-split-review-fixes`,
  candidate `3d65c3bc5`). All of Astra's I2 findings are fixed.
  - **Fresh files** exclude globally exposed modules (scripts, augmentations,
    UMD) and everything they reach. t3-server still commits.
  - **Bound tool:** it fails on a bad run, and its `--go-count` mode compares
    against an instrumented pinned Go. Go's real peaks equal Rust c4's, and
    the margin to 5M is 17%.
  - **Diagnostics builds** hash the protected state at each attempt and pool
    creation.
  - **Cycles:** synthetic node identities are rejected, and end-to-end tests
    were added.
  - **Identity manifest:** inputs are frozen.
  - **Umami:** the +3.8% doesn't reproduce (10-run A/B: I1 483, I2 477, I3
    482 ms).
  - **New D1 bug fixed** (`3d65c3bc5`): on `fuzz/1:16682` the certified path
    lost Go's JS output written before a panic.
  - **Gates:**
    - Parity full OK.
    - Identity 66/66.
    - Wall time vs Go: t3-server 0.27×, umami 0.36×, vscode 0.33×.
- **2026-11-06, `fix/aliased-output-order` merged into perf/first-principles.**
  - **Fix:** outputs that alias one file are written in program order, which
    removes tsc-rs nondeterminism on the shipped c4 path. Go is GO-BROKEN
    there: 8 hashes in 8 runs.
  - **Parity:** quick OK. In the full run, cli, tests and fuzz were clean;
    2 LSP items were rs-nondet under host load, and the LSP rerun was clean.
  - **Backlog (harness):** the `watch-ts-pattern-f12` session is timing-racy
    on both sides: Go varies (0016, 0018), and tsc-rs was rs-nondet under
    load. Add event barriers.
- **2026-11-06, D1-I3 round 2 done; `fix/aliased-output-dirlinks` merged into
  perf/first-principles.**
  - **Alias keys:** resolved through every symlink, dangling ones included,
    plus the missing path tail. Astra's dangling-directory repro gives 20/20
    identical runs in every mode.
  - **Windows:** file identity uses `winapi-util` (vol serial + file index).
    The `cfg(windows)` test has only been type-checked.
  - **Identity manifest:** child env, CPU and cgroup settings are frozen, and
    traces prove the split and fallback coverage.
  - **D1 `24f16ee88`:**
    - parity full OK;
    - IDENTITY OK, 66/66: 20 items split, 13 forced fallbacks proven;
    - 0 raw divergences with fallbacks off.
  - **Next:** a focused Astra recheck, then the validation campaign.
- **2026-11-06, D1-I3 round 3; `fix/output-layout-serial` merged.**
  - **Ordinary layout:** no symlink or reparse point, ancestors are dirs,
    existing outputs are regular with nlink 1, no case-fold collision, and
    every read succeeds. Ordinary layouts write in parallel; any other layout
    writes everything serially in program order.
  - **Smaller:** `output_aliases.rs` went 501 → 363 lines.
  - **Determinism:** all repros give 20/20 in every mode.
  - **No slowdown:** umami icount −0.1 to −0.3%.
  - **Identity:** mirrors the compiler's core count (cgroup v1/v2 through
    mountinfo). The coverage checks fail closed, and 9 mutation controls fail.
  - **D1 `521601cfe`:** parity full OK; IDENTITY OK, 66/66.
  - **Next:** a narrow Astra recheck, then the campaign.
- **2026-11-06, D1-I3 round 4; `fix/output-layout-prefix` merged.**
  - **Layout check:** an output that is another output's ancestor, or a
    collision under Unicode lower *or* upper case, makes the layout
    non-ordinary. Every repro gives 20/20 in every mode.
  - **Identity:** `compiler_cores` matches the compiler on 33 parse cases
    under a real CPU quota.
  - **D1 `3178750e6`:** parity full OK, IDENTITY OK.
- **Decision:** no further recheck round on the layout rule. Each fix moves
  layouts toward the serial path, so a misclassification can only cost
  parallelism, never correctness. The campaign adds a randomized output-layout
  fuzzer instead, so the campaign tests this rule too.
- **Validation campaign started:** lane CV1 writes the spec and builds the
  tools; CV2 runs it.
- **2026-11-06, CV1 done** (`design/d1-campaign.md`; tools in
  `scripts/perf-rearch/campaign/` on `t3/checker-split-campaign`).
  - **5% smoke on `3178750e6`:** 0 failures, no nondeterminism. 2 GO-BROKEN
    layout cases: Go's own runs vary, and tsc-rs's single result is one Go
    produced.
  - **Raw divergences caught by the fallback:**
    - real **rxjs** holdout: `ConstraintCut`, 9 TS2345 and 1 TS2322 with no
      referee;
    - a generated case: `InstantiationDepth`, +8 TS5114.

    Direct evidence that both classes must keep falling back.
  - **Mutants:** 27 of 32 detected.
    - **Undetected:** cycle-key coarsening, and 4 triggers with no witness
      input (`ConstraintDepth`, `GlobalDiagnosticVariant`, `RelationBudget`,
      `TemplateLiteralSize`).
    - **ASCII-only case folding:** needs a case-insensitive file system.
  - **Cost gaps:**
    - vscode forced fallback at the commit point: 9.38 s against Go's
      8.64 s.
    - Certified RSS is 6.3 GB against c4's 5.4 GB, so under a memory cap at
      c4's peak, certified is killed (nonzero exit) where c4 completes.
  - **Holdout configs to fix before freezing:** date-fns (checks 0 files), nest
    and vue-core (composite).
  - **Estimate:** about 19 machine-hours, 5–6 h wall here.
- **Root decisions:**
  - **D1-I4 (compiler):**
    - a memory-aware N policy: cgroup `memory.max` and MemAvailable, routing
      exact when there's no headroom;
    - evaluate the end-of-run bounds (gap and relation bound, cycle agreement)
      incrementally, so fires happen as early as possible;
    - measure the wall time of natural fallbacks.
  - **Timing rule for fallbacks:** natural runs, natural fallbacks included,
    must be ≤ Go on every input. A forced late fallback is a synthetic worst
    case: reported, not gating. Chad may override.
  - **CV2-prep (campaign, in parallel):** witnesses for the 4 triggers and for
    cycle-key coarsening, and fixed holdout configs. Then CV2 freezes on the
    D1-I4 candidate.
- **2026-11-06, two decisions from Chad:**
  - **Timing:** forced late fallbacks are report-only.
  - **Windows disk:** `C:\tsrs-scratch` is approved for the case-insensitive
    file-system checks only. It is cleaned up afterwards.
- **2026-11-06, CV2-prep done** (`t3/checker-split-campaign` `7ae1f2f28`).
  - **New witnesses:**
    - a warm-count divergence when all count triggers are off;
    - `layout-ascii` nondeterminism on the case-insensitive `/mnt/c` folder;
    - a relation-budget overflow;
    - a template-literal size case.
  - **Holdouts:** date-fns, nest and vue-core now reach the certified route.
  - **Cost gates:** natural runs ≤ Go; the memory cap at c4's peak must
    complete.
  - **Self-tests:** 45/45.
  - **Detection only by evidence or coverage (unobservable in output on every
    input found):** the cycle triggers (TypeScript marks every stack
    participant), the collection triggers, and the conservative bound
    variants. That informs the future policy decision; every fallback stays.
  - **`GlobalDiagnosticVariant`** is excluded as unreachable by audit, with
    its reason recorded.
  - **`ConstraintDepth` has no witness.** Root decision: if CV2 can't build
    one, record it as unreachable-by-search with the attempts listed, for
    Astra's campaign review to accept or reject. Its fallback stays on, so an
    unwitnessed trigger only falls back more often.
  - **Estimate:** about 20.6 machine-hours, 5.5–6.5 h wall here.
  - **CV2 starts after D1-I4 lands** and the mutant patch is rebased.
- **2026-11-06, D1-I4 done** (branch `t3/memory-aware-fallback`, head
  `4004b75e8`, code `e42526dbb`; the campaign branch is merged in).
  - **Memory-aware N:** the largest n whose estimated peak plus 10% fits the
    budget, else exact. A runtime `MemoryBudget` guard falls back at 95% of
    the budget and releases pages before the exact attempt.
    - Calibration: tight on large programs, loose on small ones; type-fest is
      the 10× outlier, which the guard covers.
    - Every capped run (1.0×, 1.25× and 1.5× c4's peak) gives Go's bytes.
  - **Early fires:** bounds and cycle agreement are checked at file and job
    boundaries; the time from a fire to the fallback dropped from 50–100 ms to
    about 2 ms.
  - **Natural fallbacks, all under Go:** zod 0.49×, react-hook-form 0.63×,
    ts-pattern tests 0.57×, rxjs 0.58×.
  - **Forced late fallback** (vscode, report-only): +4.5% against Go.
  - **Gates:** identity 66/66. Parity full lost 3 `watch-ts-pattern-f12`
    items as rs-nondet at load 31; the LSP rerun at low load lost 0.
  - **Next:**
    - CV2: rebase the mutant patch, add mutants for `MemoryBudget` and the
      early fires, freeze, run.
    - An Astra review of D1-I4, in parallel.
    - ND2: the `watch-ts-pattern-f12` rs-nondet. It's the third time; tsc-rs
      or the harness is racy under load.
- **2026-11-06, D1-I4 round 2** (head `3a692d1a1`, code `4c420eb57`).
  - **Memory-fire race fixed:** the reason is now part of the state
    (`FALLING_BACK_ON_MEMORY`), and a barrier test fails on the old order.
  - **Gates:** tests 2,870 pass; parity quick OK.
  - **Known limit:** forced n=16 at exactly 1.0× c4's peak is killed in the
    exact attempt (about 270 MiB is left behind by the dropped attempt). It
    exits nonzero and never writes wrong bytes. At 1.1× it completes, and an
    unforced run at 1.0× picks n=4 and completes.
  - **Backlog:** give the refined checkers their own jemalloc arenas, which
    would make that leftover go away.
  - **CV2 started on this candidate.** The forced 1.0× case is report-only,
    checked only for nonzero exit and no wrong output; the unforced 1.0× case
    gates.
- **2026-11-06, ND2 merged.** The `watch-ts-pattern-f12` flake was in the
  harness: the scenario waits ended before ATA's npm call finished, and Go has
  the same race.
  - **Fix:** a logging npm shim, plus one wait for every step: server quiet,
    and no npm activity for 1.5 s counted from the start of the wait.
  - **Repro:** under load with npm randomly slowed, tsc-rs runs are 32/32
    identical (before: 4 variants).
  - **Side effect:** the base's Go-race watch items are now MATCH.
  - **Cost:** about 100 s more on the LSP tier. Root accepted the 1.5 s
    floor.
  - **Parity:** full OK (`runs/nd2-e92f96b39-full`), now `base-current`.
- **2026-11-06, CV2: CAMPAIGN OK** on the D1 candidate (head `3a692d1a1`,
  code `4c420eb57`). Run dir `~/.cache/perf-rearch/campaign/cv2-4c420eb57`
  (+ `-supp`); campaign tools at `t3/checker-split-campaign` `085b4814a`.
  - **Inputs:**

    | part | items | result |
    |---|---|---|
    | parity66 × 7 CPU settings | 462 | all match Go |
    | corpus | 3,441 | all match Go |
    | holdouts | 80 | all match Go |
    | gen | 656 | match, GO-BROKEN layouts, or 1 JS-AGREES |

  - **Nondeterminism:** none in tsc-rs.
  - **Mutants:** all detected; `commit-early` only in combination with
    `early-skip`.
  - **Raw divergences, every one caught by a fallback:** 24 reported-limit,
    8 count-bound and 4 silent-limit.
  - **Routes:** commit 4,030, fallback 225, exact 280, pass-through 104.
  - **Cost:** every natural run ≤ Go.

    | project | certified vs Go |
    |---|---|
    | vscode | 0.38× |
    | t3-server | 0.31× |
    | umami | 0.45× |
    | zod (natural fallback) | 0.81× |

    Memory caps give Go's bytes. Forced n16 at 1.0× is killed (report-only).
  - **Needs reviewer acceptance:**
    - `GlobalDiagnosticVariant`: unreachable by audit and by search;
    - `commit-early`: detected only in combination.
  - **Parity full** on the candidate: OK.
  - **Next:** Astra's campaign acceptance review. If it passes, merge D1 into
    perf/first-principles: the certified split becomes the default, per
    Chad's "default if tests pass". C3 then deletes fast mode.
- **2026-11-06, Chad's decisions:**
  1. **D1 is shelved.** The certified split is not merged; its branches are
     kept (`t3/memory-aware-fallback`, `t3/checker-split-campaign`) and CV3
     was cancelled.
     - **Why:** "≤ Go" is already met without it, and D1's safety and
        validation upkeep is large, including a re-audit on every pin bump.
     - **What stays:** the shipped-path fixes it produced:
        `fix/aliased-output-order`, `-dirlinks`, `-layout-serial` and
        `-layout-prefix`.
  2. **Review cap: 3 rounds per lane.** After that, root decides (take, drop
     or redesign) and tells Chad.
  3. **Windows testing runs locally.** WSL interop works, and the Windows
     side already has Rust 1.99.1 (msvc), VS 2022 BuildTools (MSVC
     14.44.35207, SDK 10.0.26100) and Git. Lane WIN builds and tests there.
- **Next:**
  - **C2:** remove the loader switches; L1 is merged.
  - **C3:** remove fast mode, stealing, lazy members and the fast "never"
    order. These are non-default paths, and lazy members isn't
    Go-identical. The no-old-paths rule applies now that D1 won't replace
    them.
  - **WIN:** Windows build, the `cfg(windows)` tests and the layout repros on
    NTFS.
  - **W2:** keeps running.
- **2026-11-06, C3 merged** (`t3/delete-fast-checker-mode`, head
  `bf0993ed3`).
  - **Removed:** fast mode, lazy members, the never order, work stealing,
    manual arenas, the 7 `GOPORT_FAST*`/`LAZY_MEMBERS` switches and `--fast`.
  - **Size and counts:** −2,442 lines; shipped escape hatches 40 → 33;
    duplication 3.64% → 3.60%; single-threaded icount −0.04 to −0.06%.
  - **Review:** Astra accepted with one fix. Two default-path tests moved into
    `checker_p24`.
  - **Parity:** OK against the base minus the 10 deleted tests, with the
    renames recorded.
- **2026-11-06, WIN merged** (`fix/windows-layout`).
  - **Build and tests on Windows:** tsgo.exe builds in 1m49s (msvc). The
    tests pass after fixes to 3 test files that didn't build on Windows,
    including `go_baselines` (1,735 passed).
  - **Real Windows bug fixed:** with 8.3 short names (`AB00CD~1.js` next to
    `ab00cdefgh.js`), one run in 30 under load gave a second result. A
    `~<digit>` name beside another output now makes the layout non-ordinary.
  - **Layout repros:** 20 runs per case on Windows and Linux. tsc-rs gives
    one result everywhere; Go varies (up to 20 results in 20 runs).
  - **Go comparison:** `win-go-compare.sh` against Go's tsc.exe cross-built
    from the pin gives the same as Go on all 80 files.
  - **Known cost:** any reparse point (OneDrive placeholders included) makes
    the layout serial. That costs speed only.
  - **Parity:** quick OK. A full run follows once C2 merges, then the base
    gets refreshed.
- **2026-11-07, C2 merged** (`0bf6ff2a7`). The VM was off overnight (20:48 to
  07:46); the W2 thread was cancelled again, and its service resumed.
  - **Removed:** all 9 loader switches; each kept its default path, which was
    also the faster one. The loader diagnostics (dump, times, stats, test
    hooks) are behind the `diagnostics` feature.
  - **Split:** `process_all_program_files` complexity went 45 → 1 and
    `create_program` 32 → 6.
  - **Counts:** shipped env names 40 → 29. icount is flat, and `nm` shows 0
    diagnostics symbols.
  - **Parity:** the lane's full run lost 0, apart from one renamed test.
  - **ttfd:** not measured; there was no idle window.
  - **Next:** a full parity run on the combined head (C2 + C3 + WIN) to make
    the new base, ttfd on umami, and Astra's C2 review (running).
- **2026-11-07, paused at Chad's request (WSL cleanup).** Status at the pause:
  - **Frozen or stopped:**
    - `head-gate-0bf6ff2a7` and the `c2f-*` units are frozen in
      `app-batch.slice`. After the thaw, treat timeouts as suspect and re-run
      those cases.
    - `w2-resume.service` is stopped.
  - **C2 follow-up:** branch `t3/c2-followup`, head `5e463bf9d`, code
    complete.
    - Commits: `b8b23e1a0` gates the debug counts, `675038068` fixes the
      `quality.py` cfg boundary, `5e463bf9d` dedups the wait loops.
    - In flight: tests, parity quick, and the base and head goport builds.
      Logs are in `~/.cache/perf-rearch/c2f/`.
    - Still to do after the thaw: nm and disassembly checks, icount
      (CLI and LSP), clippy, and removing `../ts-rust-c2fbase`.
- **2026-11-08, resumed after the pause.** MS-A2 was rebooted with a new NVMe
  (`/` has about 1 TB free), which killed the frozen units.
  - **Combined-head gate:** restarted from scratch as
    `head-gate-0bf6ff2a7-r2`. The killed run was moved aside to
    `runs/head-0bf6ff2a7-full-killed`.
  - **C2 follow-up:** the coordinator resumed it.
  - **Timing window 2: done** (`experiments/window2.md`).
    - **One cell is over Go:** `build-warm query`, a no-op `-b`, 8 ms vs
      7 ms. Lane P3 is on it.
    - **Every other cell is at or under Go:**

      | cell | head vs Go |
      |---|---|
      | CLI | 0.37–0.67× |
      | `-b` | 0.22–0.63× |
      | incr-edit | 0.48–0.74× |
      | open12 | 0.88× |
      | ttfd | 0.93× |
      | concurrent typing | 0.28–0.69× |
      | probes | 0.13–0.26× |
- **2026-11-08, combined-head gate** (`runs/head-0bf6ff2a7-full`): 1 lost, and
  13 not run on purpose (the C3 deletions and moves, and the C2 rename).
  - **The lost item:** `watch-ts-pattern-f12/0018`. tsc-rs gives 1,147 items
    against Go's cached 1,150.
    - Every binary now gives 1,147, including `427ff93d5`, which had matched
      before.
    - A Go refill also gives 1,147 on all 4 runs.
  - **Cause:** a non-frozen input. The watch scenarios run ATA with the real
    npm (`types-registry@latest`) and the shared `~/.cache/typescript`, and
    neither is in the cache key.
  - **Consequence:** the H4 variant record merged answers from two different
    environments as "Go varies", which is the wrong widening Astra warned
    about.
  - **Lane H5:** make ATA hermetic in every LSP scenario (a frozen typings
    snapshot, a stand-in npm, no network), key all external inputs, and fail
    closed on unkeyed access. Then run a fresh full gate, which becomes the
    new base.
- **2026-11-09, P3 merged** (`8a776c935`, branch `t3/perf-rearch`).
  - **Cause:** a no-op `tsgo -b` started 3 process images: the launcher
    (THP is off on this VM), a worker, and a re-exec just to set
    `_RJEM_MALLOC_CONF`. The shipped `build-release.sh` builds that setting
    in; cargo builds didn't.
  - **Fix:** `.cargo/config.toml` `[env]` builds the jemalloc config into
    every build, the re-exec is gone, and a compile-time check guards it.
  - **Result:** build-warm query is 6.77 ms against Go's 8.30 ms (0.82×).
    Every other startup cell is ≤ Go.
  - **Parity:** full run has 0 lost, apart from the 13 intentional not-run
    names and the known 0018 environmental item.
  - **Timing window 2 is now ≤ Go in every cell.**
  - Merged without an Astra round: small, measured, and no output change.
- **2026-11-09, H5 merged.** Every LSP server now runs with:
  - a private `HOME`/XDG/`TMPDIR` and no inherited environment;
  - no network;
  - an offline stand-in npm installing from a frozen typings store
    (`ata-typings.json`, hash `d339a2b67e79850a`), with the hash in each
    key;
  - tripwires on `~/.cache/typescript`, `~/.npm` and `~/.config`;
  - node and ancestor dirs keyed.
  
  The old variant records are never read again.
  - **Gate** (`runs/h5-head-0bf6ff2a7-full`): 0 lost, every LSP item good,
    and only the 13 intentional not-run names. It is now `base-current`.
  - Merged without an Astra round: it only makes the keys stricter, and its
    negative controls fail as expected.
- **2026-11-09, ttfd on the combined head** (`5c13a7657`, goport, umami,
  idle-gated, interleaved):

  | | head | Go | timing-window-2 head |
  |---|---|---|---|
  | 7 rounds | 269.6 ms | 267.7 ms | 267.3 ms |
  | 15 rounds | 271.5 ms | 276.1 ms | — |

  - **Verdict:** a tie with Go within about 2%, and no regression from C2,
    C3 or P3.
  - **Why it's close:** head's ttfd range is narrower and it uses 42% of
    Go's CPU. Its program update is 9–27 ms slower than Go's, and the
    faster diagnostics make that up.
  - **Margin candidate (not needed for "≤ Go", not scheduled):** Go-like
    streaming and a parallel BFS level of project loads (P2 notes, L1
    backlog).
  - Data: `~/.cache/perf-rearch/ttfd-5c13a7657{,-b}/`.
- **2026-11-09, C2 follow-up merged** (`t3/c2-followup` at `9928b7b41`, merge
  `e81a14807`). Astra's two P2s from the C2 review are fixed:
  - the resolve-ahead and prefetch debug counts and the loader's wait clocks
    are diagnostics-only (`nm`: 0 `PrefetchStats` symbols; `wait_or_take` reads
    no clock);
  - `quality.py` parses cfg'd item ends with nesting (`quality_test.py`).

  icount on the LSP keyed load: equal medians. The lane's 1 lost item
  (`watch-ts-pattern-f12/0018`) was the pre-H5 environment, and it is MATCH
  under H5.
  - **Gate** (`runs/head-e81a14807-full-combined`): **PARITY OK**, 0 lost,
    0 not run, 0 new items not good, 36 grandfathered. It is now
    `base-current`. Tiers: cli 170 MATCH, lsp 2,629 MATCH + 29 GO-BROKEN,
    fuzz as grandfathered, corpus 12,727 MATCH, tests 139,908 PASS.
  - **How the run was made:** the first build hit a nightly rustc ICE
    (`verify_ich`, incremental hash of `tikv_jemalloc_ctl::keys::Access`), a
    stale incremental cache after the hard reboot. The tests tier failed to
    build the same way. I deleted `target/release/incremental` and reran
    tests alone (`runs/head-e81a14807-tests`). Then I hard-linked the full
    run's other tiers into `-combined` and merged it. The ICE files are in
    `~/.cache/perf-rearch/ice/`.
  - Merged without another Astra round: the round fixes Astra's own findings,
    and the evidence above checks each one.
  - **The perf phase is complete:** every window-2 cell is ≤ Go and ttfd ties
    with Go.

## Code-quality phase (Chad: "Proceed", 2026-11-09)

- Baseline report `q0-f853c4dea`: 713 functions over cognitive 25 (218 over
  50), duplication 3.58% (698 blocks), 21 env names read in shipped code (8
  switches), clippy 1 under the repo config and 2,120 under `--force-warn
  clippy::all`.
- **Go comparison:** our port-bloated giants are 1.7–2.3× their Go
  counterparts in lines (`structured_type_related_to_worker` 1,265 vs 641,
  `check_grammar_modifiers` 817 vs 356, `resolve_external_module` 550 vs
  319, `get_up_to_date_status` 534 vs 274). Go's `getCompletionData` (1,273)
  is longer than ours (539). Rule: Go-relative for ported code, the limit 25
  for our own (brief, "Code-quality phase").
- **Wave 1:**
  - **Q0:** the Go-relative ranking in `quality.sh`.
  - **Q3:** the shipped env names.
  - **Q4:** the mechanical clippy cleanup.
- **Wave 2,** after Q0 and Q4 merge: area lanes (checker, frontend and
  loader, LS, build/emit), each doing complexity and duplication on its own
  files.
- **Q0 merged** (`t3/go-complexity-ranking`, merge `65c26c8b2`). It is tooling
  only, it gates nothing, and its self-tests pass, so it went in without an
  Astra round. Ranking: `cleanup/q0-ranking.md`.
  - **Port bloat:** 160 targets (Δ ≥ 10, Σ 2,803); 68% of the Σ is in the
    checker. The giants are only 10–20% over Go in cognitive complexity.
  - **Own code:** 149 functions over 25 (Σ 2,389); Effect is 56% of that Σ.
  - **Root's decision on `gostd/`, `locale.rs`, the `json.rs` decoder and
    `filepath_clean`:** these port Go code outside the pin (the stdlib,
    x/text). They are treated as ported code, not as own code: their shape
    follows that Go, and they have the lowest priority in wave 2. They are
    not measured against that Go yet.
  - **Wave-2 lanes,** once Q4 merges:
    - checker A: `inference_p*`, `relater_p*`;
    - checker B: `checker_p01`–`p19`;
    - checker C: `checker_p20`–`p35`, `nodebuilder_impl_p*`,
      `emit_resolver_p*`, `services.rs`, and the rest of `checker/`;
    - frontend with execute/build/emit;
    - ls;
    - Effect.

    "Other" joins the lane that owns the nearest code.
- **Loop check (Chad asked, 2026-11-09).** Across 21 saved Astra reviews,
  no finding reopened because a later change undid an earlier fix. Two
  classes do recur:
  1. **`quality.py`'s cfg parsing.** Four fixes so far: C2's struct field,
     the C2 follow-up's item end, Q3's mod declaration, and Q3 review
     finding 3. Each closed one case and the next review found another.
     This is a heuristic with no end, and it fails open (it hides shipped
     reads). Decision:
     - Q3 reverts its mod-gating change, so the report fails safe.
     - A tooling lane (Q5, after Q3 merges) replaces the parser with
       rustc's own cfg-expanded source of the shipped build.
  2. **"Diagnostics not gated whole."** Five times: L1 r1, C1 r2, C2, Q3,
     and the C2 follow-up's motivation. Each time it is different code, and
     the fixes held, but every new lane makes the same mistake and only
     review catches it. Q5 adds a mechanical check on the same expanded
     source.

  D1-I3 (alias detection, three rounds of new cases) and H1–H4 (four rounds
  of false-good paths) were the same pattern earlier. Each ended with a
  redesign: the ordinary-layout rule (go serial when anything is unusual)
  and the H4 gate.
- **Q4 finished** (`t3/mechanical-clippy-cleanup`, head `6dae1c547`, not merged).
  - Forced `clippy::all` count 2,120 → 1,162; 61 lints locked in.
  - Parity quick OK, icount flat.
  - Clippy's own auto-fixes were wrong in 6 places, and the lane caught each
    one: the NaN branch of the float formatters, a wrong `manual_swap`, a
    test broken by `unnecessary_to_owned`, a comment dropped by
    `manual_find`, `blocks_in_conditions` not compiling in the parser, and
    code deleted by `while_let_loop`/`single_match`.
  - **Found a gate brittleness.** The 9 grandfathered Go-crash pins hash
    tsc-rs stderr, and that includes the Rust `file:line` of the panic site.
    Q4 kept the old lines with two commits and two module allows.
    - Root's fix is lane H6: normalize only that site line's number,
      re-pin, add negative controls.
    - After H6, Q4 round 2 reverts the line-keeping commits.
  - **Astra review running:** semantic equivalence of the rewrites, and
    whether the lock-in should live in one place (the list is copied into
    4 crate roots, so duplication went up). CI clippy runs on 1.95.0 and
    `quality.sh` on 1.99.1; Q5 aligns them.
  - **Merge order:** H6, then Q4 round 2, then Q3 (rebased on Q4; it
    removes Q4's allows on Q3's files), then Q5, then wave 2.
- **Q3 merged** (`t3/env-quality-cleanup` at `59a3d6218`, merge below).
  - Round 2 fixed Astra's findings 1 and 2: each dev bin's panic output is
    byte-identical again, pinned by tests, and the version-table checks are
    compiled only in debug and diagnostics builds (`nm`: 0 symbols).
  - Finding 3 (`quality.py` module gating) was reverted, so the report fails
    safe; Q5 replaces the parser.
  - Parity full `20261109-124325-full-42e3f94d` (release `11a8642a0`):
    PARITY OK, 0 lost, 0 not run.
  - The shipped binary reads 10 names, all documented in `quality.md` §8.
  - Merged without a third Astra round: the fixes are narrow, and each one
    is checked by a test and by `nm`.
- **H6 merged** (`t3/normalize-panic-site-lines`, merge `3282ab7d2`).
  - Crash pins now hash tsc-rs stderr with only the digits of the final
    `\t<file>.rs:<line>` replaced. The match is anchored to the end of
    stderr, the file is kept, and the grandfather format is v3.
  - Its 34 negative controls pass, and a real probe (one blank line above
    `go_repeat`) passes all 9 crash cases.
  - Gate `runs/h6-head-e81a14807-full`: PARITY OK, 0 lost. It is now
    `base-current`.
  - Merged before its Astra review (it is the one loosening, so it is
    reviewed in parallel, and root fixes forward) so that Q4 part B can
    revert its line-keeping commits.
  - Root fixed the `parity_referees_test.py` "lsp keys" failure that H6
    found (pre-existing since H5's `node` argument): `3ecee68ec`.
- **H6 review: ACCEPT WITH FIXES.**
  - **Finding:** a literal `<line>` collided with a normalized hash.
  - **Fix (root):** a domain-tagged hash, grandfather format v4, the negative
    controls, and a re-pin.
  - **Check:** only `stderrSite` and `evidence` changed in the 36 entries,
    and the shifted-line probe binary matches all 36.
  - Review: `reviews/2026-11-09-astra-h6.md`.
- **Q4 merged** (`t3/mechanical-clippy-cleanup` at `aaa626f7e`, merge
  `9c5eb8439`; Astra ACCEPT on round 1).
  - **Round 2:**
    - one lint policy in `[workspace.lints.clippy]`, with the 26 lints that
      still fire listed as allows (the wave-2 work list); every other
      `clippy::all` lint is now enforced in the lib crates (probe-checked
      61/61 per crate);
    - the line-keeping commits reverted after H6;
    - Q3's files cleaned;
    - wasm `say!` cfg fixed.
  - **Forced `clippy::all`:** 2,120 → 1,128. Duplication is unchanged at
    3.55%.
  - **Parity full** (`quality/q4/parity-fullB2`, hard-linked as
    `runs/q4-head-aaa626f7e-full`): PARITY OK, 0 lost, and the 9 crash pins
    match on their moved lines. It is now `base-current`.
    - The first full run lost `fuzz/1:17661` to tsc-rs's 60 s timeout at
      load 35–42. At load 2 the case takes about 6 s on both base and head,
      with equal icount, so this was the load, not the change.
  - **icount:** flat. umami emit-mt read +0.37% and then −0.30% on a recheck
    with 5 runs; that cell's spread is 0.43%.
- **Wave 2 begins** (shared lane brief: `cleanup/w2-brief.md`). Area targets
  at `62843b5a1`:
  - checker: 107 port targets, Σ Δ 1,775;
  - frontend: 14 + 14 own;
  - ls: 13 + 2;
  - execute/build/emit: 3 + 3;
  - effect: 85 own, Σ 1,312;
  - other: 12 + 33, `gostd`/`locale` last.

  Builds share one cargo slot and the gate allows one run at a time, so 3
  code lanes run beside Q5 at first:
  - **W2-CA:** checker `relater_p*`, `inference_p*`, `flow_p*`,
    `flow_skip.rs`, `nameresolver.rs`, `utilities_p*`, `mapper.rs`;
  - **W2-CB:** checker `checker_p01`–`p19`, `grammarchecks_p*`, `jsx_p*`;
  - **W2-EF:** `effect/`.

  Next, as slots free: W2-CC (the rest of checker), W2-FE (frontend,
  execute/build/emit, the "other" ports) and W2-LS.
- **W2-CA finished** (`t3/checker-code-quality`, head `e587325f7`, 39 commits).
  - **Own code:** 6/6 targets now ≤ 25.
  - **Port bloat:** 14/23 targets now below Δ10. The rest is metric residue:
    a Go tagless `switch` against a Rust else-if chain, and `core.IfElse`.
    The lane chose not to game the metric with an `if_else` helper or
    `match () {}`. Root agrees.
  - **Duplication:** 3.55% → 3.51%.
  - **Parity full** (`w2-ca/parity-full-bdd395ffa`): PARITY OK.
  - **icount:** single-threaded cells within ±0.05%.
  - **Hot-body symbol sizes:** they hold.
  - **Astra review:** running.
  - Root removed the lane helper's worktree
    (`.claude/worktrees/agent-a9ba…`, branch `w2ca-flowskip`), after
    checking that all 7 of its commits had been cherry-picked.
- **W2-CC started** in CA's slot: the rest of `checker/`.
- **Q5 merged** (`t3/quality-expanded-source-audit`, merge `e84916b7f`).
  - **What it does:** `quality.sh` reads the shipped source from rustc's own
    cfg expansion (`qexpand`), on CI's toolchain, for the default, features
    and wasm builds. Every file is checked against rustc's dep-info. The
    hand-written cfg parser is gone, which ends the loop.
  - **Inventory:** 11 shipped env names, which agrees with §8.
  - **`quality.sh gating`** is the mechanical check that verification code
    is gated whole. Validated on old commits: it re-finds 4 of the 5 helpers
    from Astra's Q3 finding, and the C1 r2 `phase_times` leftovers.
  - **Repo-config clippy** now uses CI's 1.95.0; the forced audit stays on
    stable.
  - Tooling only, so it merged without an Astra round.
  - **Convention:** root adopts Q5's gating convention (brief, "Verification
    code is gated whole"). Wave-2 lanes must report `GATING OK`.
  - **Findings at head (11), fixed by lane Q6:**
    - the `program.rs` checker call log (7 items that ship);
    - `program/reuse_verify.rs`, which ships whole;
    - `dts_twin_check`'s branches in its callers;
    - 2 items that are dead in the default build (`synthetic_live_slot_count`,
      `registered_programs`).
- **W2-CA merged** after Astra's ACCEPT, with no findings
  (`reviews/2026-11-09-astra-w2ca.md`).
- **W2-CB finished** (`t3/checker-code-quality-1`, head `b74ac373a`).
  - **Targets:** all 39 port targets and both own targets are gone. Σ Δ of
    targets went 615 → 0; Σ positive Δ 1,225 → 679.
  - **Parity full** (`20261109-190643-full-3d2c1c7cba`): PARITY OK. An
    earlier full run had an ERROR because a session opened `~/.config` (the
    H5 tripwire). That session passed when run alone, and the full rerun
    was clean.
  - **icount:** single-threaded cells within ±0.012%.
  - **Root's decision:** CB added `if_else`/`or_else_*` helpers mirroring
    Go's `core.IfElse`/`OrElse` (61 uses). They are allowed only where Go
    calls those at that spot (Go evaluates both arguments too). CA declined
    to add the helper; the rule above covers both lanes. Recorded in
    `w2-brief.md` ("Go helper idioms") and sent to W2-CC. Astra checks each
    use against Go.
- **W2-CB merged** (`t3/checker-code-quality-1` at `b4f6b74f1`, merge
  `e9b346998`).
  - Round 2 fixed Astra's 2 contract findings: one `if_else` where Go uses a
    plain `if`, and one tagless switch turned into a table. Both are back in
    Go's form, and the residue is recorded as accepted.
  - Parity quick OK.
  - Root's full gate on the merged head (CA + CB): `runs/head-e9b346998-full`.
- **W2-LS finished** (`t3/8d4bbff2`, head `d1db1a52c`).
  - **Targets:** ls port targets went 13 → 6 (Σ Δ 239 → 76), own targets
    2 → 0. `get_completion_data` is now +3 over Go.
  - **Parity full OK:** the lsp tier is the same as the base.
  - **Duplication:** 3.51% → 3.49%.
  - **Astra review:** running.
  - **Root's follow-up, after CC merges:** a small idiom lane.
    - Move one shared `if_else`/`or_else_*` into `core.rs`, which ports Go's
      `internal/core`.
    - Remove the duplicate `or_else_node` (LS's copy and CB's copy).
    - Apply `if_else` where Go calls `core.IfElse` in CA's and LS's
      residue.
- **Root's full gate on the merged CA + CB head** (`runs/head-e9b346998-full`):
  **PARITY OK**.
  - 187,196 items, 0 lost, 0 not run, 0 new items not good, 36
    grandfathered.
  - Tiers: cli 170 MATCH, lsp 2,629 + 29 GO-BROKEN, tests 139,910 PASS,
    corpus 12,727 MATCH.
  - It is now `base-current`.
- **W2-EF finished part 1** (`t3/73d8b169`, head `9eabfd63f`, 45 commits).
  - **Targets:** 61 of 85 now ≤ 25, area excess 1,312 → 124. Structure was
    factored first: annotation walks, type containment scans, module
    identifiers, an import walk (`rules/module_references.rs`), surface
    merges.
  - **Parity full OK.** `effect_cases` 1,060/1,060.
  - **icount:** Effect-on is −0.002% with identical stdout; normal cells are
    within ±0.07%.
  - **Duplication:** 3.55% → 3.46%.
  - **Astra review:** running.
  - **Continuation lane W2-EF2 after merge:** the 24 rows left (cognitive
    complexity 26–39), including `ls/schemagen.rs` and the shared
    catch/catchAll callee helper.
- **W2-LS merged** (merge below) after Astra's ACCEPT WITH FIXES. Its one
  finding (the duplicate `or_else_node`, to be shared in `core.rs`) goes to
  the idiom follow-up lane.
- (The W2-LS merge is `372112cf3`. The first attempt used a wrong branch
  name, so the docs commit `2d10ac378` landed before it.)
- **W2-FE started:** `frontend/`, execute/emit/printer/transformers/
  declarations/sourcemap, and binder/ast/api/project/lsp. It skips the Q6
  files and leaves `gostd`/`locale` for last.
- **Idiom follow-up lane:** once CC merges. Shared `if_else`/`or_else_*` go
  in `core.rs`; LS's and CB's copies go away; it applies them to the
  `core.IfElse` residue in CA and LS.
- **W2-EF review: ACCEPT WITH FIXES** (`reviews/2026-11-09-astra-w2ef.md`).
  Round 2 has two fixes:
  - state the reachable lossy-unquote change (a crash becomes answers) as
    intended, with an LSP regression test;
  - rerun parity full on the final source.

  Then W2-EF2 handles the 24 remaining rows.
- **Q6 finished** (head `1eaba8cbb`): GATING OK (and `--wasm`), parity full
  OK, icount +0.002% to +0.016%. Astra review running.
  - **What moved behind gates:** the checker call log, reuse_verify (whole),
    the `dts_twin_check` parameter, the dead items, `early_emit_verify`, and
    `resolve_ahead_verify` (2 sites).
  - **Open for root:**
    - 3 test-only counters still ship (`JS_TWIN_PRINTS`, `DTS_TWIN_JOBS`,
      `emit_pool_job_count`);
    - `emit_files.rs:1427` has the same fingerprint `debug_assert!`
      pattern;
    - `project::unfrozen_compiler_hosts`;
    - `checker/inference_p1.rs:1234` is a debug verifier.
- **Q6 merged** after Astra ACCEPT (`reviews/2026-11-09-astra-q6.md`).
  `quality.sh gating` is GATING OK at head.
- **Queued as Q7, after W2-FE** (which owns `execute/`, `project/` and
  `frontend/`):
  - gate the 3 test counters whole;
  - turn on the resolve-ahead verification in diagnostics builds, with its
    cost measured;
  - the `emit_files.rs:1427` fingerprint `debug_assert!`;
  - `project::unfrozen_compiler_hosts`;
  - `checker/inference_p1.rs:1234`, a debug verifier;
  - centralize the `debug_assertions` cfg boundary between `compilerhost`
    and `goport_core`.
- **W2-EF merged** (`t3/effect-code-quality` at `77e903a5e`, merge
  `09661c926`).
  - Round 2 states the lossy-unquote fix as an intended change (a crash
    becomes answers). `tests/effect_lsp_module_specifier.rs` fails 5 of 6
    cases on the old code.
  - Parity full on the final code: `runs/20261109-224650-full-69d23c78d7`,
    PARITY OK, with `checkoutHead` matching.
  - Root ran GATING OK on the merged head.
  - Lane reports name branches by the T3 thread id (`t3/<id>`) while the
    real branch has a descriptive name. Root resolves the branch from the
    head commit before merging.
- **W2-EF2 started:** the 24 Effect rows still over 25.
- **W2-CC finished** (`t3/checker-quality-code-reduction`, head
  `2a9b5bacf`, 51 commits).
  - **Port bloat:** rows with Δ ≥ 10 in its files went 45 → 5 (Σ 686 → 54).
    It also applied `if_else` in 16 rows, following the idiom rule.
  - **Checks:** parity full OK, single-threaded icount at or below base,
    GATING OK.
  - **Astra review:** running.
  - **Root's follow-ups:**
    - `icount_ab.sh` now gets a private scratch dir per run, so concurrent
      lanes no longer delete each other's output.
    - After the merge, remove CC's base worktree and its 5 helper worktrees.
- **W2-CC merged** (merge `5bbb0414f`) after Astra ACCEPT, with no findings
  (`reviews/2026-11-10-astra-w2cc.md`).
  - Root removed CC's base worktree and its 5 helper worktrees. All their
    commits were already merged.
  - The 4 remaining `.claude/worktrees/agent-*` belong to W2-FE, which is
    still running. One note for W2-FE's review: its helpers' diffs touch
    `gostd/`, which the lane was told to leave for last.
- **All of `checker/` is done:** CA, CB and CC are merged.
- **Next, lane Q8 (idioms):** shared `if_else`/`or_else_*` in `core.rs`, and
  apply them to the remaining `core.IfElse`/`OrElse` residue in CA's and
  LS's files.
- **W2-EF2 finished** (`t3/refactor-effect-complexity`, final code commit
  `62691936b`).
  - **Targets:** all 24 rows now ≤ 25 (excess 124 → 0), so Effect has no
    own code over 25.
  - **Tests:** 60 LSP pin tests were written from the base binary before
    the splits (`tests/effect_cases/lsp_pins.rs`). `rules/catch_callback.rs`
    is shared.
  - **Parity full** (`runs/20261110-010447-full-22d4d1ada8`): OK.
  - **icount:** Effect-on +0.001%.
  - **GATING OK.**
  - **Open:** `reconstruct_piping_flow` has had no caller since it was
    ported.
  - **Astra review:** running.
- **W2-FE finished** (head `d40740836`, 59 commits; 4 helper worktrees
  cherry-picked).
  - **Targets:** frontend port 14 → 1, own 14 → 0; execute/build/emit port
    3 → 0.
  - **Duplication:** 3.48% → 3.42%.
  - **Parity full:** OK.
  - **icount:** single-threaded cells +0.03 to +0.07%, under the bar but
    all positive; Astra is asked for the cause.
  - **Lane decisions and questions:**
    - `Scan` is left at +34: Go-style match arms cost +0.08 to +0.16%
      icount.
    - `common_directory_of_files` still has its pre-pin shape, which
      differs from Go's at the pin. This is a possible parity gap that no
      tier covers.
  - **Astra review:** running.
- **Q8 finished** (`t3/core-helper-consolidation`, head `b704bb9f5`).
  - **What changed:** `core::if_else` and a generic `core::or_else` now live
    in `core.rs` (Go's `internal/core`). The duplicate copies are removed,
    and 10 residue rows plus 6 missed sites were converted under the rule.
  - **Effect:** checker rows with Δ ≥ 10 went 14 → 9; ls rows 6 → 2.
  - **Checks:** parity full OK at `544fceb0a`; icount within ±0.005%; GATING
    OK.
  - **Astra review:** running. The main question is whether `Default`
    equals Go's zero value for every type used with `or_else`.
  - **Found along the way:**
    - CI's clippy fails on `perf/first-principles`, at
      `tests/effect_lsp_module_specifier.rs:152`
      (`duration_suboptimal_units`, from W2-EF). W2-EF2 is fixing it in
      its round 2, since it owns that test code.
    - The W2-LS review file was never written: the heredoc came after a
      failed merge in an `&&` chain. Restored in `9ca3fd987`.
  - **After W2-FE merges:**
    - point its four `crate::checker::if_else` imports at `crate::core` and
      drop the re-export;
    - run `lib-blobs.sh write`;
    - fold the `runtime_syntax.rs` `coalesce` copy and the
      `frontend/core_ls_ext.rs` core helpers into `core.rs`;
    - move the `ElementOrNil` copies to a `NodeSlice` method.
- **W2-EF2 merged** (`t3/refactor-effect-complexity` at `ef0ce4dea`).
  - **Round 2:**
    - 67 LSP pins, all written from the base source. They cover Astra's
      gaps except the duplicate-member skip, which neither base nor head can
      reach.
    - The dead `typeparser/reconstruct.rs` is deleted.
    - The CI clippy fix: `Duration::from_mins(2)` in the shared test client.
  - **Checks:** `effect_cases` 1,127/1,127, GATING OK, parity quick OK.
  - **Effect:** no own code is over 25 any more (85 → 0 across both lanes).
- **W2-FE review: ACCEPT WITH FIXES** (`reviews/2026-11-10-astra-w2fe.md`).
  - **Round 2 (narrow):** rebase, then rerun tests, GATING OK, CI clippy and
    parity quick on the final source.
  - **A real pre-existing difference from Go,** found by the review:
    `common_directory_of_files` folds case for encoded dynamic paths on
    case-insensitive API filesystems, where Go forces a case-sensitive
    comparison. Go emits `outDir/A/x.*` and `outDir/a/y.*`; Rust emits
    `outDir/x.*` and leaves `y.*` in place.
    - **Lane B2,** after W2-FE merges: port Go's version at the pin and add
      the regression case as a parity or API test.
    - **Audit lane B3:** Q0 found 2,099 `// Go:` markers that name nothing
      at the pin. Some may be functions still in their pre-pin shape, like
      this one. B3 is a read-only audit that ranks them by risk.
- **Q8 merged** (`3cd1c91f2`) after Astra ACCEPT, with no findings
  (`reviews/2026-11-10-astra-q8.md`).
  - Lib blobs: HEAD has Q8's blobs, written after its `core.rs` change. No
    other blob key changed since Q8's base, and `lib-blobs.sh stale` is
    clean.
- **After W2-FE merges** (root):
  - point FE's four `crate::checker::if_else` imports at `crate::core`;
  - drop the `checker_p01` re-export;
  - check the blobs;
  - run a full gate on the merged head;
  - then start B2 and Q7.
- **B3 merged** (tooling and a report; `audits/b3-prepin.md`). Two output
  differences from pinned Go are confirmed, both on API emit paths:
  - **D1:** the common source dir folds case on dynamic names (Astra's
    finding, rediscovered independently).
  - **D2, new:** emit takes case sensitivity from the OS instead of the
    program's filesystem (`program/go_frontend.rs:1018`, `:735`). API
    sessions with `--useCaseSensitiveFileNames` set differently from the OS
    emit to the wrong place.
  - **Seven more suspect call sites** (old `compare_paths` and
    `get_canonical_file_name` where Go uses the dynamic-aware path key).
    D2 masks them on Linux, so they can't be confirmed yet.
  - **Why the gate missed them:** it has no API tier.
  - **Plan:**
    - **B2:** after W2-FE merges. Fix D2 first, then D1, then the seven
      call sites, each with regression tests whose expected output comes
      from the Go oracle.
    - **H7:** an API emit tier in the parity gate, built on
      `b3_api_probe.py`.
    - **Backlog:**
      - F3: the LSP preferences parser against Go's generated code.
      - F4: the symlink mapper, with a symlinked-monorepo `-b` project.
      - F5: fold the 285 duplicate ports.
      - F6: marker hygiene.
- **W2-FE merged** (merge `8162cfd31`). Root then:
  - repointed FE's 4 `if_else` imports at `crate::core` and dropped the
    `checker_p01` re-export (`136a3d0d2`);
  - rewrote the lib blobs from the merged sources (`ed6edf820`). Both sides
    had rewritten them and the repo-config merge driver was missing on
    MS-A2, so root set `merge.lib-blob.driver=true` in this repo's config.
- **Full gate on the merged head** (`runs/head-ed6edf820-full`): PARITY OK.
  - 187,196 items: 0 lost, 0 not run, 0 new items not good.
  - GATING OK; CI's clippy on 1.95.0 is clean, plain and with the features.
  - It is now `base-current`.
  - One FLAKY: `tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm`.
    It has now shown up in W2-CA, W2-CC, Q8 and here; Q7 fixes it.
- **Wave 2 is complete:** checker, ls, effect, frontend, execute/build/emit
  and other are all merged.
- **Started:**
  - **B2:** the path case-sensitivity fixes D2, D1 and the call sites.
  - **Q7:** the leftover gating items, plus the flaky watch test.
- **H7 finished** (`t3/api-parity-tier`, `87a7b52ab`..`6e97777e6`).
  - **The tier:** an `api` parity tier with 38 hermetic `tsgo --api`
    scenarios. It runs in 1.3 s full and 0.6 s quick, with 60 negative
    controls.
  - **11 known DIFFs, pinned for B2:**
    - D1;
    - the D2 family: 6 scenarios, plus the source-map `sources` path and
      the tsbuildinfo root;
    - B3 risk 10 (`x.TS` TS6054);
    - B3 risks 12/13/15 (config `include` drops a case twin).
  - **JS tsc has no comparable API,** so there is no JS referee for this
    tier.
  - **H7 also fixed a pre-existing false ERROR in the H5 tripwires:** an
    fd inherited by a forked child closes after the watch starts. It now
    ignores `IN_CLOSE_NOWRITE`. That is a loosening, so Astra reviews it
    closely.
  - Gate `runs/h7-head-ed6edf820-full`: PARITY OK.
  - **B2 told:** fix all 11, rebase after H7 merges, remove the pins.
- **H7 review: REJECT** (`reviews/2026-11-10-astra-h7.md`). Nine findings,
  four of them high: surviving children, host writes, type-insensitive JSON
  comparison, and infrastructure failures counted as GO-BROKEN.
  - **Round 2 sent:** fix all nine, with negative controls built from
    Astra's probes. Check the lsp tier for the shared tripwire and sandbox
    gaps. Add the directory-collision scenario as a 12th known-bad for B2.
  - B2 continues with its own Rust tests. Its api-tier verification waits
    for H7.
- **2026-11-10: the VM restarted** in the middle of H7's round 2 and Q7.
  Both lanes were resumed from their committed state:
  - H7 at `190df0530`, plus an uncommitted `parity.md` edit;
  - Q7 at `e0f1a4187`, with 8 commits.
- **B2 finished** (`t3/fix-api-path-parity`, last code commit `193b7fe26`,
  head `afa69a727`).
  - **The 11 api DIFFs:** H7's tier (from `6e97777e6`) gives 38 MATCH and
    0 DIFF on the head, against 11 DIFF on the base.
    - D2: the emit host now takes the program filesystem's case setting.
    - D1: Go's `CaseSensitivity.CommonDirectoryOfFiles` is ported into
      `tspath`.
    - Fixed by those two, without further changes: the source-map
      `sources` and tsbuildinfo `root` differences.
    - A newly found TS5056 duplicate-output key.
    - The call sites: risks 3–5, 10 and 12–13 are fixed and observable.
      Risks 8, 9, 11 and 14 are fixed but can't be observed on Linux.
  - **Cleanup:**
    - `modulespecifiers/deps.rs`'s copy of the output-path code was folded
      into `outputpaths`.
    - The dead path helpers were deleted.
    - `tests/api_case_sensitivity.rs` holds 8 tests with Go-generated
      expectations.
  - **Checks:** parity full without the api tier (`runs/b2-324eb0b93-full`)
    is OK; icount within ±0.02%; GATING OK.
  - **Decision:** `193b7fe26` edits Q7's `program.rs`. It removes a
    duplicate of the JSON emit check, and the D2 fix needs it. Root accepts
    it. Merge order: Q7 first, then B2 rebases onto Q7 and the merged H7,
    regenerates the api pins with `--only`, and runs parity full with the
    api tier. Astra reviews B2 after that.
  - **Unconfirmed suspect:** `WORKER_FS` (`go_frontend.rs:168`) reads the
    OS disk on checker threads. A probe matched Go, so it goes to the
    backlog.
- **H7 round 2 finished** (head `5c6b0c8d5`).
  - **All 9 findings fixed,** each with a negative control. The new design
    runs every session under an init that is PID 1 of a PID namespace, on a
    read-only tree with tmpfs `/home`, `/tmp` and `/run`, compares raw
    answer bytes, validates the envelope strictly, reports infrastructure
    failures as ERROR (never cached), reads storage after every request, and
    keeps the guarded dirs out of the sandbox entirely.
  - **Pins:** 14 api pins for B2 (3 new D2 cases).
  - **Gate** `runs/h7r2-head-ed6edf820-full`: PARITY OK.
  - **Cost:** the api tier takes 3.1 s full.
  - **Astra recheck:** running.
  - **Same gaps in the lsp tier:** no PID namespace, a writable host, `stat`
    not seen, no envelope validation. These are documented. Fixing them
    resets every lsp Go fill, so lane **H8** will port `parity_api_init.py`
    to `sandbox.mjs`/`client.mjs`.
- **Q7 finished** (`t3/gate-diagnostics-counters`, head `e0f1a4187`).
  - **All 7 items done:** the counters are gated whole, the fingerprint
    reuses Q6's verifier, the inference debug scan sits in a debug-only
    module, the resolve-ahead check runs in diagnostics builds, and the cfg
    boundary sits in goport_core as macros, which removes `not(doctest)`.
  - **The watch flake:** the 25 ms median bound measured kernel teardown
    (SIGKILL shows it too). The test now waits on events, and a unit test
    checks that the futex wait has no timeout. It passed 50/50 under 96 CPU
    burners.
  - **Cost of the resolve-ahead check:** not measurable.
  - **Checks:** parity full OK (`runs/q7-e0f1a4187-full`); GATING OK; icount
    within 0.03%.
  - **Astra review:** running.
  - **Open items for the backlog:**
    - `set_emit_test_panic` is a shipped test hook.
    - `cargo fmt --check` fails on a go_baselines file at the base.
    - go_baselines has 87 clippy errors that CI doesn't run.
- **H7 recheck: REJECT** (`reviews/2026-11-10-astra-h7-r2.md`).
  - **Round 1:** all 9 findings closed.
  - **Three new findings:**
    - a crashed reader counts as EOF;
    - the compiler keeps privileges in its user namespace, so it can
      remount `/dev/shm` writable or hide `/proc` from the survivor scan;
    - the home skeleton is skipped unvalidated.
  - **Round 3 sent, the last under the cap,** with root's design for
    finding 2: drop every capability, set no_new_privs and run as a
    non-zero uid before exec; find survivors through a pidfd, cgroup or
    pre-opened `/proc`.
  - **H8:** the lsp gap list grows by four high findings (init answer not
    compared, `1e999` parsing, home writes ignored, exec failure counted as
    GO-BROKEN).
  - **Process:** the first recheck brief was refused by the provider's
    cyber filter. Sandbox reviews are now framed as harness QA (memory
    `astra-cyber-filter`).
- **Q7 review: ACCEPT WITH FIXES** (`reviews/2026-11-10-astra-q7.md`).
  - **Gating:** correct.
  - **Round 2** fixes the watch-test replacement:
    - cancel on every inspection outcome, with bounded loops;
    - portable in-process coverage from cancel to completion, with
      injected-fault controls behind `test-hooks`;
    - correct the attribution and narrow the root-cause claim.
  - **Also in round 2:** gate the emit panic hook, and fix the go_baselines
    fmt failure.
  - **Lane Q9 (later):** the go_baselines clippy (87 errors) and an
    explicit `--test go_baselines` lint step in CI.
- **H7 round 3 finished** (head `a08c934d6`).
  - **Astra's three findings fixed,** each with a control:
    - a reader exception now means malformed, EOF has to be real, and
      headers are bounded;
    - the privilege drop: the server enters a nested user namespace with
      `max_user_namespaces` 0, runs with every capability set empty, as uid
      and gid 1000 with `no_new_privs`, and the init checks that before
      exec. Survivors are counted through a `/proc` handle opened before
      the server starts;
    - the home skeleton is checked exactly.
  - **Side effects:**
    - fixtures are now mode 755/644, and their content is checked instead;
    - Go's emitter crashes with a nil dereference when it can't write its
      outputs, and tsc-rs reproduces that.
  - **Checks:** gate `runs/h7r3-head-ed6edf820-full` PARITY OK; 108 H7
    self-tests.
  - **Rounds:** the review cap's 3 lane rounds are used up. Root asked
    Astra for a final recheck that includes merge readiness. Root then
    decides: merge, with any remaining findings fixed by root or by H8.
    The tier only adds coverage.
- **H7 final recheck: REJECT** (`reviews/2026-11-10-astra-h7-r3.md`).
  - **Finding 1 (high):** storage that can't be read (the root chmodded)
    gives MATCH.
  - **Finding 2 (medium):** a host-side mmap edit-and-restore is a race on
    the host.
- **Root's decision at the cap:** H7 makes one final narrow fix for finding
  1, with controls, and documents finding 2 as a limit. Root verifies it
  with Astra's reproductions, then merges without another review round. The
  tier only adds coverage, and Astra found no weakening of the other tiers.
- **Q7 merged** (`c38920d93`) after round 2. It fixed every point of
  Astra's review:
  - the watch test cancels whatever the check finds, and all its waits
    are bounded;
  - portable in-process timing from cancel to the end, with two
    injected-fault tests behind `test-hooks`;
  - the attribution is corrected, and the claim about Go is withdrawn;
  - the emit panic hook is gated whole;
  - `cargo fmt` is fixed.

  Parity full is OK (`runs/q7r2-0f51902b1-full`). The tests tier now builds
  with `diagnostics,test-hooks`.
- **Root follow-up:** the in-process bound goes from 10 to 25 ms. The
  loaded median reached 7.2 ms, and the faults land near 54 ms. All three
  tests pass. No third review round: a change to a constant, verified by
  the fault tests.
- **H7 merged** (`11dcc9470`) after root verified the final fix.
  - H7's self-test (111 checks) passes. Astra's three final cases now give
    DIFF:
    - a write, then `chmod 000` on the storage root;
    - `chmod 0700` on `build-composite`, in full mode;
    - the same in quick mode.
  - The fix records the storage root's metadata, and any directory that
    can't be listed or stat'ed counts as a difference.
  - The mmap edit-and-restore race on the host is documented as a known
    limit; it affects the cli and lsp snapshots too.
  - **Base:** `base-current` is now `runs/h7final-head-ed6edf820-full-2`,
    the full run with the api tier.
  - The first gate run lost `fuzz/1:2450` to a timeout at load 42. Run
    alone, it MATCHes in about 10 s. The rerun was clean.
- **B2 told** to:
  - rebase onto Q7 and H7, resolving the `program.rs` overlap;
  - fix all 14 api differences (three are new since its report);
  - remove the pins with `regen --only`;
  - run the full gate, api tier included.
- **H8 started:** port the api tier's sealed, unprivileged session design
  to the lsp tier, and fix its documented gaps:
  - the initialize answer isn't compared;
  - lossy number parsing;
  - home writes are ignored;
  - an exec failure counts as GO-BROKEN;
  - no PID namespace and a writable host.

  It also refills the lsp Go and JS evidence. It runs beside B2 and touches
  harness code only.
- **B2 finished** (`t3/fix-api-path-parity`, head `c1ecb516a`, rebased onto
  Q7 and H7).
  - **All 14 api differences fixed:** the api tier gives 49 MATCH and 0
    DIFF. The three new pins needed no extra code; the D2 fix covers them.
  - **Pins removed;** the 36 fuzz entries are byte-identical.
  - **Checks:** parity full (`runs/b2-29e23ede3-full`) OK with 14
    recovered; icount flat; GATING OK; clippy and fmt clean.
  - **Astra review:** running.
  - **Tooling gap:** `regen --only` refuses items that now MATCH, so the
    lane removed them with a `--from` copy. Follow-up: give `regen` a way to
    drop recovered pins explicitly.
  - **Another flake that predates this work:**
    `build_watch_parses::build_watch_keeps_the_first_json_parse_of_a_cycle`
    fails 3/100 on the base under parallel load. It goes to the flake
    backlog, next to the Q7-style fix.
- **B2 merged** (`eaf77bac4`) after Astra ACCEPT WITH FIXES
  (`reviews/2026-11-10-astra-b2.md`).
  - **API output now matches pinned Go** on every api scenario (49/49).
  - **Root's fixes:**
    - deleted `b2_icount_ab.sh` (`817be035f`), which had written a
      tsbuildinfo into the shared Query input;
    - removed the stray `query-core/dist-ts/`. A scan found no other write
      into the inputs since 2026-11-05.
  - **`base-current`:** now `runs/b2-29e23ede3-full`.
  - **Guard:** `~/.cache/perf-rearch/projects/` is now read-only (see
    below).
- **Backlog from B2:**
  - direct regression tests for risks 11 (a dynamic realpath case) and 14
    (an overlay with `İ.ts` against `i.ts`);
  - a sort-direction test for risk 8;
  - a `regen` option to drop recovered pins;
  - the `build_watch_keeps_the_first_json_parse_of_a_cycle` flake (CRLF
    assertion at `build_watch_parses.rs:100`, which predates B2).
- **2026-11-10, 13:01–13:21 CST: the task-cap incident** (ops note).
  - **What happened:** a systemd 255 bug capped `app-t3code.slice` at 10,018
    tasks, so forks failed everywhere. H8 died at 13:13 CST, and root's turn
    died too. Ops has fixed the cap.
  - **H8's refill:** it had finished (`scratch/h8/fill.log`, `DONE`: lsp
    2,655 MATCH and 29 GO-BROKEN). Root spot-checked it:
    - **During the incident window:** the watch-hono-f12 refill 2 and fill-2
      ran then. Both agree with the runs before it.
    - **fill-2's GO-BROKEN set** equals `base-current`'s exactly, and no item
      is not good.
    - **Logs:** no fork or resource errors.
    - **Decision:** kept; nothing regenerated.
  - **Shared inputs:** git status is clean.
    - The `effect`/`t3code` `tsconfig.base.noeffect.json` files come from
      input prep (2026-11-03).
    - `zod/vitest.config.{js,d.ts,d.ts.map}` are stray emit outputs from
      2026-11-04, before the read-only guard. Every snapshot since then has
      included them, and both sides see them. They will be dropped at the
      next input prep, because removing them now resets zod's Go cache.
  - **New rule** (brief, "Machine"): one or two heavy runs at a time, under
    the shared absolute lock `/home/agent/.cache/ts-rust-heavy.lock`, in
    `app-batch.slice` with `TasksMax=24000`. Added to `w2-brief.md` and to
    every lane brief from now on.
- **H8 finished** (`t3/harden-lsp-parity-harness`, `7e873cafd`..`06a4344b9`).
  - **Shared supervisor:** `parity_sandbox_init.py` is now the init for both
    the api and lsp tiers.
  - **All lsp gaps fixed,** each with a control: 39/39 fail on the old
    harness and pass on the new one.
  - **Two new items per session:** `initialize` and `session home`. These
    account for the 26 new MATCH.
  - **Go evidence:** the refill matches the base. The GO-BROKEN set is the
    same, and so are the variant records.
  - **Gate** `runs/h8-head-865385add-full`: PARITY OK.
  - **Cost:** the lsp tier went from 460 s to 641 s, because of
    number-preserving JSON parsing and the duplicate-key check in
    `json.mjs`. Root's plan: fold a faster design into H8 round 2, together
    with any review fixes, so one lsp refill covers both.
  - **Astra review:** running.
- **H8 review: REJECT** (`reviews/2026-11-10-astra-h8.md`), with 7 findings.
  - **Finding 1 repeats H7's api-tier storage bug** in lspdiff's own JS
    walker. The cause is duplicate code, not a missed case.
  - **Round 2 (root's design points):**
    - one storage lister and one write history for both tiers, in the shared
      supervisor, recording writes through an inotify watch instead of
      sampling;
    - null-prototype maps;
    - compare the server-to-client stream through EOF;
    - validate the shutdown result;
    - the stand-in npm takes a fixed read-only config, and its evidence
      comes from the recorded history;
    - final storage is judged strictly, apart from the timing-dependent
      history;
    - Astra's fast JSON tokenizer, so that one lsp refill covers
      everything.
- **H8 round 2 finished** (head `95006241b`).
  - **Findings:** all 7 fixed, each with a control.
  - **Shared storage module:** `parity_sandbox_storage.py`, used by both
    tiers. Per session it puts storage on its own tmpfs, records every
    write through a fanotify filesystem mark, and lists storage with one
    shared lister.
  - **Message coverage:** the `session messages` item compares every
    server-to-client message through EOF.
  - **Hardened ATA:** the stand-in npm reads a fixed read-only config and
    checks each file's sha256.
  - **Path-by-path judging:** final storage and writes are judged per path.
  - **Fast JSON tokenizer.**
  - **lsp tier:** 235 s, against 460 s at the base and 641 s in round 1.
  - **Gate:** `runs/h8r2-head-865385add-full` PARITY OK (lsp 2,681 MATCH,
    29 GO-BROKEN).
  - **Astra recheck:** running.
  - **It found a tsc-rs nondeterminism:** `watch-hono-f12/0016 load server
    messages`, where one `workspace/diagnostic/refresh` is missing in 1 of
    4 runs (`GOPORT_PARSE_THREADS=1`). Go never varies there.
    - **Lane B4 started:** write a Go note, find the root cause, fix it the
      Go way, add a test that fails on the base and never on the head, and
      check sibling notifications.
- **H8 recheck: REJECT,** with 6 new gaps (`reviews/2026-11-10-astra-h8-r2.md`).
  - **The pattern:** this is the loop Chad asked about. Each round finds
    stranger cases, and Astra writes deliberately evasive servers.
  - **Root's decision:**
    - **State the threat model** in `parity.md`: buggy compilers and racy
      environments, not adversarial servers.
    - **Fix** 1 (unresolved paths fail closed), 2 (every writable mount
      recorded, checked in the init), 3 (sanitize npm's env), 4 (mask only
      ID fields) and 6 (xattrs and hard links in the listing).
    - **Out of scope, documented:** 5, a transient mmap write that is
      restored.
  - **Round 3 sent,** the last under the cap. After it, root decides
    without a further Astra round, unless a finding is inside the threat
    model.
- **B4 done; merged `9513cf308`.** The refresh-count variation is **not a
  tsc-rs bug**: Go coalesces the same refreshes when it is starved
  (`scripts/perf-rearch/b4/README.md`).
  - **Cause:** lspdiff's `settle` can't see a `didOpen` that is still
    being handled. A slow open queues the next ones, their ATA results land
    inside one 500 ms debounce window, and one refresh is lost.
  - **Repro, one starved CPU:** base harness, tsc-rs 8 of 10 short and Go 4
    of 4 short; with the barrier, Go 8 of 8 and tsc-rs 50 of 50 send 12.
  - **Merged:** a test pinning Go's refresh sequence
    (`project_lsp::refresh_sequence_test`, 0 of 50 failures under load) and
    the lspdiff barrier patch, not yet applied. No production change; the
    `tsgo` binary is byte-identical. Gate
    `runs/20261110-230324-full-c554d48991` PARITY OK.
  - **Residual race:** in 1 of 50 barrier runs, ATA's npm idle window ended
    mid-install, and the typings were installed twice. Go would do the
    same.
  - **Root's decision:**
    - Keep H8's round 3 closed. Its refill and gate are already running.
    - After H8 merges, lane **H9** applies the barrier to `vendor/lspdiff`
      and removes the patch and its applier.
    - **Fix the ATA race with a real signal, not a longer window:** enable
      work-done progress in the client, and make `settle` wait until every
      ATA progress `begin` has its `end`. Compare the progress messages if
      Go and tsc-rs agree on them; otherwise use them only as the barrier
      signal and say so in `parity.md`.
    - Then one refill, and 3 lsp-tier runs with every `session messages`
      item MATCH.
  - **No Astra review of B4:** it ships a test and docs only. The barrier is
    reviewed in H9.
- **H8 round 3 done; merged `2e08cbde7`.** Root accepts it without another
  Astra round. Every fix is inside the threat model, and each has a control
  that MATCHes on `95006241b` and DIFFs on the head.
  - **Fixed:** 1 (unresolved paths are named by the dir's last known path,
    otherwise `?<nonce>.<n>`, failing closed), 2 (no writable host binds;
    the init checks mountinfo), 3 (`env -i` npm), 4 (ids masked by JSON
    path) and 6 (xattrs and link groups).
  - **Documented:** 5 (mmap) and the other known limits, in `parity.md`,
    "The threat model".
  - **A false start:** a first version of fix 1 made overlayfs copy-up
    nondeterministic (3 api rs-nondet). That gate was stopped, and the
    version that names paths at the end of the record fixed it.
  - **Refill:** `runs/h8r3b-lsp-*`. **Gate:**
    `runs/h8r3b-head-865385add-full` PARITY OK; lsp 2,681 MATCH and 29
    GO-BROKEN in 232 s; 0 lost.
  - **Root's check:** the H8, H7, H6 and H4 JS self-tests pass on the
    merged head.
  - **base-current** moved to `h8r3b-head-865385add-full`.
  - **Next:** H9, the B4 barrier plus an ATA progress barrier, with one
    refill.
- **Started:**
  - **H9:** apply B4's barrier, add an ATA work-done-progress barrier in
    place of the npm-idle window, check it under starvation, then one
    refill and 3 lsp runs with `session messages` all MATCH.
  - **Q9:**
    - go_baselines clippy (about 87 errors), plus a CI clippy step for the
      feature sets the gate tests;
    - the root cause of the `build_watch_keeps_the_first_json_parse_of_a_cycle`
      CRLF flake.
- **H9 done; Astra review running.** Head `3195ad922` (gate on `df7a71ff3`).
  - **The barriers:**
    - B4's notification barrier is in lspdiff, and the patch is deleted;
    - a work-done-progress barrier, with a 60 s deadline that makes the run
      a harness ERROR;
    - progress is left out of the compared streams, because Go only shows
      one after 250 ms (`ProgressDelay`), so it depends on timing in Go.
  - **npm-idle stays:** ATA runs that show no progress still schedule
    refreshes.
  - **Starvation (12 loops), load refreshes:** base Go 10–11 in all 8
    runs, base tsc-rs 11 in 19 of 20; head Go 12 in 8 of 8, head tsc-rs 12
    in 50 of 50.
  - **Gate:** `runs/h9-df7a71ff3-full` PARITY OK, 0 lost. 3 lsp runs, with
    all 13 `session messages` and 73 `server messages` items MATCH.
  - **lsp time:** 240 s against 232 s (+3%).
- **H9 review: REJECT** (`reviews/2026-11-11-astra-h9.md`). All four
  findings are in scope. Fix 1 (fail closed on the notification deadline),
  2 (progress lifecycle checks) and 3 (check Go's fixed progress payload, not
  its occurrence). For 4, find a deterministic ATA completion signal, else
  document the assumption. Fix both test weaknesses. Round 2 sent.
- **Q9 done; merged.** Root reviewed the `goport_watch` change: the
  rewrite loop is bounded by the clock catching up, and the cover wait is
  capped at `BUILD_TIMEOUT`.
  - **Clippy:** go_baselines is `test = false`, so CI's `--all-targets`
    never compiled it. Its 87–88 findings are fixed mechanically, with no
    `#[allow]`. CI now lints `--test go_baselines`, plain and with
    `diagnostics,test-hooks`.
  - **The CRLF flake:** the cause was timesyncd stepping the wall clock
    back (`msa2-clock-steps`). After a step the edit got an older mtime
    than lib's build info, and Go's up-to-date check would skip it too.
    `goport_watch` now rewrites the edit until its mtime is later than
    every file under the project, and waits for a watch to cover the
    target (a second race the 300 ms wait hid).
    - **Rates:** base 4 failures of 200 runs; head 0 of 2,000; the head
      with the 300 ms wait restored, 0 of 1,000.
  - **The release `tsgo` is byte-identical.** Tests: plain 2,199 passed,
    with features 2,230 passed; parity tests tier 0 lost; GATING OK.
- **B5 started:** direct tests for B2's path-case risks 8 (sort direction
  of redirect candidates), 9, 11 (realpath differing only in case) and 14
  (overlay listing, `İ.ts` against `i.ts`). Each test must fail with its
  fix reverted.
- **H9 round 2 done** (head `73db30179`, gate on `fafe6a374`).
  - **The fixes:**
    - 1: the notification deadline is a harness ERROR;
    - 2: Go's progress lifecycle is enforced (create always comes before
      begin at the pin);
    - 3: Go's fixed progress payloads are checked, and a broken rule
      becomes a DIFF on that side;
    - 4: Go has no deterministic ATA completion signal, so `parity.md`
      states the residual bound in place of the load-independent claim.
    - Both test weaknesses are fixed.
  - **Controls:** each fails on `3195ad922` and passes on the head.
  - **Gate:** `runs/h9r2-fafe6a374-full` PARITY OK, 0 lost. 3 lsp runs,
    with all session and server messages MATCH.
  - **lsp time:** 233 s.
  - **Astra recheck** running (round 2 of 3).
- **H9 recheck: REJECT,** with 2 narrow findings in scope
  (`reviews/2026-11-11-astra-h9-r2.md`):
  - the transport type of progress messages isn't checked;
  - payload violations aren't attributed to the server that broke the
    rule.

  Everything else from round 1 is confirmed. Round 3 sent, the last; root
  decides after it.
- **B5 done; merged.** Direct tests for B2's risks 8 (sort sign and
  order, then the d.ts specifier), 9 (project-relative across packages that
  differ only in case), 11 (realpath differing only in case, for modules
  and type references) and 14 (overlay listing; Go folds `İ` to `i` when
  comparing names, never `ı`).
  - **Go's answers:** from in-package probe tests on a scratch copy of the
    pin (`scripts/perf-rearch/b5_go_probes.sh`). Where Go loops over a map,
    each case ran 200–500 times, and the tests accept any answer Go gave.
  - **Each revert** fails only its own risk's tests. No difference from Go
    was found, so no production code changed.
  - **The release `tsgo`:** sections and segments are identical. Only the
    `.llvm.<N>` symbol suffixes and the build-id differ, because the crate
    hash covers the test modules.
  - **New backlog item:** `run-cargo-capped.sh:175`'s `ulimit -v` makes
    node fail (`pthread_create: Invalid argument`), so the 14 node-backed
    go_baselines tests (jsnum, astnav_tokens) fail through the wrapper.
    They pass when run directly, as `parity_tests.py` does. This is the
    same failure B4 put down to the unit's environment.
- **T1 started:**
  - the wrapper's memory guard should measure real memory (cgroup
    `MemoryMax`) instead of `ulimit -v`, so node runs;
  - `parity_grandfather.py regen --drop-recovered`.
- **H9 round 3 done; merged.** Root accepts it without a further Astra
  round, as decided.
  - **Finding 1:** progress transport is checked (create is a request,
    `$/progress` a notification).
  - **Finding 2:** progress rules are judged per server, before the
    comparison. A tsc-rs break is a DIFF whatever Go did; a break by Go
    alone is GO-BROKEN with the rule named.
  - **Root's rerun** of the H8 self-test (all `h9r3` controls: transport,
    go-only, rs-only, both, go-varies, go-varies-rs-bad) and the H4 JS
    self-test: all OK, each status as reported.
  - **Refill;** no real item changed status. **Gate:**
    `runs/h9r3-ab7be7932-full` PARITY OK, 0 lost. 3 lsp runs, with all
    session and server messages MATCH; lsp time 234 s.
  - **base-current** moved to `h9r3-ab7be7932-full`.
  - **The review loop:** round 1 had 4 findings, round 2 had 2, both
    narrower, and none reopened.
- **B6 started (B3 F3):** probe LSP preferences with odd values against Go,
  port Go's generated parser and serializer
  (`userpreferences_generated.go`) in place of the reflective one, and keep
  the probe as a permanent lsp battery. F4 (the symlinked-monorepo mapper)
  follows when a slot frees.
- **T1 done; merged.**
  - **`regen --drop-recovered`:** it reuses the gate's `GOOD` and
    `rs_failure`. It never drops an entry whose item wasn't run, and it
    has 17 self-test checks. The dry run dropped 0 of 36.
  - **The node failures aren't the wrapper's.**
    - `~/.cargo/config.toml`'s runner `LD_PRELOAD=libd3d12core.so`
      (for lightcraft's GPU tests) adds 64 KiB of TLS to every thread.
      Node's 32 KiB inspector thread then fails with EINVAL.
    - `ulimit -v` is not involved, so the wrapper is unchanged.
    - **Workaround:** `CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER=env`.
    - **Real fix:** move the runner into lightcraft's own
      `.cargo/config.toml`. That is a machine-wide file and another
      project's, so it is **asked of Chad**.
- **B7 started (B3 F4):** check tsc-rs's symlink handling in
  project-reference mapping against Go's builder (`resolveSymlink`,
  `realpathDtsToSource`), with a symlinked-monorepo `-b` parity project.
- **B7 done; Astra review running.** No difference from Go: 7 CLI
  scenarios and the API transcripts MATCH before and after.
  - **Ported:** Go's ts#64519 shape (`ProjectReferenceFileMapperBuilder`
    with `resolve_symlink`), as a structural commit. The old
    `get_source_to_dts_if_symlink` and the mapper's host and loader are
    deleted.
  - **New harness feature:** synthetic cli fixtures
    (`parity-fixtures.json`, read-only, content-addressed), plus a
    symlinked pnpm monorepo with 3 cli items and 1 api item. No earlier
    item set `preserveSymlinks`, and the negative control (an empty
    `resolve_symlink`) DIFFs on 3 items.
  - **Gate:** `runs/20261111-112730-full-95b2a4d419` PARITY OK, 0 lost;
    icount single-threaded flat.
- **W3 started:** the last complexity targets (14 port-bloat rows with
  Δ ≥ 10, 24 own-code functions over 25), following w2-brief. B6's and
  B7's files are excluded.
- **B7 review: REJECT** (`reviews/2026-11-11-astra-b7.md`).
  - **Finding 1:** a real difference from Go that predates B7. The
    builder's resolution host uses the host directory, not the config's
    base, so a programmatic `paths` without `pathsBasePath` gives TS2307.
  - **Finding 2:** the api scenario queries nothing after the build.
  - The refactor itself is confirmed faithful. Round 2 sent.
- **B6 done; Astra review running.** Head `1867f09aa`, merges cleanly.
  - **The probe:** the new lsp `prefs` session (495 answers). The base had
    1 DIFF (`maximumHoverLength` 4294967306 wrapped in an `i32`); the head
    is 495 MATCH. The other odd inputs already matched.
  - **The port:** Go's generated parser and serializer
    (`userpreferences_generated.rs`) in place of the reflective one. The
    hand-written ports equal Go's complexity. Production code −1,557 lines.
  - **Gate:** `runs/b6-1867f09aa-full` PARITY OK, with lsp 3,176 MATCH.
  - **icount, LSP keyed load:** +0.30%, inside 1.1% spread (p = 0.14);
    the CLI control is flat.
  - **Backlog from B6:**
    - non-object init options and `workspace/configuration` answers need a
      shared-source change, so do them with the next all-session refill;
    - fold the 3 copies of the amd64 `float64_to_int` (jsnum,
      parsing_helpers, userpreferences_generated) together with the
      `core.rs` and lib-blob change;
    - formatter sizes stay `i32` (Go's `int`), which can't be probed
      without Go allocating gigabytes.
- **B6 review: REJECT** (`reviews/2026-11-11-astra-b6.md`). The 83/74/83
  lists, precedence and omission rules are confirmed.
  - **The findings:**
    - the hover cap's ×10 wrap;
    - formatter sizes still `i32` (observable: `tabSize` 2^32);
    - `i64` serialized through `f64`;
    - empty against nil slices in `MarshalJSONTo`;
    - 2 vacuous or masked probes.
  - All 5 to fix, and fold the amd64 conversion into one helper. Round 2
    sent.
- **B7 round 2 done** (head `0a79603a1`). It found 2 real differences from
  Go, both through the API, both now items: DIFF on `4ae5301b0`, MATCH on
  the head.
  - `build-paths-base-directory`: a programmatic `paths` gave TS2307.
  - `programs-config-path-base-directory`: automatic types and lib
    replacement resolved from the host directory.
  - **The fix:** the loader, its resolver and the parse workers use the
    config's base directory everywhere Go uses `Config.BaseDirectory()`.
    The call-site table is in `cleanup/b7.md`.
  - **The api `afterBuild` step:** after the builds, a query over the built
    tree, then a same-session edit that crosses the redirect (TS2322).
  - **Gate:** `runs/20261111-131608-full-bb2f2974a5` PARITY OK; api 52
    MATCH, all refilled because the driver changed every api key.
  - **icount:** single-threaded flat.
  - **Astra recheck** running (round 2 of 3). Root's own question for it:
    is closing b, to avoid Go's randomly ordered changed-projects map, more
    honest than sorting that map on both sides?
- **B7 recheck: REJECT,** with 1 finding (`reviews/2026-11-11-astra-b7-r2.md`):
  declaration emit's module specifiers still use the process directory for
  `paths` (`ProgramHost::base_directory`), giving `"../shared/value"`
  where Go writes `"alias"`. Round 3 sent, the last.
- **W3 done; Astra review running.** Head `f24c08c9b`.
  - **Port bloat:** 14 → 9 rows with Δ ≥ 10 (≥ 25: 2 → 1), including
    `Scanner::scan` 258 → 222 (Go 224) and `get_symbol_at_location`
    108 → 92 (Go 97). **Own code:** 24 → 23 over 25.
  - **Left:**
    - 5 port rows are Go's tagless-switch residue;
    - `create_late_bound_index_signatures` (`core.IfElse` eagerness);
    - `subtree_facts_of_data` (a hot match over Go's 72 methods);
    - `update_indexes` (cancellation points);
    - 21 own rows are stdlib and x/text ports within +6 of their Go.
  - **Gate:** `runs/w3-ccdaa34b4-full` PARITY OK; icount single-threaded
    at most +0.034%.
  - **Root's decision on gorel** (W3 asked):
    - score the stdlib and x/text ports against Go's own source, a real
      correction;
    - show tagless-switch residue as its own column rather than
      subtracting it, so the raw Δ stays honest.

    Both go to F6, which starts after B6 and B7 merge, since it edits
    markers in their files.
- **B8 started:** the `WORKER_FS` suspect. On a miss, checker workers ask
  the OS disk, where Go asks the program's FS (LSP overlay, API virtual).
  Probe, fix the Go way, measure.
- **W3: Astra ACCEPT; merged.**
  - **Confirmed:**
    - the scanner change is exactly 8 `break 'sw` → `return` plus 12
      continues, with the same state;
    - fallthrough order is kept;
    - `msg_in` and `new_object_type` are faithful;
    - the lib-blob payloads are identical;
    - the remaining rows are defensible (Astra reproduced all 23 Go values).
  - **Root fixed `w3.md`'s counts:** 23 stdlib rows, not 21; 6 residue
    rows plus 3 others.
  - **base-current** moved to `w3-ccdaa34b4-full`, which contains every merge through W3.
- **B7 round 3 done; merged.** Root accepts it without a further Astra
  round, as decided.
  - **The fix:** declaration emit's module specifiers use the program's
    base. Item `api/build-decl-paths-base-directory`: Go writes
    `import("alias").V`, tsc-rs on round-2 code `import("../shared/value").V`;
    DIFF there, MATCH from `049abdd54`.
  - **Root's checks:**
    - the reproduction outputs (`scratch/b7/api-r3-{old,new}`);
    - `base_directory_sites.py check` COMPLETE: 156 Go sites (76 correct,
      13 fixed, 67 n/a);
    - `parity_b7_test.py`: B7 SELFTEST OK (the changedProjects key sort).
  - **Gate:** `runs/20261111-143822-full-114452a990` PARITY OK, 0 lost;
    icount single-threaded flat.
  - **base-current stays at W3's run,** because B7's gate predates W3. B7's
    7 new items are protected by the new-not-good check. The next full
    gate on the merged head becomes the base.
- **Bug pattern** (Chad asked where the bugs come from). The real
  differences found in this phase fall into three groups:
  1. **Context taken from the process, not the program:** case
     sensitivity (B2), base directory (B7), FS on workers (B8, suspected).
  2. **Go semantics Rust types don't carry:** `int` width, nil against
     empty, integers written through `f64` (B6).
  3. **Stale pre-pin ports** (B3).

  None was in checker logic. Groups 1 and 2 can be audited mechanically:
  B2, B7 and B8 cover group 1.
- **A1 started:** a read-only audit for group 2 (Go `int` ported to a
  narrower type, nil against empty, integers through `f64`), ranked, with
  fixes and tests suggested.
- **A1 audit done** (`audits/a1-int-width-nil-empty.md`). The generated
  `lsproto` layer (1,162 fields) and the API types (509) match Go
  mechanically.
  - **New findings, top 5:**
    - A1: API `maxResponseBytesPerPage` as `i32`, so a large value fails
      the batch;
    - B1: an empty `TypeAcquisition.include/exclude` is lost in API output;
    - A2: float→`int32` enum options saturate where Go wraps to
      `i32::MIN`;
    - A3: a `formatNodeForInsertion` position ≥ 2^31 panics;
    - A5: the build-info reader wraps ids and `int32` fields where Go
      rejects them or keeps them.
  - **Lower ranks:** A4 and A6–A9, B1b and B2 (log lines only).
  - **Lane B9** fixes them once B6 merges: A2 and A6 reuse B6's
    `gostd/conv.rs` amd64 helper, and B6 owns `parsing_helpers.rs`.
- **B6 round 2 done** (head `6fb96eb1e`). All 5 findings are fixed.
  - **The fixes:**
    - `i64` hover cap with `wrapping_mul`;
    - `i64` formatter sizes, with Go's `strings.Repeat` panic text;
    - integer tokens;
    - `Option<Vec>` slices.
    - The 3 amd64 conversions are folded into
      `gostd::conv::float64_to_int64`; the lib blobs are byte-identical.
  - **The prefs battery:** 50 one-preference probes, each shown to bite in
    Go (`prefs_sensitivity.py`). DIFFs: base 7, round 1 3, round 2 0
    (902 MATCH).
  - **Gate:** `runs/b6r2-6fb96eb1e-full` PARITY OK; icount flat.
  - **Cost:** the lsp tier goes from about 300 s to 573 s (prefs is about
    117 s per server run). Astra's recheck is asked to list the redundant
    probes, and root trims after the recheck. Prefs also moves to the full
    tier only, unless a quick-tier reason holds.
  - **Astra recheck** running (round 2 of 3).
- **B6 recheck: REJECT,** with 2 findings (`reviews/2026-11-11-astra-b6-r2.md`):
  - a 2^62 indent aborts tsc-rs where Go's `makeslice` limit gives a
    recoverable InternalError;
  - `prefs_sensitivity.py` doesn't sort completion items.

  Round 3 sent, the last. It also trims the battery per Astra's redundancy
  list and moves prefs to the full tier only.
- **B8 done; Astra review running.** The `WORKER_FS` suspect was real.
  - **API emit over a virtual FS:** module specifiers, TS2835 and
    package.json lookups read the OS disk. 3 api items DIFF before and
    MATCH after, plus a control. **The fix:** the API emit runs on the
    program's own checkers on the dispatch thread, as Go's `Program.Emit`
    does.
  - **`tsc -w`:** a file only a checker probe looked for wasn't seen, so
    creating it started no rebuild. **The fix:** the full-build program
    records worker misses as seen files. Test
    `tsctests::watch_checker_file_probes`.
  - **The LSP is unaffected:** it checks on the dispatch thread.
  - **Known gaps:** search threads (no answer changes); the API build
    orchestrator with a callback FS, which is untested.
  - **Gate:** `runs/b8-1d01f5998-full` PARITY OK, 0 lost; icount flat. API
    emit is now serial per file where Go is parallel; no timing cell covers
    it.
- **B8 review: REJECT** (`reviews/2026-11-11-astra-b8.md`). Both fixes are
  confirmed faithful.
  - **Two gaps that predate B8:**
    - API builds with a callback FS still use the disk (TS2835 against
      TS2834);
    - the incremental d.ts timestamp restore bypasses the host's
      `GetMTime`/`SetMTime`.
  - **Round 2:** a callback-FS mode for the api harness, then a worker→owner
    query channel and the host metadata in the flush.
