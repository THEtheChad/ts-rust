# LSP edit cost (stage S)

Question from phase 1 (`docs/lsp-concurrency.md` section 3): on umami, the
first request after a one-character `didChange` costs tsc-rs 376–411 ms
and Go 214–237 ms. Why, and what can be fixed while the answers stay
byte-identical to Go at microsoft/TypeScript 3e9236361fa9?

Go paths are relative to `tsc/internal` of the pin. Rust paths are
relative to `crates/ts_goport/src`.

## Summary

- **The whole gap is the program rebuild.** The request itself (a hover,
  plus checker creation) costs about 10 ms in both servers.
- **Both servers rebuild the whole program on every edit of a `.tsx` file
  in a `jsx: react-jsx` project.** Go's `ReuseProgram` fast path refuses any
  file that needs a JSX runtime import (`compiler/program.go:405`), and the
  port does the same. umami is a Next.js app, so almost every component
  edit takes the slow path. An edit of a `.ts` file reuses the program in
  both: the program update takes 3 ms in tsc-rs against 7 ms in Go, and
  the first request 11 ms against 21 ms.
- **The rebuild costs about the same CPU in both, but Go spreads it over
  every core.** Phase 1 said "Go serializes it too". That is true only
  with respect to other requests. Inside the update, Go's file loader runs
  one goroutine per parse task: about 450 ms of CPU per edit over 16
  threads, which gives 100–150 ms of wall time. The port's loader is
  serial by design: it keeps Go's file order and ids (`frontend/compiler/files_parser.rs`,
  `FilesParser` PORT note, "contract 10"). Worker threads resolve modules
  ahead of it (`frontend/compiler/resolve_ahead.rs`), and about 240 ms of
  per-task bookkeeping still runs serially on the dispatch thread.
- **After the answer, tsc-rs frees the old load's task graph on the
  dispatch thread** (27 ms per edit). Go's garbage collector does this in
  the background (about 190 ms of GC CPU per edit, on other cores). The
  probe sends its next edit within a millisecond of an answer, so that
  free lands in the next request's time.
- **Fixed (contained, port-only):** the per-load seen files, missing
  directories and module resolution cache now start at the size of the
  previous load. That saves about 11 ms of dispatch-thread time per edit
  (profile: 290 to 279 ms). The answers are unchanged: 3 projects of
  `lspdiff diff`, `ts-rust-ci all` (see "Verification").
- **What remains is structural.** About 95% of it would go away with a
  port-only (or upstream) `ReuseProgram` that handles the JSX runtime
  import. Making the serial loader parallel would be the other route, and
  a much larger one. See "What remains".

## How it was measured

### Tools

- **samply 0.13.1** (`~/.local/bin/samply`). It comes from the GitHub
  release, and its sha256 matched the published `.sha256` file and
  `sha256.sum`. On this kernel (`perf_event_paranoid=2`) the stock binary
  stops with "needs to be set to 1 or lower". The paranoid level is not the
  cause: samply always asks for kernel samples (`.sample_kernel()` in
  `samply/src/linux/perf_group.rs`). User-space-only `perf_event_open` on
  your own process works at level 2 (I checked it with a direct syscall).
  So I built the checksummed release source with those three calls
  commented out. That build is `/tmp/lec/samply-user`.
- **tsc-rs build:** `CARGO_PROFILE_RELEASE_DEBUG=line-tables-only cargo
  build --release --bin tsgo`. Frames are symbolized with `addr2line -i`,
  so inlined frames are kept. The analysis script, `/tmp/lec/scripts/samply_top.py`,
  reads samply's processed profile and its `.syms.json` sidecar. It cuts out the
  probe's window (samply's sample clock is `CLOCK_MONOTONIC`, which is
  also what node's `process.hrtime` reads), and prints inclusive and self
  time, callers, source lines, or a category table.
- **Go profile:** the oracle's own `custom/startCPUProfile` and
  `custom/stopCPUProfile` around the edit loop (`probe-edit.mjs
  --cpu-profile DIR`), then `go tool pprof`.
- **Wall-time phases, both servers:** both log `Program update for … completed
  in …` and `Finished cloning snapshot … in …` as `window/logMessage`.
  The port logs Go's lines, so `--logs FILE` compares the same span.
- **CPU per thread:** `--cpu` reads `/proc/<pid>/task/*/schedstat` before
  and after the loop.
- **Why `--pprofDir` had no samples:** that is by design, not a bug. The
  port has no sampler (`pprof.rs` header). It writes Go's profile files
  with no `Sample` messages. Adding a sampler (SIGPROF plus unwinding)
  would be a separate project, and samply does the job.

`scripts/lspdiff/probe-edit.mjs` gained these options for this work:
`--edits N`, `--first-only`, `--edit FILE`, `--cpu`, `--logs FILE`,
`--cpu-profile DIR` and `--window FILE`. `client.mjs` `shutdown()` now
closes the server's stdin after `exit`. tsc-rs only exits at stdin EOF, so
before this a wrapping profiler was SIGKILLed before it could write its
file.

Profiling runs used `--first-only --edits 40`: after each one-character
edit of `BoardCloneForm.tsx`, one hover in another open file. All runs
were on the shared 16-core box with a load average of 15 to 75, so wall
times are noisy. CPU splits (samples, schedstat) are steadier than
latencies.

### Reproduce

```sh
B=/path/to/tsgo   # release build with line tables
node scripts/lspdiff/probe-edit.mjs $B /tmp/lspdiff/projects/umami --first-only --edits 20 --cpu --logs /tmp/rs.log
node scripts/lspdiff/probe-edit.mjs ~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 /tmp/lspdiff/projects/umami \
  --first-only --edits 40 --cpu-profile /tmp/goprof
# samply: a wrapper script that execs `samply-user record -s --unstable-presymbolicate -r 2000 -o OUT -- $B "$@"`
node scripts/lspdiff/probe-edit.mjs wrapper.sh /tmp/lspdiff/projects/umami --first-only --edits 40 --window /tmp/w.json
python3 -I samply_top.py OUT.json.gz /tmp/w.json $B --thread tsgo --cats cats.tsv --per 40
```

## Phase breakdown

### Wall time per edit (umami, medians of the servers' own logs)

| Span | Go | tsc-rs (before) |
|---|---|---|
| program update (`Program update … completed in`) | 152 ms (min 96) | 264–313 ms (min 214–251), over 3 runs |
| snapshot clone around it | +0.2 ms | +0.3 ms |
| hover handler in all (`handled method`) | 164 ms | 268–323 ms |
| client-side first request (probe) | 165–361 ms | 300–442 ms |
| free of the old load before the next request | (GC, background) | about 25 ms (client time minus handler time) |
| same edit in a `.ts` file (program reused) | 7.4 ms update, 21 ms request | 3.3 ms update, 10.7 ms request |

Go with `GOMAXPROCS=1`: program update 1.6 s (min 0.86 s). That run also
had the GC and the auto-import warm on the same P, at load 60–75, so it
overstates Go's serial work. It does show that Go's 150 ms comes from
parallelism, not from doing less.

### CPU per edit, by phase

tsc-rs: samply, dispatch thread, 40 edits (11.6 s of samples in a 12.1 s
window, so the thread is busy all the time). Each sample goes to the
first category in this order that has a frame on its stack. Go: pprof
over the same loop, cumulative time per function divided by 40.

| Phase | Go (thread) | tsc-rs before (thread) | tsc-rs after |
|---|---|---|---|
| file loading: parse tasks (resolve imports, metadata, parse cache, queue subtasks) | **457 ms over 16 goroutines** (resolution 331, metadata 52, `filesParser.start` 51) | — | — |
| ↳ module resolution | 331 ms, in the parse tasks | **325 ms on 7 `goport-resolve` workers** + 16 ms loader cache lookups | 12 ms loader |
| ↳ checking and replaying worker answers (port only) | — | 63 ms | 54 ms |
| ↳ resolve imports: subtasks, include reasons, per-file maps | (in the parse tasks) | 50 ms | 51 ms |
| ↳ queue subtasks (`filesParser.start`) | 51 ms (parallel) | 36 ms | 35 ms |
| ↳ run task (queued func: data, depth, links) | (in the parse tasks) | 26 ms | 26 ms |
| ↳ load file: parse cache, metadata, redirects | 52 ms metadata (parallel) | 23 ms | 22 ms |
| collect files (`getProcessedFiles`) | 25 ms (main goroutine) | 25 ms | 25 ms |
| rest of `NewProgram` and the snapshot clone | 15 ms | 15 ms | 17 ms |
| hover, including `NewChecker` | 10 + 11 ms | <5 ms in the window (the checker is made lazily, about 10 ms in the handler log) | same |
| free the old load's parse tasks | GC: 190 ms on other cores | 27 ms, dispatch thread, after the answer | 28 ms |
| auto-import warm | 175 ms in the background (`registryBuilder.updateIndexes`) | 0: it waits for 50 ms of quiet (`IDLE_QUIET_PERIOD`) | 0 |
| **serial time on the request path** | **≈45 ms main goroutine + wait for the parallel loader** | **≈290 ms** | **≈279 ms** |

Of the dispatch thread's 290 ms: 72 ms is hashing and comparing
`String` paths (FxHash over the bytes, then `memcmp`), 28 ms is
`malloc`, and 28 ms is the free after the answer. A whole-thread split
shows no single hot function. The cost is spread over every parse task of
the load (9,393 module keys and 1,110 package scopes; every key was
taken from the workers: `GOPORT_RESOLVE_AHEAD_STATS` shows `taken: 9393,
missing: 0, waited: 0`).

## The gaps, classified

| Gap (per edit, umami) | Size | Kind | Evidence |
|---|---|---|---|
| The loader runs its per-task work on one thread; Go runs one goroutine per task | ≈150–190 ms of serial work that Go spreads over 16 threads | **Design** (contract 10: serial loader, so Go's file order, store ids and resolution order hold) | Go: 457 ms of parse-task CPU in 16 goroutines against 30 ms on its main goroutine. tsc-rs: 210 ms of `FilesParser::run_queue` on the dispatch thread. `GOMAXPROCS=1` Go takes 0.9–1.6 s. |
| Checking and replaying the resolve-ahead answers | 54–63 ms | **Port-only, part of the design**: it is the price of parallel resolution behind a serial loader | `accept_ahead_answer`, `check_ahead_call`, `SharedResolutionCache::get_module`. Go has no such step. |
| Table growth in per-load maps (seen files, module cache) | 12 ms | **Port-only, fixed** | `reserve_rehash` under `SourceFS::track_path` (7 ms) and `ModuleResolutionCache::set` (5 ms) |
| Freeing the old load's task graph | 27 ms, on the next request in the probe | **Design** (no GC; `Rc` graph, not `Send`, so it cannot be freed on another thread) | `drop_garbage` → `ParseTaskLinks::drop`; 33% of it is freeing `String`s, 30% jemalloc tcache flushes |
| `String` clones and hashing on the loader (paths, `PackageId`, include reasons) | ≈20–30 ms of alloc and copy, 72 ms of hashing and comparing in all | **Design** (owned `String` for Go's immutable shared strings), plus the serial loader: Go hashes as much, in parallel | `resolve_imports_and_module_augmentations` lines 2149–2161 (`ResolvedRef`: `file_name`, `path`, `t.path`, `package_id` clones) |
| Full rebuild on a `.tsx` body edit | ≈250 ms tsc-rs, 150 ms Go | **Go's design**, kept by the port | `compiler/program.go:405`. A `.ts` edit takes `ReuseProgram` in both and costs 3 to 7 ms. |

Things that are *not* the gap: the request itself (hover about 10 ms in
both), checker setup (Go's `NewChecker` is 11 ms; tsc-rs makes its
checker lazily inside the handler), parsing (only the edited file is
parsed again in both; the parse cache serves the rest), and
`getProcessedFiles` (25 ms in both).

## Fix made

`perf(project): size the per-load tracking and module cache from the last load`
(PERF lsedit1):

- `project/compilerhost.rs` `new_compiler_host`: the new host's
  `SourceFS` reserves room for as many seen files and missing directories
  as the project's previous host tracked
  (`SourceFS::tracked_counts` and `reserve_tracking` in `project/snapshotfs.rs`).
- `frontend/compiler/resolve_ahead.rs` `ResolveAhead::start`: the
  loader's module resolution cache reserves one entry per module key of
  the previous load.

Why it keeps the answers: the module cache is only read with `get` and
`set`, never iterated. The seen files are iterated by the watch glob
mapper, whose output does not depend on order. The one exception is the
spelling it keeps for a directory that differs only in case on a
case-insensitive file system, and Go picks that one from random map order
too (`project/watch.rs` comment on `create_resolution_lookup_glob_mapper`).

Before and after (umami, dispatch thread, samply, 40 edits each):
290.5 → 279.0 ms of samples per edit. Answer checking went from 63.0 to
54.3 ms and loader resolution from 16.3 to 12.0 ms, and `reserve_rehash`
is gone from both call sites. Wall-clock A/B runs (4 alternating rounds
of 20 edits) could not resolve 11 ms at load 15–20: the per-run spread
was 190–430 ms.

## Verification

With the fix (release build at 637295e62, compared with the oracle):

| `lspdiff diff` | Answers | Go-stable equal | Differ | Go-unstable |
|---|---|---|---|---|
| `heavy` (`--files 6 --positions 4`) | 83 | 83 | 0 | 0 |
| `ts-pattern` (same) | 278 | 278 | 0 | 0 |
| `umami` (same) | 269 | 266 | 0 | 3 |
| `umami` (`--files 10 --positions 6`) | 574 | 568 | 0 | 6 |

The Go-unstable answers are the same auto-import completions as in
phase 1. `ts-rust-ci all` passed: lint, then 50 test binaries with
3,908 tests passed and 0 failed.

## What remains, and its likely size

1. **`ReuseProgram` for files with a JSX runtime import or import
   helpers** (≈250 ms → ≈5 ms per `.tsx` body edit; it removes about 95%
   of the gap, and Go's own 150 ms with it). Go refuses those files
   because cloning "does not recompute synthetic helper or JSX-runtime
   import bookkeeping" (`compiler/program.go:399-406`). A clone would have
   to:
   - make the changed file's synthetic import specifier again;
   - replace its `jsxRuntimeImportSpecifiers` and
     `importHelpersImportSpecifiers` entries;
   - fix the include reasons that point at the old synthetic node
     (`compiler/fileInclude.go:230`).

   It should then give exactly what a full load gives when the changed
   file's imports, references and JSX specifier are unchanged. This
   departs from Go's shape, so it is best proposed upstream first, or
   done as a PORT-noted extension with a differential test against the
   full build (every `.tsx` fixture edited, program files, include
   reasons, diagnostics and emit compared). Estimate: 150–300 lines plus
   tests, 1–2 weeks with the proofs.
2. **A parallel loader.** Go's shape would be one task per goroutine, with
   ids and order fixed in a merge step. This is the "contract 10" design
   question, far larger than this stage. It would win back about 150–190
   ms per edit on umami.
3. **Cheaper serial loader work** (each step a few ms; together ≈30–50
   ms):
   - `PackageId`, `file_name` and the include reason's path as shared
     strings instead of owned `String`s (8 ms of clones at
     `file_loader.rs:2161`, and less to free afterwards);
   - one hash for the loader's module cache miss and the worker answer
     lookup (`SharedResolutionCache::get_module`, 7 ms);
   - a single-probe `track_path` (get then insert hashes twice, 5 ms);
   - pre-sizing `task_data_by_path` and the `get_processed_files`
     collector maps (3–5 ms).
4. **The free after the answer** (27 ms) only hurts a client that sends
   its next message with no gap, like the probe. At typing speed it runs
   in the gap. Splitting it into chunks does not help a no-gap client:
   `drop_garbage` keeps at most `GARBAGE_LIMIT` values while busy. To
   remove it, a load would have to allocate less (item 3) or the graph
   would have to be `Send`.
