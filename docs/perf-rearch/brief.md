# perf/first-principles: agent brief

Root (the orchestrating agent) pastes or names this file in every agent prompt.
Read it once, all of it. Do not read the repo's `AGENTS.md` or
`docs/goport-agent-brief.md` for process: they describe the upstream
maintainer's workflow (batches, saved state, Blacksmith and his hosts), which
this branch does not use. Read code and docs in the repo freely.

## Goal

Make tsc-rs as fast as possible: **same input, same output**. Every wall-clock
measurement must end up equal to or faster than Go `tsc` at the pin.

- **Behavior parity is the hard bar; code-shape parity is not.** This branch
  drops upstream's rule of matching Go's code shape (`// Go:` markers,
  `// PORT:` notes, "contract" constraints such as the serial loader of
  contract 10). Any architecture is allowed if the output stays byte-identical (see the partition exception below).
- **"Output"** means everything observable: stdout and stderr of `tsc`,
  diagnostics (text, order, codes, spans), emitted JS, `.d.ts` and maps,
  `.tsbuildinfo`, exit codes, `--extendedDiagnostics`-style counts only where
  they are printed, and every LSP answer.
- **Partition-dependent output (Chad, 2026-11-04).** Byte-identical with Go's
  default run is the contract, with one exception. Where Go's *own* output
  depends on how it splits files across checkers, tsc-rs may give any output
  that Go itself gives at some checker count.
  - **Which counts:** `--checkers N` for N in {1, 2, 3, 4, 6, 8, 12, 16}.
  - **What must match:** the whole run (stdout, stderr, exit code, emit,
    build info) must equal one of those Go runs exactly.
  - **Determinism:** tsc-rs must give the same output on every run.
  - **When the exception doesn't apply:** where Go gives the same output at
    every N in the set, tsc-rs must match it byte for byte. Mechanically: run
    Go at every N. If the outputs differ, the item is GO-PARTITION and
    accepts any of them; if not, it needs an exact match.
  - **Why:** in these cases Go's default answer is an artifact of its split.
    On zod a limit hit in an earlier file caches a degraded type; JS tsc
    6.0.3 reports 0 errors there and Go c4 reports 22.
  - **Never allowed:** run-to-run variation.
- **Better than Go (Chad, 2026-11-04).** tsc-rs may differ from Go where it
  is provably better. A difference passes only by one of these mechanical
  referees; any other difference fails the gate.
  - **JS-AGREES:** JS tsc 6.0.3 (`~/.cache/perf-rearch/js-tsc`) sides with
    tsc-rs on every point where tsc-rs and Go differ.
    - Each diagnostic Go prints and tsc-rs doesn't must be absent from JS's
      output.
    - Each diagnostic tsc-rs prints and Go doesn't must be in JS's output,
      byte-identical: file, position, code and text.
    - Each emitted file that differs from Go's must equal JS's.
    - The exit code must equal JS's whenever it differs from Go's.
    - Points where tsc-rs equals Go need no JS agreement, since JS 6 and Go
      differ in many unrelated ways.
  - **GO-BROKEN:** Go crashes (panic or abnormal exit), times out, or varies
    between its own runs at the same settings on that input. tsc-rs must give
    one stable answer and must not crash. Examples are the fuzzer finds
    microsoft/TypeScript #65078 and #65079.
  - **The partition exception above** (GO-PARTITION) also stands.
  - **Scope:** JS-AGREES applies to the CLI, fuzz and corpus tiers.
  - **LSP:** JS-AGREES uses JS tsserver 6.0.3, driven directly over its own
    protocol (lane H2). Its answers are mapped to the LSP request kinds and
    compared on normalized fields. Kinds with no tsserver equivalent (watch
    registrations, pull-diagnostic order) have no JS referee. GO-BROKEN
    applies everywhere.
  - **Speed is not a difference.** Faster is always the goal.
- **Oracles:** Go at pin `3e9236361fa9` (`~/.local/bin/tsgo-oracle-3e9236361fa9`,
  LSP `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 --lsp --stdio`), Go source
  `~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc`. Go is the default
  reference. JS `tsc` 6.0.3 is the referee for JS-AGREES only; where Go and JS
  differ and tsc-rs equals Go, that is fine and not a tsc-rs bug.
- **Repo:** fork `THEtheChad/ts-rust`. Branch `perf/first-principles`, worktree
  `/home/agent/personal/ts-rust-perf`, both based on `main` @ `91f3d021c`.
  Root owns that worktree and its merges. Agents work in their own worktree.

## Machine (MS-A2)

- AMD Ryzen 9 9955HX, 16 cores / 32 threads, 47 GB RAM, WSL2. **Shared** with
  other projects' agents, whose test runs cause CPU bursts.
- No `perf`, no passwordless sudo, `perf_event_paranoid=2`. Profiling uses
  samply built for user-space only (see the baseline task), or tsc-rs's own
  timers.
- **Never use remote hosts or Blacksmith** (`remote.sh`, `testbox.sh`,
  `boatbox.sh`). Everything runs here.
- **Heavy runs** (ops note, 2026-11-10): builds, test suites, parity gates
  and refills, icount, and anything that forks many processes.
  - **Background.** On 2026-11-10, from 13:01 to 13:21 CST, every agent on
    the machine hit a task cap: `app-t3code.slice` was capped at 10,018 by a
    systemd 255 bug. Forks were refused and agents aborted, H8 among them.
    The cap is fixed, but CPU was oversubscribed: load 40.6 on 32 CPUs.
  - **At most one or two heavy runs at a time,** across all lanes.
  - **Every heavy run takes the one shared absolute lock** and runs in the
    batch slice with an explicit task cap:

    ```sh
    systemd-run --user --collect --slice=app-batch.slice --property=TasksMax=24000 \
      --unit=<name> -p WorkingDirectory=$PWD \
      flock /home/agent/.cache/ts-rust-heavy.lock bash -lc '<command>; echo DONE or FAIL rc=N to the log'
    ```

  - **Why the lock path is absolute:** a relative path doesn't serialize
    across worktrees.
  - **Why the explicit `TasksMax`:** the batch slice gives the run low CPU
    priority but has no task cap of its own.
  - **The parity gate's own lock** (`parity/.lock`) and `run-cargo-capped.sh`'s
    slots still apply inside it.

## Parity gate (mandatory for every change)

> **Harness edits reset Go evidence.** The LSP Go-variant record is keyed on
> the lspdiff sources, so any edit to them starts the record empty. Sessions
> where Go races (hono and query references, the umami watch load) can then
> come out LOST. Before judging a run after such an edit, add verified fills:
> `parity_lsp.py --tier full --only '^<session>$' --go-refill '^<session>$'`.

`scripts/perf-rearch/parity.sh quick|full <tsgo bin> --base ~/.cache/perf-rearch/parity/base-current`
prints `PARITY OK` or `PARITY FAIL` (see `docs/perf-rearch/parity.md`).
- **quick:** about 4 minutes, warm. Use it while iterating.
- **full:** about 17 minutes (45 cold). Required before you report a change
  as done.
- **Base:** `base-current` is a symlink to the latest accepted full run of
  this branch (now head e81a14807 (C1–C3 and the C2 follow-up, L1, P1, P3, ND1/ND2, WIN, H5), under the H5 hermetic harness; run b8-e2941610d-full (all of waves 2 and 3, Q7–Q9, H7–H9, B2–B8, T1: every lsp server message compared, paced by the server; the prefs session; api callback mode; api tier 49 MATCH, lsp 2,681 MATCH and 29 GO-BROKEN), 0 lost; on main the Effect items were known
  diffs). Root moves it after each merge. `base-main` stays as the main
  record.
- **Referees:** on the cli, fuzz and corpus tiers the gate applies the
  "Partition-dependent output" and "Better than Go" rules above itself:
  GO-BROKEN, GO-PARTITION and JS-AGREES are good statuses, judged in that
  order after tsc-rs's determinism (`parity.md`, "Referees"). The compare
  lists every item that left MATCH for one of them, by status: name each in
  your answer, with why it is right.
- Run full tiers under `systemd-run` (see Rules). The tests tier builds your
  checkout's source, so run it from your own worktree.
- **Quality report (report only, no gate):** before reporting, run
  `scripts/perf-rearch/quality.sh report HEAD --base perf/first-principles`
  from your worktree (about 6 to 8 minutes for a new tree, more while another
  Cargo run holds the slot; cached reruns under 2 s; `--no-clippy` skips
  clippy) and paste its summary into your final
  answer. It covers escape hatches, complexity, duplication, churn and
  hygiene; `docs/perf-rearch/quality.md` explains each one. Its escape-hatch
  inventory comes from rustc's cfg-expanded source of the default build, not
  a cfg parser (section 8).

## Rules

- Edit only the files your prompt gives you. Commit only if your prompt says
  so, with Conventional Commits, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or your own model
  name). Never push.
- **Build:** `scripts/run-cargo-capped.sh build --release -p ts_goport --bin tsgo`
  from your worktree (nightly `-Zthreads=8`, incremental). Use
  `scripts/run-cargo-capped.sh check -p ts_goport` while editing. Set
  `TS_CARGO_JOBS=16`. Never set `CARGO_TARGET_DIR`.
- **Timing builds:** every side of a timing is a stable build:
  `TS_CARGO_NIGHTLY=0 TS_CARGO_INCREMENTAL=0 RUSTUP_TOOLCHAIN=1.95.0 scripts/run-cargo-capped.sh build --release -p ts_goport --bin tsgo`,
  or `--profile goport` (fat LTO, the shipped profile) for final numbers.
- **Same-built bins only:** build both sides of a comparison yourself, with
  the same toolchain and profile. The shared
  `bins/tsgo-main-91f3d021c-goport` uses about 1% fewer instructions than a
  local `--profile goport` build of the same source (lane B1), so don't
  compare it with your own builds.
  Edit-loop builds run 2 to 7% slower; never compare them with stable builds.
- **Timing method:** a run starts only when the 1-minute load is under 1.5
  (`/proc/loadavg`). Interleave sides run by run. Record the load before and
  after each run, and repeat runs that end above 3. Report the median and
  spread of at least 3 runs. Set `GOPORT_LAUNCH=0` when measuring RSS or CPU
  through wait4. Use `scripts/perf-rearch/` tools once they exist.
- **Load-independent metric:** `scripts/perf-rearch/icount.py <cmd>` counts
  user-space instructions (perf_event_open, works here without perf or
  sudo). Repeat runs agree within 0.005% at any load. Use it with
  `--singleThreaded` for CPU-work claims while the machine is busy, and wall
  time on an idle machine for the final "≤ Go" claims.
- **Instruction A/B traps** (lane E2):
  - Editing the parser, store or binder makes the lib blobs stale (+0.4% on
    its own): run `lib-blobs.sh write` on both sides, or compare against a
    no-op edit of the same file.
  - Release builds (16 CGUs) move counts by ±0.5–1%: decide on
    `--profile goport`.
  - hono's `tsconfig.build.json` is composite: pass a scratch
    `--tsBuildInfoFile` and delete it before each run, or later runs are
    no-ops.
  - Trust deltas under 2% only with all three handled.
- **Never write into the shared inputs** (`~/.cache/perf-rearch/projects/`,
  the parity fixtures). Benches and tools work on copies, or pass a scratch
  `--outDir`/`--tsBuildInfoFile` plus `--incremental false --composite false`.
  The gate re-hashes the inputs and marks changed ones ERROR. A bench that
  wrote into `projects/t3code` mid-gate cost a full run on 2026-11-05.
  - **The tree has been read-only since 2026-11-10** (`chmod -R a-w`). That
    happened after B2's icount script wrote
    `query-core/dist-ts/tsconfig.tsbuildinfo`.
  - **A tool that fails there with EACCES is writing where it must not.**
    Fix the tool, not the mode.
  - **To prepare inputs again,** run `chmod -R u+w` on the project, prepare
    it, then run `chmod -R a-w`.
- **Memory:** big test runs, memory profilers and anything that can use more
  than 8 GB go under
  `systemd-run --user --collect --slice=app-batch.slice --nice=10 -p MemoryMax=20G -p MemorySwapMax=0 --unit=<name> ...`.
  So do your own scripts that read the whole tree or large logs.
- **systemd environment:** units don't inherit your shell. Pass
  `--setenv=PATH=$PATH --setenv=HOME=$HOME` (for cargo, rustup and node)
  and any `TS_*`/`GOPORT_*` variables you rely on, or the job fails
  (lanes E3 and the LSP audit lost runs to this).
- **CPU priority:** every `systemd-run` (services and `--scope`) passes
  `--slice=app-batch.slice --nice=10`. Without them the job leaves your
  agent's cgroup and outranks every agent on the host for CPU (AGENTS.md,
  "Detached job priority").
- **Long jobs:** longer than 10 minutes, run under
  `systemd-run --user --collect --slice=app-batch.slice --nice=10`,
  with a log that ends `DONE` or `FAIL rc=<N>`. You are a subagent: when you
  end your turn, your background jobs stop. Block in the foreground until a
  job ends (Bash timeout up to 600000 ms, with an `until grep` loop).
- **Format:** run `rustfmt --edition 2024` on every `.rs` file you change.
- **Lib blobs:** a change to the parser, scanner, factory, store, ts_ast,
  binder, core.rs, flags or a bundled lib makes `lib_parse.bin`/`lib_bind.bin`
  stale. `scripts/goport/lib-blobs.sh stale` checks; `lib-blobs.sh write`
  rebuilds them. A stale blob is slower and fails two snapshot tests.
- **Downloads** are untrusted: each in its own new directory, installs with
  `--ignore-scripts`, Python with `-I`.
- Keep tools under `scripts/perf-rearch/` (or your lane dir), never `/tmp`.
  Keep command output under 20 KB.
- **Review rounds (Chad, 2026-11-06):** at most 3 Astra rounds per lane. After
  the third, root decides whether to take, drop or redesign the work, and
  records the decision.
- **Windows** is available locally through WSL interop. The Windows side has
  rustup stable msvc (`/mnt/c/Users/agent/.cargo/bin/cargo.exe`), VS 2022
  BuildTools and Git, and `cmd.exe` is at `/mnt/c/Windows/System32/cmd.exe`
  (not on PATH). Windows work happens in `C:\tsrs-win` only (a clone, not a
  `\\wsl.localhost` path; cmd can't use UNC paths).
- If you need a decision, take the safe option, say which in your answer and
  continue.
- **Your final answer is the deliverable:** findings, numbers with their
  method, files changed and commits. Root saves it.

## Code quality: no old paths (rules from 2026-11-04)

Chad's priority: root-cause fixes, not layers of one-off patches. This branch
is a rewrite, so it does not keep old code behind switches:
- **Replace, don't switch.** A change that replaces a mechanism deletes the
  old one in the same merge. No env var, cargo feature or config option that
  selects between the old and the new path for the compiler's own work.
  Safety comes from the parity gate (it compares with Go, not with our old
  code) and from git: for an A/B, build the base commit as the other side.
- **When removing an existing switch, keep the path whose output matches
  Go**, not just the path that shipped by default. If the default diverges
  from Go and the alternative matches, the Go-matching one stays and the
  speed is recovered another way. Check pure `--noEmit`, `-b`, watch and LSP,
  not only the common CLI case.
- **Verification code is gated whole:** state, functions, counters and test
  setters, not only the env read. The shipped binary must not contain it.
  The convention (proposed by lane Q5, adopted by root 2026-11-09; `quality.md` section 9) that makes
  this checkable:
  - Diagnostic, verification and test-hook code lives in a module named
    `diag`, `test_hooks`, or `<topic>_diag`, `_verify`, `_dump`, `_stats` or
    `_times`, and that module's declaration carries the gate
    (`#[cfg(feature = "diagnostics")]`, `"test-hooks"`, or
    `any(debug_assertions, feature = "diagnostics")`). Gate the `mod`
    line, not the items one by one, and not with `cfg!()` or
    `debug_assert!` inside shipped code.
  - Its items are named after their env name's topic
    (`GOPORT_CHECKER_CALL_LOG`: `checker_call_log_*`), so a leftover shows.
  - A shipped no-op stub of a gated module (zero-sized timers) is a child
    module named `off`; each such stub needs a reviewed entry in
    `scripts/perf-rearch/gating-allow.txt`.
  - Before reporting, run `scripts/perf-rearch/quality.sh gating HEAD`
    (about 2 minutes for a new tree, seconds cached). It reads rustc's
    expanded default build, prints `GATING OK` or `GATING FAIL` with each
    finding, and exits 1 on a finding that the allow list does not hold.
    Paste its result into your final answer. Never add an allow entry for
    your own code without saying so; the reviewer judges each one.
- **Spikes** (prototypes that only measure) may add a switch on their own
  branch. It never merges: the implementation that follows replaces the path.
- **Product options stay:** user-facing behavior Chad chose (Effect rules off
  by default, opt-in disk cache) and tuning knobs such as thread counts are
  not old paths.
- **Diagnostic, verification and test-hook code** goes behind a cargo
  feature (as B1's `test-hooks` does) or a single diagnostics module, not a
  new env var per lane, and never into shipped hot paths.
- **Don't copy code to build a verifier.** Reuse or factor the original.
- **Functions:** no new function above cognitive complexity 25. If a root
  path (for example `process_all_program_files`) grows, split it rather than
  adding branches.
- **Clippy** under the repo config must not get worse.
- Your final answer lists every switch your change removed and confirms it
  added none.

## Code-quality phase (from 2026-11-09)

Chad approved this phase after perf finished (every window-2 cell ≤ Go, ttfd
ties with Go; base `runs/head-e81a14807-full-combined`). Quality is now the
primary consideration, and the output contract, the parity gate and "≤ Go" on
every timing still hold. The baseline report is
`~/.cache/perf-rearch/quality/reports/q0-f853c4dea/` (713 prod functions over
cognitive complexity 25, duplication 3.58%, 21 env names read in shipped
code, 2,120 `clippy::all` warnings under `--force-warn`).

- **Go-relative complexity.** Much of tsc-rs is a port, and Go's own functions
  are large (`structuredTypeRelatedToWorker` is 641 lines, `getCompletionData`
  1,273). Keeping the Go shape is what makes pin bumps cheap. So:
  - **Ported code:** the target is port bloat, where our function is much
    more complex than its Go counterpart. Split it back toward Go's shape
    (Go's helper boundaries and names first). A function as complex as its Go
    counterpart is not a target by itself; don't invent a split Go lacks
    unless it makes the code clearly easier to follow, and say why.
  - **Our own code** (no Go counterpart: the loader and its views,
    resolve-ahead, flow_skip, Effect, the parallel emit, THP guard, build
    caches, verifiers): the limit is cognitive complexity 25.
  - `quality.sh` reports the Go-relative ranking (lane Q0).
- **Behavior and speed don't move.** Each lane runs parity quick, icount A/B
  on the cells its code touches (checker and relater changes: all CLI cells),
  and a full gate before merge. Splitting a hot function can change inlining:
  any icount cost over +0.3% is fixed (`#[inline]` on the new helper, a
  different boundary) or reported, never waved through.
- **One owner per file.** Lanes get disjoint file lists. A lane that needs a
  file another lane owns stops and reports.
- **Mechanical and structural changes stay apart.** Clippy fixes and
  renames go in their own commits from structural splits, so review can
  read each one.
