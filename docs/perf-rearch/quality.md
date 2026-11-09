# Code quality on perf/first-principles

`scripts/perf-rearch/quality.sh` measures the Rust sources (`crates/`) at a
revision and reports the delta against a base. It **reports only**: no gate
reads it yet. Chad's worry is "a bunch of slop and one-off patches instead of
fixing root problems". So this page leads with what most directly shows
one-off patches, escape hatches, and the deltas per merge.

```sh
scripts/perf-rearch/quality.sh report HEAD --base perf/first-principles   # lanes, before reporting
scripts/perf-rearch/quality.sh report <rev> [--base <rev>] [--hatches] [--no-clippy]
scripts/perf-rearch/quality.sh history      # main and every first-parent merge of the branch since main
scripts/perf-rearch/quality.sh ranking <rev> [--top 60] [--out FILE]   # Go-relative planning page (section 7)
scripts/perf-rearch/quality.sh help
```

`report` includes the Go-relative complexity ranking of section 7 by default
(`--no-go-relative` skips it).

Measured 2026-11-04: `main` = `91f3d021c`, branch head = `502c808aa`.

## 1. Escape hatches (the one-off-patch signal)

An escape hatch is a `GOPORT_*` or `TSC_RS_*` environment variable named
by a string literal (or a `const` holding one) in production code. Test files
and `#[cfg(test)]` items are excluded. Each read site is classified:

- **switch:** the value is read into a bool gate, or branched on where it is
  read.
  - A bool gate is a fn returning `bool`, or a `static`/`const` of a bool
    type, that holds the read.
  - "Branched on" means an `if`, `match`, `&&`, `||`, `?`, `then`, `filter`
    or `is_some_and` in the same statement.
  - Gates chain: a bool fn that returns a gate's value is a gate too, up to 3
    levels. The tool counts each gate's call sites in branches ("gate branch
    uses").
- **knob:** a parsed value such as `GOPORT_PARSE_THREADS`, or a build-time
  `option_env!`.
- Reads only under a non-default cargo feature (`test-hooks`) are counted
  apart from shipped reads.

Each name then gets a class. This is a name heuristic, listed in
`quality.py` `hatch_class`:

| Class | Rule |
|---|---|
| test-hook | `_TEST_`, `_CHILD`, or feature-only. |
| diagnostic | Ends in `STATS`, `_LOG`, `DUMP`, `TIMES`, `TRACE` or `DEBUG`. |
| verification | `VERIFY` or `_CHECK`. |
| tuning | Not a switch. |
| behavior | A switch that is none of the above: a second code path for the product's own work. These are the ones that matter. |

### Main and head

| | main | head | Δ |
|---|---:|---:|---:|
| env names read in shipped prod code | 49 | 64 | +15 |
| …of which switches | 36 | 46 | +10 |
| behavior switches | 30 | 34 | +4 |
| tuning knobs | 12 | 12 | 0 |
| diagnostics | 5 | 11 | +6 |
| verification | 2 | 4 | +2 |
| test hooks (3 shipped, 4 feature-only) | 0 | 7 | +7 |
| bool gates / their branch uses | 26 / 32 | 34 / 43 | +8 / +11 |

New env names on the branch, by merge:

| Merge | Behavior | Diagnostic | Verification | Test hook |
|---|---|---|---|---|
| E0 | `TSC_RS_EFFECT` (opt-in, Chad's decision) | | | |
| A1 | `GOPORT_REUSE_SYNTHETIC` (`=0` falls back to Go's rebuild) | `REUSE_STATS`, `VERIFY_REUSE_LOG` | `VERIFY_REUSE`, `VERIFY_REUSE_DIR` | `VERIFY_REUSE_CHILD`, `_CHILD_ARGS`, `_CHILD_OUT` |
| B1 | `GOPORT_PLAIN_EARLY_EMIT` (`=0` emits after the check, as Go does) | `CHECKER_CALL_LOG`, `EMIT_PHASE_TIMES` | | `TEST_CHECK_PANIC`, `_STALL`, `_SLEEP`, `TEST_WRITE_PANIC` (feature `test-hooks`) |
| S1a | `GOPORT_FIRST_LOAD_AHEAD` (`=0` turns the S1a path off; removed in C2) | `LOAD_DUMP`, `LOAD_TIMES` (feature `diagnostics` since C2) | | |

The `--hatches` flag prints the full inventory: each name with its class,
reads, gates (branch uses / all uses) and files. The JSON has every site.

## 2. Per-merge deltas

From `quality.sh history`. Each row is the delta against the previous
point. "env read" counts names read in shipped prod code; "sw." counts the
switches among them.

| Point | prod net | prod +/− | test +/− | files | env read (sw.) | behavior sw. | gate br. | fn cog >25 | >50 | dup % | expect | `#[allow]` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| main `91f3d021c` | | | | | 49 (36) | 30 | 32 | 714 | 218 | 3.67 | 619 | 122 |
| E0 `794679c51` | +70 | +101/−31 | +188/−83 | 26 | 50 (37) | 31 | 35 | 714 | 218 | 3.67 | 619 | 122 |
| D0-1A driver `263172c23` | 0 | tooling only | | 0 | | | | | | | | |
| baseline lane `2e5518c59` | 0 | tooling only | | 0 | | | | | | | | |
| A1 `55b14f97c` | +1,604 | +1,671/−67 | +1,217/−1 | 19 | 58 (42) | 32 | 36 | 714 | 218 | 3.67 | 620 | 122 |
| A1 follow-up `7546bba8d` | +269 | +328/−59 | +103/−3 | 3 | 58 (42) | 32 | 36 | 714 | 218 | 3.67 | 620 | 122 |
| B1 `b22c2a3e6` | +1,330 | +1,362/−32 | +807/−0 | 11 | 61 (44) | 33 | 42 | 714 | 218 | 3.66 | 625 | 124 |
| -b race `43a2089df` | 0 | tooling only | | 0 | | | | | | | | |
| LSP harness `8fd260a19` | 0 | tooling only | | 0 | | | | | | | | |
| S1a `84224f514` | +1,188 | +1,218/−30 | +373/−0 | 14 | 64 (46) | 34 | 43 | 716 | 218 | 3.67 | 625 | 124 |
| **head − main** | **+4,461** | +4,621/−160 | +2,685/−84 | 63 | 64 (46) | 34 | 43 | 716 | 218 | 3.67 | 625 | 124 |

### Production growth by file

The production lines the branch added are mostly around the optimizations
rather than in them (net prod lines, head − main):

| File | Net prod lines | What it is |
|---|---:|---|
| `project/reuse_verify.rs` | +861 | A1 verification harness (child process, snapshot diff) |
| `emitter/program_emit.rs` | +584 | B1 early emit and deferred writes |
| `program/reuse_verify.rs` | +418 | A1 verification harness |
| `frontend/compiler/program_new.rs` | +327 | A1 synthetic swap |
| `frontend/compiler/file_loader.rs` | +284 | S1a, including the 171-line `dump_loader_state` |
| `program.rs` | +282 | B1 and test hooks |
| `emitter/phase_times.rs` | +281 | B1 diagnostics |
| `frontend/compiler/load_dump.rs` | +245 | S1a diagnostics |
| `frontend/compiler/resolve_ahead.rs` | +209 | S1a |

`reuse_verify.rs` (×2), `phase_times.rs` and `load_dump.rs` hold 1,805 of
the 4,461 net production lines, 40%. With `dump_loader_state` it is about
44%. That code verifies or measures. It does not make tsc-rs faster.

Test to prod lines added: E0 1.86, A1 0.73, A1 follow-up 0.31, B1 0.59,
S1a 0.31. The test lines include integration tests under
`crates/ts_goport/tests`, but not the parity harness, which lives outside
`crates/`.

### Complexity growth per merge

- **New functions above cognitive 25 or grown by 5 or more:** 34 across the
  branch. The largest:
  - S1a: `file_loader.rs::dump_loader_state` (new, cognitive 29, 171 lines).
  - S1a: `file_loader.rs::process_all_program_files`, cognitive 34 → 45.
  - A1 follow-up: `project/reuse_verify.rs::SnapshotDiff::of` (new, 23).
  - A1: `program_new.rs::NewProgram::reuse_program_with` (new, 22).
  - A1: `project.rs::Project::create_program`, 29 → 37.
  - B1: `program_emit.rs::start_emit_files_with_pool`, 14 → 22.
- **Duplication:** 6 new blocks, all 10 to 15 lines. One copies
  `trackingvfs.rs` into `reuse_verify.rs` (A1) and one copies `cachedvfs.rs`
  into `load_dump.rs` (S1a). The rest are internal repeats in `program_new.rs`,
  `resolve_ahead.rs`, `emitter.rs` and `execute_tsc.rs`.
- **Hygiene:**
  - `expect(` +6: B1 `program_emit.rs` +5, A1 +1.
  - `panic!` +3.
  - `#[allow(` +2 (B1 `phase_times.rs`).
  - `unwrap()`, `unsafe` and TODO/FIXME/HACK/XXX: none added.
- **Clippy:**
  - The repo config's 1 warning became 5: 3 `redundant_clone` in
    `project/reuse_verify.rs`, and 1 `for_loops_over_fallibles` in
    `ls/codeactions.rs` (E0).
  - Forced `clippy::all` went 2,120 → 2,131 (+11).

## 3. Snapshot numbers (main → head)

| Metric | main | head |
|---|---:|---:|
| Prod code lines (generated excluded) | 473,415 | 476,847 |
| Test code lines | 139,742 | 141,948 |
| Generated files excluded | 27 (94,349 lines) | 27 (94,352) |
| Prod functions | 21,629 | 21,772 |
| Cognitive p50 / p90 / p99 / max | 1 / 11 / 51 / 509 | 1 / 11 / 50 / 509 |
| Functions cognitive > 25 / > 50 | 714 / 218 | 716 / 218 |
| Cyclomatic p50 / p90 / max | 2 / 10 / 429 | 2 / 10 / 429 |
| Functions cyclomatic > 25 / > 50 | 408 / 82 | 412 / 83 |
| Duplicated prod lines (≥60 tokens and ≥8 lines) | 3.67% (17,359), 706 blocks | 3.67% (17,488), 711 blocks |
| `unsafe` (blocks) | 11 (5) | 11 (5) |
| `.unwrap()` / `.expect(` | 265 / 619 | 265 / 625 |
| `panic!` / `unreachable!` | 547 / 50 | 550 / 50 |
| `#[allow(` | 122 | 124 |
| TODO / FIXME / XXX | 237 / 1 / 1 | 237 / 1 / 1 |
| Clippy, repo config | 1 | 5 |
| Clippy, `--force-warn clippy::all` | 2,120 | 2,131 |

The branch has not touched the complexity hot spots. Every function in the
top 20 by cognitive complexity is upstream code, unchanged:

- `Checker::structured_type_related_to_worker` 509
- `check_grammar_modifiers` 310
- `NameResolver::resolve_name` 300
- `Scanner::scan` 268
- and so on.

Most of them mirror Go's own functions, so their complexity is inherited
from the port.

The duplication hot spots are upstream too:

- `scanner_util.rs`: 24% of its prod lines repeat `frontend/scanner`.
- The `tspath` extension helpers exist twice, in
  `frontend/tspath/extension.rs` and `modulespecifiers/tspath_extension.rs`.
- `ipc/conn_async.rs` and `conn_sync.rs` repeat each other.
- `api/session_p*`.

## 4. My read

1. **Each optimization merge added a permanent second code path.** A1, B1
   and S1a each shipped a default-on change with a `=0` kill switch "for A/B
   runs and as a fallback": `REUSE_SYNTHETIC`, `PLAIN_EARLY_EMIT`,
   `FIRST_LOAD_AHEAD`.
   - That is reasonable while a change is young.
   - But nothing retires a switch. Main already carries 30 behavior
     switches from upstream's lanes: `FAST*`, `LOAD_*`, `EARLY_*`,
     `FLOWSKIP`, `LIB_*SNAPSHOT`, and so on.
   - The branch is on the same path: about one new behavior switch per
     optimization. Every one doubles the paths the parity gate should cover.
     The gate runs only the default.
2. **Verification and instrumentation code is the biggest growth: about 44%
   of net prod lines.**
   - It is in production modules and reached through env vars:
     - 8 `GOPORT_VERIFY_REUSE*` names for one A1 harness;
     - `LOAD_DUMP` and `LOAD_TIMES`;
     - `EMIT_PHASE_TIMES` and `CHECKER_CALL_LOG`;
     - test hooks in `program.rs` and `program_emit.rs`.
   - B1 put its test hooks behind a cargo feature, which keeps them out of
     the shipped binary. A1's child-process hooks (`VERIFY_REUSE_CHILD*`)
     ship.
   - This code is honest work: it is how the lanes prove parity. But it
     should live behind one diagnostics module, or behind a feature, not as
     a new env var per lane.
3. **No sign of "slop" in the narrow sense.** The branch added:
   - no `unwrap()`, no `unsafe`, no TODO/HACK markers;
   - duplication flat at 3.67%;
   - p90 and the cognitive >50 count unchanged.

   Complexity growth is concentrated in a few functions:
   - `process_all_program_files` 34 → 45;
   - `create_program` 29 → 37;
   - `start_emit_files_with_pool` 14 → 22.

   Those are the root-path functions the optimizations had to change.
   Watch them: the next lanes in the loader (S1b onward) will hit
   `process_all_program_files` again.
4. **Clippy, under the repo's own config, sees almost nothing.**
   - Every crate root has `#![allow(clippy::all, clippy::pedantic)]`, so the
     workspace `pedantic = "warn"` setting has no effect.
   - The 4 new warnings that did surface (3 `redundant_clone`, 1
     `for_loops_over_fallibles`) are the branch's. They are cheap to fix.
   - The forced count (2,120) is upstream's: `collapsible_if` 410,
     `needless_return` 300, `identity_op` 183 and so on, mostly from the Go
     port's shape.

## 5. What each metric means, and its weaknesses

- **Cognitive complexity** (rust-code-analysis): a nesting-weighted count of
  breaks in linear flow (SonarSource's definition). Section 7 gives rca's
  exact rules and their Go mirror.
  - Per function, including the closures inside it. Closures are not listed
    as functions of their own.
  - Weakness: a flat 40-arm `match` that mirrors a Go `switch` scores lower
    than it reads, and deep `if let` chains score high. Ported code inherits
    Go's numbers. Use it for deltas on one function and for new functions,
    not as an absolute grade.
- **Cyclomatic complexity:** the number of linearly independent paths, so
  roughly the decision points plus 1. Every `match` arm and `?` counts,
  which makes Rust numbers higher than Go's for the same logic.
- **Escape hatches:** a lexical scan.
  - It misses env names built at run time and env vars read without a
    `GOPORT_`/`TSC_RS_` prefix. It lists the latter only through gates:
    `GOMAXPROCS`, `GODEBUG` and `PATH` are Go behavior, not hatches.
  - The switch and class rules are heuristics: read `--hatches` before you
    quote a single name.
  - Gate use counting for a common fn name (`enabled`) counts only uses in
    its own file or as `module::name`.
- **Duplication:** token-window hashing over production tokens, with
  comments removed and identifiers kept exact. A block needs ≥60 tokens and
  ≥8 lines (`DUP_MIN_TOKENS`, `DUP_MIN_LINES`).
  - Type-1 clones only: renamed copies are missed.
  - The percent is prod code lines in any block over all prod code lines.
  - New blocks per delta are matched by a hash of their token text.
- **Churn:** `git diff -U0 -M` line counts for `crates/`.
  - Each line is classified by the side it is on: generated, a test file, a
    `#[cfg(test)]`/`#[test]` item, or prod.
  - Inline test modules count as test, so a prod file with a new test
    module splits correctly.
  - Non-`.rs` files (libs, blobs) count as touched only.
- **Hygiene counts:** lexical.
  - `unsafe` tokens and `unsafe {` blocks; `.unwrap()`; `.expect(` (an
    `Option`/`Result` expect, or any method named `expect`);
    `panic!`/`unreachable!`/`todo!`/`unimplemented!`; `#[allow(` and
    `#![allow(`; TODO/FIXME/HACK/XXX in comments.
  - Many `expect`s and `panic!`s mirror Go `panic`s, and are correct.
- **Clippy:** `cargo clippy --workspace` (lib and bins, not tests) in a
  reusable detached worktree, `~/.cache/perf-rearch/quality/clippy-wt`, with
  its own target dir. It runs in two passes, deduplicated by primary span,
  `crates/` only:
  - the repo's config;
  - `-- --force-warn clippy::all`, which overrides the crate-root allows.
    Pedantic is not forced: it is mostly noise.

  Clippy runs in `report` only, not `history`, and is cached per git tree.
- **Maintainability Index:** dropped at Chad's request. It correlates mostly
  with size, and its thresholds are folklore.

### What counts as what

- **Generated files are excluded:**
  - a `@generated`, `Code generated` or `DO NOT EDIT` header in the first 15
    lines;
  - `lsp_generated/` and `*_generated.rs`.

  That covers the diagnostic catalogs, the LSP protocol and the Unicode
  tables. `ast/fields.rs`, "first generated, now edited by hand", is counted.
- **Test code:**
  - `tests/`, `benches/` and `examples/` directories;
  - `*_test.rs`, `*_tests.rs` and `tests.rs`;
  - files declared `#[cfg(test)] mod x;` (including `#[path]`), and their
    subdirectories;
  - inline `#[cfg(test)]` and `#[test]` items.

### Run time

Measured on MS-A2 under a load of about 12 to 38 from other agents.

- **One new `crates/` tree** (git archive, rca, lexing, duplication): about
  20 s idle, up to 30 s loaded.
- **Clippy:** about 30 s per pass warm, and 1.5 minutes or more cold or
  after a large change.
- **`report` with a base:** about 1 minute for a lane, plus any wait for a
  free cargo slot. `run-cargo-capped.sh` allows one Cargo run at a time
  here, so clippy queues behind another lane's build (20 minutes behind a
  fat-LTO build on 2026-11-04). `--no-clippy` skips it: about 30 s.
- **`history`** over 11 points (6 distinct trees): 3 min 7 s cold.
- **Cached reruns:** under 1 s. Snapshots are cached by the `crates/` tree
  hash in `~/.cache/perf-rearch/quality/snap`, and clippy by the commit tree.
- Peak RSS is under 2 GB.

## 6. Candidate gates (later; not active)

1. **No new behavior switch without a retirement entry.**
   - A delta that adds a `behavior`-class env name fails unless the lane's
     doc names the merge that removes it, or the reviewer accepts it as
     permanent.
   - A behavior switch older than N merges (suggested: 3) is listed every
     run until it is retired.
   - Diagnostic, verification and test-hook names must be feature-gated or
     go through one diagnostics module (one env name such as
     `GOPORT_DIAG=load,emit,…`), not a new env var each.
2. **No new duplicated block ≥ 60 tokens and ≥ 10 lines** in a delta, and
   duplication % must not rise by more than 0.05 points.
3. **Function complexity:**
   - no new function with cognitive > 25;
   - no existing function grown by more than 10 cognitive to above 25;
   - the count of functions with cognitive > 50 must not rise.
4. **Clippy under the repo config must not rise** (head: 5).
   - It is cheap and the new warnings are real.
   - The forced `clippy::all` count only reports, until upstream's 2,120 are
     dealt with.

## 7. Go-relative complexity (lane Q0, 2026-11-09)

The code-quality phase judges complexity against Go (`brief.md`,
"Code-quality phase"):

- **Ported code:** the target is *port bloat*, a Rust function much more
  complex than its Go counterpart at the pin.
- **Own code** (no Go counterpart): the limit is cognitive complexity 25.

`quality.sh report` maps every Rust production function to its Go
counterpart and ranks both cases. `quality.sh ranking <rev>` prints the
planning page (`docs/perf-rearch/cleanup/q0-ranking.md` is the one for
`e658d4d29`).

- **Code:**
  - `scripts/perf-rearch/gocog/` (Go, standard library `go/ast` only) scores
    the Go side.
  - `scripts/perf-rearch/gorel.py` maps and ranks.
  - `scripts/perf-rearch/gorel_test.py` tests both.
- **Go tree:** `~/.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc`,
  `internal/` and `cmd/`, read only. Override with `PERF_QUALITY_GO_ROOT`,
  and the toolchain with `PERF_QUALITY_GO` (default `~/.local/go/bin/go`).
  gocog is built with `GOTOOLCHAIN=local GOPROXY=off`, so nothing is
  downloaded.

### Metric definitions

Both sides use one definition: rust-code-analysis 0.0.25's Rust rules
(`src/metrics/cognitive.rs` and `cyclomatic.rs`, `impl … for RustCode` in
`~/.cargo/registry/src/*/rust-code-analysis-0.0.25`). gocog mirrors each rule
on the Go AST.

| Rule | Rust (rca) | Go (gocog) |
|---|---|---|
| +1 + nesting, and nesting +1 for the whole subtree (condition included) | `if` (not an else-if), `for`, `while`, `match` | `if` (not an else-if), `for` with a condition, init or post, `range`, `switch`, type switch, `select` |
| not counted, no nesting | `loop` | bare `for {}` (Rust's `loop`) |
| +1 flat | each `else` (else-if included), `let … else` | each `else` (else-if included) |
| +1 flat | labeled `break`/`continue` | labeled `break`/`continue`, `goto` |
| boolean sequence (below) | `&&`, `\|\|` | `&&`, `\|\|` |
| nesting +1 inside, figure counted in the enclosing function | closure | function literal |
| nesting reset, depth +1, figure counted in the enclosing function | nested `fn` | (none in Go) |

- **Boolean sequences** follow rca exactly, quirks included, so both sides
  agree:
  - The first `&&`/`||` of a sequence counts +1 and is remembered. A later
    operator counts +1 when it differs from the remembered one, which it
    does not replace.
  - The state is one value per function (a closure or function literal has
    its own) in source order. Only a nesting increment resets it.
  - A unary operator sets the state to "unary", so every later `&&`/`||`
    counts until the next reset. Rust's unary operators are `!`, `-` and
    deref `*`; gocog uses `!`, `-`, `+`, `^` and deref `*`, and skips type
    positions (`var x *T`, `.(*T)`).
- **Cyclomatic:** 1 per function and per closure or function literal, plus 1
  per `if`, loop, match arm or case clause (default included), select clause,
  `&&` and `||`. Rust's `?` counts too and has no Go equivalent, so Rust
  cyclomatic runs a little higher. The rankings use cognitive complexity.
- **Lines:** from `fn` to `}` and from `func` to `}`.
- **Known differences:**
  - rca does not look inside macro calls (`matches!`, `assert!`), so a
    condition there costs nothing on the Rust side.
  - Go `goto` and `select` have no Rust form.
  - gocog keeps generated Go files (`ast_generated.go` and others, flagged
    `generated`), because Rust ports some of that code by hand. rca's Rust
    side excludes generated Rust files (section 5).
- **Check:** `gorel_test.py` scores the same functions written in Go and in
  Rust (an else-if chain, the boolean quirks, a closure with a labeled loop
  exit) and requires gocog = rca for both metrics. The ported tree confirms
  it. On the 12,516 rows placed by a `// Go:` marker:
  - Rust cog equals Go cog in 76%, and is within 2 in 90%;
  - Σ Rust is 66,451 against Σ Go 65,356;
  - the median difference is 0 at Go cog ≥ 10 and ≥ 25, and 1 at ≥ 50.

### Closures and the fairness rule

Go often writes a helper as a closure inside a function; the Rust port may
lift it into its own function, or split a function for its own reasons. A
row compares like with like:

1. **Both figures include their closures.** A Go function's figure includes
   its function literals, one nesting deeper, as rca does for Rust closures.
   gocog also lists each named literal (`name := func`, `name = func`,
   `var name = func`) with two figures: its standalone figure, and its
   in-place contribution to the parent.
2. **A lifted Go closure is summed into the parent's row.** When a Rust
   function is a named closure of Go function G and G's own Rust function is
   in the same file family, it adds to that row. The family is the
   directory plus the file stem without `_pN`. The Rust function is a closure
   of G when its marker names the closure or, with no marker, its name does.
   - The Rust side adds the lifted function's figure.
   - The Go side swaps the closure's in-place contribution for its standalone
     figure, so neither side pays the closure's extra nesting.
   - Example: `get_completion_data` (599, with 18 lifted closures) against
     Go `getCompletionData` (529).
3. **Splits are summed.** Rust functions in one file family that map to the
   same Go function form one row. The head is the one named like the Go
   function. A split is found three ways:
   - a `// Go:` marker naming the Go function;
   - prose such as "body of Go `typeToTypeNode`";
   - a Go line range inside the Go function.

   Two functions of the same name (a `#[cfg(unix)]`/`#[cfg(windows)]` pair,
   or a trait method that calls the inherent one) are *variants*: one of
   them runs, so the row takes the larger.
4. **Nested Rust `fn`s** stay in their parent, as rca sums them, unless one
   maps to a Go function of its own (by marker or a high-confidence name).
   Then it gets its own row and its parent's figure leaves it out.
5. **Compound markers.** `// Go: a.go:1 A and a.go:9 B` (references joined
   by "and", "with" or a comma, outside parentheses) compares the Rust
   function with A + B. Example: `build_all_tasks` against `rangeTasks` +
   `buildOrCleanProject` + `buildOrCleanOrder`.
6. **Method families.** A marker naming one method that several Go types
   have, with no receiver, compares with the family's sum. Example:
   `Node::for_each_child` against every `ForEachChild` in `ast_generated.go`.
   A Rust enum dispatch ports a set of Go interface methods as one fn.

Not handled: a Rust closure that ports a Go top-level function (rca does not
name closures). It makes the Rust parent look larger. It is rare.

### Mapping

Each Rust production function (rca's list) is placed by the first of these
that succeeds:

1. **`// Go:` marker** (confidence `marker`): the `// Go: [<path>.go[:<line>]] <sym>`
   comment in the comment and attribute block right above the `fn`.
   - `<sym>` is `name`, `Type.name`, `(*Type).name` or `(r *Type) name`.
   - A marker naming the Rust function wins over the nearest one.
   - The Go file named must exist at the pin. Then the name is looked up in
     it: a function, then a named closure, then a method family. The marker's
     line number only breaks ties within ±2, because lines drift between
     pins.
   - When the first reference names nothing at the pin, the paragraph's
     other references are tried (`(at 673a5f17d713; ts#64554 generates it:
     …_generated.go:193 parsePreferenceIndentStyle)`).
2. **Prose** (`prose`), when no marker resolves:
   - "body/part/rest/arms/cases of Go `X`" and "split out of `X`";
   - a Go line range `checker.go:12579-12785` inside one function;
   - "Go `X`" or "Go fooBar".

   The last two count only when one name is a prefix of the other
   (`is_simple_type_related_to_kind` and `isSimpleTypeRelatedTo`).
3. **Name matcher** (`high`, `medium`, `low`, `ambiguous`).
   - **Names:** snake_case is normalized to lowercase without `_`. Rust
     names Go's exported twin `foo_exported`; otherwise the unexported Go
     function of a `Foo`/`foo` pair is taken. `T::new` is `NewT`.
   - **Candidates** come from the Go packages the Rust path names:
     `checker_p17.rs` maps to `internal/checker`, `frontend/x/` to
     `internal/x`. The `DIR_PKGS` and `STEM_PKGS` tables cover the rest
     (`emitter/`, `program/`, `declarations/`, root files). Then come the
     file's *affinity* packages, which hold ≥ 3 and ≥ 25% of the file's
     uniquely named matches.
   - **Filters,** in order: receiver (impl type), then Go file stem (the Rust
     stem without `_pN`). A different receiver is allowed only for pairs the
     markers show ≥ 3 times (`Checker` holding `Relater` and
     `NodeBuilderImpl` methods). A free function and a method match only
     when receiver or file agree.
   - **Confidence:**
     - `high`: one candidate in the path's packages, agreeing on receiver or
       file;
     - `medium`: one candidate there agreeing on neither (a name of 8+
       characters), or one in an affinity package that agrees;
     - `low`: one in an affinity package that agrees on neither (10+
       characters);
     - `ambiguous`: more than one left.
   - **Low and ambiguous matches are ranked as own code,** with a note. The
     report lists the ambiguous ones (12 at `e658d4d29`, all cog ≤ 1:
     unix/windows Go files, `incremental.Program` against
     `compiler.Program`).
   - **No name matching** in directories of our own (`effect/`, `gostd/`,
     `frontend/vfs/loadview/`, `load_view_scope/`). The same holds in files
     where most markers name Go outside the pin (`locale.rs` ports x/text).

Everything left is **own code**. That includes ports of Go outside the pin:
Go's standard library in `gostd/`, x/text in `locale.rs`, and Effect's Go
plugin. Their markers name that code; the own-code table shows the marker.

### Accuracy (at `e658d4d29`)

- **Coverage:** of 21,871 Rust prod functions, 12,737 are placed by marker,
  99 by prose, 240 by name (225 high, 15 medium) and 7 as lifted closures by
  name. 3 are low and 12 ambiguous; both are ranked as own code. 8,773 have
  no counterpart.
- **Name matcher against the markers:** where both place a function
  (11,560), they agree on 99.6%: high 10,711/10,750, medium 743/756, low
  54/54.
  - This is partly circular: the receiver pairs come from the markers.
  - Before the matcher was confined to the path and affinity packages, a
    whole-tree tier matched about 3 in 10 sampled functions right. That tier
    is gone.
  - The 15 medium name-only matches left were all checked by hand and are
    right.
- **Hand check, 60 functions:** the 40 largest by Rust cog, plus 20 drawn at
  random (seed 9, `gorel.sample_for_check`). **60/60 right.**
  - All 40 top functions are placed by marker. Each was checked against the
    Go signature, including the summed parts: `resolve` + `resolve_name`,
    `type_to_type_node` + `_body`, `check_binary_like_expression` +
    `_worker`, and the lifted closures of `get_completion_data`,
    `get_quick_info_and_declaration_at_location`, `verify_compiler_options`
    and `get_existing_node_tree_visitor`.
  - Of the 20 random functions, 11 are ported and right. 9 are own code and
    right: Effect, `gostd`, x/text, and perf code such as `flow_skip` and the
    load views.
  - Two more draws of 20 (the same seed over the row sets of earlier and
    later versions) were also 20/20, so 100 distinct functions in all.
- **Top 60 by Δ, audited:** one partial counterpart (`build_all_tasks`,
  +46) led to compound markers (+32 now). The rest are real differences
  against the named Go function. Some are perf code inside a port:
  - `get_normalized_absolute_path` (76 against 3) has fast paths;
  - `FilesParser::parse` (30 against 0) holds the parallel scheduler.
- **Markers naming nothing at the pin: 2,099.** Most are in `effect/` 1,095,
  `gostd/` 403, `locale.rs` 122, `frontend` (the JSON and filepath ports)
  135 and `ast` 87 (functions removed or renamed upstream).

### Output

- **`report`** shows:
  - the summary;
  - the top N rows by Δ and the top N/2 by ratio (Rust cog > 25);
  - the top N own-code functions over 25;
  - the ambiguous matches;
  - the per-area table.

  With `--base` it adds the Go-relative counts at both points, and the rows
  whose Δ (ported) or cog (own > 25) moved. That is the line a wave-2 lane
  quotes.
- **`report.json`:** `go_relative` holds `summary`, `areas`, every row of
  `port_bloat` and `own`, and `ambiguous`. `go_relative_delta` holds the
  delta. The full per-function `mapping` (confidence, Go id, the row it is
  summed into) stays in the cache file that `go_relative.cache` names.
- **A port-bloat row** has:
  - Rust `cog`/`cyc`/`sloc` (parts included) and `go_cog`/`go_cyc`/`go_lines`;
  - `diff` = Rust cog − Go cog, and `ratio` = Rust cog / max(1, Go cog);
  - the Go file and line, `conf`, and `parts` (closure, split or variant).

  Targets are Δ ≥ 10. The ratio ranking only lists Rust cog > 25, because
  small functions give noisy ratios.
- **Areas** (the wave-2 lanes, by path under `crates/ts_goport/src/`):

  | Area | Paths |
  |---|---|
  | checker | `checker/` |
  | frontend | `frontend/`, `program/`, `program.rs`, `scanner_util*` |
  | ls | `ls/`, with autoimport |
  | execute/build/emit | `execute/`, `emitter/`, `bin/`, `cmd/` |
  | effect | `effect/` |
  | other | everything else |

  `ranking` also gives subarea and per-file totals, so lanes can take
  disjoint file sets.
- **Run time** (MS-A2, load 20 to 27 from other agents):
  - a new `crates/` tree: about 2 to 3 s (git cat-file of the sources,
    mapping);
  - a new Go tree: plus under 1 s (gocog parses 15k functions in 0.4 to 0.9 s);
  - a cold gocog build with an empty Go build cache: plus about 4 s;
  - cached: 0.1 s.

  Caches live in `~/.cache/perf-rearch/quality/`: `go/` (Go tree + gocog
  source), `gorel/` (crates tree + Go tree + `GOREL_VERSION`) and `tools/`.
  It is cheap, so it is on by default.

### For wave-2 lanes

- **Split toward Go's helpers first.** A Rust helper with a `// Go:` marker
  that names a Go function becomes its own row against that function, and
  the parent's Δ falls by what moved.
- **A helper with no Go counterpart is own code** (limit 25), unless it is
  still a piece of one Go function. Then say so in its doc comment ("body of
  Go `X`", "part of Go `X`") and it is summed back into X's row.
- **Keep `// Go:` markers on ported functions.** They are what makes this
  ranking reliable: 99% of placements.
