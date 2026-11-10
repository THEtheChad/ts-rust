# Wave 2: shared lane brief (code-quality phase)

Read `docs/perf-rearch/brief.md` first, all of it. The sections that matter most here:

- "Code-quality phase (from 2026-11-09)";
- "Code quality: no old paths";
- the parity gate;
- the review cap.

Then read this page. Your lane prompt gives you your files and your targets.

## What a wave-2 lane does

1. **Ported code: cut port bloat.** A target is a function whose cognitive
   complexity is at least 10 above its Go counterpart (Δ ≥ 10 in
   `quality.sh ranking`).
   - Bring it back toward Go's shape. Go's own helper boundaries and names
     come first. The `// Go: file.go:line Name` markers tell you where to
     look in `~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc/internal`.
   - The usual causes are:
     - perf machinery added inside a ported function (fast paths, caches,
       interning);
     - Rust ownership plumbing (borrow juggling, clone-then-reborrow);
     - inlined Go helpers;
     - defensive branches Go doesn't have.
   - Move our own machinery into small helpers of its own, each with
     complexity ≤ 25 and a doc comment saying why it exists. Leave the
     Go-shaped part alone.
   - A function about as complex as Go's is not a target. Don't invent a
     split that Go doesn't have unless it clearly makes the code easier to
     follow, and say why in the commit.
2. **Our own code** (no Go counterpart): the limit is cognitive complexity 25.
   Split by responsibility, not by line count. Every new function gets a name
   that says what it does. No `_part1`/`_helper2` names.
3. **Duplication inside your files:** remove copied blocks by factoring the
   original, never by adding a third copy. `quality.sh report --top N`
   lists the blocks.
4. **Nothing else.**
   - No behaviour change, no perf change.
   - No renames of Go-mirrored functions.
   - No new switches.
   - No formatting churn outside the functions you touch.
   - The remaining clippy exceptions in `[workspace.lints.clippy]` are out
     of scope, unless a function you're already splitting trips one.

## Go helper idioms (root's decision, 2026-11-09)

- **`if_else(cond, a, b)`** (`checker_p01.rs`, from W2-CB) is allowed only
  where Go's code at that spot calls `core.IfElse`. It evaluates both
  arguments, as Go does.
  - Where Go writes `if`/`else`, keep Rust's `if`/`else`. Using the helper
    there would be gaming the metric.
  - Don't use it if an argument panics or is costly in a way that Go's
    isn't.
- **`or_else_*`** is allowed where Go calls `core.OrElse`.
- **Rust iterator forms** (`any`, `all`, `find`, `filter`, `map`) for Go's
  `core.Some`, `Every`, `Find`, `Filter` and `Map` are always preferred to
  flag-and-`break` loops.
- **A Go tagless `switch` written as an else-if chain** stays as it is. Its
  residue in the score is accepted.

## Rules

- **One owner per file.** Edit only the files your prompt lists. If you need
  a change elsewhere (a shared helper, for example), keep it minimal, name it
  in your report, and prefer adding a new private helper in your own file.
- **One function (or one tightly linked group) per commit:**
  `refactor(<area>): <what>`. Each commit builds and passes your area's unit
  tests.
- **Order:** work down your target list by excess (largest first). Don't
  skip a hard one silently: if a target can't come down without a behaviour
  or perf risk, record why in your progress file and move on.
- **Progress file:** `docs/perf-rearch/cleanup/w2-<lane>.md`. Keep it
  current and commit it with your work. For each target, record:
  - its name and file;
  - Rust/Go cognitive complexity before and after;
  - what you did, or why you left it;
  - the commit.

  If your context grows large (around 350k tokens), stop at a clean commit,
  finish the evidence below on what you have, and report. Root starts a
  continuation lane from your progress file.

## Heavy runs
Follow `brief.md`, "Machine", on heavy runs:
- **One or two heavy runs at a time,** across all lanes.
- **Each one under `flock /home/agent/.cache/ts-rust-heavy.lock`,** launched
  with `systemd-run --user --slice=app-batch.slice --property=TasksMax=24000`.

## Evidence before you report

- **Tests:** `scripts/run-cargo-capped.sh test --release` for the workspace,
  plain and with `--features ts_goport/diagnostics,ts_goport/test-hooks`,
  with `TS_GO_REPO` set to the pin checkout (`parity_tests.py` shows how).
- **Clippy:** CI's command on 1.95.0 with `-D warnings`, plain and with the
  features.
- **Parity:** `scripts/perf-rearch/parity.sh full <release bin> --base
  ~/.cache/perf-rearch/parity/base-current` must give PARITY OK.
  - Use `quick` while you iterate. The gate allows one run at a time.
  - A fuzz case that times out at high load is not a loss until it fails
    again at low load: rerun it alone and give both results.
  - If you hit a nightly rustc ICE (`verify_ich`), delete
    `target/release/incremental` and rebuild.
- **icount:** `scripts/perf-rearch/cleanup/icount_ab.sh` with
  `--profile goport` builds, base `perf/first-principles` against your head,
  on query, umami and hono, all cells.
  - Checker, binder, parser and emitter changes are hot, so run it before
    you report and whenever a split touches a hot loop.
  - Any single-threaded cell over +0.3% gets fixed (`#[inline]` on the new
    helper, a different boundary) or explained with numbers.
  - Multi-threaded cells are noisy (spread around 0.3–0.4%): recheck with
    5 runs before you call one a cost.
- **Gating:** `quality.sh gating HEAD` must print `GATING OK` (`brief.md`,
  "Verification code is gated whole"). Paste the result.
- **Quality:** `quality.sh report HEAD --base perf/first-principles`. Paste
  the summary and your area's Go-relative numbers before and after.

Commit with Conventional Commits ending `Co-Authored-By: Claude Opus 5.5
<noreply@anthropic.com>`. Never push. Never write into
`~/.cache/perf-rearch/projects/` or the Go checkout. The VM can power off
without warning: commit often, and run long jobs under `systemd-run --user
--collect` with a log that ends in `DONE` or `FAIL rc=N`.

## Final answer

- the per-target table: before, after, done or left and why;
- duplication removed;
- tests, clippy, parity (run dir) and icount;
- the quality delta;
- the commits;
- anything you needed outside your files.
