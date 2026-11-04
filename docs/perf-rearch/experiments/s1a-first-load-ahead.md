# S1a: first-load resolve ahead (LSP ttfd on umami)

Lane S1a of [the loader design](../design/loader.md) (sections 1 and 4) with the direction of
[Astra's review](../reviews/2026-11-04-astra-loader-design.md), findings 1, 2, 3 and 8. Source:
branch `t3/speed-umami-lsp-ttfd`, change `a76811e51` (timing bins), later commits add tests,
a debug log and tools only.

## Result

| side (umami, open `BoardCloneForm.tsx`, pull diagnostics) | ttfd, s1a-ttfd.py (7 runs) | ttfd, bench.sh (7 runs) | program update | loader (`process_all_program_files`) |
|---|---|---|---|---|
| Go oracle 3e9236361fa9 | **288.9** [276.4–462.7] | **283** [275–305] | 229.4 | – |
| perf head 5f101e76b | 407.5 [402.2–415.7] | 414 [399–429] | 366.8 | – |
| S1a bin, `GOPORT_FIRST_LOAD_AHEAD=0` | 414.4 [400.1–430.6] | 413 [393–430] | 371.0 | 315–322 |
| S1a bin, `GOPORT_FIRST_LOAD_AHEAD=1` (default) | **331.7** [328.2–340.9] | **332** [324–350] | 289.9 | 237–241 |

Milliseconds, median [range]. All four bins are `--profile goport` (1.95.0, fat LTO) builds made
here, except Go. Runs started at 1-minute load < 1.5, sides rotated each round. The manifests
below list every run.

**S1a cuts ttfd by about 80 ms (1.46× → 1.18× Go). It does not reach Go: +43 to +49 ms remain.**

## Design

- **Trigger:** a language server's first program load, which has no keys of a previous load.
  - `process_all_program_files` starts resolve ahead as before and passes `first_load` when the
    host's parse workers run.
  - `ResolveAhead::start` then posts a job with an empty key list (`FirstLoadJob`).
- **Parse workers resolve for the job:** each parse worker that resolves the imports of a parsed
  file does it through `FirstLoadJob::worker_resolver`:
  - the resolver runs on the job's `AheadFs` view (open files over the OS, every call logged);
  - it is an ahead thread (`begin_ahead_thread`);
  - it publishes to the job's answers, and also publishes the package scope of each file's
    directory (`publish_package_scope`).
- **The loader takes answers through the existing check:**
  - `take_resolved_ahead` finds an answer by key;
  - `accept_ahead_answer` checks it: cached-file state (NeedsReload rejects, NoValue must be
    missing), the snapshot's own read with its text hash for each read, and lookup agreement;
  - it then replays seen files and missing directories.
- **No adoption:** no worker text, metadata or prep is adopted (prep stays off on this host).
- **Exact effects (findings 1–3):**
  - **Lookups are seeded, not attached.** The first-load job is not attached to the
    snapshot's `AheadLookupLayer`. Instead, the check notes each lookup that the serial
    loader's own call would store in the snapshot's lookup cache: the cache has no answer, and
    for `file_exists` the snapshot has no cached file. It stores those lookups only when the
    answer passes (`AheadSeed`). Unused speculation leaves no lookup behind.
  - **Known files go through the same path.** A known-file check makes no caching call.
  - **No known files carry over.** The first-load job keeps no known files for the next job.
    Without this, the next load's known-file checks stored extra lookups. The umami edit
    session caught it: 490 extra `file_exists` lines.
  - **Type-ref answers stay per worker.** Worker resolvers on an ahead thread no longer take
    type-reference answers from other workers. Those answers would carry package.json entries
    the worker never read.
- **Off switch:** `GOPORT_FIRST_LOAD_AHEAD=0`.
- **S0:**
  - `GOPORT_LOAD_TIMES=1` prints a stderr line per load: setup, parse, finish, collect and the
    resolve-ahead counts.
  - `GOPORT_LOAD_DUMP=<file>` writes the canonical dump (`compiler/load_dump.rs`).
  - `GOPORT_RESOLVE_AHEAD_STATS` also logs why keys were missing.

## Acceptance (umami, the 7 timed s1a-on runs)

| | per run |
|---|---|
| keys (`new_keys`) | 9,393 |
| taken | 9,176–9,284 (97.7–98.8%) |
| rejected | 0 |
| missing | 109–217 |
| package scopes taken | 1,097–1,109 of 1,110 |

Why keys were missing (`GOPORT_RESOLVE_AHEAD_STATS`, 3 runs):
- **About 107–114 "none":** no worker resolved that key.
  - Synthetic imports (`react/jsx-runtime`) are not resolved without a prep.
  - Keys whose mode differs from the worker's guess, such as CommonJS-mode `node:path` and
    `path` from `@types/node`.
- **0–52 "late":** the worker published the answer after the loader asked.
- **0 "unshareable."**

The loader thread's CPU in `resolve_imports_and_module_augmentations` fell from 206 to 124 ms
(profiled). Of the 124 ms, 47 ms is the check and replay, 13 ms the answer lookup, 15 ms
`ImportNames` and 5 ms the keys it still resolves itself.

## Proof

- **Dump equality** (`scripts/perf-rearch/loader/s1a-dumps.sh`):
  - **Method:** every scenario runs S1a on twice and off twice, interleaved, and once with
    `GOPORT_RESOLVE_AHEAD=0`, the serial loader with no resolve ahead at all.
  - **What is compared:** the full load dumps plus the server requests and diagnostics,
    with file watching enabled.
  - **Result:** on equals off in all 13 scenarios: umami, ts-pattern, hono, query and t3-web
    first loads; umami→ts-pattern and t3 web→server second projects; umami and hono unsaved
    buffers; umami and query edited package.json overlays; umami and hono edits.
    - Every first-load section also equals the serial loader.
    - The only serial differences are the lookup-cache lines after a *second*, keyed
      resolve-ahead load (umami-edit, hono-unsaved). That is pre-existing behavior: those
      loads attach the job's lookups instead of caching them. They show up with S1a off too.
    - hono-edit's S1a-off runs differ from each other there (12 `entries`, 7 `file_exists`).
  - **Sensitivity:** a mutant without seeding showed 12,277 lookup lines of difference, one
    without the seen-file replay 8,951.
- **Against Go** (`s1a-go-requests.sh`): net watch registrations and diagnostics are the same
  in 10 of 13 scenarios. The 3 hono scenarios differ on one watcher id: tsc-rs names its
  root-files watcher after `perf-measures/type-check/scripts/tsconfig.json`, Go after
  `tsconfig.build.json`. The glob is the same, and perf head does it too, so S1a did not
  introduce it.
- **Parity gate:** `parity.sh full` printed **PARITY OK** against base-current, which now
  includes the new watch items.
  - **Run:** bin `tsgo-s1a-rel3` (sha be3534f7c4de, source de87df409; the gate's checkout
    44a96f542 adds only scripts and docs).
  - **Tiers:** cli 170 MATCH; tests 139,890 PASS, 1 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`);
    lsp 2,548 MATCH, 53 GO-UNSTABLE; fuzz 29,955 MATCH, 1 DIFF (not lost against the base);
    corpus 12,727 MATCH.
- **Unit test:** the go_baselines `resolveahead` tests pass, 23 of 23, including the new
  `a_first_load_takes_parse_worker_answers`.

## What remains, and the next step

The loader thread still runs about 238 ms of serial work on the first load. From the profile
of the on side (CPU, samply):
- 49 ms of snapshot reads and hashes (`get_file_by_path`). The worker has already read those
  texts, but the snapshot's own reads stay (Astra 3).
- 30 ms of parse-cache acquire and `take_prefetched` (text compare, adopt).
- 47 ms of answer checks and replay.
- 17 ms of `start` and 13 ms of metadata.
- Then 33 ms of bind and about 20 ms of collect and publish.

Go does all of this in parallel parse tasks.

Next steps, in order of expected gain:
1. **S7:** bind in the parse workers, as Go's parse cache does (−25 to −30 ms).
2. **Cheaper checks:** skip calls that the load has already checked and replayed, beyond the
   shared groups (−15 to −25 ms of the 47).
3. **S4:** parallel discovery with the snapshot reads off the loader thread.
4. **Small:** resolve the synthetic JSX import and read the import modes in the workers, which
   gives back about 110 keys (−2 to −3 ms).

A pre-existing extra hash is a separate 3 ms: `cache_source_file` hashes the text again.

## Residual risks

- **Read timing.** The check reads a file (the snapshot's `get_file`) before it knows the rest
  of the answer passes. That is the loader's own read at that point only if every earlier call
  agreed, which is the existing resolve-ahead argument.
- **Disk changes during the load.** A seeded lookup is the worker's answer from earlier in the
  load, not a new OS call. If the disk changes during the load, the snapshot can hold the
  worker's earlier answer.
- **Second-load lookup cache.** The second-load lookup-cache divergence from a serial load,
  and its run-to-run nondeterminism, predate S1a. The values agree, but the cache contents do
  not.
- **Debug-only answer check.** `debug_check_answer` (a fresh-resolver check of each taken
  answer) runs only in debug builds. This lane ran the release test suite only.
### Sample manifest A: s1a-ttfd.py (ttfd-logs.mjs), `/home/agent/.cache/perf-rearch/s1a/ttfd-ab/manifest.jsonl`

| # | label | round | attempt | load before→after | ttfd ms | program update ms | taken/missing/rejected | kept |
|---|---|---|---|---|---|---|---|---|
| 1 | go | 0 | 0 | 1.38→1.24 | 316.1 | 231.4 | - | yes |
| 2 | head | 0 | 0 | 1.24→1.14 | 415.7 | 374.3 | - | yes |
| 3 | s1a-off | 0 | 0 | 1.14→1.05 | 414.4 | 371.1 | 0/0/0 | yes |
| 4 | s1a-on | 0 | 0 | 1.05→0.96 | 340.9 | 299.5 | 9234/159/0 | yes |
| 5 | head | 1 | 0 | 0.96→0.89 | 412.9 | 370.7 | - | yes |
| 6 | s1a-off | 1 | 0 | 0.89→0.98 | 430.6 | 390.0 | 0/0/0 | yes |
| 7 | s1a-on | 1 | 0 | 0.98→0.90 | 329.8 | 288.7 | 9266/127/0 | yes |
| 8 | go | 1 | 0 | 0.90→0.83 | 281.9 | 222.7 | - | yes |
| 9 | s1a-off | 2 | 0 | 0.83→0.76 | 412.1 | 371.0 | 0/0/0 | yes |
| 10 | s1a-on | 2 | 0 | 0.76→0.70 | 328.2 | 286.8 | 9255/138/0 | yes |
| 11 | go | 2 | 0 | 0.70→0.72 | 289.3 | 230.0 | - | yes |
| 12 | head | 2 | 0 | 0.72→0.68 | 407.5 | 365.7 | - | yes |
| 13 | s1a-on | 3 | 0 | 0.68→0.63 | 339.7 | 298.0 | 9176/217/0 | yes |
| 14 | go | 3 | 0 | 0.63→0.58 | 288.9 | 229.4 | - | yes |
| 15 | head | 3 | 0 | 0.58→0.53 | 402.2 | 360.0 | - | yes |
| 16 | s1a-off | 3 | 0 | 0.53→0.49 | 414.9 | 370.9 | 0/0/0 | yes |
| 17 | go | 4 | 0 | 0.49→0.45 | 276.4 | 217.8 | - | yes |
| 18 | head | 4 | 0 | 0.45→0.41 | 407.4 | 366.8 | - | yes |
| 19 | s1a-off | 4 | 0 | 0.41→0.38 | 424.0 | 379.8 | 0/0/0 | yes |
| 20 | s1a-on | 4 | 0 | 0.38→0.35 | 338.8 | 296.6 | 9232/161/0 | yes |
| 21 | head | 5 | 0 | 0.35→0.32 | 405.4 | 364.5 | - | yes |
| 22 | s1a-off | 5 | 0 | 0.32→0.30 | 406.7 | 363.8 | 0/0/0 | yes |
| 23 | s1a-on | 5 | 0 | 0.30→0.27 | 331.7 | 289.9 | 9183/210/0 | yes |
| 24 | go | 5 | 0 | 0.27→6.71 | 303.5 | 245.3 | - | EXCLUDED: load after 6.71 > 3.0 |
| 25 | go | 5 | 1 | 1.41→1.30 | 462.7 | 403.0 | - | yes |
| 26 | s1a-off | 6 | 0 | 1.30→1.19 | 400.1 | 358.2 | 0/0/0 | yes |
| 27 | s1a-on | 6 | 0 | 1.19→1.10 | 331.3 | 289.5 | 9284/109/0 | yes |
| 28 | go | 6 | 0 | 1.10→1.01 | 282.5 | 224.1 | - | yes |
| 29 | head | 6 | 0 | 1.01→0.93 | 409.9 | 370.0 | - | yes |

### Sample manifest B: bench.sh lsp --scenarios ttfd, `/home/agent/.cache/perf-rearch/s1a/bench-ttfd/runs.jsonl`

| # | label | run | attempt | load before→after (limit) | wall ms | CPU s |
|---|---|---|---|---|---|---|
| 1 | go | 1 | 0 | 0.78→0.72 (3.05) | 298.0 | 3.08 |
| 2 | head | 1 | 0 | 0.72→0.74 (3.02) | 428.7 | 1.41 |
| 3 | s1a-off | 1 | 0 | 0.74→0.68 (3.02) | 419.0 | 1.38 |
| 4 | s1a-on | 1 | 0 | 0.68→0.63 (3.02) | 339.6 | 1.40 |
| 5 | head | 2 | 0 | 0.63→0.58 (3.02) | 403.4 | 1.37 |
| 6 | s1a-off | 2 | 0 | 0.58→0.53 (3.02) | 429.5 | 1.41 |
| 7 | s1a-on | 2 | 0 | 0.53→0.49 (3.02) | 324.2 | 1.38 |
| 8 | go | 2 | 0 | 0.49→1.74 (3.05) | 290.3 | 3.30 |
| 9 | s1a-off | 3 | 0 | 1.31→1.20 (3.02) | 392.9 | 1.36 |
| 10 | s1a-on | 3 | 0 | 1.20→1.10 (3.02) | 328.4 | 1.39 |
| 11 | go | 3 | 0 | 1.10→1.02 (3.05) | 282.7 | 3.14 |
| 12 | head | 3 | 0 | 1.02→0.93 (3.02) | 412.6 | 1.38 |
| 13 | s1a-on | 4 | 0 | 0.93→0.86 (3.02) | 332.4 | 1.39 |
| 14 | go | 4 | 0 | 0.86→0.79 (3.05) | 276.0 | 3.16 |
| 15 | head | 4 | 0 | 0.79→0.73 (3.02) | 422.4 | 1.38 |
| 16 | s1a-off | 4 | 0 | 0.73→0.67 (3.02) | 399.3 | 1.42 |
| 17 | go | 5 | 0 | 0.67→0.61 (3.05) | 304.6 | 3.20 |
| 18 | head | 5 | 0 | 0.61→0.57 (3.02) | 422.0 | 1.41 |
| 19 | s1a-off | 5 | 0 | 0.57→0.55 (3.02) | 413.3 | 1.42 |
| 20 | s1a-on | 5 | 0 | 0.55→0.51 (3.02) | 332.6 | 1.40 |
| 21 | head | 6 | 0 | 0.51→0.47 (3.02) | 413.8 | 1.39 |
| 22 | s1a-off | 6 | 0 | 0.47→0.43 (3.02) | 403.3 | 1.37 |
| 23 | s1a-on | 6 | 0 | 0.43→0.39 (3.02) | 350.2 | 1.40 |
| 24 | go | 6 | 0 | 0.39→0.44 (3.05) | 281.9 | 3.00 |
| 25 | s1a-off | 7 | 0 | 0.44→0.41 (3.02) | 417.5 | 1.40 |
| 26 | s1a-on | 7 | 0 | 0.41→0.37 (3.02) | 331.1 | 1.37 |
| 27 | go | 7 | 0 | 0.37→0.34 (3.06) | 274.8 | 3.31 |
| 28 | head | 7 | 0 | 0.34→0.40 (3.02) | 399.0 | 1.38 |

bench.sh also made one unrecorded warm-up run per side; it excluded no run (all attempts 0).
