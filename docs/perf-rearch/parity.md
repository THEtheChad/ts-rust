# perf/first-principles: the parity gate

`scripts/perf-rearch/parity.sh` checks that tsc-rs's output still matches Go `tsc` at the pin
(`3e9236361fa9`, `~/.local/bin/tsgo-oracle-3e9236361fa9`, LSP `tsgo-lsp-oracle-3e9236361fa9`)
byte for byte, except where a referee (see "Referees") accepts a difference. The rewrite lanes run
it on every change. A change passes when no item that was good in the base is bad now, the run has
every item the accepted coverage requires, every known-bad item fails exactly as its accepted
grandfather entry pins it, no new item is bad, and no item is an ERROR (see "The verdict").

```sh
scripts/perf-rearch/parity.sh quick <tsgo bin> --base ~/.cache/perf-rearch/parity/base-main   # ~4 min warm
scripts/perf-rearch/parity.sh full  <tsgo bin> --base ~/.cache/perf-rearch/parity/base-main   # ~17 min warm
scripts/perf-rearch/parity.sh compare <base dir> <run dir>
scripts/perf-rearch/parity.sh known <run dir>          # what is not good in a run
scripts/perf-rearch/parity.sh js                       # JS tsc 6.0.3 against Go (CLI tier)
scripts/perf-rearch/parity.sh help
```

The last line is `PARITY OK` (exit 0) or `PARITY FAIL` (exit 1). Before it come the lost items
(`LOST <item>: <status> <first difference>`), the base items the run should have and lacks (`NOT
RUN`), new items that are not good (`NEW`), items where tsc-rs crashed or timed out (`CRASH`),
recovered items, every item that became good by a referee (grouped by status, never cut), the
grandfathered items with their reasons, FLAKY tests and harness `ERROR`s.

### The verdict (`parity_compare.py compare`; lanes H3 and H4)

A run is judged by accepted artifacts, never by the candidate's own definitions (lane H4):

- **the base**, the `--base` dir root accepted (`base-current`);
- **the quick manifest**, `scripts/perf-rearch/parity-quick-manifest.json`: the items a quick run
  must have;
- **the grandfather file**, `scripts/perf-rearch/parity-grandfathered.json`: the known-bad items,
  each pinned to its exact failure.

compare reads the last two at the accepted git ref (`--accepted-ref`, else `$PARITY_ACCEPTED_REF`,
else `perf/first-principles`), not from the checkout that runs it, and validates both strictly. It
prints the ref, and where the checkout's own copy differs (a shrunk manifest item by item); the
accepted copy still decides. A lane that changes either file runs its gate with `--accepted-ref
HEAD` and says so: root accepts the change by merging it.

PARITY FAIL on any of:

- **LOST:** good in the base, not good now. No grandfather entry excuses it.
- **NOT RUN:** a base item, good or known-bad, that the run must have and lacks. A full run must have
  every base item; a quick run every item the accepted manifest names (its cli items, every answer
  of its lsp sessions, its api scenarios, fuzz cases 0 to 1,499, the go_baselines tests and TestLocal
  rows). A run with
  `--tiers` fails on the tiers it left out.
- **KNOWN-BAD:** a base item that is not good, is still not good, and that no grandfather entry
  matches exactly: it changed to another failure, became nondeterministic, or was never pinned.
- **NEW:** not in the base, not good (IGNORED tests excepted; SKIP counts as not good), and no
  entry matches it.
- **STALE GRANDFATHER:** an entry whose item the run has and that does not fail exactly as listed
  (good now: remove the entry; or failing differently), or whose item the run must have and lacks.
- **ERROR:** any ERROR item, including an input that changed while the tier ran.
- **INVALID:** an accepted artifact that is missing, unreadable or not exactly its schema.

A grandfather entry (`parity_grandfather.py`) pins its item by structured evidence: its status and
rsFailure, the case's content identity, Go's cached result (status, outcome, panic line), the
attempt labels each mode makes, and the fingerprint every tsc-rs attempt must have (outcome, exit
code, sha256 of stdout, of stderr in its site form and of the written files, inputs intact). The site
form replaces only the Rust line number of the port-site line that tsc-rs prints in place of Go's
goroutine trace (lane H6, "Grandfather pins without Rust line numbers"). Entries exist for fuzz,
corpus and api items, the tiers whose rows carry that evidence. Every excused item is printed with its
reason (`GRANDFATHERED`). Only one gate runs at a time, under a lock: the scratch paths are fixed,
so the paths in the output agree with the cached Go results. The test tier builds and tests the
checkout (`--checkout`, default the worktree that holds the script). Every other tier runs `<tsgo
bin>`.

## Input identities (`parity_inputs.py`; lanes H3 and H4)

Every Go, Go-N and JS cache key holds the content of what the run reads and the parameters it runs
with, so a warm cache never answers for other inputs. Every cache entry also holds that key
material and is read only when it matches (lane H4).

- **cli:** the project's path and `tree_hash` of its snapshot (below). That covers every directory
  entry but `.git`: sources, configs, `node_modules` and symlink targets. The key also holds the
  config, the arguments, the timeout and the overlay command.
- **lsp:** the session's definition (project, files, positions, scenario), the project's path and
  its snapshot's hash, for scenarios with ATA on the content hash of the frozen typings store their
  ATA installs from (lane H5, "Hermetic ATA"), the state of the module-resolution names
  (`node_modules`, `package.json`, `tsconfig.json`, `jsconfig.json`, PnP) in every ancestor dir of the
  project and of the typings location, the node that runs lspdiff (path, version, sha) and the
  servers' private home path. It
  also holds the lspdiff sources that script the session (the stand-in npm, the servers'
  environment in `sandbox.mjs`, and `settings.mjs` included) and every lspdiff setting (`vendor/lspdiff/settings.mjs`, read with
  `lspdiff.mjs info`: `LSPDIFF_SETTLE_MS`, `LSPDIFF_QUIET_MS`, `LSPDIFF_REQUEST_TIMEOUT_MS`,
  `LSPDIFF_SHUTDOWN_MS`, `ATA_IDLE_MS`, `ATA_NPM_DELAY_MS`, `ATA_DEADLINE_MS`). No other lspdiff
  module reads an `LSPDIFF_*` or `ATA_*` variable, and lspdiff writes the settings it ran with to
  `settings.json`, which must equal the keyed ones. The JS key adds `tsserver.mjs`. Since lane H8 the
  sources also hold `json.mjs` (the wire's JSON reader) and the sandbox's init
  (`parity_sandbox_init.py`, shared with the api tier), and the ancestor dirs keyed are only the ones a
  server sees (none in the sandbox's hidden dirs: on MS-A2 only `/`).
- **api** (lane H7): the scenario as expanded (request filesystem, options, requests, build steps, the
  case flag), its root, the disk fixture's content hash, the session's sources (`parity_api_session.py`
  and `parity_sandbox_init.py`: protocol client, requests, sandbox, timeouts), the protocol, the private
  home path, the ancestor dirs' lookup names, the Go binary's sha and the Go run count. See "api: API
  sessions".
- **fuzz and corpus:** the case's id and `json_hash` of its content (every file, the options, the
  extra arguments), its directory, `oracle.ARGS`, the timeout and the number of Go runs. A changed
  mutator, seed corpus or parameter makes new Go runs.
- **JS:** `parity_referees.js_identity` hashes the whole typescript package: package.json, `bin/`
  and `lib/` (the shims, `_tsc.js`, `_tsserver.js`, `typescript.js`, every other module, and the
  `lib.*.d.ts` files). It requires version 6.0.3, by package.json and by the version string
  compiled into `_tsc.js` and `typescript.js`. Any other package is no referee: JS-AGREES is then
  impossible on that tier, and the summary says why (`jsError`).

`tree_hash` caches each file's sha256 by its (dev, inode, size, mtime, ctime). Every write changes
ctime, and user space cannot set it, so a cached hash is reused only for the unchanged file.

**Snapshots and staged caches (lane H4).** No tier runs a compiler or a language server on a shared
project dir; see "Gate fixes, round 2" for the details.

- Every project, and the typings cache, is a read-only snapshot
  (`~/.cache/perf-rearch/parity/snapshots/`): an rsync copy, made once per content and reused while
  the source keeps it.
- A run sees the snapshot at the project's own path: the CLI tier's overlay has it as its lower
  dir, and the LSP tier binds it there. Paths in the output are unchanged, and another process
  writing into the project changes nothing a run reads.
- While the tier runs, inotify watches every directory of every snapshot, so a change is seen even
  when it is undone at once.
- New Go and JS cache entries are staged (`~/.cache/perf-rearch/parity/stage/`) and published only
  when the tier ends with its inputs verified clean. A tier whose inputs changed makes the items of
  that root ERROR and discards every staged entry.
- The fuzz and corpus tiers write each case's inputs again before every process, and check them
  after it (inotify while it runs, bytes when it ends).

## Tiers

Each item is MATCH (tsc-rs equals Go's default run), one of the referee statuses GO-BROKEN,
GO-PARTITION and JS-AGREES (cli, fuzz and corpus, see "Referees"; lsp has GO-BROKEN and JS-AGREES,
see "JS tsserver as the LSP referee"; api has GO-BROKEN only, see "api: API sessions"), DIFF, GO-UNSTABLE (two Go runs disagree, so the item is not
compared; cli, fuzz and corpus only with `--no-referees`, lsp no longer), GO-FAIL
(fuzz and corpus with `--no-referees`: Go timed out or panicked), ERROR (harness), SKIP, or for
tests PASS / FAIL / FLAKY /
IGNORED / UNRUN. The good statuses are MATCH, GO-BROKEN, GO-PARTITION, JS-AGREES, PASS and FLAKY.

Go results are cached under `~/.cache/perf-rearch/parity/go-cache/<sha of the Go binary>/` and are
keyed by everything that goes into the run (see "Input identities"). On a warm cache a run starts
only tsc-rs. When a cache entry
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
each). Each side runs on a fresh **overlayfs view of the project's snapshot at the project's own
path**: `unshare -rm`, then `mount -t overlay` with the snapshot as the lower dir (lane H4; before,
the live project). The configured `outDir`, `declarationDir` and `tsBuildInfoFile` therefore stay
as they are, paths in the output are the project's real paths, neither the project nor the snapshot
ever changes, and the upper dir is exactly the set of written files, including the `.tsbuildinfo`
that `--noEmit` still writes for composite/incremental configs, files a second run rewrites or
touches, and an input a run overwrote or deleted (compared with Go's like any written file). Every
tsc-rs process counts, also an attempt of a two-run side that ran again after a clock step: a
crash or timeout in any of them sets `rsFailure` and makes the item a DIFF.

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
the benchmarks use. quick runs the 24 items of the quick manifest (`noemit`, `emit` and `extdiag`
on 8 configs).

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

**Servers and the wire (lane H8).** Every server process (Go, tsc-rs, JS tsserver) runs in the api
tier's sandbox (`parity_sandbox_init.py`): PID 1 of its own PID namespace, the host's state dirs empty,
the whole tree read-only but its project overlay and its private home, no privilege, no fd but 0, 1
and 2, no network; a process left behind when it ends fails the session. The client reads the wire
strictly (Content-Length framing, exact JSON-RPC 2.0 envelopes, no duplicate key, every number's text
kept), and every session has two more items after its last answer: `initialize` (the initialize
answer) and `session home` (what the server wrote into its home: the final listing and its history).
A server that cannot be started is ERROR, never GO-BROKEN. See "LSP sandbox and wire (lane H8)".

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
"ATA in the standard sessions" below). A Go cache entry is keyed by the session, the content of the
project tree and the sha of the vendored lspdiff sources ("Input identities"), so a change to the
project or the session script makes new Go runs.

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
| `<cp> server messages` | `server/messages` | every other request (`workspace/diagnostic/refresh`, `workspace/inlayHint/refresh`, `workspace/semanticTokens/refresh`, `workspace/codeLens/refresh`, progress, …) and notification, with payload, in order. Only `window/logMessage`, the server's log with timings, is left out. With ATA on, the list ends with the npm calls ATA made since the last checkpoint (`{ ata: { args, code } }`, package names sorted, the calls sorted; lane H5) |

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
update ends before the next snapshot starts. In the scenarios with ATA on, that wait and every
checkpoint also wait for ATA's npm and last at least `ATA_IDLE_MS` (lane ND2, "ATA's npm and the
scenario waits"). Without that, Go's concurrent background tasks
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
| `ata-overlay` | quick, full | fixture `ata-small` (JS files in no tsconfig, so an inferred project with type acquisition on) with ATA on against a private typings cache and the stand-in npm (see "ATA in the watch scenarios"): the first load and ATA's registry and typings installs, the refreshes they cause, an overlay (`src/ghost.js`, not on disk) whose import makes ATA install more typings, closing the overlay, and the inferred project's watchers unregistered when the last file closes |

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

**Referees.** An answer after a Go crash or timeout, or where Go's runs differ, is GO-BROKEN, and
a DIFF of a refereed kind can be JS-AGREES, decided by JS tsserver 6.0.3. See "JS tsserver as the
LSP referee".

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

### api: API sessions (`parity_api.py`, `parity-api-scenarios.json`; lane H7)

An API session is a client the other tiers never are: it gives the server its own filesystem
(`createSnapshot` `fileSystem`) and its own case setting (`--useCaseSensitiveFileNames`), so output
paths, common directories and the overwrite checks run on a host that is not the OS's. Lane B3 found two
output differences there (`audits/b3-prepin.md`, D1 and D2) that no tier could see. Round 2 of the lane
(Astra's review of `6e97777e6`, nine findings) sealed the sandbox and made every comparison exact; see
"API tier (lane H7)" below.

- **A session** is one `tsgo --api --async` process (JSON-RPC 2.0 over stdio, Content-Length framing; Go
  `cmd/tsc/api.go`, `internal/api/proto.go`), started with `--cwd <root>` and the scenario's case flag.
  `parity_api_session.py` sends `initialize`, `createSnapshot`, then for each project of the answer, in
  its order, `getSourceFileNames`, `getConfigFileNames`, the seven diagnostics methods, `emitToString`
  and `emit`; then the scenario's updates (`updateSnapshot` on the current snapshot, the requests again,
  `release` of the replaced one) and build steps (`createBuildOrchestrator`, `build`, `buildReferences`,
  `cleanBuild`, `disposeBuildOrchestrator`). It then closes stdin. The server must exit 0 by itself
  within 10 s, and both its output streams must reach EOF.
- **Filesystems.** A request filesystem is `kind: "full"` (Go's `requestfilesystem` makes it canonical
  and total; with it `emit` returns the contents instead of writing), rooted at `/parity-api/<scenario>`,
  which exists nowhere. A **disk fixture** (written once to a content-addressed copy,
  `snapshots/api/<hash>-m755644/tree`, with a checkout's modes, dirs 755 and files 644, since the server
  writes as a user, and a fixed 2001 mtime; checked byte for byte and mode for mode before and after the
  tier and watched while it runs) is mounted as an overlay at
  `scratch/api/<scenario>/root` for the requests that use the host filesystem: the build orchestrator,
  and `kind: "layer"` filesystems, which are allowed only over a fixture (they fall back to the host,
  which in the sandbox is the fixture and nothing else). `host` symlinks are refused.
- **The sandbox** (`parity_sandbox_init.py`, until lane H8 `parity_api_init.py`; the lsp tier's too since
  then; PID 1 of a new user, mount, network and PID namespace; `unshare --pid --fork --kill-child
  --mount-proc`):
  - `/home`, `/tmp`, `/var/tmp`, `/run`, `/mnt`, `/media`, `/srv`, `/opt` and `/dev/shm` are empty tmpfs
    dirs: the user's files (the real `~/.cache/typescript`, `~/.npm`, `~/.config`, every other lane's
    trees) do not exist for the server, not even for `stat`. The dirs the sandbox makes there have mode
    755 and the fixed mtime. The command's own files under a hidden dir are bound back read-only.
  - The whole tree is read-only (`mount_setattr` `AT_RECURSIVE`). The only writable places are the
    private home (new and empty for every process, at `~/.cache/perf-rearch/parity/api-home`) and the
    fixture's overlay, both host dirs of the session that the tier reads after every request. The
    overlay's upper and work dirs are reachable only through the overlay.
  - The server gets the environment of `sandbox.mjs` (`PATH=/usr/bin:/bin`, HOME, the XDG dirs and
    TMPDIR in the home, `LANG=C.UTF-8`, nothing else), cwd `/`, no network, and no fd but 0, 1 and 2.
  - **No privileges** (round 3): the init is root of the namespaces it built; the server is not. Before
    exec it enters a user namespace of its own, nested in the init's, which owns none of the sandbox's
    namespaces and may make no other (`user.max_user_namespaces` 0 there: a new user namespace would
    give it a full capability set again); its bounding, ambient, inheritable, permitted and effective
    sets are emptied; it runs as uid and gid 1000 (the host sees the same user, so it owns its home and
    overlay) with `no_new_privs`; all of that is checked in `/proc/self/status` before exec, and anything
    else is a setup failure. So `mount`, `umount`, `mount_setattr`, `pivot_root`, `unshare` of a mount or
    user namespace and `setns` all fail for the server, and it cannot reach the init's `/proc/1/fd`.
    `/proc` itself stays writable for the server's id maps; without capabilities nothing there is the
    server's to change but its own process files.
  - When the server ends, every other process in the namespace is a **survivor**: listed through an fd
    on `/proc` that the init opened before the server started, then killed, and counted as the init
    reaps it (whatever `/proc` shows, a process reaped after the server ended outlived it). Either is a
    failure. Nothing is collected before the namespace is empty and both streams are at EOF.
  - A mount, namespace, privilege-drop or exec failure, or a failure of the init itself, is reported on
    a private pipe (never a server result).
  - Nothing of this needs more than an unprivileged user namespace: no setuid helper, no subuid range,
    no cgroup. WSL2's kernel (6.18) allows all of it.
- **Compared** (canonical strings, so `false` is never `0`): every request as sent and its answer's raw
  bytes, the JSON-RPC envelope included, after a strict check (exactly `jsonrpc` `"2.0"`, the request's
  integer `id`, and `result` or an `error` with an integer `code` and a string `message`; no duplicate
  key, no NaN); after every request, the state of the session's storage (every entry of the overlay's
  upper dir and of the home, none skipped: a file's sha256 and mode, `dir <mode>`, `deleted` or the link
  target; the root itself as entry `.`, by `lstat`, so a root of another mode or type shows; every dir
  that cannot be listed as `unlistable <why>` and every entry that cannot be stat'ed as `unstatable
  <why>`, so storage the tier cannot read is never an empty listing; a root that does not exist, on
  either side, gives no entries; and the home skeleton's deviations from its exact structure: each of
  its dirs a real dir of mode 755), so a file written and removed between two requests is seen; the exit, survivors, whether the
  server exited by itself, the sha256 of stderr, and the storage at the end. Go and tsc-rs answer with
  the same bytes wherever they agree.
- **The readers** (round 3): any exception of the stdout or stderr reader fails the session as
  malformed; EOF counts only when a read returned it (a flag each reader sets), never because a thread
  ended. Headers are bounded: lines of at most 256 bytes, at most 4 of them, a `Content-Length` of at
  most 10 digits and 256 MiB.
- **Items:** `api/<scenario>`, one per scenario; the detail names the first difference (`emitToString
  /steps/11/answer/result/outputFiles/0/fileName: Go "...", tsc-rs "..."`, or `the raw bytes differ (the
  same JSON value)`). `api/<scenario>/` in the run dir holds `go.json` and `rs.json` (the transcripts,
  with the written files' texts) and, when not MATCH, `diff.txt`.
- **Inputs:** fixtures are checked against their scenario before the tier and watched (inotify) while it
  runs; the ancestor dirs of every root that the sandbox shows from the host (only `/`; the hidden dirs
  are the sandbox's own) are keyed and watched for the names module resolution looks up. A change to
  either is ERROR. There are no tripwires: the dirs they guarded do not exist in the sandbox.
- **Key** (`material`): the expanded scenario, its root, the fixture hash, the sha of
  `parity_api_session.py` and `parity_sandbox_init.py` (`SOURCES`), `PROTOCOL` (the wire protocol: JSON-RPC
  `--async`, the pin's protocol 5 in `api_oracle.py`'s numbering), the home path, the ancestors' state,
  whether `/parity-api` exists, the timeouts, the Go command's identity (a binary's sha256) and the Go run
  count. Go's two sessions per scenario are cached in `go-cache/<go sha>/api/<scenario>-<key>.json` with
  that material, staged, and published only when the tier ends with its inputs verified clean. A Go
  timeout is not cached, nor is anything of a tier where a session had an infra failure.
- **Judged** in the order of the other tiers:
  0. **ERROR** when any session (Go or tsc-rs) had an infra failure: the sandbox could not be made, or the
     command could not be exec'd (Astra's non-executable oracle). Never GO-BROKEN, never cached.
  1. a tsc-rs session that crashed (exit not 0, killed after the exit wait, a crash line on stderr, its
     output closed before an answer, a survivor, its output held open after it ended), timed out (60 s per
     request) or answered malformed is a DIFF with `rsFailure` crash or timeout (malformed counts as
     crash); two tsc-rs runs that differ are `rs-nondet` (full runs tsc-rs twice);
  2. GO-BROKEN: a Go run crashed, timed out or answered malformed, or Go's two runs differ, and tsc-rs is
     clean and stable (quick runs tsc-rs once more first);
  3. MATCH; 4. DIFF.
- **Go variants** as on cli and fuzz: two Go runs when the entry is made; runs that differ make the item
  GO-BROKEN. There is no accumulated variant record (the lsp tier's): a new fill happens only for new key
  material, and material is never merged.
- **No GO-PARTITION:** the partition rule is about the CLI's `--checkers`, and no scenario sets
  `checkers`.
- **No JS-AGREES:** JS tsc 6.0.3 has no API server. Its package has `tsc` and `tsserver` only, no
  `--api`, no `createSnapshot` or `emitToString`, no request filesystems, no `^/~ts-uri~/` names and no
  server case flag. Its in-process compiler API (`ts.createProgram` with a `CompilerHost`) can take a
  filesystem and a case setting, but it is another surface with other answers, so it is no referee here
  (`jsError` in the tier's summary). For the record, on D2's tree (`A/x.ts`, `a/y.ts`, `outDir`) with a
  host that says `useCaseSensitiveFileNames: false`, it emits `out/x.js` and `out/y.js`, as Go does
  (`scratch/h7/js_c2.cjs`).
- **quick** runs the 22 scenarios the quick manifest names (`"api"`, manifest version 2), tsc-rs once;
  **full** runs all 49, tsc-rs twice.
- `parity_api.py probe <bin> <scenario>` runs one session in the sandbox and prints its transcript, so
  `diff <(parity_api.py probe <Go> X) <(parity_api.py probe <tsc-rs> X)` reproduces an item (it replaces
  B3's `b3_api_probe.py`). `parity_api.py list [quick]` lists the scenarios, `key X` prints a key's
  material.

## Referees (`parity_referees.py`; Chad, 2026-11-04)

The brief's "Goal" allows three kinds of difference from Go's default run (4 checkers), each decided
mechanically. They apply to the cli, fuzz and corpus tiers. The lsp tier has GO-BROKEN and
JS-AGREES in the same order, but no GO-PARTITION: the partition rule does not cover its checker
pool (see "JS tsserver as the LSP referee"). An item that is not MATCH is judged in this order:

1. **tsc-rs determinism.** Two tsc-rs runs that differ are a DIFF (`rs-nondet`), whatever Go does.
   Where a tier ran tsc-rs once (quick, corpus), it runs it once more before it reports any of the
   three statuses below; a difference there is `rs-nondet` too.
2. **GO-BROKEN.** Go's default run crashed (a Go panic, a signal, `oracle.crashed`), timed out, or
   its repeated runs (2; 8 with `--traceResolution`) differ. Good when tsc-rs neither crashes nor
   times out; the detail says what Go did (for a crash, Go's `panic:` line). If tsc-rs crashes or
   times out too, the item is a DIFF (`go-broken (...), tsc-rs ...`); on fuzz and corpus an exact
   copy of Go's crash output is MATCH. A tsc-rs crash is a panic trace, exit 70 or a signal, and
   also a Go-style `panic: ` line on stderr: that is how tsc-rs reproduces a Go crash on purpose
   (`strings: negative Repeat count`, nil pointers; the difffuzz finds TypeScript #65078 and
   #65079). Such a reproduction is a crash, so it is not GO-BROKEN.
   *Mapping:* the Go cache keeps GO-FAIL (fuzz: a timeout or crash in any Go run) and
   GO-UNSTABLE (runs differ) as before; they are now properties of the Go result, and the case's
   status is GO-BROKEN, MATCH or DIFF from them. On cli, Go's two runs differing used to be the item
   status GO-UNSTABLE; it is now GO-BROKEN (or DIFF). The same holds on lsp. GO-UNSTABLE and
   GO-FAIL as item statuses are left only under `--no-referees` (the `js` mode). GO-BROKEN is judged at the default
   settings only: a Go race at some `--checkers N` is not GO-BROKEN (see step 3).
3. **GO-PARTITION.** Go runs with `--checkers N` for N in {1, 2, 3, 4, 6, 8, 12, 16}, as often per
   N as the tier runs its default (2, or 8 with `--traceResolution`), normalized the same way.
   An N whose own runs differ is a Go race at that N, not a partition, and is left out (`unstable`
   in the row); so is an N where Go timed out (`failed`; not cached). If the remaining outputs
   differ across N and tsc-rs's output (exit, stdout, stderr, every written file; both runs of
   incr) equals one exactly, the item is GO-PARTITION with the matching N in `detail` and the
   groups of N with equal output in `referees.partition.groups`. If they differ and tsc-rs equals
   none, the item goes on to step 4 and, failing that, is a DIFF whose detail starts
   `go-partition-miss`. If Go gives one output at every N, step 3 has nothing to say.
   It does not apply where the item's own arguments fix the checkers or are another mode:
   `--checkers`, `--singleThreaded`, `-b`/`--build`, `-w`/`--watch`, and `--showConfig`, which
   prints `"checkers": N` itself (`referees.partition.skipped` says why). The CLI items never set
   the first two; difffuzz drops both options from its seeds.
4. **JS-AGREES.** JS tsc 6.0.3 runs once (it is single-threaded), with the same arguments in the
   same place (cli: `node <tsc>` on the item's overlay, as `parity.sh js` does; fuzz: the case
   dir). It must side with tsc-rs on every point where tsc-rs and Go differ:
   - every diagnostic Go prints and tsc-rs doesn't is absent from JS's output: JS has neither that
     diagnostic nor more diagnostics of that code at that place than tsc-rs has;
   - every diagnostic tsc-rs prints and Go doesn't is in JS's output, equal in file, line, column,
     category, code and text (message chain and related information included), as often as
     tsc-rs prints it;
   - every written file that differs from Go's (content, missing or extra) equals JS's;
   - the exit code, if it differs from Go's, equals JS's;
   - per run, for the two-run variants.
   Points where tsc-rs equals Go need nothing from JS. **Every stdout line is accounted for**
   (`_stdout_points`, lane H3). Each stdout is split into tokens: diagnostic blocks (header,
   message chain, code frames, related information), blank lines, the `Found N errors` summary
   with the blank lines before it and its table, and every other line. Take out the blocks of the
   diagnostics only one side prints, each with the blank line that joined it to its neighbour.
   Go's and tsc-rs's tokens must then be equal byte for byte. The summary must be the one Go writes
   for tsc-rs's own errors: count, file table, line numbers and layout (`render_summary`, Go's
   `WriteErrorSummaryText`). The renderer is checked against Go's own summary in the same item
   first. A block only tsc-rs prints must equal JS's block for that diagnostic byte for byte, code
   frame included. In `--pretty` with related information that is undecidable, since JS 6 lays the
   block out differently, so such a block has no referee. What JS cannot referee fails the check and
   leaves a DIFF:
   - stderr that differs from Go's;
   - any other stdout line or blank line that differs from Go's (listFiles, explainFiles,
     showConfig, extendedDiagnostics statistics, `-b` messages);
   - a block of a diagnostic both print that differs from Go's (frame, colors, chain);
   - diagnostics both print in another order;
   - a summary that is not Go's for tsc-rs's errors, or one where Go prints none;
   - a difference with no point at all. The detail counts the points
   by kind and the most common codes (`JS sides with tsc-rs on 22 points: 22 go-only (TS2344 x22)`);
   `referees.js.points` lists up to 50 (`go-only TS2344 at src/v4/x.ts(10,5)`, `file out/a.js`,
   `exit 2>0`).
   Since every written file that differs must equal JS's, an item whose `.tsbuildinfo` differs from
   Go's (composite or incremental configs, where the build info records diagnostics) cannot be
   JS-AGREES: JS's build info has another format and version.
5. Otherwise **DIFF**, with the reasons of steps 3 and 4 in front of the first difference.

`--referee-all` (parity_cli.py, parity_fuzz.py) runs step 4 even where step 3 decided, and records
it in `referees.js`; the status stays the first referee's. `--no-referees` gives the old statuses.

**Demonstration on zod** (2026-11-05). The D1 spike `tsgo-d1s1-a17219bf7-goport` with
`GOPORT_CERT_SPLIT=refine GOPORT_CERT_N=8`, as the item `cli/zod/zod/noemit`
(`--rs "env GOPORT_CERT_SPLIT=refine GOPORT_CERT_N=8 <bin>" --only '^cli/zod/zod/noemit$' --referee-all`):
- Go's output differs at every one of the 8 N (22 errors at c4; 19, 20, 19, 22, 21, 23, 21, 20 at
  c1 to c16), each N stable over its 2 runs. tsc-rs (the same in both runs) prints 21 errors and
  equals none of them: Go c16 plus a TS5115 at `fix-json-issue.test.ts(17,14)` that c4 also prints.
  So step 3 gives `go-partition-miss`.
- Against c4, the one difference is c4's TS2344 at `src/v4/mini/tests/assignability.test.ts(149,61)`,
  which tsc-rs does not print. JS 6.0.3 prints 15 errors there, all TS6133/TS6196, and not that
  TS2344. So the JS delta rule holds, and the item is **JS-AGREES** (`JS sides with tsc-rs on 1
  points: 1 go-only (TS2344 x1)`).
- With `GOPORT_CERT_N` 1, 2, 3, 4, 6, 12 or 16 the spike's output equals Go c4 byte for byte. Only
  N=8 differs.

**Diagnostic parsing** (`tokenize`, `parse_output`). `tokenize` is lossless: joining its tokens
gives the stdout back. `parse_output` is the view for comparing diagnostics with JS, never with Go.
Both formats parse: `file(line,col): error TSn: text`
(`--pretty false`) and `file:line:col - error TSn: text` (`--pretty true`, ANSI colors removed),
plus global `error TSn: text`. Indented lines right after the header are the message chain. In
pretty output, code frames (the reverse-video gutter lines and squiggles) are dropped, and related
information is read after the blank line. JS 6 and Go differ in one systematic way here, and it is
normalized: Go prints related information as `  file:line:col - text` before its code frame, JS 6
as `  file:line:col`, the frame, then `    text`; both become `(file, line, col, text)`. The
`Found N errors ...` line and its `Errors  Files` table are a summary, not diagnostics: tsc-rs's
summary must be the one Go renders for tsc-rs's errors (above). Blank lines are ignored outside diagnostics (Go ends pretty output with one more blank line
than JS). `--pretty false` output prints no related information and no summary, in Go and JS alike.
Neither prints a version banner in these modes.

**Caches.** Go's per-N results sit next to its default result in the Go cache:
`go-cache/<go sha>/cli/<item key>.p1-c<N>.json` and `go-cache/<go sha>/{fuzz,corpus}/<case>.r<runs>.p1-c<N>.pkl`.
JS's results are in `~/.cache/perf-rearch/parity/js-cache/<js_identity>/{cli,fuzz,corpus}/`,
keyed like the Go cache (see "Input identities"). Timeouts are not cached. All extra runs happen only for an item that is
not MATCH, once; later gate runs read them from the cache.

## Run times

**Cost.** The referees run only for items that are not MATCH, and their Go and JS results are
cached, so a run on a binary that matches Go everywhere costs nothing extra on cli and corpus.
- **Per item, once:** 16 Go runs (8 N × 2), or 64 for a `--traceResolution` case (8 N × 8), plus 1
  JS run. On `cli/zod/zod/noemit` at a load of 42: Go 1.7 to 12.2 s per run (c1 is the slowest),
  about 68 s for the 16, and JS 32.9 s; the item took 114 s. On `fuzz/1:23080`: 64 Go runs, 16.3 s
  in all, and JS 1.2 s.
- **Every run: tsc-rs on Go-broken fuzz cases.** Before, GO-FAIL and GO-UNSTABLE cases did not run
  tsc-rs. Now the 44 such cases of the full batch do: the 18 GO-BROKEN ones take 0.1 to 0.9 s each.
  The 26 where Go timed out (deep nesting) time out in tsc-rs too. Each gets one 60 s try, no
  retry (a timeout there is a DIFF either way).
- **Every tsc-rs attempt counts (lane H3).** A fuzz or corpus row's `attempts` lists each tsc-rs
  process with its seconds and outcome. A timeout or crash in any attempt makes the case a DIFF with
  `rsFailure`, and `parity_compare.py` fails it whatever the base says, unless
  `parity-grandfathered.json` lists it. After a timeout tsc-rs runs once more with twice the time.
  That retry is diagnosis only: it shows whether the timeout was load (the retry answered) or a
  hang, and the case stays a DIFF either way. An exact copy of Go's crash output is still MATCH. Before that cap, the first full run spent up to
  2 × (60 + 120) s on each: its fuzz tier took 1,612 s against the base's 965 s, at a higher load.
  With the cap the 26 cases are 26 × 60 s = 1,563 s of worker time, about 130 s of the tier's 12
  workers; all 45 non-MATCH cases alone took 180 s of wall time on 12 workers at a load of 53. The
  final full run's fuzz tier took 823 s (base 965 s, at a lower load).


These were measured on MS-A2 at a 1-minute load of 15 to 45 (other lanes were busy). The tiers
run in sequence, except tests, which runs beside them. Wall times:

| tier | quick, warm | full, warm | full, first run (fills the Go cache) |
|---|---|---|---|
| cli | 12 s | 261 s (tsc-rs ×2) | 420 s |
| tests | 230 s (go_baselines only) | 260 to 300 s | 260 s |
| lsp | 50 s (7 sessions, 4 at a time) | 137 s (12 sessions, tsc-rs ×3) | 252 s (Go ×4 per scenario) |
| fuzz | 38 s | 516 s (30,000 × tsc-rs ×2) | 24 min |
| corpus | – | 135 s | 10 min |
| api (lane H7) | 1.7 s (22 scenarios) | 3.1 s (49 scenarios, tsc-rs ×2) | 12.3 s (Go ×2 per scenario) |
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
`runSession` uses). The repro is `scripts/perf-rearch/nd1/repro.mjs` (`--ata` turns the old
behavior back on).

## ATA in the watch scenarios (lane H3, 2026-11-05)

Not every watch scenario keeps ATA on:

| scenario | ATA |
|---|---|
| `fsevents`, `fsevents-narrow`, `watch-*`, `unsaved-ts-pattern`, `second-project-query` | on, hermetic since lane H5: a private typings cache, empty at the start of every server run, and the stand-in npm installing from the frozen typings store, whose content hash is in their key ("Hermetic ATA" below). Until H5 they ran the user's npm (behind ND2's logging shim) against the network registry and a snapshot of the shared `~/.cache/typescript/7.1`. Their refereed answers are on configured projects |
| `pkgjson-overlay` | off (lane H2: its npm call raced the overlay steps) |
| `ata-overlay` | on, deterministic: a private typings cache and the stand-in npm with the fixture's store |

`ata-overlay` restores what turning ATA off in `pkgjson-overlay` lost: the interaction of ATA with
the inferred project, overlays and the watchers. For every server run (Go and tsc-rs alike):

- `<root>-ata/xdg` is `XDG_CACHE_HOME`, written fresh, so the typings location is
  `<root>-ata/xdg/typescript/7.1`. Both servers take it from `os.UserCacheDir`.
- `vendor/lspdiff/ata-npm/npm` is first on `PATH`; both servers run `npm` from `PATH`. It is
  offline and takes `ATA_NPM_DELAY_MS` (500) per call. `install types-registry@latest` writes the
  fixture's registry (`ata-fake-lib`, `ata-fake-two`), and `install @types/<x>@latest` copies the
  fixture's typings. It logs every call.
- After every step the script waits until ATA is idle (`ataIdle`). The barrier is the stand-in
  npm's own log (lane H4): each call writes a start line and, when its work is done, a done line
  with its pid. ATA is idle when every started call is done, its process has exited, and none started
  for `ATA_IDLE_MS` (1.5 s). A wait that reaches `ATA_DEADLINE_MS` (60 s) fails that server run
  (a timeout), never a success. The script then logs the npm calls since the last wait as an item
  (`<step> ata npm`, method `ata/npm`).

The first draft sent its requests while npm ran, and both servers varied from run to run. A request
in that window started a second install of `@types/ata-fake-lib` (3 npm calls instead of 2), and
the next answers then depended on which install a snapshot saw. That is the ND1 race again, in
Go's design and in tsc-rs. With the idle waits, 4 Go runs and 3 tsc-rs runs of
`tsgo-head-efef52257-rel` gave 44 equal answers:

- didOpen installs the registry and `@types/ata-fake-lib`, and the hover shows the typed `pad`;
- the load sends `workspace/inlayHint/refresh` and `workspace/diagnostic/refresh`;
- the overlay's import installs `@types/ata-fake-two`;
- closing the last file unregisters the inferred project's watchers.

## ATA's npm and the scenario waits (lane ND2, 2026-11-06)

`watch-ts-pattern-f12` items `0016 load server messages`, `0018 benchmarks/random-digit.ts:126:1
auto-import completion` and `0026 completion server messages` were `rs-nondet` in three gate runs
at load about 30 (20261105-232849-quick, 20261106-095914-full and D1-I3's runs), and GO-BROKEN on
Go's own runs (`go | go2,go3,go4`). The odd run's `load` checkpoint had
`workspace/inlayHint/refresh` alone, without `workspace/diagnostic/refresh`. Its auto-import
completion had 1,115 items instead of 1,150: the 35 it lacked (`__filename`, `global`, `exports`,
`expect`, `afterEach`, `fdescribe`, `SuppressedError`, ...) are `@types/node`'s and
`@types/jest`'s. The refresh then came at the `completion` checkpoint, or later. Go's run 1 is the
same late variant.

**Cause: the harness, not tsc-rs.** It is the ND1 race again, now in the scenario's waits:

- ts-pattern's tsconfig includes only `src/`. `benchmarks/random-digit.ts`, the first file opened,
  and the `tests/` files go into the inferred project, which has type acquisition on.
- Its first ATA request runs `npm install --ignore-scripts types-registry@latest` in the typings
  cache (Go `project/ata/ata.go:379` in `init`; tsc-rs `project/ata/ata.rs` `init`), then finds the
  project's typings, and sets them as the project's pending ATA change with a diagnostics refresh
  500 ms later (Go `project/session.go:2061-2069`; tsc-rs `project/session.rs` around 3669-3678).
  The next request's snapshot adopts the pending change (Go `session.go:1737` `flushChanges`; a
  `didOpen`'s snapshot does not, `session.go:332` `flushChangesLocked`).
- The LSP stream shows none of that: the server is silent while npm runs. A checkpoint ended after
  1 s of quiet, so when npm took long enough the `load` checkpoint ended first. The completion's
  snapshot then lacked the typings, and the refresh moved to a later checkpoint. npm took 0.3 to
  1.15 s here at low load; the margin was about 1 s.
- Go and tsc-rs share it. The protocol has no "ATA done" signal (the work-done progress Go sends
  is delayed and needs `window.workDoneProgress`), so only the harness can wait for it.

**Repro** (`scripts/perf-rearch/nd2/`): `repro.py` runs the scenario in the parity sandbox N times
and tags each run by those three items; `burn.sh` is a CPU burner scope; `slow-npm/npm` delays
ATA's npm calls (`ND2_NPM_DELAY`, fixed or `rand:<max>`) and logs their times; `load-runs.sh` runs
4 repro instances side by side under 48 burners with a random npm delay.

| harness | load | npm | tsc-rs runs | variants |
|---|---|---|---|---|
| before | 40 to 57, 48 burners | as is | 54 | 1 (CPU load alone does not tip it) |
| before | idle | 2.5 s more | 3, and 3 Go | the late one, every run of both servers |
| before | 48 burners, 4 side by side (load 37 to 48) | 0 to 3 s more | 32, and 16 Go | 4 for tsc-rs (18 of 32 late), 3 for Go (9 of 16 late) |
| after | 48 burners, 4 side by side (load 50 to 62) | 0 to 3 s more | 32, and 16 Go | 1: all 32 tsc-rs runs equal on every answer; every round MATCH 52 of 52 |

**Fix.** The scenarios with ATA on the user's typings run the user's npm behind a logging shim,
and every wait covers it (lane H5 replaced the shim and the user's npm with the stand-in npm; the
waits are unchanged):

- `vendor/lspdiff/user-npm/npm` is first on the server's `PATH`. It writes the call's start line
  (pid) itself, before node starts, then runs `user-npm.mjs` with the same pid. That script runs
  the real npm (`ATA_REAL_NPM`, the npm on lspdiff's `PATH`, which parity_lsp.py keys) with the
  same arguments, directory and output, and writes the done line (args, exit code). The log is in
  a new temporary dir per server run.
- `Script.settle` (`scenario.mjs`), used by `quiet()`, `checkpoint()` and `ataIdle()`: the server is
  quiet for the wait's window with no request pending, and with ATA on, `npmIdle` holds: every
  started call is done and has exited, and the log has not changed for `ATA_IDLE_MS` (1.5 s),
  counted from the start of the wait too, so that a call ATA is about to start is seen. After an
  npm wait the server must be quiet again, which takes the refresh that ATA's result schedules. A
  wait that reaches `ATA_DEADLINE_MS` fails the run. `ata-overlay` keeps its barrier, now the same
  code.
- The first draft counted the idle window only from the last log change and wrote the start line
  from node. At load 90, npm's start line came up to about 0.6 s after the `didOpen` (Go and
  tsc-rs alike), after the 300 ms quiet window. The 12 opens then went out back to back, and the
  refreshes their ATA requests schedule coalesced: one `diagnostic/refresh` at `load` instead of
  two in 8 of 32 tsc-rs and 11 of 16 Go runs. A quiet wait right after a notification ends at once
  when the server was quiet before it, since a notification has no answer to wait for. The
  `ATA_IDLE_MS` floor gives every wait of these scenarios time for the server's work to show.
- With the fix, each `didOpen`'s ATA request ends, and its refresh fires, before the next open:
  the `load` checkpoint has `workspace/inlayHint/refresh` and 12 `workspace/diagnostic/refresh`
  on both servers, and the completion sees the typings (1,150 items).
- Cost: about 1.5 s per wait in these scenarios. Per server run, from the gate runs below:
  watch-ts-pattern-f12 6.3 to 28.5 s, watch-umami-f12 and watch-hono-f12 about 7 to 29 s,
  fsevents 19.4 to 25 s. Over the three tsc-rs runs of the full lsp tier that is about 340 s more
  session time, about 100 s more wall time with 4 sessions side by side (warm: 141 s before).

**Gate.** Go fills first for the sessions where Go races
(`parity_lsp.py --tier full --only <re> --go-refill <re>` with hono-f12-p6, umami-f12-p6,
watch-hono-f12, watch-umami-f12, watch-narrow-umami-f12 and second-project-query), then `parity.sh
full` of `tsgo-head-46578c173-rel` (harness 7658e8e35, the source of perf/first-principles at
46578c173) against `base-current` (h4r3-a8392a002-full): run `nd2-e92f96b39-full`, **PARITY OK**,
187,187 items, 0 lost, 0 not run, 0 new not good, 36 grandfathered (the known fuzz items), the
known FLAKY `watch_ends_soon_after_sigint_or_sigterm`. lsp: 2,631 MATCH and 27 GO-BROKEN (the
references of hono-f12-p6 and umami-f12-p6, as in the base), no DIFF. Every watch scenario is MATCH
on every item, including the Go-race items of the base (watch-umami-f12 and
watch-narrow-umami-f12 `load watch events` and `end watch ids`, watch-hono-f12 `end watch ids`):
with the waits, Go's background tasks run in order there too.

Negative controls (`selftest/h4-selftest.mjs`, part `nd2`): a wait sees an npm call that starts
after the wait began and lasts until its done line, then waits for quiet again; with ATA on a wait
lasts `ATA_IDLE_MS` even when npm never runs; with ATA off it is the server's quiet window alone.
They fail with the idle window counted from the last log change only.

## Hermetic ATA and external inputs (lane H5, 2026-11-09)

**The problem.** The combined-head gate (`runs/head-0bf6ff2a7-full`) lost
`lsp/watch-ts-pattern-f12/0018`, an auto-import completion: tsc-rs gave 1,147 items and Go's cached
answer 1,150 (`Less`, `less` and `gc` missing). Every binary gave 1,147 by then, including
`427ff93d5`, which had matched, and a `--go-refill` gave 1,147 on all 4 Go runs. The environment had
changed, not the compiler. The watch scenarios ran ATA with the user's npm (`types-registry@latest`
over the network) against a snapshot of the shared `~/.cache/typescript/7.1`, and the registry
was in no key. So a cached Go answer could describe another environment than the one tsc-rs ran in,
and the H4 variant record merged answers from two environments as "Go varies": after the refill,
0018 was GO-BROKEN with the variants 1,150 and 1,147, the wrong widening that incomplete key
material allows.

**Design** (the `ata-overlay` design, now for every scenario with ATA on):

- **A private home per server process** (`sandbox.mjs` `serverEnv`). Every server (Go, tsc-rs, JS)
  gets `HOME` = a new empty dir, the XDG dirs and `TMPDIR` under it, `PATH=/usr/bin:/bin`, `LANG=C.UTF-8`,
  and nothing else of the harness's environment but what its caller passes (a scenario's ATA
  plumbing, a `--rs-env` setting). In the sandbox the home is bound at a fixed path,
  `~/.cache/perf-rearch/parity/lsp-home`, the same for every run and both servers, since the typings
  location (`os.UserCacheDir()/typescript/7.1`) shows in watch registrations. That dir must be empty
  before and after the tier.
- **The stand-in npm with a store** (`ata-npm/fake-npm.mjs`, first on `PATH`; the only npm a server
  can find). It is offline, takes `ATA_NPM_DELAY_MS` per call, and installs from a store: `registry/`
  (types-registry), `node_modules/` as npm laid it out, the install's `package-lock.json`, and
  `closure.json` (per `@types/<x>`, the lock paths an install of it lays out; `@types/node` brings its
  nested `undici-types` 8.9.0, `bun-types` the top-level 8.12.4). It records what it installs in
  `package.json` and `package-lock.json` as npm does. A call for a package the store lacks fails with
  nothing installed and logs `missing`; a call in a directory outside the private `XDG_CACHE_HOME`
  logs `refused`. Either makes the scenario's wait throw a harness error, so lspdiff exits and the
  session is ERROR, never a crash of the server (which would be GO-BROKEN for Go).
- **The frozen typings store** (`ata_typings.py build`): built once from the registry, in a new
  empty dir with its own npm cache and an empty user config, with `--ignore-scripts`, the registry
  tarball extracted with tarfile's `data` filter, and links or special files refused. It is
  read-only in `~/.cache/perf-rearch/ata-typings/<sha[:24]>/`; the repo holds only its manifest
  `scripts/perf-rearch/ata-typings.json` (sha, versions, every file's sha256). `sha` is the tree hash
  under a fixed label, so it names the content and not the path. The store is the 7 `@types` the
  scenarios acquire (the discovery run, below): `bun` 1.4.4, `jsdom` 30.0.0, `less` 3.0.8, `node`
  26.6.6, `stylus` 0.48.43, `ws` 8.18.2, `yargs` 17.0.35, with their dependencies (14 packages,
  1,737 files), and types-registry 0.1.794; store `d339a2b67e79850a`.
- **parity_lsp.py** snapshots the store like a project (read-only copy, bound at its path, inotify
  while the tier runs, verified at its end), requires its hash to be the manifest's (else the
  sessions that use it are ERROR), passes it to lspdiff (`--ata-store`), and puts the hash in the key
  of every session that uses it.
- **No network.** lspdiff and every server run in a network namespace of their own (`unshare -rmn`):
  only a loopback that is down.
- **Kept:** ND2's completion barrier (the waits pair the stand-in npm's start and done lines and
  last at least `ATA_IDLE_MS`), and what the scenarios test: ATA's installs still change the inferred
  projects, schedule the refreshes and add the typings watchers. Every checkpoint's `server
  messages` item of a scenario with ATA on now ends with the npm calls ATA made since the last
  checkpoint, each call's package names sorted (Go builds the list from a map), so which typings each
  server acquires is compared too. They are not items of their own: a first draft had an `<cp> ata
  npm` item per checkpoint, which moved every later item's index, so 414 base items were NOT RUN.

**External inputs, found and keyed.**

| input | before | now |
|---|---|---|
| the types registry and `@types/*` (network) | not keyed | the frozen store: its content hash is in the key; no network |
| the shared typings cache `~/.cache/typescript/7.1` | a snapshot, keyed | not read: the typings cache is in the private home, empty at the start; a tripwire covers the real one |
| npm (the user's, its cache `~/.npm`, its config) | path and version keyed | not run: the stand-in npm (a keyed source); tripwires on `~/.npm` and `~/.config` |
| `HOME`, the XDG dirs, `TMPDIR` | the user's, passed through | private and new per process; the fixed home path is in the key |
| the rest of the harness's environment (`GO*`, `GOPORT_*`, `NODE_OPTIONS`, ...) | passed to every server | not passed: `serverEnv` is a keyed source |
| node (runs lspdiff, the stand-in npm and JS tsserver) | not keyed | path, version and sha in the key |
| ancestor dirs of the project and of the typings location (`node_modules`, `package.json`, `tsconfig.json`, `jsconfig.json`, `.pnp.cjs`, `.pnp.js`, `bower_components`: module resolution and the default `typeRoots` walk up to `/`) | not keyed | their state is in the key (none exists on MS-A2), and the dirs are watched for those names while the tier runs: any create, delete or change makes every lsp item ERROR |

**Fail closed.** In lspdiff's namespace (so in every server's), each guarded dir that exists
(`~/.cache/typescript`, `~/.npm`, `~/.config`) is covered by a read-only bait dir of the session's
own, which inotify watches for every access, reads included (`parity_watch.ACCESS`; since lane H7
without IN_CLOSE_NOWRITE, see "The tripwire race"). The typings bait
holds what ATA's init reads first (`7.1/package.json`, `package-lock.json`,
`node_modules/types-registry/index.json`). Any access makes the session ERROR ("a process of the
session touched an unkeyed path") and discards every staged Go and JS entry of the tier. The watch
drops the events of its own listing of the dirs (the first try reported them as accesses).
Not seen: a `stat` or a lookup of a name that does not exist (no inotify event), and a write (the
bait is read-only, so it fails and leaves nothing).

**Variant records.** The key material changed (sources, `CACHE_VERSION` 3, the store hash, node,
home, ancestors, no npm), so every lsp Go fill and every variant record starts fresh. The old records
stay on disk under other file names and are never read. The record's material has the store hash, so
two stores never share a record (control below). After the key change, the record of
`watch-ts-pattern-f12` has the one 0018 variant of the new store; the 1,150 and 1,147 variants of the
user-npm environment are in the old record only.

**Discovery run** (`parity_lsp.py --tier full --no-js` on the 9 scenarios with ATA on, store
`d339a2b67e79850a`, tsc-rs `tsgo-head-0bf6ff2a7-rel`, 592 s): no store miss; 591 MATCH and 4
GO-BROKEN (watch-hono-f12 `end watch ids`, watch-umami-f12 `load watch events` and `end watch ids`,
and watch-hono-f12's `load ata npm` item of the first draft, before the names were sorted: Go's order
of the package names varied). The npm calls, the same for Go
and tsc-rs: `watch-hono-f12` installs the registry and all 7 `@types` (two calls: `jsdom ws` and
`bun less node stylus yargs`); `watch-ts-pattern-f12` the registry and `@types/node`; `ata-overlay`
its two fixture packages; fsevents, fsevents-narrow, unsaved-ts-pattern, second-project-query and the
umami scenarios make no npm call. `watch-ts-pattern-f12/0018` is MATCH (4 Go runs and tsc-rs agree).

**Negative controls** (`parity_h5_test.py`, unit and `--e2e`; the `h5` part of
`selftest/h4-selftest.mjs`), all ok on the final harness (`9da6b4dea`):

| control | result |
|---|---|
| another store hash is another key; the old store's cache dir, priors (any run count) and refills are not read | ok (unit) |
| another store hash is another variant record (file name and material): never merged | ok (unit) |
| e2e: a store with one byte changed (in `@types/node/index.d.ts`) is another key and another record; Go runs again (no cached log, no prior, an empty record) | ok |
| e2e: a manifest whose hash is not its store's makes the session ERROR | ok |
| e2e: a store that lacks a package ATA asks for (`@types/node` on watch-ts-pattern-f12) makes the session ERROR ("lspdiff: harness error ... lacks @types/node"), not GO-BROKEN | ok |
| e2e: a tsc-rs (a wrapper of the head binary) that reads `~/.cache/typescript/7.1/package.json`, or that runs with `XDG_CACHE_HOME` = the real `~/.cache` (its ATA reads the real cache), makes ata-overlay ERROR, and no Go or JS log of that tier is published | ok, ok |
| tripwire: a read and a listing through the read-only bind in a nested namespace are seen; the watch's own listing is not | ok (unit) |
| ancestors: a `package.json` made above a project is seen and changes the key; another file there is not; no ancestor of the real projects holds a lookup name | ok (unit) |
| the stand-in npm: installs the registry and closures and records them; a missing package fails the call with nothing installed; a call outside the private cache is refused; either is a harness error of the scenario's wait | ok (JS) |
| a server gets none of lspdiff's environment and a private home; in the sandbox the home is at its fixed path, empty again after, and there is no network | ok (JS) |

The H4 and earlier self-tests still pass (`H4 SELFTEST OK`, `H4 LSP SELFTEST OK`).

**Go fills.** First a refill of the sessions where Go races
(`parity_lsp.py --tier full --only <re> --go-refill <re>` with hono-f12-p6, umami-f12-p6,
watch-hono-f12, watch-umami-f12, watch-narrow-umami-f12 and second-project-query; 549 s; it also ran
watch-ts-pattern-f12 and ata-overlay to check that the item names are the base's), then the gate,
which made each session's main fill (hono and query 8 Go runs, the others 4) and used the refill as a
prior.

**Gate.** `parity.sh full ~/.cache/perf-rearch/bins/tsgo-head-0bf6ff2a7-rel` (sha `0e02a10d506e`, the
perf head code; the tests tier on this lane's checkout, whose `crates/` equal `0bf6ff2a7`) against
`base-current` (`nd2-e92f96b39-full`), harness `9da6b4dea`, run
`runs/h5-head-0bf6ff2a7-full`, 28 min (06:52 to 07:20 UTC, load 0.3 to 16):

- 187,187 items compared: **0 lost**, 0 known-bad not as pinned, 0 new not good, 0 crashes, 36
  grandfathered (exactly as pinned), 0 stale entries.
- **13 NOT RUN, on purpose, the only difference**: the tests the C3 deletions and moves and the C2
  rename removed, the same 13 as in `runs/head-0bf6ff2a7-full`. They are `goport_core.lib` tests
  `checker_p24::tests::fast_never_order_can_skip_a_union_property` and
  `fast_never_order_tests_public_methods_last`, the five `lazy_members::tests` (`pr_case_diagnostics_match_go_in_both_modes`,
  `resolve_inside_prepare_is_not_repeated`, `switch_off_lookup_resolves_full_table`,
  `switch_on_lookup_is_lazy_and_full_table_reuses_members`, `switch_on_reserved_name_resolves_full_table`),
  `fast_mode::tests::cost_split_balances_and_keeps_imports_together` and
  `take_flag_reads_the_command_line_as_go`,
  `files_parser::move_up_tests::a_queued_job_moves_up_only_with_the_switch`, and `tsgo_fast`'s
  `build_keeps_four_checkers`, `fast_mode_is_off_by_default` and `same_output_on_every_run`. So the
  verdict line is PARITY FAIL on NOT RUN alone.
- cli 170 MATCH; tests 139,908 PASS, 1,731 IGNORED (no FLAKY this run); fuzz 29,955 MATCH, 36 DIFF
  (all grandfathered), 8 GO-BROKEN, 1 JS-AGREES; corpus 12,727 MATCH.
- **lsp: every item is good**: 2,629 MATCH and 29 GO-BROKEN, no DIFF, the same 2,658 item names as
  the base. `watch-ts-pattern-f12/0018` (the lost item of the combined-head gate) is MATCH: 1,147
  items on 4 Go runs and 3 tsc-rs runs, under the store's key. The GO-BROKEN items: 23 hono-f12-p6
  references and 5 umami-f12-p6 completions (GO-BROKEN in the base too) and two newly GO-BROKEN from
  MATCH: `hono-f12-p6/0295` references (Go's runs differ in order: `go | go2..go8`, the cross-project
  search of `ls/crossproject.go`) and `watch-hono-f12/0050 end watch ids` (the raw watcher counter of
  Go's background tasks; every Go run of the gate and refill differs). Both are races in Go, with
  tsc-rs stable on its 3 runs.
- This run is meant as the new base: one clean run dir, no retries in it. The stopped first run is
  `runs/h5-head-0bf6ff2a7-full-stopped` (the `ata npm` items of the first draft; cli, tests and lsp
  ran, lsp 2,693 MATCH and 33 GO-BROKEN).

**Cost.**
- **Store:** built once in under a minute (the registry tarball and 7 installs over the network), 17 MB, plus
  38 MB of kept downloads (`ata-typings/dl-*`). The manifest in the repo is 236 KB.
- **Per tier:** the store's snapshot and checks add about 1 s (lsp snapshots 20.8 s, end check
  4.6 s); the tripwires and the ancestor watch cost nothing measurable.
- **Per server run:** the private home is one mkdir and one bind. The stand-in npm takes
  `ATA_NPM_DELAY_MS` (500 ms) per call, as ata-overlay's did, against 0.3 to 1.15 s for the real npm.
  watch-ts-pattern-f12 runs took 28 to 31 s before and after.
- **lsp tier:** 587 s here at a load of about 5, against the base's 687 s (another run, another load).
- **New Go fills:** every lsp key changed, so the first runs refill all 13 sessions: about 9 min for
  the refill and about 7 min in the gate's lsp tier.

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

1. **tsc-rs determinism.** A tsc-rs run that crashed or timed out (detail `tsc-rs: rs crashed
   after N answers (...)`, `rsFailure`), or a later tsc-rs run that differs from the first
   (`rs-nondet`, `rs-schedule`), makes a DIFF whatever Go and JS say. A tsc-rs run that ended early
   also adds an item of its own, `<run> server`. In full, tsc-rs runs three times. In quick (one
   plain run), when any answer would be GO-BROKEN or JS-AGREES, one more tsc-rs session runs
   (`rs-confirm`), as on the other tiers. **It is a complete tsc-rs run (lane H3):** every answer
   of it, MATCH ones included, is compared with run 1. An answer it changes or lacks is a DIFF
   `rs-nondet: the confirming tsc-rs run ...`. So is every answer after its crash or timeout
   (`rsFailure`), a request only it asked (`rs-confirm extra answers`), and the crash itself
   (`rs-confirm server`).
   **Alignment (lane H3).** Two requests of the standard session depend on earlier answers: rename
   runs only after a prepareRename that gave a result, and quick fixes run for the first 3
   diagnostics. Answers are therefore aligned across all runs by request identity (the step name
   and its occurrence), not by index. A request that a run was not asked is an answer of its own
   ("not asked"). Only Go asked: DIFF `only Go was asked this request`. Only tsc-rs asked: `only
   tsc-rs ...`. Go's runs disagree: GO-BROKEN. tsc-rs's runs disagree: rs-nondet. Item `i` is the
   request's place in Go's run 1, with the requests only other runs made in their place. Where all
   runs ask the same requests, the item names are as before.
2. **GO-BROKEN.** A Go run crashed (the server exited; its `panic:` line is in the detail when
   stderr had one) or timed out before the answer, or Go's 4 runs differ on it. The detail says
   which: `go crashed after 63 answers (server exited (3) during textDocument/hover)`,
   `go2 timed out after 31 answers (...)`, or `Go runs differ (go,go3 | go2,go4)`. Every answer
   after a crash or timeout is GO-BROKEN, because that Go run has no answer there. A request
   counts as timed out when it gets no answer in `LSPDIFF_REQUEST_TIMEOUT_MS` (default 300 s,
   `client.mjs`). The server is then killed, and the session's answers so far are kept.
   *Mapping:* "Go's runs differ" is the old GO-UNSTABLE item status. The lsp tier no longer reports
   GO-UNSTABLE, in full or in quick. A Go crash used to make the whole session one ERROR item, and
   an unanswered request hung the tier until its hour limit. Now both are per-answer GO-BROKEN. A
   Go crash is cached (`<run>.broken.json` beside the run's log in the Go cache). A timeout is not
   cached: it depends on the load, so the next run retries it. JS is not asked here: GO-BROKEN
   comes first. `--js-all` records, for the report, which Go runs JS's normalized answer equals,
   and whether it equals tsc-rs's.
3. **JS-AGREES.** The answer is a DIFF of a refereed kind (below), and JS sides with tsc-rs on every
   point where tsc-rs's normalized answer differs from Go's. `detail` says how many points JS
   decided and names the first three (`JS sides with tsc-rs on 1 point: display: ...`).
   `referees.js.points` lists up to 20 of them.
4. Otherwise **DIFF**. When JS ran, `referees.js.reason` says why it did not decide.

JS replays a session only when the session has a DIFF of a refereed kind that passed step 1. On a
run with no such DIFF (every run so far), JS costs nothing. `--js-all` replays every session,
records JS's view of every item that is not MATCH, and writes the calibration to
`results.json` `jsCalibration`. `--no-js` turns the referee off. In comparisons against the base,
JS-AGREES and GO-BROKEN count as good. Every item whose status became one of them is listed in its
own section, with those that were MATCH in the base counted and listed first. This is lane H1's
referee section of `parity_compare.py`, with the same status names: the lsp tier adds none.

### The driver (`tsserver.mjs`)

`TsserverClient` has `LspClient`'s interface (`request`, `notify`, `settle`, `shutdown`, `events`).
`session.mjs` `startServer` makes one for `{ tsserver: <path> }`. So `runSession` and every
`scenario.mjs` scenario replay the same steps on tsserver: the same files with the same text, the
same `didChange` edits, the same requests at the same positions, and the same file-system steps.

- **Process:** `node --max-old-space-size=8192 tsserver.js --disableAutomaticTypingAcquisition
  --canUseWatchEvents --suppressDiagnosticEvents --noGetErrOnBackgroundUpdate --locale en`. ATA is
  off in every session, the watch scenarios included. Most of those scenarios keep ATA on for the
  LSP servers, but their refereed answers are on configured projects. In `ata-overlay` JS has no
  typings, so it decides nothing there.
- **Failed answers (lane H3).** An answer is `{ failed: <why> }` in the log, never a result, when:
  - tsserver answered `success:false`, `No content available.` included;
  - the response had no body, or the message was malformed or had no Content-Length;
  - tsserver exited, or gave no response in `LSPDIFF_REQUEST_TIMEOUT_MS`;
  - a step the mapping needs gave nothing (`completionEntryDetails`);
  - the request kind has no tsserver equivalent;
  - the `configure` or `compilerOptionsForInferredProjects` step failed (then every answer fails).
  `session.mjs`, `scenario.mjs` and the JS cache keep the field. `referee.mjs` never takes a failed
  answer as evidence (`JS failed here (no evidence)`). Past a JS crash there is no JS answer, and
  the crash is cached in `js.broken.json`. Only prepareRename's refusal is an LSP error answer, as
  Go's is.
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
- **Cache:** `~/.cache/perf-rearch/parity/js-cache/<js_identity>/lsp/<session>-<key>/js.jsonl`
  ("Input identities": the whole typescript 6.0.3 package; the session key with `tsserver.mjs`
  among the sources). JS runs once: it is single-threaded,
  and a session is one process. The Go cache key now hashes only the session-scripting sources, so
  changes to the diff code (`lspdiff.mjs`, `referee.mjs`) no longer make new Go runs. This change of
  key made one new Go fill.

### Mappings and normalized shape, per kind (`referee.mjs`)

All three servers' answers go through the same normalizer, after the root is masked. Each normalized
answer has *scalars* (one point each), *sets* (list answers: a key per element with every refereed
field, and a residue of **all** the element's other fields) and *rest* (everything else in the
answer, the error's code and data included). Key and residue together are the whole element, so no
field is dropped. Every part has two views (lane H3):

- **exact:** the raw JSON. **Go and tsc-rs are compared on it**: they bundle the same libs and write
  the same encodings, so any byte that differs between them is a point. `%40` against `@`, or a
  location moved between two identical lib lines, is a difference.
- **ref:** for comparing with JS only. Equivalent encodings map to one form:
  - **URIs** are parsed structurally: `fileURLToPath` plus the query and fragment. `%40types` and
    `@types` are one path; `a%23b.ts` (a file named `a#b.ts`) and `a#b.ts` (file `a`, fragment
    `#b.ts`) stay apart.
  - **A position in a `bundled:///libs` file is anchored to its text.** Go's libs (the pin's
    `internal/bundled/libs`) and JS 6.0.3's differ, for example by 1,470 lines in `lib.dom.d.ts`.
    The anchor (`anchorLine`) is the line's text plus the hash of the smallest window of 2k+1 lines
    around it (k up to 40) that occurs once in that side's copy and at most once in the other's.
    The same declaration then gets the same anchor in both copies. `lib.dom.d.ts` has 235 copies of
    the generic `addEventListener(type: string, ...)` line; each gets its own anchor. A line with no
    unique window has no anchor, and a point on it has no referee.

A point (a scalar or a go-only or rs-only element) has no referee when:
- its ref view is lossy: documentation as plain words, inlay labels without whitespace;
- its ref is ambiguous;
- its ref equals Go's (the two differ only in encoding);
- another element of the answer has the same ref.

The unrefereed fields of an rs-only element must be a form Go gives an element of that answer, or
JS's form for that element. Otherwise a corruption there would ride on an approved element.

| LSP request | tsserver | refereed (key / scalars) | not refereed (residue / rest) |
|---|---|---|---|
| `textDocument/diagnostic` | `syntacticDiagnosticsSync` + `semanticDiagnosticsSync` + `suggestionDiagnosticsSync` (Go `getAllDiagnostics` order; declaration diagnostics are in the semantic ones in both). Category error/warning/message/suggestion → severity 1/2/3/4. Go's `reportStyleChecksAsWarnings` applies: 6133, 6138, 6192, 6196, 7027–7030 error → warning | per diagnostic: range, severity, code, message text (with its chain), tags, related information (uri, range, message) | `source`, `data` and any other field (residue); the order |
| `textDocument/hover` | `quickinfo`: `"```typescript\n" + displayString + "\n```\n"`, then documentation and `*@tag* — text` | display text (inside the first fence), documentation as plain words (links `[x](y)` → x, `{@link X}` → X, emphasis, inline code, ` — ` and whitespace runs removed), range | anything else |
| `textDocument/completion` | `completionInfo` (trigger character and kind). Kind by Go `getCompletionsSymbolKind`. An optional member of a member completion gets `?` on its label and its name as insert text (Go `createLSPCompletionItem`). `labelDetails` from tsserver's, or the auto-import source | isIncomplete; per item: label, kind, sortText, insertText, `labelDetails.description` | data, filterText, textEdit, commitCharacters, tags, `labelDetails.detail`; itemDefaults; the order (free, as in lspdiff) |
| `completionItem/resolve` | `completionEntryDetails` for the item's name, source and data. detail = the code actions' descriptions, or the display parts. Edits of the item's file | label, detail, documentation words; the additional edits as a set | – |
| `textDocument/definition` | `definitionAndBoundSpan` → LocationLink (origin = bound span, target selection = span, target range = context span) | per link: origin range, uri, selection range | target range (JS 6 gives the initializer for a function-valued const, Go the declaration) and any other field (residue); the order |
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
or in rest, the answer is a DIFF (`... differs outside the refereed fields`). A difference in a
field whose JS view is lossy (whitespace in an inlay label, markdown markup) is a point with no
referee (`its JS view is lossy`). JS-AGREES needs at least one refereed point and JS's agreement on
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

### Parity run and cost (2026-11-05)

**Full gate.** `tsgo-head-efef52257-rel` against `base-current` (`head-efef52257-full`, the same
binary), harness 63a4c7b1c (H1 merged). The client and session sources changed, so the Go cache was
refilled. Run dir: `runs/h2-head-efef52257-full-2/`, 02:05 to 02:33 local, load 18 to 35.

Result: **PARITY OK**. 187,130 items compared, 0 lost, 0 not run, 0 new items not good,
43 recovered, 49 newly good by a referee (all GO-BROKEN).

| tier | result |
|---|---|
| cli | 170 MATCH |
| tests | 139,899 PASS, 2 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`, `build_statistics_time_the_started_check_and_emit`) |
| lsp | 2,561 MATCH, 40 GO-BROKEN, no DIFF, no JS-AGREES |
| fuzz | 29,955 MATCH, 9 GO-BROKEN, 36 DIFF (as in H1's run) |
| corpus | 12,727 MATCH |

**The 40 lsp GO-BROKEN items:**

- 33 are the base's GO-UNSTABLE items (22 hono references, 5 umami completions, 6 watch items) and
  `watch-narrow-umami-f12/0012`, the base's known DIFF.
- 8 were MATCH in the base: `pkgjson-overlay` 0007, 0014, 0017–0019, 0023, 0024 and 0033 (server
  messages and the inferred project's ATA watcher). Go's fourth run of the new fill differed there
  from its first three.

No session had a DIFF of a refereed kind, so JS did not run in the gate.

On the 27 GO-BROKEN items JS can normalize (`--js-all` run below):

- 11 hono references: JS equals tsc-rs and every Go run. Go's runs differ only in the order of the
  references.
- 11 other hono references: tsc-rs equals every Go run as a set, and JS differs (the JS 6
  differences above).
- The 5 umami completions: neither tsc-rs nor JS equals any Go run, normalized.

**A shared race in the watch scenarios.** The first full run of this lane, at harness a8d90e985
(`runs/h2-head-efef52257-full/`, load 50 to 87), was PARITY FAIL. It lost exactly those 8
`pkgjson-overlay` items as `rs-nondet`: tsc-rs's first run had no `workspace/diagnostic/refresh`
at the load checkpoint, and no unregister of the `/dev/null/inferred` watcher when the dependency
overlay closed. That is not this lane:

- a pkgjson-overlay probe (`target/h2/flake*.log` in the lane worktree, 16 sessions, load 8 to 26)
  hit it in 2 of 8 sessions with the vendored lspdiff of `b24ed46e2`, and in 2 of 8 with this lane's;
- Go's own fourth run hit the same items;
- the `--js-all` runs below also had `rs-nondet`/`rs-schedule` on `watch-hono-f12` server messages
  and on 3 of the pkgjson-overlay items.

It is the ATA timing that the watch scenarios keep, the race ND1 removed from the standard
sessions. In this Go fill, the 8 items are GO-BROKEN, not MATCH, so they now check only tsc-rs's
own determinism. Turning ATA off in `pkgjson-overlay` (it only tests package.json overlays) or
settling longer would make them comparable again. That change is not made here.

**Cost.**

- **A gate run with no DIFF of a refereed kind** (every run so far): JS never starts. The only
  added work is hashing tsserver once per tier (well under 1 s). The lsp tier took 372 s here,
  with the Go refill. The earlier run of this lane, on a warm Go cache, took 258 s; base-current
  took 284 s.
- **A session that has a refereed DIFF**, with JS cold: one tsserver replay of that session. At
  loads of 29 to 35 that took ts-pattern 11 s, hono 36 s, second-project 14 s, unsaved 5 s,
  pkgjson-overlay 5 s, fsevents 16 s, fsevents-narrow 17 s, watch-ts-pattern 12 s, watch-hono 15 s,
  watch-umami and watch-narrow-umami 20 s each, and umami 119 s. In quick, one more tsc-rs
  session confirms (`rs-confirm`).
- **The same session, with JS warm:** reading the cached log (0.2 to 0.3 s for ts-pattern and hono,
  3 to 3.6 s for umami's 340 MB) and judging the DIFF candidates (milliseconds).
- **`--js-all` (calibration, all 12 sessions):** JS cold, the lsp tier took 341 s (290 s of JS
  sessions, run 4 at a time beside the tsc-rs runs; umami's session 335 s). JS warm, it took
  233 s (umami's session 232 s, including 27 s to normalize all its answers). Go was warm in both.

### Self-test

`node scripts/perf-rearch/vendor/lspdiff/selftest/referee-selftest.mjs` (`--unit` for part 1 only)
prints `referee self-test OK` (48 checks; about 6 minutes at a load of 50). The negative controls of
lane H3 (part 1b, part 2b) are under "Gate fixes" below.

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
  - with the default kinds, the hover stays DIFF (hover is not refereed);
  - with the proxy as Go, and Go's own server as tsc-rs, a Go run that crashes (it exits at a
    hover) and one that hangs (never answers a references request; timeout 5 s) make every answer
    from there GO-BROKEN, naming the crash and the timeout. One confirming tsc-rs run checks them,
    and the answers before stay MATCH.

## Gate fixes (lane H3, 2026-11-05)

Astra's review of H1 and H2 (`reviews/2026-11-05-astra-h1h2.md`, at `e0b29c61c`) found false-good
paths in the referees and the compare. The rules this lane holds the gate to:

- A referee only ever adds evidence. A failed, malformed, ambiguous or incomplete piece of evidence
  fails the item.
- Every observable difference between tsc-rs and Go is either identical or covered by a referee
  rule for that exact point. Normalization maps equivalent encodings, never distinct values.
- Every tsc-rs attempt counts for determinism.

| # | finding | fix | where |
|---|---|---|---|
| 1 | a failed tsserver command became an empty answer | a distinct `failed` answer, kept through the logs and the JS cache, never evidence | `tsserver.mjs`, `referee.mjs`, `lspdiff.mjs` |
| 2 | LSP fields dropped (diagnostic `source`, the origin of every definition link but the first, error code and data; also signature `activeParameter` and parameter documentation, prepareRename `defaultBehavior`, rename `documentChanges` ops, completion `labelDetails` and resolve fields, semantic token bits) | key plus residue is the whole element; the error's code and data are rest; lossy JS views are no referee; an rs-only element's other fields must be Go's or JS's form | `referee.mjs` |
| 3 | lib lines merged by text; `%23` read as `#` | Go and tsc-rs compared exactly; for JS, structural URIs and lib anchors on a window of lines unique in both copies | `referee.mjs` |
| 4 | stdout corruption passed (summary, its table, code frames, blank lines) | every stdout token accounted for, the summary rendered from tsc-rs's own errors, rs-only blocks equal to JS's | `parity_referees.py` |
| 5 | the confirming run checked only the pending items | a complete tsc-rs run: every answer, missing and extra ones, crash and timeout | `lspdiff.mjs` |
| 6 | a retry overwrote a tsc-rs timeout | every attempt kept; any timeout or crash is a DIFF with `rsFailure` | `parity_fuzz.py` |
| 7 | the JS cache hashed the `tsc.js` shim | `js_identity`: the whole 6.0.3 package, version verified (cli, fuzz, corpus, lsp) | `parity_referees.py`, `parity_lsp.py` |
| 8 | cache keys missed content edits | content keys (`parity_inputs.py`), the inputs checked again at the tier's end | all tiers |
| 9 | new bad items passed | NEW and CRASH fail; `parity-grandfathered.json` with reasons | `parity_compare.py` |
| 10 | an empty quick run passed against a full base | `parity_modes.py`: every base item the mode runs is required | `parity_compare.py`, `parity.sh` |
| 11 | conditional requests shifted the index alignment | alignment by request identity; a request one side was not asked is a difference | `lspdiff.mjs` |
| 12 | turning ATA off in `pkgjson-overlay` lost the ATA/overlay interaction | the deterministic `ata-overlay` scenario; the docs say which scenarios keep ATA | `scenario.mjs`, `fixtures.mjs`, `ata-npm/` |

### Negative controls

Each control is one of the reviewer's false-good scenarios, run as he ran it where he had a probe
(`~/.cache/perf-rearch/scratch/review-e0b29c61c/`): the failing tsserver, the compare dirs, the
`_tsc.js` change, the lib lines. Both self-tests ran against this lane's head and against the
harness at `e0b29c61c` with the new test files copied in (`git archive e0b29c61c
scripts/perf-rearch` in `scratch/h3/old-e0b29c61c/`).

| self-test | new code | `e0b29c61c` |
|---|---|---|
| `parity_referees_test.py` | `SELFTEST OK`, 123 checks | 19 FAIL |
| `referee-selftest.mjs` | `referee self-test OK`, 48 checks | 15 FAIL |

Fail on `e0b29c61c`, pass now:

- **1, failed referee commands:** the reviewer's `failing-tsserver.cjs` as the referee of a dropped
  reference (old JS-AGREES); a failed answer in the unit judge.
- **2 and 4, mixed defects** (an approved change plus an unrelated corruption): a diagnostic
  `source`; the origin of a second definition link; an error code; hover markup next to an
  approved display; a deleted summary; a wrong summary line; a common diagnostic's code frame
  dropped or changed; a blank line added; an rs-only block whose frame is not JS's.
- **3, ambiguous anchors and URIs:** a location moved from `lib.dom.d.ts` 0-based line 3733 to 3812
  (two of the 235 copies of the generic `addEventListener` line), the two lines getting different
  anchors, `%23` against `#`, and `%40` against `@` between Go and tsc-rs.
- **5, the confirming run:** changing a MATCH answer, and crashing at the first hover.
- **6, timeout then success:** the reviewer's case (Go's runs differ, tsc-rs times out once, then
  answers twice: old GO-BROKEN), and the same with Go fine (old MATCH).
- **7, the JS implementation:** a changed `_tsc.js` (old: cache hit), and a package that is not 6.0.3
  (old JS-AGREES).
- **8, input content with a warm cache:** a cli project edit, a fuzz case changed at the same index,
  and the lsp key's content and project dir.
- **9 and 10, the compare:** the reviewer's `new-bad` and `quick-empty` dirs, a quick run against
  a real-named full base, and a tsc-rs timeout on a known diff (and that a grandfathered entry
  excuses it, with its reason printed).
- **11, conditional requests:** tsc-rs answers prepareRename with null, so only Go is asked the
  rename. Old: lspdiff threw `TypeError: Cannot read properties of undefined (reading 'line')` on
  the shifted answers, an ERROR for the whole session. Now it is one DIFF (`only Go was asked`),
  and every other answer MATCHes.

Fail on `e0b29c61c` for reasons other than a false good: the inlay check (the old judge called it
"no refereed point"); the two parts that use modules or parameters the old harness lacks; and
`corpus slow`, which looks for the new per-N cache file names. Five coverage checks of finding 4
pass on the old code too, since those cases were never false goods there: a wrong summary count,
an extra stdout line, a table counting the wrong file, a changed plain line, a changed chain.

### Gate runs with the fixes

All on `tsgo-head-efef52257-rel` against `base-current` (`head-efef52257-full`), harness
`cf8859bb2`, the tests tier on this lane's checkout. Every Go cache key changed, so the first run
refilled the Go cache. No item was renamed, so the base needed no regeneration: compare reads the
base's statuses, and its GO-UNSTABLE and GO-FAIL items are known diffs.

| run | dir (`runs/`) | wall | result |
|---|---|---|---|
| full, cold Go cache | `h3-head-efef52257-full` | 61 min, load 6 to 67 | PARITY FAIL: 13 lost (12 ERROR from an input change, and `fuzz/1:25496`) |
| full, warm | `h3-head-efef52257-full-warm` | 24 min, load 8 to 35 | PARITY FAIL: 1 lost, `fuzz/1:25496` |
| quick, warm | `h3-head-efef52257-quick` | 5 min, load 19 to 33 | **PARITY OK**: 142,741 compared, all 141,005 good base items a quick run must have, 0 not run |

Warm full, per tier:

| tier | wall | result |
|---|---|---|
| cli | 286 s | 170 MATCH |
| tests | 215 s | 139,900 PASS, 1 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`), 1,731 IGNORED |
| lsp | 168 s | 2,617 MATCH, 28 GO-BROKEN, no DIFF |
| fuzz | 825 s | 29,954 MATCH, 8 GO-BROKEN, 1 GO-PARTITION, 37 DIFF |
| corpus | 147 s | 12,727 MATCH |

The compare: 187,130 items compared, 1 lost, 0 not run, 0 new items not good, 0 CRASH, 35
grandfathered, 43 recovered, and 37 newly good by a referee (36 GO-BROKEN, 1 GO-PARTITION, no
JS-AGREES). None of the 37 was MATCH in the base:

- **lsp GO-BROKEN (28)**, all GO-UNSTABLE in the base: 22 hono references (Go's cross-project
  search order), 5 umami completions, and watch-hono's raw watcher ids. The base's DIFF
  `watch-narrow-umami-f12/0012` is MATCH now.
- **fuzz GO-BROKEN (8):** `2813`, `12500`, `12702`, `15106`, `20151`, `20360` and `29616` were
  GO-UNSTABLE in the base. The base's known DIFF `23080` (the `--traceResolution` race) is GO-BROKEN
  now: the new Go fill hit both answers.
- **fuzz GO-PARTITION (1):** `19122`, GO-UNSTABLE in the base. In the new fill Go's two default runs
  agreed, and tsc-rs equals Go at `--checkers` 8, 12 and 16 (2 Go outputs over N). By the rule that
  is good. Go's default run has varied on this case before, though (H1: GO-BROKEN).
- `5391` (the same `--traceResolution` race) is a DIFF now; it was GO-UNSTABLE, a known diff.
- The 35 grandfathered cases each failed in exactly the listed way: Go's timeout, or the reproduced
  panic line.

### What the fixes revealed

- **`fuzz/1:25496`: tsc-rs is 8 times slower than Go** (a lost item, not hidden). The case is a
  deep-nesting mutation of `conformance/.../assignmentCompatWithCallSignatures4.ts`, converted to
  JS. Measured back to back at a load of 50 to 57: Go 10.7 s and 10.6 s, tsc-rs 83.5 s and 84.5 s,
  same output. Both full runs timed out at 60 s, and the diagnostic retries answered after 63 to
  88 s. The base had it as MATCH because the old retry (120 s) overwrote the timeout: finding 6.
  It is a tsc-rs performance defect, not a harness one.
- **Another lane's bench wrote into a shared project dir during the gate.** In the cold run the
  t3code tree's hash was `d386c2ec75c4` when the cli tier started and `1b2872465328` when it ended.
  `apps/web` changed at 04:31:42. Lane d1i2b's wall bench ran its warm-up compiles in
  `projects/t3code` (`apps/server/tsconfig.noeffect.json`, noemit) from 04:31:18 to 04:32:52
  (`scratch/d1i2b/wall.log`) and left the tree as it was. Before the fix, this was invisible:
  the overlay's lower dir is the live tree, so the runs that mounted then saw other inputs than
  Go's cached result. Now its 12 items were ERROR. In the warm run, with no bench running, they all
  MATCH.

### Cost

- **Input hashing:** the first hash of everything took 273 s, once. A warm pass, 4 trees at a time
  at a load of 36, takes 26 s for the cli tier's 10 projects and 11 s for the lsp tier's 4 projects
  and the typings cache. Each tier makes two passes, at its start and its end. `js_identity`
  (24 MB) takes 0.08 s. Fuzz and corpus keys hash the case in memory, which is negligible.
- **Warm full gate:** 24 min. The H1 warm run had cli 272 s, lsp 264 s, fuzz 823 s and corpus
  256 s. Here cli took 286 s (the hash passes), lsp 168 s (ata-overlay adds a tsc-rs run of
  about 25 s per server run, three in full), fuzz 825 s and corpus 147 s, at another load.
- **Quick:** 5 min. cli 16 s, lsp 88 s (umami's GO-BROKEN answers make the full confirming run, as
  before), fuzz 65 s, tests 281 s.
- **Cold full:** 61 min, refilling every Go cache entry. cli 442 s, lsp 315 s (Go 4 times per
  session), fuzz 2,180 s, corpus 723 s.
- **New Go runs:** `ata-overlay` costs 4 Go runs of about 25 s when its cache entry is made. Since
  the keys are content keys, any change in a project tree now refills that project's entries.

## Gate fixes, round 2 (lane H4, 2026-11-05)

Astra's re-review of H3 (`reviews/2026-11-05-astra-h3.md`, at `e55c5b993`) found 12 more false-good
paths; its probes are in `~/.cache/perf-rearch/scratch/review-h3-astra/`. The rules this round holds
the gate to:

- **Evidence is structural.** Every oracle answer is validated against a schema before use, and every
  cached item carries the exact inputs and settings that made it and is staged until its run verified
  clean.
- **Acceptance comes from accepted artifacts:** the base, the quick manifest and the grandfather
  file, the last two at the accepted git ref, never from the candidate.
- **Every tsc-rs process is judged on all its outcomes,** for every attempt: exit code or signal,
  stdout and stderr, the files it writes, any input it changes, and its shutdown.
- **A known-bad item is pinned to its exact failure.** Any change fails, nondeterminism always, and
  an item that starts passing fails until its entry is removed.

| # | finding | fix | where | commit |
|---|---|---|---|---|
| 1 | poisoned caches: entries published before the end-of-tier check, so A→B cached B under A and A→B→A escaped | every input is a read-only snapshot (rsync copy, once per content) seen at its own path; inotify on every snapshot dir while the tier runs, plus a stat-identity and content check at its end; cache entries carry their key material, are staged, and are published only after a clean check (else discarded); lspdiff and every server process see only the snapshots | `parity_inputs.py`, `parity_watch.py`, `parity_cli.py`, `parity_lsp.py`, `lspdiff.mjs`, `sandbox.mjs` | `d2996cc09`, `fdaab1abd` |
| 2 | a timeout, then an exact Go-crash copy, was MATCH | the shortcut needs every attempt (retries and confirmations included) to equal Go's crash | `parity_fuzz.py` `judge` | `d2996cc09` |
| 3 | a known-bad item could change failure, become nondeterministic or vanish | known-bad items must be present and match their entry exactly; nondeterminism is never excused; a stale entry fails | `parity_compare.py` `judge` | `6e59be5e3` |
| 4 | grandfather matching by status and a detail substring | entries pin structured evidence: input identity, Go's result, attempt labels per mode, each attempt's fingerprint; strict schema; `parity_grandfather.py` regenerates the 35 entries from evidence runs | `parity_grandfather.py`, `parity-grandfathered.json` | `6e59be5e3`, see below |
| 5 | an rs-only LSP element could borrow another element's unrefereed fields | an rs-only element needs JS's evidence for the whole element (key and residue, as often) | `referee.mjs` `judge` | `fdaab1abd` |
| 6 | `success:true, body:{}` from tsserver was empty evidence | a response schema per command (`tsserver.mjs` `BODY`); a body that does not fit is a failed answer | `tsserver.mjs` | `fdaab1abd` |
| 7 | the quick coverage came from the candidate's own definition | `parity-quick-manifest.json`, read at the accepted ref; the tiers run at least its items; a shrink in the checkout is printed and changes nothing | `parity_modes.py`, `parity_compare.py`, the tiers | `6e59be5e3`, `d2996cc09` |
| 8 | fuzz and corpus did not see a compiler change its inputs | inputs written again before every process, watched with inotify while it runs and checked byte for byte after it; tsc-rs: rsFailure `input`; Go or JS: ERROR, never cached | `oracle.py`, `parity_fuzz.py` | `d2996cc09` |
| 9 | an LSP crash or non-zero exit after the last answer passed; stderr not judged | a mandatory session result per server run (shutdown answer, exit code or signal, stderr crash lines, inputs changed); item `session end`; an unclean JS run is no evidence | `sandbox.mjs`, `client.mjs`, `tsserver.mjs`, `lspdiff.mjs` | `fdaab1abd` |
| 10 | request identity was label plus occurrence | step, method and canonical params (keys sorted, root masked); a run that asks shared requests in another order is a DIFF (Go: GO-BROKEN; JS: no evidence) | `lspdiff.mjs` | `fdaab1abd` |
| 11 | the ATA wait could succeed with npm running; ATA settings not keyed | the stand-in npm's start/done lines paired by pid, the process checked gone, the deadline a failure; `settings.mjs` holds every setting and parity_lsp.py keys all of them | `scenario.mjs`, `fake-npm.mjs`, `settings.mjs`, `parity_lsp.py` | `fdaab1abd` |
| 12 | root's entry outside `items` was ignored | strict schema: unknown or misplaced keys make the file invalid, and compare fails loudly | `parity_grandfather.py` | `6e59be5e3` |

### Decisions

- **stderr of the language servers is checked for crash lines, not compared with Go's.** Both servers
  log to stderr with timings and other wording (tsc-rs wrote 73 to 147 bytes in the probes, Go
  none), so a byte comparison would fail on every run. A line matching `sandbox.mjs` `CRASH_RE`
  (Go's `panic:`, `fatal error:`, goroutine traces and signal lines; Rust's `panicked at` and stack
  overflow; an abort or core dump) always fails the session, whatever the exit code. The whole stream
  is hashed into the session result.
- **Shutdown.** The client must get an answer to `shutdown` without an error in
  `LSPDIFF_SHUTDOWN_MS` (30 s), and after `exit` the server must exit by itself with code 0 in that
  time; otherwise it is killed and the session is not clean. The client used to send `shutdown` with
  `params: null`, which Go and tsc-rs both refuse with `InvalidParams: expected no params, got null`:
  H3's client swallowed that, the session result found it. It now sends no params. A server's
  child (ATA's npm) can keep the stderr pipe open after the server exited; the result waits for it at
  most 2 s (`stderr.complete`). Exit took 13 to 160 ms for Go and up to 2.6 s for tsc-rs (fsevents)
  at a load of 50 to 90.
- **Fuzz inputs: what counts as a change.** A write, truncation, chmod, create, delete or rename of an
  input (inotify `IN_MODIFY`, `IN_ATTRIB`, create, delete and move events), and any input whose bytes
  differ at the end. Not `IN_CLOSE_WRITE`: the first full run on the P1 base had 34 false input
  changes (21 ERROR, 13 DIFF; about 1 process in 2,000) from it. Another thread's child holds a copy
  of a write fd it inherited at fork and closes it at exec, after an unrelated run's watch started;
  `parity_h4_test.py` reproduces that with a forked child. That run also found one real case:
  `fuzz/1:4232`, where Go emits over an input `.js` (allowJs). What a process leaves in place of an
  input is part of its result (`oracle.Result.inputs_after`, in the key), so tsc-rs must leave exactly
  what Go leaves; any other change by tsc-rs is rsFailure `input`, and a change a Go or JS process
  undoes again is an ERROR.
- **CLI written files.** A cli run that overwrites or deletes an input does so in the overlay's
  upper dir, where it is a written file (or a whiteout) and is compared with Go's like any other. A
  run never reaches the snapshot or the project. A project with an earlier build in its tree makes
  such writes legitimately (Go makes the same ones).
- **LSP inputs.** A server that writes into its project fails its session (rsFailure `input`). The
  typings cache is writable in each server's overlay (ATA installs there), starts from the same
  snapshot in every run, and what was written is only counted (`cacheWrites`). In fixture scenarios
  (`fsevents`, `pkgjson-overlay`, `ata-overlay`) the scenario writes the fixture itself; after each
  run the fixture must hold exactly what the script made it, or the session is not clean.
- **Accepted ref for this lane.** The manifest is new and the grandfather file has a new format, so
  neither is on `perf/first-principles` yet: this lane's gate runs pass `--accepted-ref` with its own
  commit, which the output names. After the merge, the default ref has them.
- **Base.** `base-current` is `p1-head-a8392a002-full` (root moved it after P1 merged; PARITY OK under
  the H3 gate). Its fuzz rows hold the old attempt records (outcome only, no fingerprints, no case
  identity), so the entries cannot come from it. They come from two evidence runs of the base's own
  binary (`tsgo-head-a8392a002-rel`, sha `dc0d697a68e2…`, the same Go) under this harness. The compare
  needs only the base's statuses, which say which items are known-bad. No new base is needed.
- **Known-bad means any status that is not good**, the old GO-UNSTABLE and GO-FAIL included. Under
  the new referees an old GO-UNSTABLE item is GO-BROKEN while Go's runs still differ, and otherwise
  judged like any item; an old GO-FAIL item where tsc-rs crashes or times out too is a DIFF with an
  rsFailure. An item the old harness called GO-UNSTABLE that is now an output DIFF of tsc-rs fails as
  KNOWN-BAD until an entry pins it.
- **`fuzz/1:5391`** is such an item, and the one output DIFF of the base. It is the
  `--traceResolution` race of `fuzz/1:23080` (Base on main, known diff 2): Go's resolver threads
  decide whether a line reads `File '<x>/package.json' does not exist according to earlier cached
  lookups.` or `... does not exist.`, and tsc-rs always prints the second form. In the base's Go fill
  the 8 default runs agreed on the first form, while Go at `--checkers` 2 and 12 varied between its own
  runs (`referees.partition.unstable`), so the item is a DIFF and not GO-BROKEN. The lane pins it as
  an output DIFF (rsFailure null): tsc-rs's attempts and Go's stdout fingerprint must stay as they
  are. If a later Go fill sees the race at the default settings, the item becomes GO-BROKEN and the
  entry goes stale: remove it then. Root decides whether the entry stays.

### Round 3 (Astra's H4 review, `reviews/2026-11-05-astra-h4.md`)

| # | finding | fix | commit |
|---|---|---|---|
| 1 | `drain()` could return before the reader parsed an event it had read: a same-byte rewrite got MATCH | `Watch.finish()` stops the reader, joins it and drains the queue under the parse lock before any final answer; `oracle.run` also checks each input's stat identity (a write sets the ctime). The snapshots' stat check stays | `b3f36718c` |
| 2 | an incomplete LSP stdout at EOF passed | after stdout ends, any byte that was no whole message fails the session; a header line that is not `Name: value` is a protocol error | `8dc888b67` |
| 3 | accumulated evidence trusted keys, not input validity | only Go runs with a clean session add variants, each with provenance; priors need a clean session result; no H3 imports; a Go run that changed its inputs stops the session | `b31deac7d` |
| 4 | a malformed record widened acceptance | strict schema on read and write, a malformed record stops lspdiff | `b31deac7d` |
| 5 | the normalizer fingerprint omitted `answer` and `hash` | it covers the format version and every function from answer to hash | `b31deac7d` |
| 6 | a failed watch construction leaked fds and watches, and fuzz raised | the partial watcher closes its instance and pipe; fuzz falls back to the stat and byte check (the attempt's `watch` says so), no ERROR | `b3f36718c` |

**Round 3 gate:** `tsgo-head-a8392a002-rel` against `base-current` (`h4-p1-a8392a002-full-3`),
harness `8e8b41b48` (`--accepted-ref` that commit), `runs/h4r3-a8392a002-full`, 24 min, load 4 to
12: **PARITY OK**. 187,187 items compared, 0 lost, 0 not run, 0 known-bad not as pinned, 0 new not
good, 36 grandfathered, 0 stale, 0 newly good by a referee. cli 170 MATCH; tests 139,900 PASS, 1 FLAKY
(`watch_ends_soon_after_sigint_or_sigterm`), 1,731 IGNORED; lsp 2,625 MATCH, 33 GO-BROKEN (every Go
fill new: `client.mjs` changed; `0476` GO-BROKEN, `go,go2,go4,go5,go7,go8 | go3,go6`); fuzz 29,955
MATCH, 36 DIFF (all grandfathered), 8 GO-BROKEN, 1 JS-AGREES; corpus 12,727 MATCH. No fuzz or corpus
attempt needed the stat fallback.

### Accumulated Go evidence (root's decision, after the first H4 report)

Any output the pinned Go binary has ever produced for the same inputs and settings is a legitimate
Go output. The lsp tier therefore keeps, per key material, a record of every Go answer variant it
has seen (`go-cache/<go sha>/lsp-variants/<session>-<sha>.json`, the sha256 of each normalized
answer per request identity):

- **Key material:** the Go binary's full sha, the session's definition, the project's path and
  snapshot hash, the typings snapshot and npm (scenarios with the user's ATA), the scripting sources
  and every lspdiff setting. That is the session's cache-key material without the run count and the
  log format version (`parity_lsp.variants_material`). lspdiff reads a record only if its material
  and its normalizer (the sha of lspdiff's normalize and identity code) are this run's.
- **Merging is monotone.** Each fill adds its Go answers to the record (staged, published with the
  logs), and nothing is ever removed. A request whose record holds a variant this fill's Go runs did
  not give is GO-BROKEN (`Go answers vary across fills`).
- **Earlier fills:** lspdiff also gets, as `--go-prior`, the Go logs of the session's other cache
  dirs whose `key.json` is this material at another run count.
- **Only verified runs (round 3).** A variant enters the record only from a Go run whose session is
  clean: its inputs intact (`sandbox.mjs`), a clean end, and a tier whose snapshots verified clean
  (the record is staged). Each variant keeps its provenance (`fill`, `run`, `log`). A prior log
  counts only with a clean `<run>.session.json` beside it. A Go run of the fill that changed its
  inputs stops the session (an ERROR: no Go evidence); a Go crash or timeout before an answer is
  still GO-BROKEN by contract.
- **No imports from before H4.** H4 imported the base's H3 logs where the base's recorded key could
  be recomputed from this run's content hashes. A key says what a run was given, not that it kept its
  inputs (H3 ran on the live tree), so round 3 removed the import. Records are now version 2, and the
  version-1 records, which held the imported H3 answers, were deleted from the cache.
- **Strict record schema:** exactly `{version: 2, material, normalizer, variants}`; `variants` maps a
  request key to `{16-hex hash: [provenance, ...]}`; each provenance is exactly `{fill, run, log}`.
  Anything else stops lspdiff with `malformed Go variant record`, and parity_lsp makes the session an
  ERROR.
- **Normalizer identity:** the sha of the record format version and the source of every function
  from answer to hash (`normalize`, `canon`, `requestKeys`, `answer`, `hash`).
- **More fills:** `parity_lsp.py --go-refill REGEX` runs a session's Go runs once more into
  `go-cache/<go sha>/lsp-refills/<key>/<run>/` (with its `key.json`); those dirs are priors of the
  key, so their clean runs join the record.
- **More Go runs on multi-project workspaces:** sessions on hono and query run Go 8 times (`g8` in
  the key), since `ls/crossproject.go` searches projects in goroutines. The other sessions keep 4.

Negative controls (`h4-selftest.mjs`, `parity_h4_test.py`):
- a variant an earlier fill of the same material gave makes the item GO-BROKEN;
- the record keeps it without its log (monotone);
- a record of another binary or another input is never merged;
- prior logs come only from the same material at another run count;
- round 3: a prior log whose session changed its inputs, ended unclean, or has no session result is
  never evidence; a Go run of the fill that changed its inputs stops the session; malformed records
  fail loudly; a changed `answer` function does not read the record; Go logs of a harness before H4
  are never priors.

`lsp/hono-f12-p6/0476` after round 3: the keys changed (`client.mjs` is a session source), so every
lsp Go fill is new and the record started empty. The first verified 8-run fill of hono disagrees
with itself on 0476 (`go,go2,go4,go5,go7,go8 | go3,go6`), and one `--go-refill` fill again (`go,go5,go6,go8 |
go2,go3,go4,go7`). The record holds both orders with 16 runs of provenance, so 0476 stays GO-BROKEN on verified H4-or-later evidence alone.

### Regenerating the grandfather entries

```sh
B=~/.cache/perf-rearch/scratch/p1/tsgo-head-a8392a002-rel R=~/.cache/perf-rearch/parity/runs
scripts/perf-rearch/parity_grandfather.py evidence $B --mode full  --out $R/h4-gf-evidence-p1-full  --add fuzz/1:5391
scripts/perf-rearch/parity_grandfather.py evidence $B --mode quick --out $R/h4-gf-evidence-p1-quick --add fuzz/1:5391
scripts/perf-rearch/parity_grandfather.py regen --full $R/h4-gf-evidence-p1-full --quick $R/h4-gf-evidence-p1-quick \
    --old $R/p1-head-a8392a002-full --add 'fuzz/1:5391=<reason>'
scripts/perf-rearch/parity_grandfather.py check
```

`evidence` runs the fuzz tier on exactly the listed cases, as the mode runs them, under the gate
lock. `regen` keeps the item list and the reasons, adds the `--add` items, and rebuilds each entry
from the rows. It refuses
an item whose attempts differ from each other, whose row lacks the evidence, or whose quick and full
rows disagree. `--old` prints, per item, whether the outcome kinds and Go's panic line agree with an
older run's.

### Grandfather entries that recover (lane T1, 2026-11-11)

When tsc-rs starts to pass a grandfathered item, the gate reports its entry as stale
(`good now: remove the entry`), and a plain `regen` refuses the item, since it is no longer a DIFF.
`regen --drop-recovered` removes such entries instead:

```sh
scripts/perf-rearch/parity_grandfather.py regen --full $R/<evidence>-full --quick $R/<evidence>-quick --drop-recovered
```

- **Recovered** means the `--full` run has the item with a good status (`parity_compare.GOOD`:
  MATCH, GO-BROKEN, GO-PARTITION, JS-AGREES, PASS or FLAKY) and no tsc-rs failure (`rs_failure`).
  This is the same test the gate uses for a stale entry.
- **Each dropped entry is printed** before anything is written:
  `dropped <item>: <status> in <run> (<detail>); the entry was '<old reason>'`. The summary line
  counts them (`N recovered dropped`).
- **Not run is never dropped.** If the `--full` run lacks the item, its entry is not dropped, and
  regen refuses it as before and writes nothing.
- **A good status with a tsc-rs failure** is not dropped either. The gate reports such an item as a
  crash, and regen refuses to pin it.
- **Quick and full must agree.** If a `--quick` run has a recovered item as not good, regen stops
  with an error.
- **`--only`** limits the candidates to the items it matches. Every other entry stays byte for byte,
  even if its item recovered.
- **Without the option, regen is unchanged.** With no recovered item, the option writes the same
  file as plain regen.

**Self-test.** `python3 -I scripts/perf-rearch/parity_t1_test.py` takes about 5 s. Its rows come
from the real fuzz tier on fake binaries, as in H6. It checks:
- the GO-BROKEN and MATCH items are dropped and printed;
- the still-bad item is kept and pinned again;
- good with a tsc-rs crash is kept;
- not run is kept, and regen refuses it and writes nothing;
- an entry outside `--only` stays, and one inside it is dropped;
- a quick run that disagrees is an error;
- plain regen refuses a recovered item, and on a run with no recovered item it matches the option.

Each of three mutations of the drop rule fails 2 to 5 of the checks.

**Dry run on `base-current`** (`runs/h8r3b-head-865385add-full`, binary
`tsgo-head-865385add-rel`). No quick run of that binary existed, so the quick evidence for the two
quick-manifest entries (`fuzz/1:411`, `fuzz/1:795`) came from
`evidence --mode quick --only '^fuzz/1:(411|795)$'` (`scratch/t1-quick-865385add`). With `--out` to a
scratch file, `regen --drop-recovered` dropped 0 of 36 entries. All 36 are DIFF in the base, the
output equals plain regen's byte for byte, and every pin equals the committed one except `evidence`.

### Negative controls

`parity_h4_test.py` (27 checks) and `vendor/lspdiff/selftest/h4-selftest.mjs` (15 checks) run each
probe of the review as the reviewer ran it, plus one check for each new mechanism's own bypass. They
ran on this lane's head and on `e55c5b993` with the two files copied in (`git archive e55c5b993
scripts/perf-rearch` in `scratch/h4/old-e55c5b993/`):

| check | `e55c5b993` | H4 |
|---|---|---|
| 1: a project edited while Go runs (cache-probe-v2, through the real tier) | FAIL (run 2 MATCH from the poisoned cache) | ok |
| 1: an input changed and put back while the tier runs | FAIL (GO-UNSTABLE, not ERROR) | ok (ERROR) |
| 1: the next run makes Go's result again | FAIL | ok |
| bypass: a staged entry of an invalidated run is never published | FAIL (published) | ok |
| 2: timeout, then an exact Go-crash copy (retry-crash) | FAIL (MATCH) | ok (DIFF, rsFailure timeout) |
| 3: a known-bad item turns rs-nondet | FAIL (passes) | ok |
| 3: a known-bad item disappears | FAIL (passes) | ok (NOT RUN) |
| 3: a known-bad item no entry pins | FAIL (passes) | ok |
| 3: a GO-UNSTABLE base item that is a DIFF now (`fuzz/1:5391`) | FAIL (passes) | ok |
| bypass: a stale entry (its item is good now) | FAIL (printed, passes) | ok |
| 4: another crash with the same panic text | FAIL (GRANDFATHERED) | ok |
| 4: an entry without its evidence | FAIL (matches anything) | ok (INVALID) |
| 4: the same failure on another input | FAIL | ok |
| 5: a definition target borrowing another target's range | FAIL (JS-AGREES) | ok |
| 6: `success:true, body:{}` (references, hover; the judge on it) | FAIL ×3 | ok |
| 7: quick against a shrunk definition | FAIL (passes) | ok (NOT RUN) |
| bypass: a shrunk manifest is printed item by item | FAIL | ok |
| 8: an overwritten input (mutate-input) | FAIL (not seen) | ok |
| 8: an input changed and put back; an input deleted | FAIL ×2 (MATCH) | ok |
| 9: panic and exit 70 after the last answer (late-crash) | FAIL (all MATCH) | ok |
| 9: a panic line with exit 0; no exit after `exit` | FAIL ×2 | ok |
| 9: a server that writes into its project | FAIL | ok (rsFailure input; project untouched) |
| 10: other params under the same label (alignment); another order | FAIL ×2 (MATCH) | ok |
| 11: the ATA wait at its deadline (ata-probe) | FAIL (success) | ok (throws, timeout) |
| 11: ATA settings in the cache key; every setting in settings.mjs | FAIL ×2 | ok |
| 12: an entry outside `items`; an unknown key | FAIL ×2 (no INVALID) | ok |

On `e55c5b993` the 23 finding and bypass checks of Python had 21 FAIL, and JS 12 FAIL of 14 (what
passes there: the two controls, `finding 3: fails another way` and `finding 9: control`). Four more
checks guard this lane's own input check (on `e55c5b993` they raise: no input check): a late close
of an fd a forked child inherited is no change, an emit that overwrites an input exactly as Go does
is MATCH, one that does it otherwise is rsFailure input, and one that does not make Go's overwrite is
a DIFF.
The H1 to H3 self-tests pass too: `parity_referees_test.py` `SELFTEST OK`, `referee-selftest.mjs`
`referee self-test OK` (after following the new APIs: run-level items are not answers, an unclean JS
run is "no evidence").

### Gate runs (P1 base)

`tsgo-head-a8392a002-rel` (sha256 `dc0d697a68e2…`, the binary of `base-current` =
`p1-head-a8392a002-full`) against `base-current`, harness `00afc1f40` (`--accepted-ref` that
commit), the tests tier on this lane's checkout (perf/first-principles `71ab48913` plus this lane).

| run | dir (`runs/`) | wall | result |
|---|---|---|---|
| **full, with accumulated Go evidence** (harness `990c9c2fa`) | `h4-p1-a8392a002-full-3` | 22 min (16:35 to 16:57 UTC, load 8 to 20) | **PARITY OK**: 187,174 compared, 0 lost, 0 not run, 0 known-bad not as pinned, 0 new not good, 36 grandfathered, 0 stale; 6 newly good by a referee (below) |
| full, before accumulated evidence | `h4-p1-a8392a002-full-2` | 29 min (15:03 to 15:32 UTC, load 3 to 22) | PARITY FAIL: 1 lost, `lsp/hono-f12-p6/0476` (below) |
| quick | `h4-p1-a8392a002-quick` | 8 min | **PARITY OK**: 142,785 compared, all 141,056 base items the accepted manifest requires, 0 not run, 2 grandfathered |

The final full run: cli 170 MATCH (261 s); tests 139,900 PASS, 1 FLAKY, 1,731 IGNORED (218 s); lsp
2,625 MATCH, 33 GO-BROKEN, every `session end` MATCH (186 s; the three standard sessions imported
the base's 4 Go logs each, hono and query ran Go 8 times); fuzz 29,955 MATCH, 36 DIFF (all
grandfathered), 8 GO-BROKEN, 1 JS-AGREES (770 s); corpus 12,727 MATCH (184 s, warm). Newly good by
a referee, none excused by an entry: `lsp/hono-f12-p6/0295` references (Go's 8 runs differ: one in 8
gave another order), the 4 umami watch items below, and `fuzz/1:19122` (below). `0476` is
GO-BROKEN as in the base.

The run before it (`-full-2`), per tier:

| tier | wall | result |
|---|---|---|
| cli | 307 s (snapshot check 147 s, end check 26 s) | 170 MATCH |
| tests | 213 s | 139,900 PASS, 1 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`), 1,731 IGNORED |
| lsp | 143 s (snapshots 20 s, end check 7 s) | 2,627 MATCH, 30 GO-BROKEN, 1 DIFF; every `session end` item (13) MATCH |
| fuzz | 727 s (base 541 s at load 30) | 29,955 MATCH, 36 DIFF (all grandfathered), 8 GO-BROKEN, 1 JS-AGREES |
| corpus | 695 s (its Go cache refilled: the run before was stopped in it) | 12,727 MATCH |

The compare: 187,174 items, 185,443 base items required, 1 lost, 0 not run, 0 known-bad not as
pinned, 0 new not good, 36 grandfathered (exactly as pinned), 0 stale entries, 0 recovered, 5 newly
good by a referee:

- **LOST `lsp/hono-f12-p6/0476`** (`references` at `method-not-allowed/index.ts:116:31`). The base has
  it GO-BROKEN: `Go runs differ (go | go2,go3,go4)`. In this lane's Go fill (new keys) Go's 4 runs
  agreed, on an order tsc-rs does not give; tsc-rs gave the same order in its 3 runs, and both sides
  list the same 30 locations. It is the cross-project search order of Go's goroutines
  (`ls/crossproject.go`; H2: "the 22 GO-BROKEN references of hono differ between Go's runs only in
  order"), which 4 runs did not catch this time. It is not a harness pass or a tsc-rs change, and it is
  not excused: root decides (more Go runs on hono, or a new base).
- **GO-BROKEN from MATCH (4):** `watch-umami-f12` and `watch-narrow-umami-f12`, items 0012 (`load
  watch events`) and 0050 (`end watch ids`). Go's 4 runs differ there: the background-task race of
  the umami first load ("Watch scenarios").
- **JS-AGREES (1): `fuzz/1:19122`**, GO-PARTITION in the base. In this fill Go varies at `--checkers`
  1, 3, 4 and 6, and JS 6.0.3 sides with tsc-rs on both TS1435 points.
- **Grandfathered (36):** the 35 entries (26 timeouts where Go times out too, 9 reproduced Go
  crashes) and `fuzz/1:5391`, each with every attempt's fingerprint as pinned.

**The first full run on the P1 base** (`h4-p1-a8392a002-full`, stopped in its corpus tier) is the
reason for `00afc1f40`: 21 ERROR and 13 DIFF fuzz items from false input changes (`IN_CLOSE_WRITE`,
see "Decisions"), and `fuzz/1:4232`, where Go overwrites an input. A full run before root moved the
base (`h4-head-efef52257-full`, on the old base and binary) was stopped after its cli and tests tiers.

### Cost

- **Snapshots, cold:** 488 s for all 13 roots (12 GB, about 750,000 files), 4 at a time at a load of
  52 (t3code 425 s, query 320 s, umami 199 s, vscode 174 s, the others under 2 min). Made once per
  content; disk 14 GB.
- **Snapshots, warm, per tier run:** checking that a snapshot is still the source's content and
  unchanged (the source's stat-cached hash and the snapshot's stat identity): cli 97 to 147 s for its 10
  roots, lsp 20 s for its 5. At the end: cli 26 s, lsp 7 s (inotify plus a stat-identity and content
  check). Watching about 100,000 dirs with inotify costs no measurable time.
- **Staging:** a rename per new cache entry at the end of a tier; nothing on a warm run.
- **Fuzz and corpus:** each process rewrites its case dir and runs under an inotify watch with a
  byte check after it. Fuzz 727 s against the base's 541 s, at a lower load (20 against 30): about
  +30%. Warm corpus, alone at a load of 7 to 14: 179 s (the base 134 s, H3 147 s, each at another load).
- **LSP:** a user and mount namespace per server and an overlay per input: lsp 143 s against the
  base's 169 s. The `session end` item adds nothing measurable; the shutdown waits are those of
  before (exit took 13 to 160 ms for Go, up to 2.6 s for tsc-rs).
- **Quick:** 8 min (cli 42 s, lsp 147 s, fuzz 61 s, tests 166 s).
- **New Go fills:** every Go cache key changed (versions, snapshots, settings), so the first run of
  this harness refills them: about 25 min of fuzz and 12 min of corpus Go runs, 4 Go runs per lsp
  session.

## Referees on the perf head (2026-11-05)

Full gate with the referees (lane H1, harness 69a8525b0) on `tsgo-head-efef52257-rel` against
`base-current` (`head-efef52257-full`, the same binary). Run dir:
`~/.cache/perf-rearch/parity/runs/h1-head-efef52257-full-referees-2/`, 07:28 to 07:55 UTC (27 min),
load 28 to 61. Result: **PARITY OK**, 187,130 items compared, 0 lost, 0 not run, 0 new items not
good, 9 recovered.

| tier | wall | result |
|---|---|---|
| cli | 272 s | 170 MATCH: no referee ran |
| tests | 297 s | 139,899 PASS, 2 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`, and `api_async_held_request_keeps_its_reply_at_the_end`, both timing tests of `tsgo_stdio_end`), 1,731 IGNORED |
| lsp | 264 s | 2,567 MATCH, 33 GO-UNSTABLE, 1 DIFF (the base's known `watch-narrow-umami-f12/0012`); no referees on lsp |
| fuzz | 823 s (base 965 s) | 29,955 MATCH, 9 GO-BROKEN, 36 DIFF |
| corpus | 256 s | 12,727 MATCH |

None of the 45 fuzz cases that are not MATCH was good in the base (35 GO-FAIL, 9 GO-UNSTABLE, 1
DIFF):
- **GO-BROKEN (9), recovered:** the 9 cases on which Go's runs differ (`1:2813`, `5391`, `12500`,
  `12702`, `15106`, `19122`, `20151`, `20360`, `29616`). tsc-rs gave the same answer in both runs,
  in under 1 s.
- **DIFF, tsc-rs reproduces Go's panic (9):** `1:3909`, `4134`, `4327`, `17373`, `19954`, `20483`
  (Go `panic: strings: negative Repeat count`) and `1:795`, `16682`, `18749` (nil pointer). tsc-rs
  prints the same `panic:` line and exits 2. An earlier run of the referees (harness 4151af485)
  counted these as GO-BROKEN because the copy has no goroutine trace. They become GO-BROKEN when
  tsc-rs answers there instead.
- **DIFF, both time out (26):** the deep-nesting cases on which Go timed out; tsc-rs times out at
  60 s too.
- **DIFF `1:23080`:** the known `--traceResolution` race. Go gives one output at every N (8 runs
  each), and JS cannot referee a trace line.

The earlier run (`h1-head-efef52257-full-referees`, harness 4151af485) was also PARITY OK. Its fuzz
tier took 1,612 s before the timeout cap, and exited 1 after writing its results (the summary line
failed on DIFFs without a signature; fixed in 5dcf897e5).

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
- **The API beyond the api tier's sessions** (lane H7 covers JSON-RPC `--async` sessions: request
  filesystems, the case flag, dynamic names, the diagnostics methods, emit and the build orchestrator).
  Not covered: the msgpack sync protocol, the checker and type queries (`scripts/goport/api_oracle.py`
  batteries), callback filesystems (`--callbacks`), `layer` request filesystems and `host` symlinks
  (they read the real disk), `updateSnapshot`, API sessions inside an LSP server
  (`custom/initializeAPISession`), and the native-preview client suite (`np-suite.sh`).
- **Not compared at all:** lsp GO-UNSTABLE items and the masked `--extendedDiagnostics` memory and
  time values. Where Go is broken (GO-BROKEN), only tsc-rs's own stability and absence of crashes
  and hangs are checked: there is no Go answer to compare with.
- **Referees.** GO-BROKEN, GO-PARTITION and JS-AGREES accept the differences they decide without
  further checks (see "Referees" for what each cannot referee). JS-AGREES compares JS only on the
  points where tsc-rs and Go differ. The lsp tier has no referees; the brief's GO-BROKEN rule for
  lsp is not implemented here.
- **Nondeterminism that two tsc-rs runs do not show.** Full mode runs tsc-rs twice on cli and
  fuzz, and three times on lsp (the third with `GOPORT_PARSE_THREADS=1`). A race that needs more
  runs or other load can still pass, and other worker settings (`GOPORT_MAPPED_THREADS`,
  large-program workers) are not varied.
- **Inputs.** 10 projects (23 configs), 4 LSP projects plus the watch-small fixture, 42,727 test-case runs (30,000 fuzz plus
  12,727 corpus). Corpus cases with `@link`, `@symlink`, `@currentDirectory`, their own
  `tsconfig.json` or `..` paths are skipped by difffuzz's seed parser (TestLocal covers them).
- **Project dirs owned by another lane.** The baseline lane owns most project dirs. Since lane H4
  a run reads only snapshots of them, so an edit there during a run changes nothing the run sees; the
  next run snapshots the new content, which changes the Go cache keys. A changed input can still
  turn base MATCHes into new results, so rerun the base after one.
- **Inputs outside the snapshots.** The Go and tsc-rs binaries are keyed by their sha, and JS by its
  package. In the lsp tier (lane H5) the node, the typings store and the ancestor dirs' lookup names
  are keyed too, and the servers see none of the harness's environment; since lane H8 the servers run
  in the api tier's sealed sandbox, where the shared typings cache, npm's cache, `~/.config` and every
  other user file do not exist. Not keyed: the kernel, the CPU count and the host's load (which change
  how Go's goroutines race, not its inputs), and the system libraries (`/usr`, `/etc`: visible,
  read-only). The CLI, fuzz and corpus
  tiers still pass the harness's environment through and do not key their ancestor dirs (none holds
  a lookup name on MS-A2 now).
- **Transient changes of a fixture scenario's dir by a server:** closed by lane H8. The server sees the
  fixture dir through a read-only bind (the script writes it from outside), so it cannot change it at
  all; the end check stays.
- **The home with ATA on (lane H8).** In the scenarios with ATA on the home is listed at every wait
  (quiet, checkpoint), where ATA is idle, not after every answer (ATA writes it in the background, so a
  listing between two waits would depend on npm's timing). A file a server writes and removes again
  between two waits of such a scenario is not seen; one that stays, or one a server writes in a session
  with ATA off, is.

## Grandfather pins without Rust line numbers (lane H6, 2026-11-09)

Nine grandfather entries are cases where tsc-rs reproduces Go's crash on purpose: `fuzz/1:795`, `3909`,
`4134`, `4327`, `16682`, `17373`, `18749`, `19954` and `20483`. Where Go prints its goroutine trace,
tsc-rs prints one port-site line with its own Rust `file:line`
(`crates/ts_goport/src/gopanic.rs`: `print_go_panic`, `go_fatal_newosproc`, through `go_site_file`):

```
panic: strings: negative Repeat count

	crates/ts_goport/src/execute/tsc/diagnostics.rs:1383
```

Version 2 of the grandfather file pinned the sha256 of that stderr, so a source line added or removed
above the call changed the hash: the item became KNOWN-BAD not as pinned and the gate failed (lane
Q4 kept old line numbers by hand to avoid it). The contract of these items is the same panic line,
the same exit code, the same stdout and the same written files; the Rust line number is not output Go
has.

**The site form** (`parity_grandfather.stderr_site`). tsc-rs's stderr with the line number of the
site line replaced by `<line>`, applied only when stderr ends with the Go panic block exactly as
`gopanic.rs` prints it:

```
^(panic: |fatal error: )<rest of line>\n    a Go panic line, or fatal error: newosproc
(<non-blank line>\n)*                       more panic lines ([recovered]), the signal line, ...
\n                                          one blank line
\t<[A-Za-z0-9_./-]+>.rs:<digits>\n\Z        the site line, the last line of stderr
```

Only the digits are replaced. The file path, the panic lines, the signal line and every other byte
stay in the hash, and the exit code, stdout and written files are pinned beside it as before. Any
other stderr (a site line that is not last, no blank line before it, no panic line, a Go trace, the
`tsgo: panic at <file>:<line>` hook line of a Rust panic) is hashed as it is.

**Where it applies.** `parity_fuzz.py` records `stderrSite` (the sha256 of the site form) on every
tsc-rs attempt of the fuzz and corpus tiers, beside the raw `stderr` hash, which stays for
diagnosis. A grandfather entry's `attempt` pins `stderrSite` instead of `stderr`; `regen` (pinning)
and `match` (comparing, in `parity_compare.py`) both read the row's field, so one function makes
both sides. No cache changes: tsc-rs results are never cached, and Go's evidence never hashed
stderr (a Go trace holds addresses).

**Version.** The grandfather file is version 3. A version 2 file is INVALID, `regen` refuses an
evidence row without `stderrSite` (a harness before H6), and `match` never matches one, so a raw
hash is never compared with a site-form hash. A later change to the form bumps the version again.

### Re-pinning

```sh
B=~/.cache/perf-rearch/bins/tsgo-head-e81a14807-rel R=~/.cache/perf-rearch/parity/runs
scripts/perf-rearch/parity_grandfather.py evidence $B --mode full  --out $R/h6-gf-evidence-e81a14807-full
scripts/perf-rearch/parity_grandfather.py evidence $B --mode quick --out $R/h6-gf-evidence-e81a14807-quick
scripts/perf-rearch/parity_grandfather.py regen --full $R/h6-gf-evidence-e81a14807-full \
    --quick $R/h6-gf-evidence-e81a14807-quick --old $R/head-e81a14807-full-combined
```

(under the gate lock, `~/.cache/perf-rearch/parity/.lock`). Every one of the 36 entries had to be
regenerated, since the attempt key changed. Against the version 2 file:

- **The nine crash copies** change only `attempt.stderrSite`. The raw stderr of every evidence
  attempt still equals the version 2 pin, so the binary's output did not move; only the form did.

  | items | site | v2 `stderr` | v3 `stderrSite` |
  |---|---|---|---|
  | 795, 18749 | `frontend/tsoptions/tsconfig_p2.rs` (nil dereference, signal line) | `7c3b7b895e432b8d` | `fc47757c74f48255` |
  | 16682 | `ast/node.rs` (nil dereference, repanicked) | `6f73fad48b9a7140` | `83dac7be3f1572c1` |
  | 3909, 4134, 4327, 17373, 19954, 20483 | `execute/tsc/diagnostics.rs` (`go_repeat`) | `b06e51ae2e238e26` | `8e984e210c4a994e` |

- **The other 27** (timeouts and the `fuzz/1:5391` output DIFF) have no site line: `stderrSite`
  equals the old raw hash, and every fingerprint is the same.
- Reasons, input identities, Go's evidence (`go`: status, outcome, panic line, stdout and written
  hashes) and attempt lists are unchanged for all 36; only `evidence` names the new runs. Go's
  results came from the cache (0 entries published).

### Negative controls

`parity_h6_test.py` (34 checks, `H6 SELFTEST OK`):

- **form:** each `gopanic.rs` text (a panic, with the signal line, repanicked, `fatal error:
  newosproc`) has only its number replaced and gives the same form at another line; nine texts stay
  unchanged (site line not last, no blank line, no panic line, a blank line inside the block, a Go
  path, no final newline, a Go trace, the Rust hook line, a site line inside other output).
- **pin:** the real fuzz tier (`parity_fuzz.run_case`, fake Go and tsc-rs) makes the evidence row;
  regen's entry builder pins it; tsc-rs changes and the case runs again. The same crash at another
  line of the same file MATCHes the pin (and its raw stderr hash differs: version 2 failed there).
  Another file, another panic line, an extra stderr line before the block, one after the site line,
  an added signal line and another exit code do not.
- **compare:** through `parity_compare`, the moved line is GRANDFATHERED (PARITY OK) and another
  file is KNOWN-BAD (PARITY FAIL); a version 2 file is GRANDFATHER INVALID; regen refuses and match
  rejects a row without `stderrSite`.

`parity_h4_test.py` follows the new attempt key and version (`H4 SELFTEST OK`).
`parity_referees_test.py` fails one check, `lsp keys` (`session_material() missing 1 required
positional argument: 'node'`), on `perf/first-principles` as well: it is not from this lane.

**Real probe.** A scratch worktree of `perf/first-principles` (`8e08084c3`; the crates equal
`e81a14807`) with one blank line added above `go_repeat` in `execute/tsc/diagnostics.rs`, built
with `run-cargo-capped.sh build --release` (`tsgo-h6-probe-blankline`, sha256 `8ae64cf4ee4b…`),
ran the nine cases (`runs/h6-probe-blankline-full`, `-quick`). The six `go_repeat` cases now print
`diagnostics.rs:1384`; their raw stderr no longer equals the version 2 pin, and all nine MATCH the
version 3 pins in full mode (and `795` in quick, the only one of the nine in the quick manifest).
The scratch worktree and its branch were deleted.

### Gate run

`parity.sh full ~/.cache/perf-rearch/bins/tsgo-head-e81a14807-rel --base
~/.cache/perf-rearch/parity/base-current --accepted-ref a19827982` (this lane's grandfather file;
root accepts it by merging), run `runs/h6-head-e81a14807-full`, about 32 minutes at load 3 to 9:
**PARITY OK**. 187,194 items compared (185,463 base items a full run must have): 0 lost, 0 not run,
0 known-bad not as pinned, 0 new not good, 0 crashes on good items, 36 grandfathered (the nine crash
copies among them), 0 stale entries, 0 recovered, 0 newly good by a referee. Tiers: cli 170 MATCH;
tests 139,908 PASS, 1,731 IGNORED; lsp 2,629 MATCH, 29 GO-BROKEN; fuzz 29,955 MATCH, 36 DIFF,
8 GO-BROKEN, 1 JS-AGREES; corpus 12,727 MATCH (the referee statuses are the base's).

The harness and the file go together: this checkout's compare finds a version 2 file at an older
accepted ref INVALID, and an older harness finds the version 3 file INVALID.

## API tier (lane H7, 2026-11-10)

B3 confirmed two output differences from pinned Go on the API emit path (D1, D2) and listed more
suspect call sites. The gate missed them because no tier was an API client. This lane adds the `api`
tier ("api: API sessions" above) and its scenario corpus, and pins every difference it finds as a
known-bad tracking entry for the fix lane B2. Round 2 answers Astra's review of round 1 (`6e97777e6`,
REJECT, `reviews/2026-11-10-astra-h7.md`).

### The corpus (`parity-api-scenarios.json`, 49 scenarios; quick: the 22 marked q)

| group | scenarios | covers |
|---|---|---|
| dynamic names (D1, B3 risks) | `dyn-commondir` (q), `dyn-commondir-insensitive` (q), `dyn-commondir-samecase`, `dyn-resolve` (q) | B3 c0 and c3: the common directory of `^/~ts-uri~/` names in `A/` and `a/` (D1), module resolution under a dynamic root, declarations and maps to a dynamic `outDir` |
| | `dyn-json-case`, `dyn-json-case-insensitive` (q) | B3 c1: a dynamic JSON module, `outDir` `SRC` and `rootDir` `src`: risks 3 and 4 (`source_file_may_be_emitted`, `json_file_may_be_emitted`, the `rootDir` branch) and 5 (`get_output_js_file_name`'s overwrite check) |
| | `dyn-json-config-outdir-case`, `dyn-json-config-outdir-case-insensitive` (q) | risks 3 and 4 on the config branch: a config project in a dynamic `src/` with `outDir ../SRC` |
| | `dyn-reference-ext`, `dyn-reference-ext-insensitive` (q) | risk 10 (`get_source_file_from_reference`): `/// <reference path="x.TS">` under a dynamic root |
| | `dyn-config-include`, `dyn-config-include-insensitive` (q) | risks 12, 13 and 15 (tsconfig wildcard and file-name keys, `GlobVisitor`, `to_canonical_key`): `include` matching `src/A.ts` and `src/a.ts` |
| | `dyn-config-include-dirs`, `dyn-config-include-dirs-insensitive` (q) | Astra's finding 9, risk 13: `include` matching `src/A/x.ts` and `src/a/y.ts`, directories that differ only in case |
| mixed-case dirs (D2) | `case-mixed-dirs`, `case-mixed-dirs-sensitive` (q), `case-mixed-dirs-insensitive` (q), `-rootdir-insensitive` (q), `-reversed-insensitive`, `-decl-maps-insensitive`, `-decldir-insensitive` | B3 c2, c2b, c2c, c2d: `A/x.ts` and `a/y.ts` with an `outDir` on the OS default, a case-sensitive and a case-insensitive host; `rootDir` (isolates D2), the other root order, declaration plus declaration and source maps with `inlineSources`, `emitDeclarationOnly` into a `declarationDir` |
| outDir and rootDir by case, overwrites | `case-json-overwrite-insensitive` (q), `case-json-overwrite-sensitive`, `case-js-overwrite-insensitive` (q), `case-js-overwrite-sensitive`, `js-overwrite` (q), `case-outdir-rootdir-insensitive` | B3 c1b: a JSON output that would overwrite its input on an insensitive host; `allowJs` outputs that would overwrite their inputs (TS5055), with and without a case-only `outDir`; TypeScript outputs into a case-only `outDir` with maps |
| request filesystem | `case-duplicate-files-insensitive`, `case-import-casing-insensitive`, `symlink-dir`, `relative-names` | `A.ts` and `a.ts` in one insensitive filesystem (Go refuses the duplicate), an import whose casing differs, a request-filesystem symlink, file names relative to `--cwd` |
| emit kinds | `json-module` (q), `decl-maps` (q), `errors-and-libs`, `composite-program` | a JSON module with declarations; declaration and source maps with `sourceRoot` and `mapRoot`; a full filesystem without `noLib` (no bundled libs) plus type and syntax errors; `composite` with a `tsBuildInfoFile` through `createPrograms` |
| snapshot updates (round 2) | `update-reconfigure-emit` (q), `update-file-change-emit`, `update-mixed-dirs-insensitive` (q) | repeated emits with changed options (`reconfigurePrograms`: declarations, maps, another `outDir`), `release` of the replaced snapshot and a second `release` of it (an error answer); a new full filesystem with an edit and a new file; D2 after an update |
| layer filesystems (round 2) | `layer-emit-disk` (q), `layer-emit-mixed-dirs`, `layer-emit-mixed-dirs-insensitive` (q) | a `layer` filesystem over a disk fixture: a file only in the layer importing one only on disk, and `emit` writing through the host filesystem into the fixture; D2 on that writing path |
| build orchestrator (disk fixtures) | `build-composite` (q), `build-clean`, `build-references`, `build-dispose`, `build-mixed-dirs`, `build-mixed-dirs-insensitive` (q), `build-symlink-references`, `build-symlink-references-insensitive` | a composite project built twice (cold, then up to date) with maps and `tsconfig.tsbuildinfo`; build then `cleanBuild`; a solution with two composite projects, a type error and `buildReferences`; build, `disposeBuildOrchestrator`, then build and dispose on the disposed one (error answers); the c2 tree as a composite config project on both case settings; B3 risk 7 (project-reference symlink redirects): a pnpm-style monorepo where `packages/b` references `packages/a` and imports it through `node_modules/a -> ../../a` |

B3 risks 2 (D1), 3, 4, 5, 7, 10, 12, 13 and 15 each have a scenario. Risks 3 to 5 do not show yet: on
Linux D2 makes the emit side case-sensitive, so tsc-rs takes the case-sensitive path and gives Go's
answer by accident. Go emits `SRC/data.json` in `dyn-json-case-insensitive` and
`dyn-json-config-outdir-case-insensitive`, and so does tsc-rs today. Once B2 fixes D2, those items will
show whether the call sites behind them still compare dynamic names with the OS's case rules. B3's
LSP-preference risk (6, `userpreferences.rs`) is not an API path: this tier does not cover it.

**Known coverage gaps** (not in the corpus):
- dynamic redirect targets and package paths that differ only in case (B3 risks 8 and 9,
  `compare_paths_by_redirect`, `package_json_paths_are_equal`): reaching them needs a declaration emit or
  an auto-import that generates a module specifier through two such redirects; no cheap scenario does;
- edit-then-rebuild through the build orchestrator: the edit would have to happen on disk between two
  `build` requests, inside the sandbox, and no request makes one (layer and full filesystems are
  snapshot inputs, which the orchestrator does not read);
- callback filesystems (`--callbacks`): the client would have to answer the server's filesystem
  requests; the driver enables no callbacks and fails a session that sends one;
- `host` symlinks in a request filesystem (refused: they read the host);
- the msgpack sync protocol, the checker and type queries (`scripts/goport/api_oracle.py`), and API
  sessions inside an LSP server (`custom/initializeAPISession`).

### Known differences at `ed6edf820` (14, pinned for B2)

Each one reproduces with

```sh
P=scripts/perf-rearch/parity_api.py
diff <(python3 $P probe ~/.local/bin/tsgo-oracle-3e9236361fa9 <scenario>) <(python3 $P probe <tsc-rs bin> <scenario>)
```

| scenario | difference (Go, then tsc-rs) | B3 |
|---|---|---|
| `dyn-commondir-insensitive` | `virtualout/A/x.{js,js.map,d.ts}` and `virtualout/a/y.*`; tsc-rs `virtualout/x.*`, and `y.*` beside its source as `^/~ts-uri~/mem/auth/a/y.*` | D1 |
| `case-mixed-dirs-insensitive` | `out/x.js`, `out/y.js`; tsc-rs `out/x.js`, `a/y.js` | D2 |
| `case-mixed-dirs-rootdir-insensitive` | `rootDir A`: `out/x.js`, `out/y.js`; tsc-rs `out/x.js`, `a/y.js` | D2 (isolated) |
| `case-mixed-dirs-reversed-insensitive` | `out/x.js`, `out/y.js`; tsc-rs `A/x.js`, `out/y.js` | D2 |
| `case-mixed-dirs-decl-maps-insensitive` | every output (js, map, d.ts, d.ts.map) in `out/`; tsc-rs `a/y.*` beside the source | D2 |
| `case-mixed-dirs-decldir-insensitive` | `types/x.d.ts`, `types/y.d.ts`; tsc-rs `a/y.d.ts` | D2 |
| `case-json-overwrite-insensitive` | no `SRC/data.json` (it would overwrite `src/data.json`); tsc-rs emits it | D2 (c1b) |
| `case-outdir-rootdir-insensitive` | the maps' `sources` are `a.ts` and `b.ts` (`SRC/` and `src/` are one dir on this host); tsc-rs `../src/a.ts`, `../../src/sub/b.ts` | D2, at the source-map writer |
| `build-mixed-dirs-insensitive` | the build orchestrator's `out/tsconfig.tsbuildinfo` has `"root":[[1,3]]`; tsc-rs `[[2,3]]` (it does not match the root `A/x.ts` to its `fileNames` entry `../a/x.ts`); every other output equal | D2 family (build info; not attributed further) |
| `update-mixed-dirs-insensitive` (round 2) | `out/x.js`, `out/y.js` before and after an `updateSnapshot`; tsc-rs `a/y.js` | D2, after an update |
| `layer-emit-mixed-dirs-insensitive` (round 2) | `emit` through a layer filesystem writes `out/x.js`, `out/y.js` into the fixture; tsc-rs `out/x.js`, `a/y.js` | D2, on the path that writes |
| `dyn-reference-ext-insensitive` | TS6054 "File 'x.TS' has an unsupported extension" and no `x.TS` in the program; tsc-rs takes `x.TS` into the program and emits `out/x.TS.js` | risk 10, confirmed |
| `dyn-config-include-insensitive` | the config project has `src/A.ts` and `src/a.ts`; tsc-rs drops `src/a.ts` (and its output) | risks 12, 13, 15, confirmed |
| `dyn-config-include-dirs-insensitive` (round 2, Astra's finding 9) | the config project has `src/A/x.ts` and `src/a/y.ts`; tsc-rs drops `src/a/y.ts` | risk 13, confirmed |

Risks 10, 12, 13 and 15 show although D2 is not fixed: they are on the program side (file loading and
config parsing), which already takes the session's case setting. Every other scenario is MATCH.

**Pinned, not accepted.** Each is a `known-bad` tracking entry in `parity-grandfathered.json`, made with
the grandfather tooling (`parity_grandfather.py evidence` and `regen --only '^api/'` on
`tsgo-head-ed6edf820-rel`, full and quick: runs `h7r2-gf-evidence-ed6edf820-{full,quick}`). Its reason
says that it is a confirmed difference, that fix lane B2 is open and removes the entry, and that any
change fails the gate. The pins are the structured evidence every entry has: the scenario's key, Go's
result (since round 2 its `stdout` fingerprint covers Go's answers, how it ended and its stderr; its
`written` fingerprint the storage after every request) and the fingerprint of every tsc-rs session. A
fix makes the item good and its entry stale, so B2's gate fails until B2 removes the entry. Any other
change also fails the gate. `regen --only` rebuilds only the matching entries and keeps every other entry
byte for byte (the 36 fuzz entries are unchanged); since round 2 it refuses a `--from` file of an older
version, whose kept entries it would otherwise relabel.

### Round 2: Astra's nine findings

Every control is in `parity_h7_test.py` and uses Astra's probes where there are some (her proxy,
`scratch/review-h7-astra/proxy-extra.py`, without its two log writes outside the session; her
version 3 grandfather file; her non-executable oracle). The end-to-end controls ran on round 1's harness
too (`parity_h7_test.py --e2e-only --harness <git archive 6e97777e6>`): each finding's control fails there
and passes now. The "on 6e97777e6" column is that run; for 7 and 8 it is Astra's review.

| # | finding | fix | control | on 6e97777e6 | now |
|---|---|---|---|---|---|
| 1 | a child that outlives the server holding its output passed (the reader joins timed out unchecked) | the init is PID 1 of a PID namespace: when the server ends, every other process is a survivor, a failure, and is killed; both streams must reach EOF | `child-stdout` | MATCH, after 20 s | DIFF rsFailure crash, at once |
| 2 | the host outside the mounted dirs was writable | the whole tree read-only, the host's state dirs empty tmpfs; writable: the home and the fixture's overlay, read after every request | `outside-write` (the review dir, `/etc`, `/var/tmp`, `/tmp`) | 3 host files written, MATCH | none written |
| 3 | answers compared as parsed values (`false` = `0`) and the envelope dropped | the answer's raw bytes compared; strict envelope (`jsonrpc` `"2.0"`, the integer `id`, `result` or a typed `error`, nothing else; no duplicate key, no NaN) | `bool-number`; `bad-rpc`; unit: `1` against `1.0`, other bytes of the same value, 7 malformed envelopes | MATCH, MATCH | DIFF; DIFF rsFailure crash |
| 4 | an oracle that could not be exec'd was GO-BROKEN, and cached | setup and exec failures come back on the init's private pipe: ERROR, nothing cached | Astra's `unexecutable-go` | GO-BROKEN, cached | ERROR, not cached |
| 5 | a build output corrupted and then removed by `cleanBuild` passed | the storage (upper dir and home) is read after every request and compared | `corrupt-build` on `build-clean` | MATCH | DIFF |
| 6 | the tripwire saw no `stat`, `statx` or `O_PATH` access | the guarded dirs do not exist in the sandbox (no tripwires); the server gets no fd but 0, 1, 2; the `parity_watch` comment now says what IN_CLOSE_NOWRITE's removal rests on | `stat-bait`; `fds` | MATCH; MATCH | DIFF (the path does not exist); MATCH |
| 7 | `regen --only` kept 36 version 3 entries and wrote `version: 4` | kept entries need a valid `--from` of this version | Astra's `grandfather-v3.json` with `--only '^api/'` | relabelled | refused, nothing written |
| 8 | the pins left Go's stderr out | Go's `stdout` fingerprint holds its stderr sha | unit: another Go stderr is other evidence | matched | other evidence |
| 9 | no scenario for dynamic directories that differ only in case | `dyn-config-include-dirs-insensitive` and its control | unit: the scenario exists, quick runs it, B2's pin tracks it | absent | DIFF, pinned for B2 |

### Round 3: Astra's recheck of `5c6b0c8d5`

Astra's cases (`scratch/review-h7r2-astra/probe.py`) are the self-test's fake as they are
(`parity_h7_fake.py`, plus a `privs` mode). Run on round 2's harness (`--e2e-only --harness <git archive
5c6b0c8d5>`), each finding's control fails; on this head, all pass.

| # | finding | fix | control | on 5c6b0c8d5 | now |
|---|---|---|---|---|---|
| 1 | a reader that died of an exception counted as a clean EOF (`Content-Length: ` and 5,000 digits after the last answer: `int()` raised, MATCH) | any reader exception fails the session as malformed; EOF is a flag set on `b''`; header lines, count, digits and frame size bounded | `trailing-length` (and `trailing-deep`, caught before too) | MATCH | DIFF rsFailure crash |
| 2 | the server was root in its user namespace: it cleared read-only on `/dev/shm` and wrote there, and mounted a tmpfs over `/proc` to hide a survivor | the server drops every privilege before exec (nested user namespace owning nothing, no further user namespaces, all five capability sets empty, uid and gid 1000, `no_new_privs`, checked); survivors from a `/proc` fd opened before the server and from what PID 1 reaps after the kill | `shm-remount`, `hide-proc-survivor`; `privs` (no capability; `mount`, `umount`, `mount_setattr` of `/dev/shm` and of `/` recursively, `pivot_root`, `unshare(CLONE_NEWNS)`, `unshare(CLONE_NEWUSER)`, `setns` into PID 1's mount namespace: every one fails); `detached`, `report-access` (caught before too) | MATCH, MATCH; ERROR | DIFF (each operation fails, so the probe fails); MATCH |
| 3 | the home skeleton's paths were skipped unchecked (`$HOME/tmp` replaced by a file: MATCH) | every home entry listed with its type and mode; the skeleton checked against its exact structure (each dir real, mode 755) | `home-skeleton`; unit: a file for a dir, another mode, a dir gone | MATCH | DIFF |

Astra's other round 2 cases (a large integer, `-0`, a `\u` escape in a key, a BOM, trailing whitespace,
`1e999`) stay not MATCH. One consequence of the drop: the server writes as a user, so a fixture's dirs
have a checkout's modes (755) instead of the snapshot's 555; with 555 the reference builds could not
create `lib/out` and `app/out`, and Go's emitter then crashes with a nil dereference (and tsc-rs, which
reproduces Go's panics, does too). The 14 api pins were regenerated for the new keys
(`h7r3-gf-evidence-ed6edf820-{full,quick}`, `regen --only '^api/'`); their reasons, and the 36 fuzz
entries, are unchanged.

### The final fix (root's decision after the review cap)

Astra's final recheck of `a08c934d6` closed every round 2 blocker and found one more false MATCH:
`_walk` did not record the storage root's own metadata, and `os.walk` silently skips a directory it
cannot list. A server that wrote `unexpected.js` after Go's answers and then made the fixture root mode
000 (`review-noemit`, a disk scenario with no emit) got MATCH, and so did a root of mode 0700 on
`build-composite`, quick and full (`scratch/review-h7r3-astra/confirmed-root-hide-full`,
`confirmed-root-mode-{full,quick}`).

- **Fix:** the root is entry `.` (its type and mode, by `lstat`; a root that is not a dir is recorded
  as such and not walked); `os.walk` gets an `onerror` that records each dir it cannot list as
  `unlistable <strerror>` (on the root, beside its `.` entry); an entry that cannot be stat'ed (a dir
  that can be listed but not searched) is `unstatable <strerror>`. A root that does not exist on either
  side still gives no entries.
- **Controls** (`parity_h7_test.py`; Astra's two modes, `root-hide` and `root-mode`, are in
  `parity_h7_fake.py` as she wrote them): `root-hide` on her `review-noemit` scenario, and `root-mode` on
  `build-composite` in full and in quick. All three MATCH on `a08c934d6` (`--e2e-only --harness <git
  archive a08c934d6>`) and DIFF now.

- **Pins** regenerated on the final sources (`h7final-gf-evidence-ed6edf820-{full,quick}`, `regen --only
  '^api/'`): the 14 reasons and the 36 fuzz entries unchanged.
- **Gate** (`--accepted-ref t3/api-parity-tier` at `7bfaa714b`, base `runs/head-ed6edf820-full`): run
  `runs/h7final-head-ed6edf820-full-2`, 25 min (16:17 to 16:42 UTC, load 5 to 28): **PARITY OK**, 187,270
  items compared, 0 lost, 0 not run, 0 known-bad not as pinned, 0 new not good, 50 grandfathered (36 fuzz,
  14 api), 0 stale; api 35 MATCH, 14 DIFF; cli 170 MATCH; tests 139,983 PASS, 1 FLAKY (the usual
  `watch_ends_soon_after_sigint_or_sigterm`); lsp 2,629 MATCH, 29 GO-BROKEN; fuzz 29,955 MATCH, 36 DIFF
  (grandfathered), 8 GO-BROKEN, 1 JS-AGREES; corpus 12,727 MATCH. The run before it
  (`runs/h7final-head-ed6edf820-full`, same sources) was PARITY FAIL on one lost item, `fuzz/1:2450`:
  tsc-rs's first attempt timed out (60.17 s of 60) during another project's build burst (load 42; the
  fuzz tier's tsc-rs CPU time 10,321 s against the base's 5,602 s), and its retry (29 s) and second run
  (12 s) answered. Alone, at a load of 26, the case MATCHes in about 10 s a run. The fuzz tier uses
  nothing this fix changed.

**Known limit of the fixture check (document only; no fix).** A process on the host that maps a fixture
file writable and shared before the tier starts its watch can change the file's bytes through the
mapping and change them back before the end check: both content checks pass, and inotify sees nothing
(writes through an mmap make no event). Astra's reproducer is `scratch/review-h7r3-astra/mmap-probe.py`
(`mmap-probe.json`: before PASS, the bytes changed during the tier, no watch event, after PASS). That
is another host process racing the harness, not the compiler under test, which runs in the sandbox
and cannot reach the fixture snapshot. The same holds for the snapshots of the cli and lsp tiers.

### The lsp tier: the same gaps (reported here; fixed by lane H8, see "LSP sandbox and wire (lane H8)")

`vendor/lspdiff/sandbox.mjs` runs each server under `unshare -rmn` with overlays of its inputs and its
home bound, and `client.mjs` kills only that process. With Astra's corrections from her recheck:
- **survivors (finding 1):** no PID namespace; a server's child can outlive it. Stdout's EOF is enforced
  (the client reads to the end); stderr's is not: the session result waits at most 2 s for stderr to
  close and records `stderr.complete: false`, which does not change the verdict;
- **privileges (round 3 finding 2):** the server is root in its user namespace, which owns its mount
  namespace: it can remount, unmount or mount over anything there, `/proc` included;
- **the initialize answer is not compared** (high; `session.mjs:21`): only an `error` in it is checked;
- **number parsing** (high; `client.mjs:56`): `JSON.parse` reads `1e999` as `Infinity`, which
  `JSON.stringify` writes as `null`, so a server that answers `1e999` where Go answers `null` matches;
- **private-home writes** (medium; `sandbox.mjs:162`): what a server writes into its private home is
  counted (`homeWrites`) but changes no verdict;
- **sandbox exec failures** (high; `lspdiff.mjs:305`): a server that cannot be started (a namespace or
  exec failure) is a crash of that run, so on the Go side it is GO-BROKEN, not ERROR;
- **host writes (finding 2):** everything but the overlays and the home stays writable with the user's
  permissions; a server can write outside its session unseen;
- **metadata reads (finding 6):** the tripwires see reads and listings of the guarded dirs, not `stat`,
  `statx` or `O_PATH`;
- **envelopes (finding 3):** `client.mjs` dispatches answers by `id` and does not check `jsonrpc` or the
  envelope's keys (an answer with both `result` and `error`, or extra keys, passes; an answer to an
  unknown id is dropped; `JSON.parse` takes the last of duplicate keys). The answers themselves are
  normalized with `JSON.stringify`, which keeps types (`false` is not `0`).
Fixing them changes `sandbox.mjs`, `client.mjs` and `session.mjs`, which are session sources: every lsp Go
fill and variant record would start empty, and the Go-race sessions need refills (H5's "Go fills"). That
is lane H8; `parity_api_init.py` and `parity_api_session.py`'s readers are the design to port.

### Negative controls (`parity_h7_test.py`, 108 checks, `H7 SELFTEST OK`, about 30 s to 2.5 min)

Besides the table above, the round 1 controls (fakes in front of the Go oracle, through the real tier
and `parity_compare.py compare`): a transparent proxy MATCHes; a wrong output path is a DIFF and PARITY
FAIL (LOST against a base where it MATCHed, NEW against one without it); a crash, a late exit 3 and a
malformed header are DIFF rsFailure crash; a hang is DIFF rsFailure timeout; another output on every run
is rs-nondet, and as Go GO-BROKEN (full, and quick after the confirming run); a home write is a DIFF; a
read of the real typings cache finds nothing (the fake fails: DIFF). Cache controls on a scenario of its
own: a second run reads Go from the cache with the same key; another case flag and one more fixture byte
are other keys (Go runs again); an entry whose material is not the key's is not read; a fixture whose
bytes are not its scenario's is ERROR. Unit: every key input (case flag, no flag, a file, the filesystem
shape, an option, the requests, the updates, the fixture, the ancestors, the Go binary, both sandbox
sources, the protocol, the home path) is another key; the format refuses a layer filesystem without a
fixture, a host symlink, no filesystem, build steps without a fixture, an unknown key, an absolute
fixture path, a fixture symlink that leaves the fixture, an unknown request; the judge's order; the
tripwire race of `parity_watch.ACCESS`. The H4, H5, H6 and referee self-tests pass on this harness.

### The tripwire race (found by this lane's first gate run)

The first full gate of this lane (`runs/h7-head-ed6edf820-full-stopped`, stopped after its api tier)
had one lsp ERROR: `lsp/hono-f12-p6: a process of the session touched an unkeyed path:
/home/agent/.config (close-nowrite /home/agent/.config)`. The first event on the bait was a close,
with no open after the watch started. That is the race lane H4 found for IN_CLOSE_WRITE, now on H5's
access mask:

- `Watch.__init__` lists the bait dirs (`os.scandir`), then drops its own events (`_discard`).
- Another thread of the tier forks a process (`subprocess`) during that listing. The child has a copy
  of the dir fd and closes it (`close_fds`), and the description's last close comes after `_discard`.
- The close alone was counted as an access.

`ACCESS` no longer holds IN_CLOSE_NOWRITE. That loses nothing only because of the lsp tier's lifecycle:
each session's watch is built before lspdiff starts, every server is a descendant started after it, and
none gets an fd but 0, 1 and 2, so every read or listing by a session process opens after the watch
(IN_OPEN). It is no blanket rule: `stat`, `statx`, `access` and `open(O_PATH)` with `fstat` make no
inotify event at all (Astra, H7 review), so the lsp tripwires do not see metadata reads; see "The lsp
tier: the same gaps". `parity_h7_test.py` has the race: 400 watches while 4 threads fork. The old
mask tripped 15 to 52 of them in each run, the new one none. A read, a listing and a listing of a
subdir after the watch started still trip. `parity_h5_test.py` passes (`H5 SELFTEST OK`). The lsp tier's
tripwires had the same latent flake since H5. (The api tier has had no tripwires since round 2.)

### Gate run, round 1

`parity.sh full ~/.cache/perf-rearch/bins/tsgo-head-ed6edf820-rel --base ~/.cache/perf-rearch/parity/base-current
--accepted-ref 5ae5006ca` (this lane's manifest and grandfather file; root accepts them by merging; the
tests tier on this lane's checkout, whose `crates/` equal `ed6edf820`). `base-current` was
`runs/head-ed6edf820-full`, root's accepted run of the same binary (sha256 `310f56f918c3…`, built from
`ed6edf820` in root's checkout). Run `runs/h7-head-ed6edf820-full`, 26 min (09:40 to 10:06 UTC, load 4 to
34): **PARITY OK**.

- 187,270 items compared (185,539 base items a full run must have): 0 lost, 0 not run, 0 known-bad not
  as pinned, 0 new items not good, 0 crashes on good items, **47 grandfathered** (the 36 fuzz entries
  and the 11 new api entries, each failing exactly as pinned), 0 stale entries, 0 recovered, 0 newly
  good by a referee.
- api: **27 MATCH, 11 DIFF** (the known differences above), 1.3 s. The 74 new items are all new to the
  base: the 27 MATCHes count as good, and the 11 DIFFs pass only through their entries.
- cli 170 MATCH; tests 139,983 PASS, 1 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`), 1,731
  IGNORED; lsp 2,629 MATCH, 29 GO-BROKEN; fuzz 29,955 MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN,
  1 JS-AGREES; corpus 12,727 MATCH. All as in the base.

### Gate run, round 2

`parity.sh full ~/.cache/perf-rearch/bins/tsgo-head-ed6edf820-rel --base ~/.cache/perf-rearch/parity/base-current
--accepted-ref t3/api-parity-tier` (this lane's branch at `f38ea4053`, the harness of the run), against
`base-current` = `runs/head-ed6edf820-full` (root's accepted run of the same binary). Run
`runs/h7r2-head-ed6edf820-full`, 23 min (14:02 to 14:25 UTC, load 13 to 17; it waited for another lane's
gate first): **PARITY OK**.

- 187,270 items compared (185,539 base items a full run must have): 0 lost, 0 not run, 0 known-bad not
  as pinned, 0 new items not good, 0 crashes on good items, **50 grandfathered** (the 36 fuzz entries,
  unchanged, and the 14 api entries, each failing exactly as pinned), 0 stale entries, 0 recovered, 0
  newly good by a referee.
- api: **35 MATCH, 14 DIFF** (the known differences above), 3.1 s.
- cli 170 MATCH; tests 139,983 PASS, 1 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`), 1,731
  IGNORED; lsp 2,629 MATCH, 29 GO-BROKEN; fuzz 29,955 MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN,
  1 JS-AGREES; corpus 12,727 MATCH. All as in the base.
- An earlier round 2 run (`runs/h7r2-head-ed6edf820-full-interrupted`) had the same cli, tests, lsp and api
  results and was cut off in its fuzz tier when the VM restarted.

### Gate run, round 3

Same command and base (`--accepted-ref t3/api-parity-tier`, the branch at `0c615dda3`, the harness of the
run). Run `runs/h7r3-head-ed6edf820-full`, 23 min (14:59 to 15:21 UTC, load 6 to 16): **PARITY OK**.
187,270 items compared (185,539 base items a full run must have): 0 lost, 0 not run, 0 known-bad not as
pinned, 0 new items not good, 0 crashes on good items, 50 grandfathered (the 36 fuzz entries, unchanged,
and the 14 api entries, re-pinned for round 3's keys, each failing exactly as pinned), 0 stale entries.
api 35 MATCH, 14 DIFF in 7.2 s (each session now also enters its own user namespace and drops its
privileges: about 0.1 s more than round 2's). cli 170 MATCH; tests 139,983 PASS, 1 FLAKY
(`watch_ends_soon_after_sigint_or_sigterm`), 1,731 IGNORED; lsp 2,629 MATCH, 29 GO-BROKEN; fuzz 29,955
MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN, 1 JS-AGREES; corpus 12,727 MATCH. All as in the base.

### Cost

- **api tier, round 2** (each session now starts the sandbox's init, a Python process, and the storage
  is read after every request): 3.1 s full with the Go cache warm (49 scenarios, tsc-rs twice) in the gate
  at a load of about 15; quick 1.7 s (22 scenarios) and cold 12.3 s (every Go entry new, as after an
  edit of either sandbox source) at a load of 37 to 47. A session takes 0.41 s median, 0.71 s at most,
  there; 8 scenarios run side by side. Round 1 took 1.3 s full and 0.6 s quick.
- **Inputs:** the 8 fixtures (6 distinct) are a few KB each, made once; checking them, the ancestors'
  state and the watches cost under 0.1 s.
- **Go cache:** one JSON entry per scenario (2 Go sessions, with the storage after every request and the
  written files' texts): 49 entries in the current keys; the dir holds 2.2 MB with the older keys and
  the self-tests' entries.
- **Self-tests:** `parity_h7_test.py` takes about 2.5 minutes (89 checks; the tripwire race about 10 s,
  the end-to-end controls the rest); `--unit` about 14 s.
## LSP sandbox and wire (lane H8, 2026-11-10)

The api tier runs each session sealed and unprivileged (lane H7, three review rounds). The lsp tier had
the same gaps or worse (Astra's H7 round 2 recheck, question 4; "The lsp tier: the same gaps" above):
each let a misbehaving server be reported as MATCH or GO-BROKEN. This lane ports the api tier's design
to the lsp tier, sharing its code, and fixes each gap at the level of the design.

### The design

- **One init for both tiers.** `parity_api_init.py` became `parity_sandbox_init.py`, the PID 1 of every
  api and lsp server. Its spec is general: `mounts` in order (binds, read-only unless `rw`, and overlays
  of a read-only lower with an upper and work dir the server cannot reach), `expose` (host files and
  dirs the server needs, bound back read-only at their own paths when a hidden dir covers them), the
  server's `cwd`, the fixed `mtime` and the `cmd`. The policy is the init's own, the same for both tiers:
  `HIDDEN` (the user's files, `/tmp`, `/var/tmp`, `/run`, `/mnt`, `/media`, `/srv`, `/opt`, `/dev/shm`)
  are empty tmpfs dirs; the whole tree is read-only but the `rw` binds and the overlays; a mount point
  outside the hidden dirs is refused (it would be made on the host's filesystem); every symlink in a
  hidden dir on the way to the cwd, an exposed path or a mount point is made again, read-only (umami's
  project path is a symlink to its snapshot's path). The server drops every privilege before exec (a
  nested user namespace that owns nothing and may make no other, no capability, uid and gid 1000,
  `no_new_privs`, checked), keeps no fd but 0, 1 and 2, and has no network. When it ends, every process
  left in its PID namespace is a survivor (listed through a `/proc` fd opened before the server
  started), killed and reported. A setup, privilege-drop, cwd or exec failure comes back on the init's
  report pipe, never as a server result. Callers run `python3 -I parity_sandbox_init.py launch SPEC`; the
  init execs `unshare` with itself as PID 1 (`UNSHARE` is in the init too), so both tiers start servers
  the same way. The api tier's spec is its old one in the new form (the home bound `rw`, the fixture's
  overlay, the command's files exposed): its 49 scenarios, its pins (before B2 removed them: Go's result
  and every attempt fingerprint of the 14 entries unchanged, only their keys) and `parity_h7_test.py`'s
  108 checks are the same on it.
- **The storage, made, recorded and listed in one place** (round 2; `parity_sandbox_storage.py`, which
  the init imports): the init mounts one tmpfs per session in its own user namespace for the private home
  (`home/`, with its skeleton) and each overlay's upper and work dirs (`ovl<k>/`), binds the home at the
  home path, mounts the overlays on it, and detaches it (keeping an fd: the server reaches it only through
  the home and the overlays). Before the server starts it marks the tmpfs with fanotify (a filesystem
  mark, which a user namespace may set on a filesystem it mounted): every create, write, attribute
  change, delete and rename anywhere in the storage is recorded, in a dir made a moment before too (no
  watch per dir, so no race), as the order-free record `writes` (for every path ever touched, what
  happened to it; the overlays' work dirs left out). After the end (survivors killed) it lists the
  storage with the one lister both tiers use (`walk`, the api tier's `_walk` of H7's final fix: every
  entry, the roots as `.`, an unlistable dir or unreadable file said so, never skipped; the init has
  every capability over the tmpfs, so a mode the server sets hides nothing), and reports both. The api
  tier asks the init for a listing after every request (a control pipe) and compares the write record as
  part of its storage; lspdiff judges them as items (below).
- **lspdiff's servers** (`sandbox.mjs` `ServerProcess`; Go, tsc-rs and JS tsserver) run under it when
  `LSPDIFF_SANDBOX` is set (parity_lsp.py always sets it): the private home at the fixed home path, an
  overlay per project snapshot at its own path, and as read-only binds the files the command
  names (each word that names a file, by its realpath: `--go` and `--rs` are commands, split at
  spaces, as the api tier's), a fixture scenario's dir (the script writes it from outside; the server
  can only read it), tsserver's package, and with ATA on the stand-in npm, its store and, at the fixed
  dir `/run/lspdiff-ata`, its config (read-only), node (read-only) and its log's dir (`rw`, the one
  writable place outside the storage; it must hold the log and nothing else after the run). The
  stand-in reads only that config (round 2: nothing of the environment, which the server controls), and
  checks every store file it installs against the store's sha256 list (the frozen store's manifest,
  `ata-typings.json`; the fixture store's own); the log is the ATA barrier's and a report, not evidence:
  what was installed is in the write record and the storage.
  The stand-in npm runs in the server's PID namespace, so the ATA barrier's liveness check reads the
  init's descendants from the host's `/proc` (NSpid). The stand-in now writes `package.json` and the
  lock with their maps sorted, as npm does, so the home does not depend on the order of ATA's calls; it
  lays packages out with npm's modes (dirs 755, files 644) instead of the frozen store's (its snapshot
  has dirs 555, which only a server that was root in its namespace could write into: the first fill
  after the privilege drop timed out in ATA on watch-ts-pattern-f12 and watch-hono-f12, Go and tsc-rs
  alike); and a failure of its own is logged as `error`, a harness error of the scenario, not a wait
  that runs into its deadline.
- **The wire** (`client.mjs`, `json.mjs`), as the api tier reads its server: a header is only
  `Content-Length` (and `Content-Type`), at most 4 lines of 256 bytes, a length of at most 10 digits and
  256 MB; a body is UTF-8 without a BOM and one JSON text with no duplicate key; every message is exactly
  a JSON-RPC 2.0 envelope: an answer to a pending request (`{jsonrpc, id, result}` or `{jsonrpc, id,
  error: {code, message, data?}}`), a server request or a notification. Anything else breaks the
  protocol: the run ends there, a crash (rsFailure crash for tsc-rs, GO-BROKEN for Go). Answers are
  compared as canonical JSON (the server's key order, strings re-escaped, no insignificant whitespace);
  `json.mjs` `parseKeep` keeps every number whose text JSON.stringify would not give back (`1e999`,
  `-0`, `1.0`, `1e0`, integers past 2^53) as that text (`JSON.rawJSON`), so the comparison never maps two
  texts of a number to one value. The referee's normalizer reads answers the same way. Round 2 (cost):
  `scan` is one tokenizer pass (escape-aware strings, brackets, the commas before keys, and only the
  number tokens that can be non-canonical: a fraction, an exponent, `-0`, 16 or more digits), which finds
  a duplicate decoded key and checks each such number's text; a body whose numbers are all canonical is
  parsed with plain `JSON.parse`, the others with the reviver. An entry that holds a kept number is
  marked `rawNumbers`, and lspdiff and the referee read and normalize with the reviver only what is
  marked. No plain object is a map keyed by names a server gives (round 2, finding 2: a key `__proto__`
  in a `{}` set its prototype instead; the lister is Python now, and the referee and tsserver.mjs build
  such objects with `Object.fromEntries` or a `Map`).
- **Four new items per session, after the last answer** (so every earlier item keeps its index):
  - `<n> initialize`, the initialize answer, and `<n+1> session messages`, every server-to-client
    request and notification after the last checkpoint (a standard session: all of them) through the
    end of the server's output, `window/logMessage` left out and watcher ids masked (round 2, finding 4:
    a message sent while shutdown is answered, or in a session without checkpoints, was dropped). Both
    are answers like any other: tsc-rs's determinism, GO-BROKEN when Go's runs differ, accumulated Go
    evidence, then MATCH or DIFF.
  - `<n> session storage` and `<n> session writes` (round 2, finding 7: two items, judged path by path):
    the storage after the end (every path of the home and of each overlay's upper dir, and the home
    skeleton's deviations) and the write record. On each path, tsc-rs's value (absent counts as one)
    must be a Go value. The storage is judged against this fill's Go runs only, never a recorded or prior
    variant: where they agree on a path tsc-rs must equal it; where they differ on a path (the final
    storage itself varies across Go's runs), tsc-rs must equal one of them, and only that path is
    exempt. The writes count this fill's Go runs, the clean prior runs and the per-path variants recorded
    by earlier fills (one record key per path); only a path whose Go value varies is exempt, and a path
    tsc-rs touched that no Go run ever did is a DIFF. The item is MATCH when every path is equal,
    GO-BROKEN when tsc-rs differs only on paths where Go varies, else DIFF; tsc-rs's runs (and the
    confirming run) must agree on every path.
- **Shutdown** (round 2, finding 5): its result must be `null`; any other is a broken shutdown.
- **Session result:** a survivor, an output stream held open 10 s after the end (both streams; stderr's
  EOF used to be waited on for 2 s and not enforced), the exit, stderr crash lines, changed inputs and
  the shutdown, as before. `infra` (the init's setup report, a spawn failure, or no report from an init
  lspdiff did not kill) stops lspdiff with exit 3: the session is ERROR, and that run is neither staged
  nor added to the variant record.
- **Gone:** the tripwires (`GUARDED`, the bait dirs and their inotify watch): the dirs they guarded do not
  exist for a server, not even for `stat`. The ancestor dirs keyed are the ones a server sees (none in a
  hidden dir; on MS-A2 only `/`).
- **Keys:** the init, its storage module and `json.mjs` are session sources, and `CACHE_VERSION` is 5
  (round 2), so every lsp Go and JS key changed and every variant record started empty. The api tier's
  key holds the init and the storage module too.

### The gaps, their fixes and their controls

`parity_h8_test.py` (`--unit`: 15 checks, seconds; end to end: 24 checks through `parity_lsp.py --tier
quick --no-js` on ts-pattern-f12-p6, about 10 minutes, under the gate lock) with `parity_h8_fake.py`, an
LSP proxy in front of the Go oracle: Astra's lsp probes (`scratch/review-h7r2-astra/lsp/`: init.py,
overflow.py, home.py, the non-executable Go) and the api tier's round 2 and 3 probes as modes. The
"before" column is the same test run against the harness before this lane (`--e2e-only --harness
<git archive c788830c4>`).

| gap | fix | control | before (`c788830c4`) | now |
|---|---|---|---|---|
| no PID namespace: a child outlives the server; stderr's EOF not enforced | the init is PID 1; survivors fail; both streams must reach EOF | `detached`, `hide-proc-survivor` (tmpfs over `/proc` first), `child-stderr` | MATCH ×3 | session end DIFF; DIFF; session end DIFF |
| everything outside the overlays and the home writable; the server root in its namespace | the sealed read-only tree; every privilege dropped | `outside-write` (the review dir, `/etc`, `/var/tmp`, `/tmp`); `shm-remount`; `privs` (no capability; mount, umount, mount_setattr, unshare of a mount or user namespace all fail) | 3 host files written; MATCH; the probe fails (it could) | none written; DIFF; good |
| the tripwires do not see `stat` or `O_PATH` | the guarded dirs do not exist; no fd but 0, 1, 2 | `stat-bait`; `fds` | MATCH; good | DIFF; good |
| high: the initialize answer not compared (`session.mjs:21`) | item `initialize` | `init-null` (Astra's init.py) | MATCH | initialize DIFF |
| high: lossy number parsing (`client.mjs:56`) | `parseKeep`: a number keeps its text | `overflow` (Astra's overflow.py, 1e999 for null), `minus-zero`, `float` (1.0), `exp` (1e0) | MATCH ×4 | DIFF ×4 |
| medium: unexpected home writes change no verdict (`sandbox.mjs:162`) | item `session home` | `home-write` (Astra's home.py), `home-transient` (written and removed between two answers), `home-skeleton`, `home-root-hide`, `home-root-mode` (her storage-root chmod probes, on the home) | MATCH ×5 | session home DIFF ×5 |
| high: an exec or setup failure is GO-BROKEN (`lspdiff.mjs:305`) | the init's report: ERROR, nothing kept | `unexec-go` (Astra's) | GO-BROKEN, cached | ERROR, nothing cached |
| JSON-RPC envelopes not validated | strict framing and envelope | `dup-key`, `bad-rpc` (`"9.9"`), `extra-key`, `stray-answer` (an answer to no request) | MATCH ×4 | DIFF rsFailure crash ×4 |
| controls | | `pass`, `fds`, `privs` | good, good, DIFF | good ×3 |

The other self-tests on this harness: `parity_h7_test.py` (111 checks: 64 unit, 47 end to end; the api tier on the shared init:
`H7 SELFTEST OK`; its finding 9 check no longer asks for the pin B2 removed), `parity_h4_test.py`,
`parity_h5_test.py` (unit; its tripwire checks are gone with the tripwires, its ancestors checks follow
the visible-only key, its e2e checks now ask that the real typings cache does not exist for tsc-rs and
that a leaked `XDG_CACHE_HOME` cannot write it), `parity_h6_test.py`, `selftest/h4-selftest.mjs` (the ATA
barrier's probe gets the server's liveness check) and `selftest/referee-selftest.mjs`: all OK. The
referee self-test had failed since lane H5 (its proxy, `#!/usr/bin/env node`, found no node on a
server's PATH, and its settings came through an environment the servers no longer get: 15 checks
failed on `c788830c4`); its proxy now takes a settings file and runs as a command.

### Round 2: Astra's review of `06a4344b9` (`reviews/2026-11-10-astra-h8.md`, REJECT)

Her reproductions are in `~/.cache/perf-rearch/scratch/review-h8-astra/`. Root's design point: the storage
is listed and recorded in one place for both tiers (her finding 1 was the api tier's `_walk` bug again,
in lspdiff's own JS lister), so the lister, the home skeleton and the write record are the init's
(`parity_sandbox_storage.py`), and the api tier uses them too. Each control is in `parity_h8_test.py`
(her probes as `parity_h8_fake.py` modes, through `parity_lsp.py` on ts-pattern-f12-p6 or, as she ran
them, lspdiff alone): "before" is `--e2e-only --harness <git archive 06a4344b9>`.

| # | finding | fix | control | before (`06a4344b9`) | now |
|---|---|---|---|---|---|
| 1 | unreadable input storage gives MATCH: listing failures ignored, the overlay root's metadata not listed (`sandbox.mjs:83`) | the init lists the storage with the one lister (H7's `_walk`): the roots as `.`, an unlistable dir said so; the init has every capability over its tmpfs | `input-root-hide`, `input-root-mode` (lspdiff alone, as she ran them, and through the tier) | 59 MATCH, 59 MATCH | session end DIFF rsFailure input, session storage DIFF |
| 2 | `$HOME/__proto__` invisible (`sandbox.mjs:105`: a `{}`) | the lister is Python; the referee's `refValue` and tsserver.mjs's rename `changes` no longer assign server-given names into a `{}` | `home-proto`; unit: the lister lists `__proto__`, the record records it | MATCH | session storage DIFF |
| 3 | the home history misses writes inside one request (sampled) | the write record (fanotify on the storage's tmpfs, set before the server starts; complete, no per-dir race) | `within-request` (create and unlink before each answer); unit: a file made and removed in a new dir is recorded | MATCH | session writes DIFF |
| 4 | server messages dropped: standard sessions all of them (`session.mjs:150`), watch sessions those after the last checkpoint (`scenario.mjs:675`) | item `session messages`: every server-to-client message after the last checkpoint through EOF | `request` (applyEdit during initialize), `late-request`, `late-diag` (while shutdown is answered); her watch-session cases | MATCH ×3 | session messages DIFF ×3 |
| 5 | the shutdown result is not checked (`client.mjs:219`) | it must be null | `shutdown-result` (12345) | MATCH | session end DIFF |
| 6 | the server controls ATA's evidence: the stand-in trusts `ATA_NPM_LOG`, `ATA_STORE` and the delay from the environment, its log dir is writable | the stand-in reads a fixed read-only config, checks every store file against its sha256, ignores the environment; the evidence is the write record | `ata-unlogged` on ata-overlay (her fake-extra.py: an npm call of its own with `ATA_NPM_LOG=/dev/null`); JS: the environment ignored, a store file with other bytes fails | 47 MATCH | the npm item and session writes DIFF |
| 7 | a timing variation exempts unrelated home damage forever: storage and history were one answer, and a Go variant exempted all of it (`lspdiff.mjs:388`, `:491`) | two items, judged path by path: storage strict to this fill's Go runs; writes exempt only on the paths where Go varies, recorded per path | her synthetic case: a Go that writes P in about half of its runs, a tsc-rs that writes P and Q, now and in a later fill through the record; control: P alone | GO-BROKEN (her `home-variants`, `home-prior-variants`); the controls MATCH or fail | DIFF on Q only; P alone GO-BROKEN; storage P DIFF against a fill without it |
| cost | the lsp tier 460 s → 641 s (round 1) | `scan` plus plain `JSON.parse`; `rawNumbers` marks | (below) | | |

The round 1 controls pass on this head (`H8 SELFTEST OK`: 21 unit, 39 end to end); the ones on the home
read the new items (`session storage` for a persistent write, `session writes` for a transient one).

### Round 3: Astra's recheck of `95006241b` (`reviews/2026-11-10-astra-h8-r2.md`, REJECT; the last round)

Her reproductions are in `~/.cache/perf-rearch/scratch/review-h8r2-astra/`. Root's decision on scope,
which ends the review loop: the threat model below; findings 1, 2, 3, 4 and 6 fixed, finding 5
documented as out of scope. Controls: `parity_h8_test.py` `round3()` (her fakes as `parity_h8_fake.py`
modes, lspdiff alone as she ran it) and unit checks; "before" is `--harness <git archive 95006241b>`.

| # | finding | fix | control | before (`95006241b`) | now |
|---|---|---|---|---|---|
| 1 | unresolved paths are one identity: writes to `A/f` and `B/f` in dirs made and removed before their handles were resolved both read `?/f` (`parity_sandbox_storage.py:215`) | the record keeps every event by its dir's handle and names it when the record ends, by the dir's last known path (a dir learned late, where a rename put it, names its earlier events too); a dir never seen at any path is `?<nonce>.<n>`, n one per such dir and the nonce new in every run, so two are never equal, in a run or across runs, and each is a path no Go run has: fail closed. (A first version named unresolved events as they were read, which made overlayfs's own copy-up nondeterministic: a dir it makes in its work dir and moves into the upper dir at once had its attrib event unresolved in some runs; the gate's api tier showed it as rs-nondet on build-references and two more, and that gate run was stopped) | `path-go`/`path-rs`; unit: two files in dirs gone before the reader reads are two distinct unresolved paths | 60 MATCH | session writes DIFF |
| 2 | a writable place outside the record: the stand-in npm's log dir `/run/lspdiff-ata/log` was a writable host bind (`scenario.mjs:659`) | the log is on the recorded storage (the init's `plumbing/`, bound at that path; lspdiff reads it through the init's `/proc/<pid>/root`); there are no writable host binds; before exec the init checks `/proc/self/mountinfo`: a writable mount that is not on the storage's tmpfs, an overlay of ours, or `/proc` is a setup ERROR | `ata-log-transient` on ata-overlay; unit: a writable bind of a host dir is a setup failure | 49 MATCH | session writes DIFF (`plumbing/extra`) |
| 3 | the stand-in npm trusts its runtime environment: `NODE_OPTIONS=--import=...` runs other code before it reads its config (`ata-npm/npm:5`) | `env -i`: node and the stand-in at fixed paths (`/run/lspdiff-ata/node`, `/run/lspdiff-ata/fake-npm.mjs`, both read-only binds), no flags, nothing of the environment | `npm-env` on ata-overlay | 49 MATCH | DIFF (the call reaches the stand-in, which refuses a package the store lacks) |
| 4 | masking watcher ids changed diagnostic payloads: a replace over the whole message made `bad watcher 1.` equal `bad watcher 999.` (`session.mjs:41`) | only the registration ids are masked, by JSON path (`params.registrations[*].id`, `params.unregisterations[*].id`) | `mask-go`/`mask-rs` | 60 MATCH | session messages DIFF |
| 5 | mmap writes escape the write record | out of scope (the threat model below) | her `fake-probes.py` `mmap-good`/`mmap-bad` (`review-h8r2-astra/probe-mmap`) | MATCH | MATCH (documented) |
| 6 | the listing misses xattrs and hard links (`parity_sandbox_storage.py:84`) | each entry's extended attributes (name and value hash) and, for an inode with more than one name, its link count and a link group (the walk-order index of its first name, never the raw inode) | `xattr-good`/`xattr-bad`, `links-good`/`links-bad`; unit | 60 MATCH ×2 | session storage DIFF ×2 |

A control session (ata-overlay, the same fake on both sides) is MATCH on both harnesses.

### The threat model, and its known limits (root, 2026-11-10)

The gate guards against **buggy compilers and racy environments**: a tsc-rs that answers wrongly, writes
where it should not, leaves processes or output behind, breaks the protocol or misbehaves under load or
another schedule, and a Go or a host whose timing varies. It is not built against a server written to
evade observation; the sandbox's privilege drop, the sealed tree and the write record make most such
evasions fail anyway, but where one needs deliberate behaviour no compiler has by accident, it is out of
scope. Known limits:

- **Writes through a shared writable mapping** (round 3 finding 5; Astra's
  `review-h8r2-astra/probe-mmap`, `fake-probes.py` `mmap-bad`): fanotify sees no event for a store into a
  mapped page, so a file changed through `mmap` and changed back before the end leaves no trace. A
  change that stays shows in the final listing, which hashes every file's bytes after the end.
- **Writes to an unlinked or `O_TMPFILE` file** that never gets a name: no path, and nothing persists.
- **A dir made and removed before the record names it** (a temp dir of a racing writer): its events have
  no path; they are recorded under distinct unresolved names, so the session differs from Go's (fail
  closed), never matched; a real compiler that does this in its home makes its session a DIFF.
- **Host-side writers** racing the harness's fixture checks (H7's known limit of the fixture check).
- **The stand-in npm's log** is in the server's writable storage: a server can write it. It is the ATA
  barrier and a report, recorded like every write; the evidence of what was installed is the write record
  and the final listing.

### Go and JS evidence

Round 1's refill is kept as it was (`runs/h8b-lsp-fill-{1,2}` and the refills; the end of it overlapped the
machine's task-cap incident of 13:01 to 13:21 CST, and root checked it: no fork error, the same results).
Round 2 changed keyed sources (the init and its storage module, `json.mjs`, the stand-in npm, the
scripts; `CACHE_VERSION` 5), so every lsp Go fill and variant record started empty again, and this is
the one refill of round 2: on `tsgo-head-865385add-rel` (the head's crates; B2), under the heavy-run rule
(`flock ~/.cache/ts-rust-heavy.lock`, `app-batch.slice`, `TasksMax=24000`, then the gate's lock;
`scratch/h8/fill.sh`), 23:56 to 00:54 UTC: a full fill (`runs/h8r2-lsp-fill-1`, 626 s, Go 4 runs per
session, 8 on hono and query), two `--go-refill` fills of each session where Go races (hono-f12-p6,
umami-f12-p6, second-project-query, watch-umami-f12, watch-narrow-umami-f12, watch-hono-f12), then a full
run judged with all of it (`runs/h8r2-lsp-fill-2`, 676 s).

- **Against `base-current`** (`runs/b2-29e23ede3-full`, 2,629 MATCH and 29 GO-BROKEN): fill-1 2,681
  MATCH and 29 GO-BROKEN; every base item has its base status, the GO-BROKEN set is the base's (hono 23,
  umami 5, watch-hono 1), and the 52 more items are the new ones, all MATCH (`initialize`, `session
  messages`, `session storage` and `session writes` of each of the 13 sessions: tsc-rs's initialize
  answer, its messages through EOF, its storage and every write it made equal Go's everywhere).
- **One DIFF in fill-2, not in any other run:** `watch-hono-f12/0016 load server messages`, rs-schedule:
  tsc-rs's run with `GOPORT_PARSE_THREADS=1` sent one `workspace/diagnostic/refresh` fewer at the load
  checkpoint than its run 1 (two refreshes after ATA's two installs were one). Go's 8 runs agreed; the
  same session was MATCH in fill-1, both refills and the gate. A timing-dependent coalescing of refreshes
  in tsc-rs, seen once in 4 runs of the session (12 tsc-rs runs); not this lane's code, and not excused.
- **Go-variant records** (round 2 against the base's): the unstable request sets (requests with more
  than one recorded Go answer) are unchanged in every session: hono-f12-p6 23, umami-f12-p6 5,
  watch-hono-f12 1, every other session 0. The answer-hash sets are identical on every stable request
  (2,620 common requests); on the 25 unstable ones (hono 19 of its 23, umami 5, watch-hono 1) they differ:
  the base's records hold the answer orders of many more fills (32 against 5 on hono), and Go's races give
  other orders in each fill, so neither set is a subset of the other there (round 2 holds 24 orders of a
  hono `references` answer where the base holds 16). The new requests (the initialize answer, the
  messages, and one key per written path: 171 on watch-ts-pattern-f12, 1,842 on watch-hono-f12, ATA's
  typings) never varied in any fill.
- **JS:** the JS keys changed too. No session had a DIFF of a refereed kind, so no JS replay was needed.
  Round 1's `--js-all` on ts-pattern-f12-p6 and pkgjson-overlay (JS tsserver in the sandbox) gave exactly
  the calibration the old harness gives on the same sessions, kind by kind (`scratch/h8/js-all{,-old}.log`).

**Round 3's refills** (its keyed sources changed: the init and its storage module, the scripts, the
stand-in npm): the same plan under the heavy-run rule. The first (`runs/h8r3-lsp-*`, 02:50 to 03:43 UTC:
fill-1 and fill-2 each 2,681 MATCH and 29 GO-BROKEN, no DIFF) was made before the record's last fix (above)
and is superseded; the final one is `runs/h8r3b-lsp-*` (05:39 to 06:28 UTC): fill-1 (630 s) and fill-2 (234 s) each 2,681 MATCH and 29 GO-BROKEN, every
refill MATCH or its base GO-BROKEN, no DIFF in any run. Every base item has its base status, the GO-BROKEN
set is the base's, and the 52 new items all MATCH. The variant records against the base's: the base's hold
2,645 requests, all present; the unstable request sets are unchanged in every session (hono-f12-p6 23,
umami-f12-p6 5, watch-hono-f12 1, others 0); the answer-hash sets are identical on all 2,616 stable
requests and on 4 of the 29 unstable ones, and differ on the other 25 (Go's racing orders, as in round 2);
no new request (initialize, messages, per-path writes) varied in any fill.

### Gate run

**Round 3 (the last):** `parity.sh full tsgo-head-865385add-rel --base base-current --accepted-ref
t3/harden-lsp-parity-harness` (harness `22c476832`; base `runs/b2-29e23ede3-full`),
`runs/h8r3b-head-865385add-full`, 22 min (06:28 to 06:51 UTC, load 1 to 17), under the heavy-run rule:
**PARITY OK**. 187,337 base items compared, 0 lost, 0 not run, 0 known-bad not as pinned, 0 new items not
good (the 52 new lsp items all MATCH), 0 crashes on good items, 36 grandfathered (as pinned), 0 stale, 0
recovered, none newly good by a referee. cli 170 MATCH; tests 140,002 PASS, 1,731 IGNORED, no FLAKY; lsp
2,681 MATCH, 29 GO-BROKEN (the base's 29; `watch-hono-f12/0016`, lane B4's flake, did not show) in 232 s;
api 49 MATCH; fuzz 29,955 MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN, 1 JS-AGREES (the base's); corpus
12,727 MATCH. A run before it on round 3's first record (`runs/h8r3-head-865385add-full`) was stopped in
its fuzz tier after api showed 3 DIFF (rs-nondet on the build scenarios: the copy-up race of the first
version of finding 1's fix, above); it is not evidence.

Round 2's:

`parity.sh full tsgo-head-865385add-rel --base base-current --accepted-ref t3/harden-lsp-parity-harness`
(harness `b64de20d5`; base `runs/b2-29e23ede3-full`), `runs/h8r2-head-865385add-full`, 23 min (01:31 to
01:54 UTC, load 1 to 16), under the heavy-run rule: **PARITY OK**. 187,337 base items compared (185,606 a
full run must have), 0 lost, 0 not run, 0 known-bad not as pinned, 0 new items not good (the 52 new lsp
items all MATCH), 0 crashes on good items, 36 grandfathered (the fuzz entries, exactly as pinned), 0
stale, 0 recovered, none newly good by a referee. cli 170 MATCH; tests 140,001 PASS, 1,731 IGNORED, 1
FLAKY (`tsctests::contentmapper_watch::content_mapper_build_watch_reads_source_mtimes_after_a_mapper_error`,
failed, then passed alone); lsp 2,681 MATCH, 29 GO-BROKEN (the base's 29); api 49 MATCH; fuzz 29,955
MATCH, 36 DIFF (grandfathered), 8 GO-BROKEN, 1 JS-AGREES (the base's); corpus 12,727 MATCH. Round 1's gate
(`runs/h8-head-865385add-full`, harness `31c3e1d44`) was PARITY OK too.

### Cost

| | base (`b2-29e23ede3-full`) | round 1 (`h8-head-865385add-full`) | round 2 (`h8r2-head-865385add-full`) |
|---|---|---|---|
| lsp tier wall | 460 s | 641 s | 235 s |
| tsc-rs's own runs, summed over sessions | 1,046 s | 978 s | 733 s |
| lspdiff's own time on umami-f12-p6 (session minus tsc-rs) | 112 s | 314 s | 44 s |
| lspdiff's own time on any other session | 0.1 to 14 s | about the same | 0 to 3 s |
| a server start and end (`/bin/true`, 20 runs) | 9.7 ms | 47.4 ms | about the same as round 1 |

- **Round 2's changes:** `json.mjs` `scan` (one tokenizer pass, plain `JSON.parse` unless a number must
  keep its text; on umami's 340 MB of answers a run: 2.8 s on the wire against 9.6 s of round 1's reviver
  and duplicate scan, and 0.7 s against 5 s to read a cached log); lspdiff reads a prior Go log only when
  the variant record lacks it (umami's 8 prior logs of 340 MB were read and normalized on every run) and
  normalizes each answer once. The last two are not keyed and changed no Go fill.
- **The tier's wall** also depends on the load: the gate ran at load 1 to 16, the base at 10 to 44 (its
  tsc-rs runs of umami took 322 s, these 61 s). lspdiff's own time is the comparable part: lower than
  the base's in every session.
- **The sandbox:** +38 ms a server process (two more Python starts, the namespaces, the storage tmpfs and
  its fanotify mark); the write record and the final listing are the init's, after the server ended.
- **The other tiers:** the api tier shares the init and its storage (5 s, the same 49 scenarios); the cli,
  fuzz and corpus tiers do not use this lane's code.
- **Self-tests:** `parity_h8_test.py --unit` about 10 s; the end-to-end controls about 15 minutes.

## Files

- `scripts/perf-rearch/parity.sh`: the driver (`help`).
- `parity_cli.py`, `parity_tests.py`, `parity_lsp.py`, `parity_fuzz.py`: the tiers. Each prints
  its usage with `--help`.
- `parity_compare.py`: `merge`, `compare` and `known`; `parity_modes.py`: what quick and full run
  and require (`parity.sh` and `compare` read it); `parity-quick-manifest.json`: the accepted quick
  coverage; `parity_inputs.py`: content hashes, snapshots and staged caches; `parity_watch.py`: the
  inotify watch; `parity-grandfathered.json`: the known-bad items pinned to their failure;
  `parity_grandfather.py`: its schema, matcher, evidence runs and `regen`.
- `parity_h4_test.py` and `vendor/lspdiff/selftest/h4-selftest.mjs`: lane H4's negative controls
  (about 1 minute together; the JS file also has lane ND2's and H5's).
- `parity_h6_test.py`: lane H6's negative controls for the stderr site form (about 10 seconds).
- `parity_t1_test.py`: lane T1's self-test of `regen --drop-recovered` (about 5 seconds).
- `parity_api.py`: the api tier (also `probe`, `list`, `key`); `parity_api_session.py`: one API session
  (the protocol client, the requests, the storage reads); `parity_sandbox_init.py`: the session's sandbox,
  PID 1 of its namespaces (both keyed: their sha is in every api key); `parity-api-scenarios.json`: the
  scenarios; `parity_h7_test.py`: lane H7's negative controls (30 s to 2.5 minutes; `--unit` about 14 s;
  `--e2e-only --harness DIR` against another harness); `parity_h7_fake.py`: its fake server, Astra's
  review probe.
- `ata_typings.py`: builds and checks the frozen typings store (`build`, `check`, `path`);
  `ata-typings.json`: its manifest (hash, versions, every file's sha256; the store itself is in
  `~/.cache/perf-rearch/ata-typings/<sha>/`); `parity_h5_test.py`: lane H5's negative controls (unit
  checks in seconds, `--e2e BIN` through the real tier).
- `parity_referees.py`: GO-BROKEN, GO-PARTITION, JS-AGREES and the diagnostic parser;
  `parity_referees_test.py`: their self-test (fake compilers, every classification path, about 2
  minutes; `SELFTEST OK`).
- `parity-projects.json`: the CLI projects; `prepare-parity-projects.sh`: the parity lane's
  project copies.
- `parity_sandbox_init.py`: PID 1 of every api and lsp server's sandbox (lane H8 made it shared; it was
  `parity_api_init.py`); `parity_h8_test.py`: lane H8's negative controls for the lsp tier (`--unit` in
  seconds; the end-to-end controls about 10 minutes; `--e2e-only --harness DIR` against another harness);
  `parity_h8_fake.py`: its fake LSP server, a proxy in front of the Go oracle.
- `vendor/lspdiff/` (perf/lsp-concurrency 931fad409, plus `--go-cache`, `items.json`,
  `--scenario`, `--rs-runs`, `--rs-env`, `--go-runs`, `--server-logs` and the JS referee's `--js`,
  `--js-cache`, `--js-all`, `--js-kinds`; `scenario.mjs` holds the watch scenarios and
  `fixtures.mjs` the watch-small and ata-small fixtures; `ata-npm/` the stand-in npm, the only npm a
  server can run; `tsserver.mjs` drives JS tsserver, `referee.mjs` holds
  the normalizers and the JS-AGREES judge, `sandbox.mjs` the server process (under the shared init) and
  its session result, `json.mjs` the strict JSON reader of the wire,
  `settings.mjs` every setting, `selftest/` their self-tests) and `vendor/difffuzz/` (feat/difffuzz
  033e55442; `oracle.py` checks the inputs since lane H4).
- State: `~/.cache/perf-rearch/parity/` holds `base-main/`, `js-vs-go/`, `go-cache/`, `js-cache/`,
  `snapshots/`, `stage/`, `runs/`, `logs/` and `scratch/`.


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

### Version 4: the site hash is domain-tagged (Astra's H6 review)
Astra found a false pass in version 3. A stderr that ends in a literal
`\t<file>.rs:<line>` doesn't match `SITE`, so it was hashed unchanged. That
gave the same `stderrSite` as a normalized crash at any real line, and it
passed against all 9 crash pins.

- **The fix:** `stderr_site_hash()` now hashes `site\0` + the normalized form
  when `SITE` matched, and `raw\0` + stderr when it didn't. The format is
  version 4, so a version 3 file is INVALID.
- **New negative controls** in `parity_h6_test.py`:
  - the literal `<line>` form for each panic shape;
  - an end-to-end pin mismatch;
  - a compare that gives KNOWN-BAD and PARITY FAIL.
- **Re-pinned** from `runs/h6fix-gf-evidence-e81a14807-{full,quick}` (with
  `--old runs/h6-head-e81a14807-full`).
  - In all 36 entries, only `attempt.stderrSite` and `evidence` changed.
  - The shifted-line probe binary (`tsgo-h6-probe-blankline`, one blank line
    above `go_repeat`; run `runs/h6fix-probe-blankline-full`) matches all 36
    pins.
