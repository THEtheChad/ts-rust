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
