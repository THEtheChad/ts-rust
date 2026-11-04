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
  contract 10). Any architecture is allowed if the output stays byte-identical.
- **"Output"** means everything observable: stdout and stderr of `tsc`,
  diagnostics (text, order, codes, spans), emitted JS, `.d.ts` and maps,
  `.tsbuildinfo`, exit codes, `--extendedDiagnostics`-style counts only where
  they are printed, and every LSP answer.
- **Oracles:** Go at pin `3e9236361fa9` (`~/.local/bin/tsgo-oracle-3e9236361fa9`,
  LSP `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 --lsp --stdio`), Go source
  `~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc`. The JS `tsc` is a
  secondary reference: where Go and JS differ, Go wins. Record such cases, but
  they are not tsc-rs bugs.
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

## Parity gate (mandatory for every change)

`scripts/perf-rearch/parity.sh quick|full <tsgo bin> --base ~/.cache/perf-rearch/parity/base-main/`
prints `PARITY OK` or `PARITY FAIL` (see `docs/perf-rearch/parity.md`).
- **quick:** about 4 minutes, warm. Use it while iterating.
- **full:** about 17 minutes (45 cold). Required before you report a change
  as done.
- **Known diffs on main:** see `known.txt` in the base. The Effect items go
  away with lane E0.
- Run full tiers under `systemd-run` (see Rules). The tests tier builds your
  checkout's source, so run it from your own worktree.

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
- **Memory:** big test runs, memory profilers and anything that can use more
  than 8 GB go under
  `systemd-run --user --collect -p MemoryMax=20G -p MemorySwapMax=0 --unit=<name> ...`.
  So do your own scripts that read the whole tree or large logs.
- **Long jobs:** longer than 10 minutes, run under `systemd-run --user --collect`,
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
- If you need a decision, take the safe option, say which in your answer and
  continue.
- **Your final answer is the deliverable:** findings, numbers with their
  method, files changed and commits. Root saves it.
