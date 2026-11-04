# perf-rearch baseline: tsc-rs main against Go

Lane: baseline (phase 1 of `perf/first-principles`, brief `docs/perf-rearch/brief.md`).
tsc-rs `main` @ `91f3d021c`, shipped profile (`--profile goport`), against the Go
oracle at pin `3e9236361fa9`. Measured 2026-11-03 on MS-A2. Tools:
`scripts/perf-rearch/` (README there). Raw data: `~/.cache/perf-rearch/baseline/`
and `~/.cache/perf-rearch/profiles/`.

<!-- STATUS -->

## Machine

AMD Ryzen 9 9955HX, 16 cores / 32 threads, 47 GB, WSL2 kernel
6.18.54.1-microsoft-standard-WSL2, `perf_event_paranoid=2`, no perf, no sudo.
The box is shared: during this lane the parity, audit and other projects' jobs kept the
1-minute load at 20 to 99 for hours (see "Load" below).

## Binaries

| Label | Binary | Notes |
|---|---|---|
| `rs` | `~/.cache/perf-rearch/bins/tsgo-main-91f3d021c-goport` | main @ 91f3d021c, `--profile goport` (fat LTO), the timed side |
| `go` | `~/.local/bin/tsgo-oracle-3e9236361fa9` (CLI), `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 --lsp --stdio` (LSP) | Version 7.1.0-beta |
| prof | `~/.cache/perf-rearch/bins/tsgo-main-91f3d021c-prof` | same source, stable 1.95.0 `--release` with `CARGO_PROFILE_RELEASE_DEBUG=line-tables-only`, for samply only |

## Projects

`scripts/perf-rearch/setup-projects.sh` (pins in `projects.tsv`), under
`~/.cache/perf-rearch/projects/`. Sizes from Go's `--extendedDiagnostics`.

| Project | Repo @ commit | Config | Files | Lines | Types (Go) | Go check, `--noEmit` |
|---|---|---|---|---|---|---|
| ts-pattern | gvergnaud/ts-pattern @ c92ca435c7e1 | `tsconfig.json` | 118 | 65k | 52k | 0 errors |
| query | TanStack/query @ 44645e9eb1da | `packages/query-core/tsconfig.prod.json` | 186 | 119k | 18k | 0 errors |
| hono | honojs/hono @ 06880c4a2b04 | `tsconfig.build.json` | 352 | 139k | 306k | 2 errors (TS7031) |
| zod | colinhacks/zod @ 43f729db4aa0 | `packages/zod/tsconfig.json` | 621 | 230k | 781k | 19 errors (TS6196 and others, test files) |
| effect | Effect-TS/effect @ 0d083ba26b2e | `packages/effect/tsconfig.noeffect.json` | 649 | 462k | 722k | 2 errors (TS2345) |
| umami | umami-software/umami @ 559e3c607c23 | `tsconfig.json` | 4,422 | 642k | 372k | 43 errors (prisma client not generated) |
| t3-web | pingdotgg/t3code @ cd41c4ada0c7 | `apps/web/tsconfig.noeffect.json` | 5,396 | 1.23M | 1.55M | 0 errors |
| t3-server | pingdotgg/t3code @ cd41c4ada0c7 | `apps/server/tsconfig.noeffect.json` | 3,107 | 1.46M | 2.82M | 1 error (TS2322) |
| vscode | microsoft/vscode @ 3f07e1aba32a | `src/tsconfig.json` | 11,014 | 3.75M | 3.62M | 1 error (TS2345) |

- Dependencies were installed with install scripts off, so generated code is
  missing in some projects (umami's prisma client). Both compilers see the same
  tree and print the same diagnostics. The errors are part of the input.
- `*.noeffect.json`: the project's config and its base with `plugins`
  removed (as `scripts/bench-apps/t3code.sh` does). Go ignores plugins. tsc-rs
  runs the Effect rules built in, even when an extending config sets
  `"plugins": []` (checker audit: 38% of Effect's check CPU), so with the
  plugin config the two would not do the same work. **effect and T3 Code are
  like for like only through these configs.**
- `npm ci` (not pnpm) installs the two `package-lock.json` repos (ts-pattern,
  VS Code), so the `node_modules` tree is the lockfile's, as in `scripts/bench-apps`.
- Sentry was skipped: setting it up adds about 1.5 GB and its check repeats
  VS Code's size class. The README's other four apps were skipped for time.

## Method

`scripts/perf-rearch/bench.sh` (`bench.sh help`), following the brief:

- Each wall run starts only when the 1-minute load is under 1.5. The sides
  alternate run by run, and the order rotates every run. Each cell gets one
  warm-up run (page cache) and 3 recorded runs. The tables give the median and
  min..max.
- The load before and after each run is recorded. A run is repeated (twice
  at most, then flagged) when the load after it is above 3 plus what the run
  itself adds to the 1-minute average. A bare 3 would repeat every large
  multi-threaded run: 16 threads for 10 s add about 2.5. It is also repeated
  when the rest of the machine used more than one core during it
  (`/proc/stat` minus our CPU).
- Wall is measured to the exit of the started process. tsc-rs's launcher
  exits when its worker sends the exit code, before the worker unmaps its
  heap; that is what a user waits for. CPU (user+sys) and max RSS come from
  `wait4` on the process and every descendant: the tool is a child subreaper,
  so the worker is reaped and counted too. This replaces the brief's
  `GOPORT_LAUNCH=0`, which would add the worker's teardown to the wall time.
  `PR_LAUNCH=0` restores it.
- Every run also counts user-space instructions (perf counter, inherit,
  `exclude_kernel`; root's `icount.py` method). The **icount** scenario is
  single-threaded: tsc-rs `--singleThreaded`, Go `--singleThreaded` with
  `GOMAXPROCS=1`. It needs no load gate. tsc-rs repeats exactly (within
  0.013%). Go varies by 2–17% between runs, because its GC work depends on
  timing, so treat Go's count as ±3% (hono and query: ±15%).
- Output: exit code and a hash of stdout+stderr for every run, and for emit a
  hash of the emitted tree. A cell whose output differs from Go's says so.
  None differs here.
- No run writes into a project: emit goes to `--outDir`/`--declarationDir` in
  the result dir's scratch, incremental build info to `--tsBuildInfoFile`
  there, and `-b` runs on a copy. The tool stops if `git status` of an input
  changes.

CLI scenarios. All use `-p <config> --pretty false`. A composite config gets
`--composite false --declaration true`, because composite needs build info
written next to the input.

| Scenario | Flags |
|---|---|
| noemit | `--noEmit --incremental false` |
| emit | `--noEmit false --incremental false --outDir S` (and `--declarationDir S` if the config declares): the config's own outputs |
| emit-js | emit with `--sourceMap false --declaration false --declarationMap false`: JS only |
| emit-cjs | emit-js with `--module commonjs --target es2017` (the emit audit's CommonJS mode) |
| single | noemit `--singleThreaded`, both sides |
| icount | single, user-space instructions; Go with `GOMAXPROCS=1` |
| incr-warm | noemit with `--incremental --tsBuildInfoFile S`: one cold build, then the measured no-change rebuilds |
| build-cold / build-warm | `-b <config>` on a scratch copy: from clean, and the no-change rebuild |

LSP scenarios, on umami (`scripts/lspdiff`, wrapped by `bench.sh lsp`):

| Scenario | What | Cell value |
|---|---|---|
| ttfd | spawn, initialize, open `BoardCloneForm.tsx`, pull its diagnostics (`lsp-ttfd.mjs`) | time to the answer |
| probe-tsx / probe-ts | `probe-edit.mjs --edits 20 --cpu`: a one-character edit, then a hover in another file, a second hover, and the edited file's diagnostics; `.tsx` = `BoardCloneForm.tsx`, `.ts` = `useFunnelQuery.ts` | median first request after an edit |
| concurrent-120/250/500 | `lspdiff.mjs bench --modes concurrent --pulls all` at 120, 250 and 500 ms per key | completion p95 |

<!-- GAP TABLE -->

## CPU work: single-threaded instructions (load-independent)

`~/.cache/perf-rearch/baseline/icount/` (`icount-table.md` is the full table, with
single-threaded walls). Each cell is tsc-rs / Go user-space instructions, single
threaded, 2 runs per side, with the ratio. The output (exit code, stdout, emitted
tree) was the same as Go's in every cell.

| Project | check (`icount`) | emit as configured | JS only | CommonJS ES2017 |
|---|---|---|---|---|
| ts-pattern | 0.6 / 1.4 G = **0.42** | 0.6 / 1.4 G = **0.46** | 0.6 / 1.4 G = **0.43** | 0.6 / 1.4 G = **0.42** |
| query | 0.7 / 1.4 G = **0.48** | 0.7 / 1.6 G = **0.46** | 0.8 / 1.5 G = **0.54** | 0.8 / 1.5 G = **0.55** |
| hono | 2.1 / 5.4 G = **0.40** | 2.9 / 6.2 G = **0.46** | 2.6 / 5.7 G = **0.46** | 2.9 / 5.6 G = **0.51** |
| zod | 11.5 / 26.7 G = **0.43** | 13.3 / 30.7 G = **0.43** | 13.3 / 31.4 G = **0.42** | n/a (TS5110) |
| effect | 14.7 / 26.4 G = **0.56** | 24.5 / 46.0 G = **0.53** | 17.9 / 30.9 G = **0.58** | 20.5 / 34.1 G = **0.60** |
| umami | 16.5 / 31.5 G = **0.52** | 19.0 / 35.8 G = **0.53** | 18.5 / 32.7 G = **0.56** | 19.9 / 34.7 G = **0.57** |
| t3-web | 47.1 / 91.8 G = **0.51** | 57.9 / 107.9 G = **0.54** | 52.6 / 89.3 G = **0.59** | 58.6 / 95.0 G = **0.62** |
| t3-server | 71.4 / 188.9 G = **0.38** | 81.4 / 199.7 G = **0.41** | 81.4 / 197.2 G = **0.41** | n/a (TS5110) |
| vscode | 164.2 / 251.3 G = **0.65** | 274.6 / 360.0 G = **0.76** | 274.6 / 364.9 G = **0.75** | n/a (TS5110) |

- **Single-threaded, tsc-rs does 24–62% less work than Go in every cell.**
  So wherever tsc-rs is slower than Go on the wall, the cause is how the work
  is spread over threads (or a phase that runs serially), not the amount of
  work.
- The margin is smallest on VS Code (0.65 check, 0.75 emit) and in emit
  generally. Emit adds more instructions in tsc-rs than in Go: VS Code
  emit minus check is 110 G for tsc-rs against 109 G for Go (about equal),
  T3 web JS-only adds 5.5 G for tsc-rs against −2.5 G (noise) for Go. Go's
  emit costs relatively less than its check.
- Repeatability: tsc-rs repeats within 0.02%. Go varies by 3% in the median
  cell and by up to 17% (hono, query), because its GC work depends on
  timing. Treat Go's numbers as ±3%.
- CommonJS is n/a for zod, T3 server and VS Code: their configs use
  `moduleResolution: nodenext`, and `--module commonjs` with it is TS5110,
  which stops the run at the config (`bench.sh` skips the cell).
- Emit cells run on a scratch copy of the repo with `--rootDir` at its root.
  Without it, TypeScript 7 stops at TS5011/TS6059 before the check whenever
  outDir is set (zod, ts-pattern, T3 Code). T3 web's config also makes
  **both** compilers write JS and d.ts next to `scripts/lib/*.ts` outside the
  outDir; the copy keeps that out of the input.

## Profiles

samply 0.13.1 built for user space (`~/.local/bin/samply-user`, steps in the
README), at 1–4 kHz, on the prof build and on the Go oracle (its symbol table
gives Go function names). `samply_top.py` weights each sample by its on-CPU
time and gives it the phase of its innermost frame that names one (parse,
bind, check, emit, load/resolve, lsp). For Go, the runtime frames (maps,
hashing) count toward the package frame that calls them. A Go stack with no
package frame (background GC, scavenger) is `gc/runtime`, and the "Go
GC/alloc" column is the part of each phase that sits under GC or allocation
frames.

**All profiles were taken at load 20 to 90.** CPU shares within a profile hold,
but absolute CPU time is inflated by SMT contention, and timelines stretch.
Treat the critical-path reading below as qualitative. Files:
`~/.cache/perf-rearch/profiles/<name>.json.gz` with `.syms.json`, plus
`<name>.summary.md` (tables) and `.summary.json`. `samply load <file>` opens
one in the Firefox Profiler.

### 1. VS Code, CLI check (the largest project)

`vscode-noemit-{rs,go}`. On-CPU total: tsc-rs 85.8 s, Go 86.5 s.

| Phase | tsc-rs CPU | tsc-rs threads | Go CPU | of it Go GC/alloc |
|---|---|---|---|---|
| check | 68.9 s (80%) | 4 `checker-N`: 19.5, 18.0, 17.4, 16.0 s | 48.6 s (56%) | 5.1 s |
| parse | 7.6 s (9%) | `goport-parse` ×15 | 8.5 s (10%) | 1.5 s |
| load/resolve | 4.9 s (6%) | goport-parse, main, checkers | 4.4 s (5%) | 0.7 s |
| bind | 4.1 s (5%) | 20 unnamed `tsgo` threads (`ParallelBinds::run`) | 6.0 s (7%) | 0.6 s |
| gc/runtime | — | | 18.9 s (22%) | 12.1 s |

- Top self frames, tsc-rs: `SymbolArena::get_name` 2.6%, memcmp 1.1%, memcpy
  1.1%, `Scanner::scan` 1.0%, `intern::intern_hashed` 1.0%,
  `get_property_of_type_ex_body` 0.9%, `instantiate_symbol` 0.9%,
  `NameResolver::resolve_name` 0.8%, `get_object_type_instantiation` 0.8%,
  `get_named_members` 0.8%. The profile is flat: no frame above 3%.
- Top inclusive frames, tsc-rs: `check_source_file` 81%,
  `check_source_elements` 78%, `check_call_expression` 52%,
  `resolve_call`/`choose_overload` 44–45%, `check_deferred_nodes` 38%,
  `is_signature_applicable` 32%.
- Go: `runtime.tryDeferToSpanScan` 6.6%, map access (`mapaccess2_*`,
  `memHashAES`) about 12% together, under the checker.
- Timeline (1 s slices): parse+load runs first, then bind, then check, with no
  overlap, in both. tsc-rs's check tail narrows from about 3 cores to under 2
  as checkers finish unevenly: the slowest checker has 1.22× the CPU of the
  fastest.

### 2. umami, LSP `.tsx` edit (per-edit program update)

`umami-probe-tsx-rs` (samply, cut to the edit loop with
`probe-edit.mjs --window`; 40 edits, `--first-only`) and
`umami-probe-tsx-go.top-{flat,cum}.txt` (Go pprof through
`custom/startCPUProfile`).

| | tsc-rs | Go |
|---|---|---|
| first request after an edit (median, at load) | 135 ms | 293 ms (at load 50+; 67 ms in the smoke run) |
| "Program update … completed in" | 104 ms | about 53 ms (smoke run) |
| CPU per edit | 341 ms: `goport-resolve` ×7 199 ms, main thread `tsgo` 142 ms | 1,494 ms over about 4 cores |

| Phase (tsc-rs) | CPU | % | threads |
|---|---|---|---|
| load/resolve | 12.1 s | 88% | goport-resolve 7.6 s, tsgo 4.3 s |
| lsp | 1.2 s | 8% | tsgo |
| parse | 0.17 s | 1% | the edited file only |
| check | 0.12 s | 1% | |

- Inclusive: `DefaultResolver::resolve_module_name_worker` 54%,
  `ResolutionState::resolve_node_like` 38%, `Snapshot::clone_body` 35% →
  `Project::create_program` → `NewProgram::update_program` →
  `process_all_program_files` 33%, `FilesParser::parse` 29%.
- Self: memcmp 6.9%, memcpy 5.5%, `RawTable::reserve_rehash` 4.2%, jemalloc
  `edata_heap_remove_first` 3.6%, `AheadFs::directory_lookup` 2.9%,
  `FilesParser::start` 2.8%, memchr 5.6% together, `HashMap::insert` 2.2%.
- So every `.tsx` edit rebuilds the program and **resolves every module
  again**, as Go does: Go's `ReuseProgram` refuses files with a JSX runtime
  import. Go's own profile is the same work (`filesParser.start` 41%,
  `resolveModuleName` 29%). Go spreads it over about 4 cores, while tsc-rs
  runs it on 7 resolver threads plus a long serial part on the main thread
  (`tsgo`, 142 ms per edit). tsc-rs uses a quarter of Go's CPU and still
  finishes the update later. The serial main-thread part sets the latency.
  Hash-table growth, memcmp/memcpy of paths and jemalloc extent work are
  about 25% of the self time.

### 3. umami, LSP typing at 120 ms/key (concurrent)

`umami-concurrent120-{rs,go}`: one whole `lspdiff bench` session under
samply. At load about 60, both servers fell behind (completion p95 tsc-rs
13.9 s, Go 4.9 s), so this shows where CPU goes, not the latency.

| Phase | tsc-rs CPU (88 s total) | Go CPU (301 s total) |
|---|---|---|
| load/resolve (program rebuild) | 68.8 s (78%): goport-resolve 38.3 s, main 28.9 s | 84.9 s (28%) |
| lsp (snapshot, requests) | 8.4 s (10%), main | 35.0 s (12%) |
| check | 7.2 s (8%), main | 29.1 s (10%) |
| parse / bind | 3.0 s | 36.3 s |
| gc/runtime | — | 105.6 s (35%) |

- Main-thread (`tsgo`) CPU is 45.6 s of the session: the program rebuild
  (28.9 s), the checker answering requests (7.2 s) and snapshot work all run
  on the dispatch thread, so a request waits behind each keystroke's rebuild.
  This matches section 7 of `docs/lsp-concurrency.md` (`perf/lsp-concurrency`):
  tsc-rs saturates at 120 ms/key.
- Self frames: the same as profile 2 (memcmp 6.5%, memcpy 5.1%,
  `reserve_rehash` 4.0%, jemalloc extents 3.8%, `FilesParser::start` 3.3%).

### 4. umami, JS-only emit (the emit audit's slower mode)

`umami-emitjs-{rs,go}`: `--noEmit false --outDir S --sourceMap false
--declaration false`. The emitted trees were identical.

| Phase | tsc-rs CPU (9.3 s) | Go CPU (17.8 s) |
|---|---|---|
| check | 5.1 s (55%) | 8.3 s (47%) |
| emit | 1.05 s (11%): `dts-twin` ×4 0.67 s, checkers 0.38 s | 0.68 s (4%) + 0.15 s GC |
| load/resolve, parse, bind | 3.1 s | 3.2 s |

- tsc-rs spends **1.5× Go's CPU in emit**, and the emit runs only after the
  whole check, on the 4 checker threads and their 4 `dts-twin` printer
  threads (the emit audit's finding: import elision makes the checker the
  emit resolver, so the 12-thread pool stays idle). In the timeline, emit is
  the last 0.5 s, after a check tail where one checker runs alone.
- Emit self frames: `LocalKey::with` (TLS) 7.6% of `dts-twin`,
  `Vec::extend_with` 3.2%, dropping print-pack slot tables 3.0%, memcpy,
  `EmitContext::emit_flags`, `release_print_packs`,
  `TextWriter::update_line_count_and_pos_for`. Inclusive:
  `TwinPrint::print` → `print_js_file` 82% → `emit_source_file` 68%.

### 5. T3 Code server, CLI check (the largest Go wall)

`t3server-noemit-{rs,go}`, under samply: tsc-rs 11.7 s against Go 29.4 s. tsc-rs
check is 27.5 s of CPU (88%) on 4 checkers. Their CPU is 9.3, 7.6, 7.4 and 3.6 s:
the critical path is the 9.3 s checker, while the mean is 6.9 s. Go has
check 62.3 s and GC 18.3 s.

## Load

The brief's gate never opened for hours. From 16:36 to at least 20:00 the
1-minute load stayed between 12 and 99. The other perf-rearch lanes were running
(units `parity-base-main`, `parity-base-extra`, `parity-full-vs-base`,
`b1-tests3`, `lsp-audit-all`), along with another project's Rust builds.
`perf-rearch-baseline-wall` (`scripts/perf-rearch/baseline.sh … cli lsp thp`)
waits for the gate and resumes from `runs.jsonl` if restarted.

## Not measured, and why

<!-- UNMEASURED -->
