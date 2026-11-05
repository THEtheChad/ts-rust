# perf/first-principles: the parity gate

`scripts/perf-rearch/parity.sh` checks that tsc-rs's output still matches Go `tsc` at the pin
(`3e9236361fa9`, `~/.local/bin/tsgo-oracle-3e9236361fa9`, LSP `tsgo-lsp-oracle-3e9236361fa9`)
byte for byte. The rewrite lanes run it on every change. A change passes when no item that was
good in the base on main is bad now.

```sh
scripts/perf-rearch/parity.sh quick <tsgo bin> --base ~/.cache/perf-rearch/parity/base-main   # ~4 min warm
scripts/perf-rearch/parity.sh full  <tsgo bin> --base ~/.cache/perf-rearch/parity/base-main   # ~17 min warm
scripts/perf-rearch/parity.sh compare <base dir> <run dir>
scripts/perf-rearch/parity.sh known <run dir>          # what is not good in a run
scripts/perf-rearch/parity.sh js                       # JS tsc 6.0.3 against Go (CLI tier)
scripts/perf-rearch/parity.sh help
```

The last line is `PARITY OK` (exit 0) or `PARITY FAIL` (exit 1). Before it come the lost items
(`LOST <item>: <status> <first difference>`), any item the run did not reach (when it was the same
mode as the base), harness `ERROR`s, new items that are not good, recovered items and FLAKY tests.
Only one gate runs at a time, under a lock: the scratch paths are fixed, so the paths in the
output agree with the cached Go results. The test tier builds and tests the checkout (`--checkout`,
default the worktree that holds the script). Every other tier runs `<tsgo bin>`.

## Tiers

Each item is MATCH (tsc-rs equals Go), DIFF, GO-UNSTABLE (two Go runs disagree, so the item is not
compared), GO-FAIL (Go timed out or panicked; fuzz only), ERROR (harness), SKIP, or for tests
PASS / FAIL / FLAKY / IGNORED / UNRUN. The good statuses are MATCH, PASS and FLAKY.

Go results are cached under `~/.cache/perf-rearch/parity/go-cache/<sha of the Go binary>/` and are
keyed by everything that goes into the run, including a project fingerprint (the commit plus the
mtimes of the `node_modules` dirs). On a warm cache a run starts only tsc-rs. When a cache entry
is made, Go runs twice (8 times for `--traceResolution` fuzz cases). The full tier runs tsc-rs
twice on cli and fuzz, and a difference between its own two runs is a DIFF (`rs-nondet`). That
is how the gate catches nondeterminism from parallel rewrites.

### cli: real projects × flag variants (`parity_cli.py`, `parity-projects.json`)

Each config runs these variants:

| variant | command |
|---|---|
| `noemit` | `-p CFG --noEmit --pretty false` |
| `emit` | `-p CFG --noEmit false --pretty false` |
| `decl` | emit plus `--declaration --declarationMap --sourceMap` |
| `incr` | emit plus `--incremental`, run twice (cold, then no-op) |
| `listfiles` | `--noEmit --listFiles` |
| `extdiag` | `--noEmit --extendedDiagnostics`: the Memory values are masked, the `* time` lines are dropped (Go and tsc-rs print a time line only when its duration is not zero), and the statistics padding is reduced to one space (the padding follows the widest value) |
| `pretty` | `--noEmit --pretty true` (colors, code frames) |
| `showconfig` | `--showConfig` |
| `build` | `-b CFG --verbose --pretty false`, run twice (cold, then up to date); the wall clock at the start of each message is masked |

What is compared: the exit code, stdout, stderr, and **every file the run writes** (sha256 of
each). Each side runs on a fresh **overlayfs view of the project at its own path**:
`unshare -rm`, then `mount -t overlay` with the project as the lower dir. The configured
`outDir`, `declarationDir` and `tsBuildInfoFile` therefore stay as they are, paths in the
output are the project's real paths, the project never changes, and the upper dir is exactly the
set of written files, including the `.tsbuildinfo` that `--noEmit` still writes for
composite/incremental configs, and files a second run rewrites or touches.

There are 170 items over 23 configs:

| project | configs | dir |
|---|---|---|
| ts-pattern | `src` (tsconfig.json), `tests` | baseline lane's `~/.cache/perf-rearch/projects/` |
| query | `core`, `core-legacy`, `core-prod` (query-core), `react`, `sync-persister` (also `-b`, a 3-project chain) | baseline lane |
| hono | `spec`, `build`, `solution` (only `-b` of the 9-reference solution, and `showconfig`) | baseline lane |
| zod | `zod-build`, `zod`, `resolution` | baseline lane |
| effect | `effect` (packages/effect/tsconfig.json, with the Effect plugin), `noeffect` (the baseline lane's plugin-free config) | baseline lane |
| umami | `root` | `~/.cache/lspdiff/projects/umami` (via the baseline lane's symlink) |
| t3code | `web`, `server` (`tsconfig.noeffect.json`; no `incr`, `pretty`) | baseline lane |
| vscode | `src` (`noemit`, `emit`, `extdiag` only: 11,000 files, about 25 s per Go run) | baseline lane |
| react-hook-form | `root`, `app` | `~/.cache/perf-rearch/parity-projects/` |
| svelte | `svelte`, `runtime` | parity-projects |

Each project is at the commit that the repo's `scripts/prepare-*-inputs.mjs` pins. The parity
lane's own copies are made by `scripts/perf-rearch/prepare-parity-projects.sh` (a shallow fetch
of the pin, then the project's package manager with a frozen lockfile and `--ignore-scripts`; for
svelte, its type generator). The repo's prepare scripts themselves pin the Node and npm binaries
by hash and only write under the main checkout's `target/`, so they were not run here. A
project's first existing dir is used, so the CLI tier prefers the baseline lane's dirs, which
the benchmarks use. quick runs `noemit`, `emit` and `extdiag` on 8 configs (24 items).

### tests: cargo suites and TestLocal (`parity_tests.py`)

The build is `scripts/run-cargo-capped.sh test --release --locked --workspace --no-run` plus
`-p ts_goport --test go_baselines --no-run`, which is the edit-loop release build of the
checkout. It then runs each test binary as `ci.yml`'s test and test-local jobs do: in its crate
dir, with `RUST_TEST_THREADS=1`, `TS_GO_REPO` set to the pin's `tsc/`, and its own `TSCTEST_TMP`
and `COMPILER_RUNNER_TMP`. Up to 4 binaries run side by side; each is its own process.

- all 38 workspace test binaries (full only);
- go_baselines, the default set (`--exact --skip compiler_runner::test_local`);
- go_baselines TestLocal in 4 shards (`COMPILER_RUNNER_SHARD=i/4`, `COMPILER_RUNNER_JOBS=8`).
  Every case comes from `COMPILER_RUNNER_RESULTS`: 137,682 rows of `<kind> <key>`, each compared
  with Go's `testdata/baselines/reference` (errors, JS/d.ts output, source maps, types, symbols,
  module resolution and more).

Items are `test/<binary>/<name>` (3,911) and `test-local/<kind> <key>`. A failed test of a
binary runs once more on its own, and is FLAKY if it then passes.

### lsp: differential LSP sessions (`parity_lsp.py`, `vendor/lspdiff`)

This tier uses lspdiff from `perf/lsp-concurrency` (931fad409). The vendored copy adds
`--go-cache`, a per-answer `items.json`, the watch scenarios (`scenario.mjs`, `fixtures.mjs`),
repeated tsc-rs runs and more Go runs (below). Up to 4 sessions run side by side (`--jobs`).

**Standard sessions** (`session.mjs`; umami, ts-pattern and, in full only, hono). Each session opens 12 files of the project. For
each file it asks for diagnostics, semantic tokens, inlay hints, document symbols and folding
ranges. At 6 identifiers per file it asks for hover, definition, type definition, references,
document highlight, signature help, prepareRename/rename and code actions; at the member
positions, for completion; and it asks for quick fixes for the first 3 errors. Then it types
into one file, asking for a completion per keystroke, and requests diagnostics again. Answers are
compared as normalized JSON (the root path is masked; completion items and WorkspaceEdit files
are sorted, since Go itself does not keep those orders). The client advertises no dynamic watch
registration, so both servers log "file watching: disabled". Automatic type acquisition (ATA) is
off (`disableAutomaticTypeAcquisition`), so an answer does not depend on when npm returns (see
"ATA in the standard sessions" below). A Go cache entry is keyed by the session, the project
fingerprint and the sha of the vendored lspdiff sources, so a change to the session script makes
new Go runs.

**Watch scenarios** (`scenario.mjs`). Here the client advertises what VS Code does:
`workspace.didChangeWatchedFiles` with `dynamicRegistration` and `relativePatternSupport`,
and `refreshSupport` for diagnostics, semantic tokens, inlay hints and code lenses. It answers
`client/registerCapability`, `client/unregisterCapability` and every other server request
with `null`, and records every server-to-client request and notification in order, with
payloads. A scenario's steps are its requests, which are compared like the standard answers,
plus checkpoints. A checkpoint waits until the server has been quiet for 1 s (`LSPDIFF_SETTLE_MS`)
with no request pending, then adds five items for what arrived since the last checkpoint:

| item | method | content |
|---|---|---|
| `<cp> watch events` | `watch/events` | every register and unregister, in order, with its watchers (or other `registerOptions`, such as `typescript-config-watch-id`) |
| `<cp> watch delta` | `watch/delta` | the registrations added and removed since the last checkpoint, sorted: the net effect, free of order |
| `<cp> watch active` | `watch/active` | every registration in force after the checkpoint, sorted |
| `<cp> publishDiagnostics` | `publishDiagnostics` | the pushed diagnostics, stable-sorted by uri |
| `<cp> server messages` | `server/messages` | every other request (`workspace/diagnostic/refresh`, `workspace/inlayHint/refresh`, `workspace/semanticTokens/refresh`, `workspace/codeLens/refresh`, progress, …) and notification, with payload, in order. Only `window/logMessage`, the server's log with timings, is left out |

Normalization: the project root is `<root>`, as for answers. Registration ids are
`<name> watcher <n>.<i>`, and the `<n>` comes from a process-wide counter (Go
`project/watch.go` `watcherID`). Go takes that counter both when it makes a `WatchedFiles` and
lazily in `Watchers()` (`computeWatchersOnce`), which its background tasks call. Each task is
its own goroutine (`project/background/queue.go:35`), so `<n>` depends on Go's schedule. On
watch-hono, the inferred project's `program files` watcher was 37, 42, 39 and 36 in four Go
runs, and 34 in all three tsc-rs runs. Every item masks `<n>` except one per session,
`end watch ids`: the sorted raw ids. It is compared too (it MATCHes where Go is stable) and gives
tsc-rs's rs-nondet check on the raw ids.

Scripts also let the server go quiet (300 ms) after each `didOpen`, so one snapshot's watch
update ends before the next snapshot starts. Without that, Go's concurrent background tasks
reorder registrations. In the probes, Go registered `auto-import watcher 11.0` before the older
`10.0`, unregistered 11 and registered 10 again. Under load, it also registered a second
`program files for /dev/null/inferred` watcher with a glob the configured project already held.
tsc-rs runs those tasks in order, and its sequence is what Go does when its tasks happen to run
in order (seen on a low-load run). Go runs 4 times when a scenario's cache entry is made
(`--go-runs 4`; standard sessions 2), and any Go run that differs makes the item GO-UNSTABLE.

| session | tier | what it exercises |
|---|---|---|
| `fsevents` | quick, full | fixture `watch-small` (a fresh copy in `scratch/lsp-fsevents/` before every server run): first load with one missing import; auto-import completion and `completionItem/resolve`; then, each on disk plus its `workspace/didChangeWatchedFiles` event, with diagnostics and a hover after it: create the missing `src/c.ts`, delete `src/b.ts`, rename `c.ts` to `c2.ts` (one event with the delete and the create), edit `package.json` `imports`, edit a dependency's `package.json` `types`, edit a file through its realpath behind the directory symlink `node_modules/linked-pkg -> packages/linked-pkg`, break and fix `tsconfig.json`, create `src/sub/d.ts` in a new dir under the include glob, open and close it; an overlay not on disk (`src/ghost.ts`), then created on disk while open, then closed |
| `fsevents-narrow` | quick, full | the same, with the workspace folder at `<root>/src`: `package.json`, `node_modules` and `tsconfig.json` are outside the workspace, so Go watches the directories the loader looked in there instead of covering them with `<root>/**/*` |
| `pkgjson-overlay` | quick, full | the fixture with `package.json` and `node_modules/fake-pkg/package.json` open with edited text *before* the first load; then the overlay edited, the dependency's overlay closed, the root overlay closed |
| `watch-ts-pattern-f12` | quick, full | the standard session's first load (12 files, diagnostics each), a member completion, auto-import completion on a new last line plus 3 resolves, the typing and the undo (one `didChange` per character, no completion per keystroke), and a `didClose` |
| `unsaved-ts-pattern` | quick, full | the first load starts from an open file that is not on disk (`src/lspdiff-unsaved.ts`): diagnostics, hover, definition and completion in it; then a file on disk opened (references across both), the buffer edited and closed |
| `watch-umami-f12`, `watch-hono-f12` | full | `watch` on umami and hono |
| `watch-narrow-umami-f12` | full | `watch` on umami with the workspace folder at `umami/src` |
| `second-project-query` | full | query: `query-core` loads (diagnostics, hover, completion), then a `react-query` file opens: a second tsconfig project's first load with the session's caches warm (diagnostics, hover, definition into query-core, references, completion), then auto-import completion and resolves in it |

Go dedupes watchers per glob (`watchRegistry.Acquire`), and inside the workspace it covers the
project with one `<root>/**/*` glob, plus `bundled:///libs/**/*` and `node_modules/**/*`. With the
workspace at the project root, the watch items therefore see the loader's lookups only outside the
workspace, which is why the `-narrow` sessions exist. In second-project, react-query's globs
equal query-core's except its own `node_modules`, so that is its only new registration.

**Repeat runs.** In full, tsc-rs runs three times on every session (standard and watch): twice as
is, and once with `GOPORT_PARSE_THREADS=1` (`--rs-runs 2 --rs-env GOPORT_PARSE_THREADS=1`). An
answer on which run 2 differs from run 1 is a DIFF with detail `rs-nondet`, and one on which the
single-worker run differs is `rs-schedule`, even when one of the runs matches Go. quick runs
tsc-rs once. `--server-logs` (lspdiff) writes each scenario run's `window/logMessage` stream to
`<run>.server.log` for diagnosis.

quick runs the same sessions as full, minus hono, watch-umami, watch-narrow-umami, watch-hono and
second-project, so quick compares directly against a full base.

**Referees.** An answer where Go's runs differ is GO-BROKEN in full (GO-UNSTABLE in quick), and a
DIFF of a refereed kind can be JS-AGREES, decided by JS tsserver 6.0.3. See "JS tsserver as the LSP
referee".

### fuzz: fixed-seed difffuzz batch (`parity_fuzz.py`, `vendor/difffuzz`)

This tier uses difffuzz from `feat/difffuzz` (033e55442). It runs cases 0 to N-1 of seed 1
(quick 1,500, full 30,000): mutated compiler and conformance cases of the pin (comments,
directives, JSDoc, deep nesting, Unicode, line endings, BOMs, truncation, splices, option and
tsconfig mutations, 30% with extra flags such as `--listFiles`, `--explainFiles`,
`--traceResolution`, `--showConfig`). Each case runs in its own fixed dir
`/tmp/parity-fuzz/<seed>-<i>` and is compared on exit, stdout, stderr and written files, with
difffuzz's signature as the DIFF detail. A DIFF writes a repro (`fuzz/diffs/<case>/repro`,
`go.out`, `rs.out`, `diff`).

### corpus: every pin test case through the CLI (`parity_fuzz.py --corpus`, full only)

This runs the 12,727 compiler and conformance cases that difffuzz can turn into a tsconfig
project, unmutated, through the real binary. TestLocal checks the same cases in-process through
the test harness; this tier checks them through the CLI (option parsing, file system, the
multi-checker program and the output writer).

## Run times

These were measured on MS-A2 at a 1-minute load of 15 to 45 (other lanes were busy). The tiers
run in sequence, except tests, which runs beside them. Wall times:

| tier | quick, warm | full, warm | full, first run (fills the Go cache) |
|---|---|---|---|
| cli | 12 s | 261 s (tsc-rs ×2) | 420 s |
| tests | 230 s (go_baselines only) | 260 to 300 s | 260 s |
| lsp | 50 s (7 sessions, 4 at a time) | 137 s (12 sessions, tsc-rs ×3) | 252 s (Go ×4 per scenario) |
| fuzz | 38 s | 516 s (30,000 × tsc-rs ×2) | 24 min |
| corpus | – | 135 s | 10 min |
| **total** | **about 4 min** (tests-bound), plus the go_baselines build after a change | **about 17 min** (994 s plus the test build) | about 45 min |

The lsp times are from 2026-11-04 on the head bin `tsgo-head-b2febcd86-goport-w1`, at a load of
55 (quick) and 32 falling to 7 (full). In the same window, the tier as it was before the watch
scenarios (the 3 standard sessions, one after another, tsc-rs once) would take about 50 s: its
tsc-rs runs took 26 to 30 s (umami), 7 s (ts-pattern) and 7 s (hono). The watch scenarios and the
two extra tsc-rs runs therefore add about 85 s to a warm full run, and about 10 s to quick, where
umami's standard session bounds the tier. Most scenario time is the settle wait at checkpoints
(fsevents: 17 checkpoints, 24 s per server run). The full gate that made this base took 21 min
(12:21 to 12:42 UTC, load 5 at the start and 50 at the end), with a cold `target/`.

The test build is incremental: 0.4 s with nothing changed. After a checker change, rebuilding the
go_baselines and workspace test binaries takes several minutes. A cold `target/` took about 25
min with sccache. The Go side is cached, so after a pin or Go-binary change the first run costs
about twice the warm time. The `js` mode takes about 9.5 minutes.

## ATA in the standard sessions (lane ND1, 2026-11-04)

`lsp/hono-f12-p6/0057` (the inlay hints of `runtime-tests/deno/ssg.test.tsx`) was a recurring
`rs-schedule`/`rs-nondet` DIFF: on about 1 run in 12, the parameter hint of
`console.error(...)` at 50:19 was `message:` (`@types/node` `console.d.ts`) instead of Go's
`...data:` (`lib.dom.d.ts`). That was not a tsc-rs bug. It is a race in Go's design that both
servers share:

- `ssg.test.tsx` is in no tsconfig, so it gets the inferred project (138 files, no
  `@types/node`). Inferred projects have type acquisition on, so adopting that snapshot starts ATA
  as a background task (Go `project/session.go:2027` `triggerATAForUpdatedProjects`; tsc-rs
  `project/session.rs` `trigger_ata_for_updated_projects`).
- ATA's first step runs `npm install --ignore-scripts types-registry@latest` in
  `~/.cache/typescript/7.1` (Go `project/ata/ata.go:378`; tsc-rs `project/ata/ata.rs` `init`).
  That takes about 0.5 s and uses the network. The typings it finds (the project's `@types/node`,
  `bun-types` and others) are then pending, and the next request that takes a snapshot adds them
  (Go `snapshot.go:551` `DidUpdateATAState`). The inferred project then has 754 files, and
  `Console.error` has three overloads, with the node one first.
- So an answer on the inferred project depends on whether npm returned before the request. Go
  gets through the first file's 55 requests in about 0.2 s, before npm returns. tsc-rs's first
  request, `bench.mts diagnostic`, takes 0.65 to 1.05 s (it waits for the 12 didOpens to load
  the projects; Go 0.12 s). So tsc-rs reached ssg.test.tsx close to the moment npm returned, and
  load or `GOPORT_PARSE_THREADS=1` tipped it. Under 6 parallel sessions with one parse thread,
  17 of 60 runs gave `message:`.
- With a stand-in npm (`scripts/perf-rearch/nd1/fake-npm/npm`): returning at once, Go and tsc-rs
  both answer `message:` (3 of 3 each); after 5 s, both answer `...data:` (3 of 3 each).

The 20 GO-UNSTABLE `references` items of hono, and the `rs-nondet` references items 0178, 0232,
0335, 0595 and 0635 of later runs, are the same race: their extra locations are in ATA's typings
(`~/.cache/typescript/7.1/node_modules/@types/node/*`, `bun-types`). ATA also changes
`~/.cache/typescript/7.1` itself, so the Go cache could go stale.

The fix is in the harness. Standard sessions turn ATA off for both servers
(`vendor/lspdiff/client.mjs` `initializeParams(..., { ata: false })`, which `session.mjs`
`runSession` uses). The watch scenarios keep ATA on. They settle at checkpoints and register its
watch globs (`program files for /dev/null/inferred`). The repro is `scripts/perf-rearch/nd1/repro.mjs`
(`--ata` turns the old behavior back on).

## LSP watch scenarios on the perf head (2026-11-04)

The full gate with the new LSP items ran on `tsgo-head-b2febcd86-goport-w1` (sha256
`e409ee41a731…`; the tests tier on this lane's checkout, whose `crates/` equal b2febcd86), against
`base-current` (`perf-b22c2a3e6-full`). Run dir:
`~/.cache/perf-rearch/parity/runs/20261104-062114-full-lspwatch/`. Result: **PARITY OK**, 186,611
items compared, 0 lost, 0 not run, 0 new items not good. cli 170 MATCH; tests 139,889 PASS,
1 FLAKY (the known `watch_ends_soon_after_sigint_or_sigterm`); fuzz the known `1:23080`; corpus
12,727 MATCH.

lsp: 2,601 items, 2,548 MATCH and 53 GO-UNSTABLE, no DIFF. The 2,093 standard items are as in the
base (2,045 MATCH, 48 GO-UNSTABLE). There are 508 new items (9 scenario sessions), and the three
tsc-rs runs agreed on every item of every session: no `rs-nondet`, no `rs-schedule`.

| session | items | MATCH | GO-UNSTABLE |
|---|---|---|---|
| fsevents | 101 | 101 | 0 |
| fsevents-narrow | 101 | 101 | 0 |
| pkgjson-overlay | 34 | 34 | 0 |
| watch-ts-pattern-f12 | 51 | 51 | 0 |
| unsaved-ts-pattern | 35 | 35 | 0 |
| watch-umami-f12 | 51 | 49 | 2: `load watch events`, `end watch ids` |
| watch-narrow-umami-f12 | 51 | 49 | 2: the same |
| watch-hono-f12 | 51 | 50 | 1: `end watch ids` |
| second-project-query | 33 | 33 | 0 |

The 5 GO-UNSTABLE items are Go's background race (see "Watch scenarios"). On umami, Go runs 1
and 2 registered `auto-import watcher` and dropped it again, and took counter values up to 78.
Runs 3 and 4 did not, and tsc-rs equals runs 3 and 4 byte for byte, raw ids included. On hono,
only the raw counter of the inferred project's watcher varies (Go 36 to 42, tsc-rs 34 three
times). These are not tsc-rs differences, so no new known diff is listed.

On the way there, before the quiet wait after `didOpen` and the 4 Go runs, the probes showed
DIFFs that were harness problems, not tsc-rs ones: Go's out-of-order background tasks
(a transient `auto-import watcher 11.0`, and under load an extra `/dev/null/inferred`
registration in pkgjson-overlay that a low-load Go run did not make), and the raw watcher
counter. Both are handled as described above.

What the watch items saw, on both servers: `typescript-config-watch-id`
(`workspace/didChangeConfiguration`), `root files for <tsconfig>`, `program files for
<tsconfig>` (`bundled:///libs/**/*` and, narrow, the directories above the workspace),
`auto-import` (`node_modules/**/*` per package dir), `program files for /dev/null/inferred`
(`~/.cache/typescript/7.1/node_modules/**/*`); 22 `workspace/diagnostic/refresh` and 9
`workspace/inlayHint/refresh` requests; `publishDiagnostics` for each tsconfig, with the
`bogusOption` error in fsevents. Neither server sent a semanticTokens or codeLens refresh in these
scenarios, so those are compared only as absent.

## JS tsserver as the LSP referee (lane H2, 2026-11-05)

The brief's "Better than Go" lets tsc-rs differ from Go where a mechanical referee shows it is
better. On the lsp tier the referee is **JS tsserver 6.0.3**
(`~/.cache/perf-rearch/js-tsc/node_modules/typescript/lib/tsserver.js`), driven over its own
protocol by `vendor/lspdiff/tsserver.mjs`. No third-party adapter (typescript-language-server or
the like) is involved.

### Order of checks and statuses

`parity_lsp.py` judges each answer in this order:

1. **tsc-rs determinism.** In full, tsc-rs runs three times, and a later run that differs from the
   first is a DIFF (`rs-nondet`, `rs-schedule`), whatever Go and JS say. In quick (one tsc-rs run),
   an answer that would become JS-AGREES needs one more tsc-rs session (`rs-confirm`) that gives it
   again; otherwise it is a DIFF `rs-nondet: the run made to confirm JS-AGREES differs from run 1`.
2. **GO-BROKEN.** This maps from the old GO-UNSTABLE: Go's 4 sessions differ on the answer. In full
   it is GO-BROKEN (good), because step 1 already ran and tsc-rs's three runs agree. In quick it
   stays GO-UNSTABLE (not compared), because tsc-rs ran once. A Go crash makes the whole session
   an ERROR, as before. JS is not asked here: GO-BROKEN comes first.
3. **JS-AGREES.** The answer is a DIFF of a refereed kind (below), and JS sides with tsc-rs on every
   point where tsc-rs's normalized answer differs from Go's. `detail` says how many points JS
   decided and names the first three (`JS sides with tsc-rs on 1 point: display: ...`).
   `referees.js.points` lists up to 20 of them.
4. Otherwise **DIFF**. When JS ran, `referees.js.reason` says why it did not decide.

JS replays a session only when the session has a DIFF of a refereed kind that passed step 1. On a
run with no such DIFF (every run so far), JS costs nothing. `--js-all` replays every session,
records JS's view of every item that is not MATCH, and writes the calibration to
`results.json` `jsCalibration`. `--no-js` turns the referee off. In comparisons against the base,
JS-AGREES and GO-BROKEN count as good, and every item whose status became one of them is listed
in its own section. That listing is `parity_compare.py`'s referee section from lane H1: this lane
adds only the LSP statuses and does not change the shared compare code.

### The driver (`tsserver.mjs`)

`TsserverClient` has `LspClient`'s interface (`request`, `notify`, `settle`, `shutdown`, `events`).
`session.mjs` `startServer` makes one for `{ tsserver: <path> }`. So `runSession` and every
`scenario.mjs` scenario replay the same steps on tsserver: the same files with the same text, the
same `didChange` edits, the same requests at the same positions, and the same file-system steps.

- **Process:** `node --max-old-space-size=8192 tsserver.js --disableAutomaticTypingAcquisition
  --canUseWatchEvents --suppressDiagnosticEvents --noGetErrOnBackgroundUpdate --locale en`. ATA is
  off in every session, the watch scenarios included. Those scenarios keep ATA on for the LSP
  servers, but their refereed answers are on configured projects.
- **Initialize:** `configure` with the session's `userPreferences`, and Go's defaults where
  tsserver's differ: `providePrefixAndSuffixTextForRename`, `allowRenameOfImportPath`,
  `includeCompletionsForImportStatements`, `displayPartsForJSDoc`,
  `provideRefactorNotApplicableReason` (Go `lsutil.NewDefaultUserPreferences`; rename.go treats
  prefix/suffix as on unless turned off), plus `useLabelDetailsInCompletionEntries` and
  `interactiveInlayHints`. Then `compilerOptionsForInferredProjects` with Go's inferred-project
  options (`project/project.go` `NewInferredProject`). Pull diagnostics get tags and related
  information only when the client's `textDocument.diagnostic` capability asks, as in Go
  `DiagnosticToLSPPull`. The session asks for neither.
- **Documents:** `didOpen` becomes `open`, with the text and a `scriptKindName` from the language
  id. Each LSP content change becomes one `change` (1-based line/offset of the text before it).
  `didClose` becomes `close`.
- **File-system events:** tsserver asks for watchers (`createFileWatcher`,
  `createDirectoryWatcher`). Each `workspace/didChangeWatchedFiles` change goes, as one
  `watchChange`, to every watcher on that file, on its directory, or on a parent when the watcher is
  recursive. A watcher path behind a symlink also matches the event's realpath.
- **Positions:** tsserver uses 1-based `{ line, offset }` in UTF-16 code units, LSP 0-based
  `{ line, character }` in UTF-16 code units: line − 1, offset − 1. Offsets (semantic
  classifications, inlay hint spans) are converted through the open document's text.
- **Lib files:** `<typescript>/lib/lib.*.d.ts` becomes `bundled:///libs/lib.*.d.ts`.
- **Cache:** `~/.cache/perf-rearch/parity/js-cache/<tsserver sha>/lsp/<session>-<project
  fingerprint>-<sources sha>/js.jsonl`. The tsserver sha covers `tsserver.js`, `_tsserver.js` and
  `typescript.js`. The sources sha covers the session-scripting sources (`client`, `fixtures`,
  `project`, `scenario`, `session.mjs`) plus `tsserver.mjs`. JS runs once: it is single-threaded,
  and a session is one process. The Go cache key now hashes only the session-scripting sources, so
  changes to the diff code (`lspdiff.mjs`, `referee.mjs`) no longer make new Go runs. This change of
  key made one new Go fill.

### Mappings and normalized shape, per kind (`referee.mjs`)

All three servers' answers go through the same normalizer, after the root is masked. Each normalized
answer has *scalars* (one point each), *sets* (list answers: a key per element with every refereed
field, and a residue of the fields JS cannot referee) and *rest* (everything else). Two rules apply
to every kind:

- **URIs are compared without percent-encoding.** Go writes `node_modules/%40types`, Node writes `@`.
- **A range in a `bundled:///libs` file is compared by the text of its lines, not by line numbers.**
  Go's libs (the pin's `internal/bundled/libs`) and JS 6.0.3's differ, for example by 1,470 lines in
  `lib.dom.d.ts`. So a location becomes `«interface Console {»:10-«...»:17`.

| LSP request | tsserver | refereed (key / scalars) | not refereed (residue / rest) |
|---|---|---|---|
| `textDocument/diagnostic` | `syntacticDiagnosticsSync` + `semanticDiagnosticsSync` + `suggestionDiagnosticsSync` (Go `getAllDiagnostics` order; declaration diagnostics are in the semantic ones in both). Category error/warning/message/suggestion → severity 1/2/3/4. Go's `reportStyleChecksAsWarnings` applies: 6133, 6138, 6192, 6196, 7027–7030 error → warning | per diagnostic: range, severity, code, message text (with its chain), tags, related information (uri, range, message) | `source`, `data`; the order |
| `textDocument/hover` | `quickinfo`: `"```typescript\n" + displayString + "\n```\n"`, then documentation and `*@tag* — text` | display text (inside the first fence), documentation as plain words (links `[x](y)` → x, `{@link X}` → X, emphasis, inline code, ` — ` and whitespace runs removed), range | anything else |
| `textDocument/completion` | `completionInfo` (trigger character and kind). Kind by Go `getCompletionsSymbolKind`. An optional member of a member completion gets `?` on its label and its name as insert text (Go `createLSPCompletionItem`). `labelDetails` from tsserver's, or the auto-import source | isIncomplete; per item: label, kind, sortText, insertText, `labelDetails.description` | data, filterText, textEdit, commitCharacters, tags, `labelDetails.detail`; itemDefaults; the order (free, as in lspdiff) |
| `completionItem/resolve` | `completionEntryDetails` for the item's name, source and data. detail = the code actions' descriptions, or the display parts. Edits of the item's file | label, detail, documentation words; the additional edits as a set | – |
| `textDocument/definition` | `definitionAndBoundSpan` → LocationLink (origin = bound span, target selection = span, target range = context span) | origin range; per target: uri, selection range | target range (JS 6 gives the initializer for a function-valued const, Go the declaration); the order |
| `textDocument/typeDefinition`, `implementation`, `references` | `typeDefinition`, `implementation`, `references` (all refs; the session asks includeDeclaration) | per location: uri, range | the order |
| `textDocument/documentHighlight` | `documentHighlights` (filesToSearch = the file); `writtenReference` → 3 (write), `definition`/`reference` → 2 (read), `none` → 1 | per highlight: range, kind | the order |
| `textDocument/signatureHelp` | `signatureHelp`. The trigger reason as Go derives it from the context (none without one, as the session sends). label = prefix + parameters joined by the separator + suffix. Documentation is the comment only (Go `commentOnly`) | activeSignature, activeParameter; per signature: label, parameter labels, documentation words | the order |
| `textDocument/prepareRename` | `rename`. canRename → { range: triggerSpan, placeholder: displayName }; refused → an error with `localizedErrorMessage` (Go's error message) | range, placeholder, error message | – |
| `textDocument/rename` | `rename` locs → `changes`, newText = prefixText + name + suffixText | per edit: uri, range, newText | the order |
| `textDocument/inlayHint` | `provideInlayHints` over the range; Type/Parameter → kind 1/2, whitespaceBefore/After → padding | per hint: position, label text **without whitespace** (Go joins an interactive hint's parts without the spaces: `uniquesymbol`, `Record<string,number>`), kind, padding | part locations, tooltip, textEdits; the order |
| `textDocument/documentSymbol` | `navtree` children: `alias` items (imports) dropped, source order (navtree sorts by name), kind by Go `getSymbolKindFromNode` | per symbol: path of names, kind, range, selection range | detail, tags; the order |
| `textDocument/semanticTokens/full` | `encodedSemanticClassifications-full` format 2020. TS types class, enum, interface, namespace, typeParameter, type, parameter, variable, enumMember, property, function, member (→ method); modifiers declaration, static, async, readonly, defaultLibrary (local is not in the client legend). Encoded in Go's legend: Go's type and modifier order, filtered by the client's capabilities | per token: line, character, length, type, modifiers | resultId; the order |
| `textDocument/foldingRange` | `getOutliningSpans`: textSpan → start/end line and character; kind comment, imports or region | per range: start, end, kind | collapsedText; the order |

**Order has no JS referee.** For every list but completion items, the elements that Go and tsc-rs
both have must be in the same order. A changed order is a DIFF (`... the order differs from Go's
(order has no JS referee)`), even when JS has tsc-rs's order. The brief lists pull-diagnostic order
as unrefereed, and `tsserver.mjs` builds some lists itself (the three diagnostic commands).

**Fields outside the referee.** If Go and tsc-rs differ in a residue of an element they both have,
or in rest, the answer is a DIFF (`... differs outside the refereed fields`). If they differ only
where the normalizer looks away (whitespace in an inlay label, markdown markup), that is a DIFF too
(`no refereed point differs`). JS-AGREES needs at least one refereed point and JS's agreement on
all of them.

**No referee:**

- `textDocument/codeAction` (quick fixes and refactors; the driver does not ask tsserver);
- the watch items `watch/events`, `watch/delta`, `watch/active` and `watch/ids`
  (client/registerCapability has no tsserver equivalent; its watchers are its own);
- `publishDiagnostics` and `server/messages` (pushed diagnostics and refresh requests);
- the order of every list but completion items, pull-diagnostic order included;
- the kinds that failed the calibration below.

### Calibration

The calibration ran the full lsp tier with `--js-all` on `tsgo-head-efef52257-rel`
(`runs/h2-calib-2/lsp`, all 12 sessions, Go cache refilled). On every answer where tsc-rs equals
Go (MATCH), it checked how often JS's normalized answer equals Go's too (refereed fields only;
lists as sets). A kind is refereed at **90% or more**. Below that, a JS answer differs from Go's
too often for reasons that have nothing to do with tsc-rs, so JS's verdict would be noise.

| kind | JS = Go on MATCH answers | refereed |
|---|---|---|
| `textDocument/prepareRename` | 36/36 (100%) | yes |
| `textDocument/definition` | 217/218 (99.5%) | yes |
| `textDocument/rename` | 24/25 (96.0%) | yes |
| `textDocument/references` | 189/197 (95.9%) | yes |
| `textDocument/diagnostic` | 133/142 (93.7%) | yes |
| `textDocument/documentHighlight` | 199/216 (92.1%) | yes (umami's `.tsx` files 59/72: JSX tags) |
| `textDocument/signatureHelp` | 192/216 (88.9%) | no |
| `textDocument/typeDefinition` | 177/216 (81.9%) | no |
| `textDocument/foldingRange` | 26/36 (72.2%) | no |
| `textDocument/semanticTokens/full` | 25/36 (69.4%) | no |
| `textDocument/hover` | 160/240 (66.7%) | no |
| `textDocument/documentSymbol` | 23/36 (63.9%) | no |
| `completionItem/resolve` | 12/19 (63.2%; 2 more with no JS step: JS lists other auto-imports) | no |
| `textDocument/inlayHint` | 20/36 (55.6%) | no |
| `textDocument/completion` | 163/300 (54.3%) | no |
| `textDocument/implementation` | not asked by any session | no |

The list is `referee.mjs` `REFEREED_KINDS`. The first pass (`runs/h2-calib-1`, all but the fixture
sessions) found these mapping bugs. All are fixed, and none is left among the remaining
disagreements:

- tags on pull diagnostics (Go sends none without the client capability);
- navtree's alias items and name order;
- Go's prefix/suffix rename default;
- `%40` in Go's URIs;
- lib line numbers;
- the signature help trigger reason (Go: none without a context, so not "invoked"; JS had answered
  where Go gives null);
- signature documentation tags;
- `{@link}` rendering;
- prepareRename refusals (Go answers with an error);
- definition target ranges (now a residue).

**What is left is real JS 6 against Go 7 behavior**, each seen on a Go-equal answer:

- **Type display.** Go sorts union members (`"" | "eight" | ... | "zero"`, JS in declaration
  order) and object members, prints `typeof unset` where JS prints `unique symbol`, writes
  `readonly [ {...} ]` with spaces, adds `(distributed)` and constraints to type-parameter hovers,
  and drops JS's `\nimport X` line on alias hovers. These differences hit hover, signatureHelp,
  inlayHint and completion details.
- **Libs.** Go's newer `lib.dom.d.ts` and es2026 libs (`Math.sumPrecise`, `GPU*`, `Serial*`) change
  global completions. The lib anchoring handles moved lines, not changed ones.
- **Missing or extra answers.** Go gives no typeDefinition for `export type { X }` specifiers, no
  documentation on auto-import resolves and type-argument signature help, and no `80006`-style
  refactoring suggestions. JS lacks Go's `TS2344` in one ts-pattern test and Go's `6385`
  deprecation suggestion in query-core. They differ on overload-error elaboration (`TS2769`: Go
  gives "The last overload gave the following error", JS "Overload 1 of 2"), and on the span of an
  unused parameter.
- **Documents.** Go's document symbols name property assignments (`context.env`, JS `<unknown>`),
  normalize string names to `"..."`, and list `export type { ... }` names. Go's folding ranges
  start the imports fold after a leading comment line and fold the header comment.
- **Semantic tokens.** Go classifies some callable properties as property (JS: method) and some
  `const` functions as variable (JS: function).
- **JSX highlights.** Go highlights `<Tag` (read), JS the tag name (text).
- **Overlays.** In `pkgjson-overlay`, tsserver does not resolve modules through open
  `package.json` overlays, which Go and tsc-rs do: diagnostics 2/4, hovers 2/4 there.
- **Auto-import edits.** Go always ends the import with `;`. JS follows the file (no semicolons in
  hono's `bench.mts` and react-query). JS is the better answer here, but its kind
  (completionItem/resolve) is not refereed.
- **Go-runs-differ references.** The 22 GO-BROKEN references of hono differ between Go's runs only
  in order. As sets, tsc-rs and every Go run are equal.

Calibration cost: JS's cold sessions took 228 s together (umami 95 s; the others 6 to 20 s). They
ran 4 at a time beside the tsc-rs runs, at a load of 25 to 60.

### Self-test

`node scripts/perf-rearch/vendor/lspdiff/selftest/referee-selftest.mjs` (`--unit` for part 1 only)
prints `referee self-test OK`.

- **Part 1** (19 checks) tests the judge on hand-made answers:
  - rs-only and go-only elements, with and without JS;
  - points where tsc-rs equals Go;
  - scalars;
  - residue and rest differences (not refereed);
  - a whitespace-only inlay difference (no refereed point);
  - diagnostic order (no referee) and completion order (free);
  - no normalizer and no JS step;
  - lib anchoring, with `interface Console {` on Go line 43687 and JS line 42785;
  - the semantic-token legend.
- **Part 2** writes a two-file project to `~/.cache/perf-rearch/parity/scratch/referee-selftest/`.
  It runs lspdiff with Go's LSP server as Go and `selftest/proxy-lsp.mjs` as tsc-rs: Go behind a
  proxy that rewrites chosen answers. It checks that:
  - a hover rewritten to JS's union order is JS-AGREES (with `--js-kinds` hover);
  - a hover rewritten to `banana` is DIFF, with JS's reason;
  - a references answer that drops a location JS has is DIFF (`go-only ...: JS has it`);
  - a rewrite made only in run 1 is DIFF `rs-nondet` through the confirming run;
  - every other answer is MATCH, and `--js-all` writes the calibration;
  - with the default kinds, the hover stays DIFF (hover is not refereed).

## Base on main (91f3d021c)

The base is `~/.cache/perf-rearch/parity/base-main/`, made on 2026-11-03 with
`tsgo-main-91f3d021c-goport` (sha256 `bbf40914b644…`). The test tier used the same commit
(the worktree at 91f3d021c, edit-loop release build). The base was assembled from three full
runs of the same binary (`meta.json` "assembled"): lsp and corpus from the first; cli rerun with
the noeffect config and project fingerprints; tests (with the retry) and fuzz (30,000) rerun.
`known.txt` lists every item that is not good.

| tier | items | good | known DIFF | not compared |
|---|---|---|---|---|
| cli | 170 | 163 MATCH | 7 | 0 |
| tests | 141,593 | 139,861 PASS + 1 FLAKY | 0 | 1,731 IGNORED (1,728 TestLocal rows are Go `skip` subtests; 3 `#[ignore]` tests) |
| lsp | 2,093 | 2,045 MATCH | 0 | 48 GO-UNSTABLE |
| fuzz | 30,000 | 29,955 MATCH | 1 | 35 GO-FAIL, 9 GO-UNSTABLE |
| corpus | 12,727 | 12,727 MATCH | 0 | 0 |

A rerun of quick against this base gave PARITY OK (141,002 items compared). A rerun of full gave
one loss, `cli/query/react/extdiag` (`rs-nondet`): one tsc-rs run had no `Emit time:` line. That
is the zero-duration rule above, which the harness now handles, and is not a tsc-rs issue. The
22 extdiag items, rerun with 4 tsc-rs runs each, then all match except the known
`effect/effect`. The FLAKY test failed and passed again in that run. A negative control
gave PARITY FAIL with 116 lost items (6 CLI items, 110 fuzz cases). The control was a wrapper
binary that adds one stdout line in query-core and in fuzz cases `1-7*`.

### Known diffs on main (tsc-rs ≠ Go; not compared, listed when they recover)

1. **Effect plugin, 7 items: `cli/effect/effect/{noemit,emit,decl,incr,listfiles,pretty,extdiag}`.**
   tsc-rs has the Effect language-service plugin built in (`effect-tsgo`, the `effect_cases`
   tests), and Go does not. With effect's own tsconfig (`plugins: @effect/language-service`),
   tsc-rs's `.tsbuildinfo` has `"version": "7.1.0-beta+effect-tsgo.0.51.1"` and an `"effect"`
   options block, and `--extendedDiagnostics` reports more symbols, types and instantiations
   (1,652,242 / 769,886 / 2,577,236 against Go's 1,561,819 / 721,842 / 2,446,863). Stdout,
   diagnostics and the emitted JS/d.ts are equal. This is intended behavior, not a port bug. The
   plugin-free `cli/effect/noeffect/*` items all MATCH.
2. **`fuzz/1:23080`: the `--traceResolution` order.** Go prints
   `File '.../model/package.json' does not exist according to earlier cached lookups.` 27 times
   in 30 runs, and `... does not exist.` 3 times: the order in which its resolver threads fill
   the cache decides. tsc-rs always prints the second form. That is Go's own race, and the 8 Go
   runs of the cache did not hit the minority answer. Case `fuzz/1:5391` is the same race, and
   there Go's runs disagreed (GO-UNSTABLE).

There were two more findings on the way to the base, and both are harness issues, not tsc-rs
issues. They are fixed and not in the base:

- `extdiag` padding follows the widest value, which is a masked memory value. This made svelte
  and react-hook-form look like DIFFs, and svelte like `rs-nondet`.
- `-b --verbose` prints the time of day.
- A `--extendedDiagnostics` time line (`Emit time`, `Bind time`, `Check time`, `Config time`,
  `BuildInfo read time`, `Changes compute time`) appears only when its duration is not zero
  (`statistics.go` at the pin; tsc-rs's `statistics.rs` does the same), so it comes and goes with
  the clock. The time lines are now dropped.

### Not compared on main

- **lsp GO-UNSTABLE (48).** Go answers differently from run to run on 5 umami completions, 23
  ts-pattern completions and 20 hono references, even after lspdiff's normalization.
- **fuzz GO-FAIL (35).** These are 26 Go timeouts (60 s, deep-nesting cases under load) and 9 Go
  nil-pointer panics: 6 in `diagnosticwriter.writeCodeSnippet`, 2 in
  `tsoptions.CreateDiagnosticForNodeInSourceFile` and 1 in `ast.IsAccessExpression`. difffuzz
  knows two Go crashes, TypeScript #65078 and #65079, that tsc-rs reproduces on purpose. tsc-rs
  is not compared on these cases, so a change to its crash reproduction would not show.
- **fuzz GO-UNSTABLE (9).** Six are `--traceResolution` races (`fuzz/1:2813`, `5391`, `12500`,
  `12702`, `15106`, `20151`). Three have no trace flag: `19122`, `29616`, and `20360` (`--noCheck`).
- **FLAKY (1).** `test/tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm` has a timing
  bound that fails under load. It passed when it ran alone, and passed in the other three runs.

## JS tsc against Go (`parity.sh js`, `~/.cache/perf-rearch/parity/js-vs-go/`)

The JS counterpart of the pin is **`typescript@6.0.3`**. The pin is the gitHead of npm
`typescript@7.1.0-beta` (Go, version string `7.1.0-beta`), and 6.0.3 is the last JS release
and the pin repo's own `devDependencies.typescript` (`^6.0.3`). It is installed in
`~/.cache/perf-rearch/js-tsc/` (`pnpm add --ignore-scripts typescript@6.0.3`) and runs on the
CLI tier against the same cached Go results.

Of 170 items, **14 are equal and 156 differ**. That is expected: these are not tsc-rs bugs, and
**Go wins**. The equal items are `ts-pattern/src` noemit and pretty, `query/core-prod` noemit and
pretty, `zod/zod-build` noemit and emit, `zod/resolution` noemit and emit, `react-hook-form/root`
noemit and pretty, and svelte `svelte`/`runtime` emit and decl. The differences fall into these
groups (an item can be in more than one):

| difference | items | detail |
|---|---|---|
| emitted files | 91 | `.js.map` mappings (12,468 files), `.d.ts.map` (9,049), `.d.ts` text (1,358), `.js` text (572: effect, hono, react-hook-form, t3code, umami), and `.tsbuildinfo` in every project (format and version) |
| diagnostics | 40 | Go-only errors: TS7031 in checkJs `.js` files (svelte, hono: Go's JSDoc rules, `tsc/CHANGES.md`), TS5115 infinite instantiation (zod), TS2322/2345/2769/2578/2344/2416 (effect, hono, t3code, umami, vscode), TS5055 (umami incr: on the second run Go reads the emitted `build/*.js` through `include: **/*.js`). Type display order: Go prints JSX `children` last (`{ alignItems…; children: Element[] }`), JS first |
| removed options | 14 | TS 7 errors where 6.0 deprecates: Go TS5102/TS5108 "has been removed" against JS TS5101/TS5107 "is deprecated" (ts-pattern tests `downlevelIteration`, `moduleResolution=node10`; react-hook-form app `esModuleInterop=false`) |
| `--extendedDiagnostics` | 22 | JS prints other statistics (`Lines of Library`, `I/O read`, …) and different counts |
| `--showConfig` | 22 | Go lists more effective options |
| `--listFiles` | 21 | Go lists `bundled:///libs/lib.*.d.ts`; JS lists `node_modules/typescript/lib/lib.*.d.ts` |
| `-b --verbose` | 2 | JS names projects by absolute path, Go by relative path |
| symlinked root | umami | JS realpaths the symlinked project root (`~/.cache/lspdiff/...`); Go keeps the path it was given |
| JS out of memory | 3 | vscode: JS tsc aborts with a heap OOM (exit -6) at Node's default heap |

Details are in `js-vs-go/js/items/<item>/diff.txt` and `js-vs-go/js.log`.

## Not covered

- **The tsc-rs binary in the test tier.** That tier tests the source of `--checkout`. A lane must
  build its binary from the same tree.
- **Cargo doc tests.** `cargo test --workspace` runs them in CI; this gate does not.
- **Wasm** (`npm/wasm`, `scripts/wasm/corpus.mjs`), Windows and macOS (path case, separators,
  case-insensitive file systems).
- **Watch mode** (`--watch`, `-b -w`), edits between incremental or `-b` runs (the second run is
  a no-op only; `scripts/goport/compare-build.sh` has edit scenarios), `--build --clean`,
  `--init`, `--help`, `--version`, `--generateTrace` and `--generateCpuProfile` output.
- **LSP beyond the scripted sessions.** Workspace symbols, call hierarchy, document links,
  formatting, cancellation, and the session-level concurrency that perf/lsp-concurrency
  benchmarks (answers are compared, not timing or interleaving). The watch scenarios have these
  limits:
  - The client sends every file-system event, whether or not a registered glob matches it (VS Code
    sends only matching ones). Both servers get the same events, and the globs are compared.
  - Events outside the scenario's own steps aren't sent: there are no events for what the server
    writes, and none for real project dirs (only the fixture is changed).
  - A checkpoint's items depend on a 1 s quiet window. A registration that arrives after it is
    counted at the next checkpoint (the same for both servers).
  - Go's debounced work (500 ms diagnostics refresh, 30 s idle cache clean) is reached only
    where the scenario waits long enough. No step idles for 30 s.
  - Within the workspace, Go watches whole trees (`<root>/**/*`). Seen files and missing
    directories become separate globs only outside it (the `-narrow` sessions, the inferred
    project's typings dir).
  - The ordered `watch events` item is GO-UNSTABLE wherever Go's background tasks race (the first
    load of umami). There, only `watch delta` and `watch active` protect the result.
  - Not exercised: a symlink event on the link path itself, case-only renames, and directory
    deletes with children.
- **The msgpack/JSON API** (`scripts/goport/api_oracle.py`) and the native-preview client suite
  (`np-suite.sh`).
- **Not compared at all:** GO-UNSTABLE and GO-FAIL items, the masked `--extendedDiagnostics`
  memory and time values, and the Go-crash cases.
- **Nondeterminism that two tsc-rs runs do not show.** Full mode runs tsc-rs twice on cli and
  fuzz, and three times on lsp (the third with `GOPORT_PARSE_THREADS=1`). A race that needs more
  runs or other load can still pass, and other worker settings (`GOPORT_MAPPED_THREADS`,
  large-program workers) are not varied.
- **Inputs.** 10 projects (23 configs), 4 LSP projects plus the watch-small fixture, 42,727 test-case runs (30,000 fuzz plus
  12,727 corpus). Corpus cases with `@link`, `@symlink`, `@currentDirectory`, their own
  `tsconfig.json` or `..` paths are skipped by difffuzz's seed parser (TestLocal covers them).
- **Project dirs owned by another lane.** The baseline lane owns most project dirs. A reinstall
  there changes the fingerprint and therefore the Go cache key, never the result silently. But a
  changed input can turn base MATCHes into new items, so rerun the base after one.

## Files

- `scripts/perf-rearch/parity.sh`: the driver (`help`).
- `parity_cli.py`, `parity_tests.py`, `parity_lsp.py`, `parity_fuzz.py`: the tiers. Each prints
  its usage with `--help`.
- `parity_compare.py`: `merge`, `compare` and `known`.
- `parity-projects.json`: the CLI projects; `prepare-parity-projects.sh`: the parity lane's
  project copies.
- `vendor/lspdiff/` (perf/lsp-concurrency 931fad409, plus `--go-cache`, `items.json`,
  `--scenario`, `--rs-runs`, `--rs-env`, `--go-runs`, `--server-logs` and the JS referee's `--js`,
  `--js-cache`, `--js-all`, `--js-kinds`; `scenario.mjs` holds the watch scenarios and
  `fixtures.mjs` the watch-small fixture; `tsserver.mjs` drives JS tsserver, `referee.mjs` holds
  the normalizers and the JS-AGREES judge, `selftest/` their self-test) and
  `vendor/difffuzz/` (feat/difffuzz 033e55442, unchanged).
- State: `~/.cache/perf-rearch/parity/` holds `base-main/`, `js-vs-go/`, `go-cache/`, `runs/`,
  `logs/` and `scratch/`.


## Clock steps on this machine (2026-11-04)

`systemd-timesyncd` steps the wall clock back about 1.14 s every 32 s here.
Checks that rely on mtime order (`-b` up-to-date, incremental) can then flip
in Go and in tsc-rs alike. The CLI tier measures `CLOCK_REALTIME −
CLOCK_MONOTONIC` around every `build` and `incr` side.
- A side that saw a step of more than 5 ms reruns, up to 6 times.
- If every attempt saw a step, the item is an ERROR, never a DIFF.
- Each item records `clockRetries`.

`scripts/perf-rearch/clock-steps.py` logs the steps, and
`scripts/perf-rearch/b-race-repro.py` reproduces the effect (`--step-back`
forces it).
