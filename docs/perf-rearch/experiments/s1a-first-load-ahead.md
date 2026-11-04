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

## Revision after Astra's S1a review (fixes at `265c262a2`, `98f94361a`)

### Fixes
- **1 (high): validate uncached lookups at demand.**
  - **Check:** a first-load check makes every `file_exists`, `directory_exists` and `realpath` that the snapshot's lookup cache has no answer for through that cache, as the serial loader's own call would. The calls run in call order, and the check stops at the first difference, so an answer's reads happen only after every lookup before them agreed.
  - **Rejection:** a rejected answer leaves only a serial prefix (the lookups it made, and at most the read the serial loader would make at that point).
  - **Seeds removed:** the validating calls store the lookups themselves.
  - **Package.json cache:** a taken first-load answer's package.json lookups go into the loader's real package.json cache as pending reads, with the text the check read on the snapshot. The `InfoCache` then holds a serial load's entries; each text is copied once per load (`98f94361a`).
  - **Disk-change tests:** `resolveahead_test.rs` has three tests whose disk changes between the parse workers' answers and the loader's check. `Mode::Force` waits for the idle parse workers, then a hook mutates the disk: a nearby package appears while the ancestor package.json changes, a resolved file is deleted, a node_modules symlink is repointed. Each is compared with a serial load whose disk changes at the same point. The comparison covers the program, resolutions, seen files, missing directories, cached files with hashes, the lookup cache, cached directories and aliases, and the test requires rejections.
  - **They pass, and catch the bug:** all 26 resolve-ahead tests pass. With validation removed (a mutant build), all three new tests fail.
- **2 (medium): strict proof.**
  - **Dump:** it now records full diagnostics (resolution and processing), type-reference resolved and original paths, include reasons and traces, and the resolver's own caches (module and type-reference answers, and the real `InfoCache` with pending reads parsed) apart from the `package_json_cache_entries` union.
  - **Session recorder:** `load-dump-session.mjs` records every server request, notification and response payload in full (only `window/logMessage`, which carries timings, is left out).
  - **New sessions:** auto-import completion plus `completionItem/resolve`, cancellation, and file-system mutation on a scratch copy.
  - **Comparison:** `compare-requests.py` is byte for byte with no normalization. `dump-sections.py` compares dumps section by section and separates sections that vary without S1a too.
- **3 (low):** missing keys are collected only when the debug log is on.

### Dump equality (`dumps4`, bin `98f94361a` release)
18 scenarios, each run with S1a on twice, off twice, and once with resolve ahead disabled entirely.
- **Protocol:** byte-identical across all runs of every scenario, for all payloads.
- **First-load sections:** identical in all 18, including against the serial loader.
- **Sections that vary without S1a too:**
  - the auto-import snapshot (t3-web: 4 different values in 5 S1a-off runs, and 4 in 5 S1a-on runs, with an overlap);
  - keyed second loads (umami-fsmut, hono-edit), whose lookup cache and `InfoCache` vary run to run with S1a off as well;
  - serial-only differences after keyed loads, which predate S1a (keyed loads attach the job's lookups).
- **Earlier bin:** the run on `265c262a2` (`dumps3`) agrees.

### Parity
**PARITY OK** on bin `98f94361a`.

| Tier | Result |
|---|---|
| cli | 170 MATCH |
| tests | 139,893 PASS, 1 FLAKY (`watch_ends_soon_after_sigint_or_sigterm`) |
| lsp | 2,548 MATCH, 53 GO-UNSTABLE |
| fuzz | 29,955 MATCH, 1 known DIFF |
| corpus | 12,727 MATCH |

- **First run on `265c262a2` failed.** It printed PARITY FAIL on 8 `pkgjson-overlay` items, all `rs-nondet`: only tsc-rs's first of three runs differed, with an extra `workspace/diagnostic/refresh` at load 40–56. Re-running that session alone gave 34/34 MATCH all six times (three with S1a on, three off).
- **Second run on `265c262a2`:** PARITY OK, with 1 rs-nondet `references` item that the base already has as GO-UNSTABLE.

### What fix 1 costs
**The exact validation takes back most of S1a's saving.**

Loader-thread CPU in `process_all_program_files` (samply):

| Build | Loader CPU | Of which `resolve_module_name` |
|---|---|---|
| S1a before fix 1 | 275 ms | 82 ms |
| fixed bin, S1a on | 318–319 ms | 110–116 ms |
| fixed bin, S1a off | 344 ms | 157 ms |

- **What it costs:** the validating lookups are real OS calls (`OverlayFS::file_exists`, `directory_exists`, `realpath`) made on the loader thread, as the serial loader makes them. They are about 30 ms of the resolution, plus about 8 ms more in package scopes.
- **What is left:** S1a still skips the resolver logic, and saves about 25 ms of loader CPU against off.
- **Before the copy fix:** the copy of package.json texts took 29 ms more (`265c262a2`, fixed in `98f94361a`).

ttfd (umami, s1a-ttfd.py, 9 rounds, runs repeated when load after > 2.0; the machine was busy with other agents, so the spread is wide):

| side | n | ttfd median [range] | program update | loader (median, min) |
|---|---|---|---|---|
| Go | 9 | 295.5 [287.9–430.7] | 235.6 | – |
| perf head | 9 | 433.0 [405.1–625.3] | 388.7 | – |
| S1a before fix 1 (`a76811e51`) | 9 | 451.7 [334.2–526.3] | 383.5 | 318.9, 244.2 |
| fixed bin, S1a off | 9 | 433.5 [410.3–719.0] | 388.2 | 327.5, 315.1 |
| fixed bin, S1a on | 9 | 436.9 [395.3–685.0] | 396.4 | 331.5, 302.3 |

- **Idle reference:** in the idle window of the first timing run, S1a before fix 1 was 331.7 against head 407.5.
- **Today's medians:** they do not separate the tsc-rs sides beyond noise. The minimums, and the in-process loader times taken in the same runs, show the fixed S1a about 10–25 ms below off and about 60 ms above S1a before fix 1.
- **Stopped runs:** `ttfd-ab2` (23 runs) and `ttfd-ab3-stopped` (15 runs). I stopped them to rebuild the bin or because of noise; one run in `ttfd-ab2` overlapped a profile I took. Their manifests are kept in `~/.cache/perf-rearch/s1a/`.

### Acceptance (fixed bin, S1a on, 9 timed runs)
- Taken: 9,273 (median) of 9,393. Rejected: 0. Missing: 120.
- Why missing, unchanged: about 107–114 never resolved by a worker (synthetic `react/jsx-runtime`, CommonJS-mode keys), the rest late.

### What it means for the next lanes
Exact validation moves the cost from the resolver to the lookups themselves. Ways to win it back:
- **Make the lookups cheaper:** a single `statx` per path, or asking the overlay directly.
- **Move the loader off the critical path (S4).** The parse workers already make the same OS calls in parallel.
- **Do fewer:** skip calls already checked, which root has planned.

The answer-level design cannot beat the serial loader by much while each lookup must be made again on the loader thread.
#### Sample manifest C: s1a-ttfd.py, REPEAT_LOAD=2, `/home/agent/.cache/perf-rearch/s1a/ttfd-ab4/manifest.jsonl`

| # | label | round | attempt | load before→after | ttfd ms | program update ms | loader ms | taken/missing/rejected | kept |
|---|---|---|---|---|---|---|---|---|---|
| 1 | go | 0 | 0 | 1.40→1.93 | 299.3 | 240.8 | - | - | yes |
| 2 | head | 0 | 0 | 1.38→1.75 | 625.3 | 567.9 | - | - | yes |
| 3 | s1a-old | 0 | 0 | 1.41→1.46 | 498.0 | 437.3 | 370.35 | 9284/109/0 | yes |
| 4 | fix-off | 0 | 0 | 1.46→2.22 | 516.7 | 471.3 | 411.65 | 0/0/0 | EXCLUDED: load after 2.22 > 2.0 |
| 5 | fix-off | 0 | 1 | 1.40→2.25 | 667.1 | 595.8 | 511.50 | 0/0/0 | EXCLUDED: load after 2.25 > 2.0 |
| 6 | fix-off | 0 | 2 | 1.49→1.48 | 476.1 | 434.6 | 370.00 | 0/0/0 | yes |
| 7 | fix-on | 0 | 0 | 1.48→1.36 | 398.8 | 358.3 | 302.86 | 9237/156/0 | yes |
| 8 | head | 1 | 0 | 1.36→2.54 | 592.8 | 527.0 | - | - | EXCLUDED: load after 2.54 > 2.0 |
| 9 | head | 1 | 1 | 1.41→1.30 | 430.5 | 388.7 | - | - | yes |
| 10 | s1a-old | 1 | 0 | 1.30→1.84 | 526.3 | 466.5 | 408.68 | 9249/144/0 | yes |
| 11 | fix-off | 1 | 0 | 1.43→1.31 | 420.9 | 378.1 | 324.83 | 0/0/0 | yes |
| 12 | fix-on | 1 | 0 | 1.31→2.57 | 479.8 | 438.3 | 375.89 | 9284/109/0 | EXCLUDED: load after 2.57 > 2.0 |
| 13 | fix-on | 1 | 1 | 1.43→1.32 | 395.9 | 354.3 | 302.30 | 9266/127/0 | yes |
| 14 | go | 1 | 0 | 1.32→2.17 | 389.6 | 314.5 | - | - | EXCLUDED: load after 2.17 > 2.0 |
| 15 | go | 1 | 1 | 1.43→1.31 | 291.8 | 230.8 | - | - | yes |
| 16 | s1a-old | 2 | 0 | 1.31→1.53 | 347.5 | 305.4 | 247.99 | 9235/158/0 | yes |
| 17 | fix-off | 2 | 0 | 1.45→1.34 | 423.3 | 380.5 | 322.05 | 0/0/0 | yes |
| 18 | fix-on | 2 | 0 | 1.34→1.55 | 685.0 | 589.2 | 543.72 | 9265/128/0 | yes |
| 19 | go | 2 | 0 | 1.39→1.35 | 295.5 | 235.6 | - | - | yes |
| 20 | head | 2 | 0 | 1.35→1.65 | 501.0 | 455.0 | - | - | yes |
| 21 | fix-off | 3 | 0 | 1.49→3.29 | 417.0 | 375.5 | 328.44 | 0/0/0 | EXCLUDED: load after 3.29 > 2.0 |
| 22 | fix-off | 3 | 1 | 1.39→1.28 | 410.3 | 368.9 | 315.14 | 0/0/0 | yes |
| 23 | fix-on | 3 | 0 | 1.28→1.77 | 523.8 | 478.2 | 406.37 | 9285/108/0 | yes |
| 24 | go | 3 | 0 | 1.38→1.27 | 312.6 | 251.1 | - | - | yes |
| 25 | head | 3 | 0 | 1.27→1.81 | 421.3 | 377.3 | - | - | yes |
| 26 | s1a-old | 3 | 0 | 1.47→2.00 | 435.2 | 385.1 | 301.91 | 9283/110/0 | yes |
| 27 | fix-on | 4 | 0 | 1.38→1.27 | 436.9 | 396.4 | 331.51 | 9285/108/0 | yes |
| 28 | go | 4 | 0 | 1.27→2.21 | 298.2 | 237.7 | - | - | EXCLUDED: load after 2.21 > 2.0 |
| 29 | go | 4 | 1 | 1.46→1.34 | 290.5 | 230.6 | - | - | yes |
| 30 | head | 4 | 0 | 1.34→2.83 | 703.0 | 644.0 | - | - | EXCLUDED: load after 2.83 > 2.0 |
| 31 | head | 4 | 1 | 1.38→1.27 | 417.2 | 374.2 | - | - | yes |
| 32 | s1a-old | 4 | 0 | 1.27→2.05 | 638.0 | 571.4 | 507.55 | 9282/111/0 | EXCLUDED: load after 2.05 > 2.0 |
| 33 | s1a-old | 4 | 1 | 1.47→3.60 | 537.0 | 487.0 | 420.84 | 9210/183/0 | EXCLUDED: load after 3.6 > 2.0 |
| 34 | s1a-old | 4 | 2 | 1.44→1.96 | 334.2 | 293.0 | 244.23 | 9248/145/0 | yes |
| 35 | fix-off | 4 | 0 | 1.48→2.01 | 423.5 | 382.8 | 318.28 | 0/0/0 | EXCLUDED: load after 2.01 > 2.0 |
| 36 | fix-off | 4 | 1 | 1.44→1.33 | 418.7 | 376.7 | 327.52 | 0/0/0 | yes |
| 37 | go | 5 | 0 | 1.33→1.30 | 430.7 | 350.4 | - | - | yes |
| 38 | head | 5 | 0 | 1.30→1.36 | 569.0 | 516.0 | - | - | yes |
| 39 | s1a-old | 5 | 0 | 1.36→1.33 | 492.0 | 435.3 | 381.92 | 9198/195/0 | yes |
| 40 | fix-off | 5 | 0 | 1.33→1.30 | 719.0 | 663.0 | 598.44 | 0/0/0 | yes |
| 41 | fix-on | 5 | 0 | 1.30→1.39 | 497.5 | 455.6 | 392.91 | 9241/152/0 | yes |
| 42 | head | 6 | 0 | 1.39→1.28 | 433.0 | 388.6 | - | - | yes |
| 43 | s1a-old | 6 | 0 | 1.28→1.26 | 456.3 | 383.5 | 318.89 | 9284/109/0 | yes |
| 44 | fix-off | 6 | 0 | 1.26→1.24 | 571.1 | 516.9 | 463.23 | 0/0/0 | yes |
| 45 | fix-on | 6 | 0 | 1.24→1.22 | 517.9 | 451.6 | 411.71 | 9273/120/0 | yes |
| 46 | go | 6 | 0 | 1.22→1.36 | 430.1 | 342.7 | - | - | yes |
| 47 | s1a-old | 7 | 0 | 1.36→1.33 | 451.7 | 379.2 | 326.42 | 9285/108/0 | yes |
| 48 | fix-off | 7 | 0 | 1.33→1.39 | 528.0 | 485.9 | 427.94 | 0/0/0 | yes |
| 49 | fix-on | 7 | 0 | 1.39→1.60 | 398.8 | 356.3 | 303.60 | 9285/108/0 | yes |
| 50 | go | 7 | 0 | 1.40→3.63 | 361.5 | 296.0 | - | - | EXCLUDED: load after 3.63 > 2.0 |
| 51 | go | 7 | 1 | 1.45→1.33 | 287.9 | 228.4 | - | - | yes |
| 52 | head | 7 | 0 | 1.33→1.62 | 508.4 | 455.5 | - | - | yes |
| 53 | fix-off | 8 | 0 | 1.49→1.37 | 433.5 | 388.2 | 324.53 | 0/0/0 | yes |
| 54 | fix-on | 8 | 0 | 1.37→2.79 | 442.2 | 385.4 | 315.98 | 9143/250/0 | EXCLUDED: load after 2.79 > 2.0 |
| 55 | fix-on | 8 | 1 | 1.47→2.48 | 579.4 | 530.0 | 450.05 | 9283/110/0 | EXCLUDED: load after 2.48 > 2.0 |
| 56 | fix-on | 8 | 2 | 1.38→1.27 | 395.3 | 352.6 | 304.17 | 9285/108/0 | yes |
| 57 | go | 8 | 0 | 1.27→2.05 | 531.2 | 438.8 | - | - | EXCLUDED: load after 2.05 > 2.0 |
| 58 | go | 8 | 1 | 1.48→1.37 | 288.2 | 226.6 | - | - | yes |
| 59 | head | 8 | 0 | 1.37→2.92 | 738.5 | 679.8 | - | - | EXCLUDED: load after 2.92 > 2.0 |
| 60 | head | 8 | 1 | 1.39→1.28 | 405.1 | 361.0 | - | - | yes |
| 61 | s1a-old | 8 | 0 | 1.28→3.02 | 348.0 | 306.0 | 244.85 | 9260/133/0 | EXCLUDED: load after 3.02 > 2.0 |
| 62 | s1a-old | 8 | 1 | 1.42→3.15 | 345.0 | 301.5 | 251.73 | 9223/170/0 | EXCLUDED: load after 3.15 > 2.0 |
| 63 | s1a-old | 8 | 2 | 1.48→1.36 | 343.3 | 299.7 | 248.57 | 9269/124/0 | yes |

## Second re-review: strict proof tools (`84a8d1936`, `c5527122f`)

All four review fixes are in `84a8d1936`. The settle wait for background-heavy sessions is in the next commit.

- **Record-level comparison** (`dump-sections.py`):
  - **Fail:** a record that every S1a-off run has and an on run lacks; a record an on run has more often than any off run; the same records in another order when all the off runs agree on the order.
  - **Inconclusive:** a record whose count varies between the off runs. Each one is listed.
  - **Excluded:** two documented kinds, the raw `InfoCache` stores. They are listed with the reason, and their behavior is compared through the `info_cache_get` and `package_json` records.
  - **Self-test:** `selftest` reproduces Astra's case (off-run noise plus a wrong resolution in both on runs) as a FAIL, and a refresh moved across a response as a FAIL.
- **Message stream** (`load-dump-session.mjs`): every message in both directions, raw and in order, ids and `window/logMessage` included. Only durations and clock times inside log text are normalized.
  - The two directions are compared as two ordered streams. Their interleaving is a race in the client: it sends its next request when it gets a response, while the server may still be writing the log line for that response.
  - The client now sends `exit` without params.
- **`InfoCache` dump:** `info_cache_entry` and `info_cache_pending` are listed apart, and `info_cache_get` gives what `get()` returns. A goport_core test checks adopted first-load lookups against a serial cache, through enumeration and through `get()`.
- **`wait_idle`:** it panics after 60 seconds.
- **Parity:** `parity.sh quick` (bin `tsgo-s1a-fix4`, source `84a8d1936`) printed PARITY OK.
  - cli 24 MATCH; tests 137,765 PASS; lsp 1,686 MATCH, 28 GO-UNSTABLE; fuzz 1,498 MATCH.

### Strict dump results (`dumps6`; background-heavy cases rerun with the settle wait in `dumps7`)
- **Pass, 13 scenarios:**
  - 12 scenarios with 3 on and 3 off runs, all with 0 inconclusive records: the umami, ts-pattern, hono, query and t3-web first loads; second projects; unsaved buffers; package.json overlays; cancel.
  - umami-autoimport, 6 on and 6 off.
- **Pass in `dumps7`, after the settle wait:** umami-edit and hono-edit, 6 on and 6 off.
- **Timing, not state:** without the wait, umami-edit and t3web-autoimport failed in the snapshot taken while the first load's background auto-import work runs. A faster first load moves that work into another snapshot. With a 4-second wait before the later step, 4 on and 4 off runs match record for record (`settle/`). `s1a-dumps.sh` now waits for autoimport, fsmut and edit sessions.
- **Remaining FAILs, all in parts that vary without S1a:**
  - **t3web-autoimport, the auto-import snapshot:** one of 6 on runs has 74 lookup records (the `jose` package) that no off run has. The off runs vary in the same section: records seen in 1 or 2 of 6 off runs. This is background registry timing.
  - **tspattern-fsmut, the server stream:** one of 6 on runs has one extra snapshot clone ("Cloning snapshot 3", "Adopted snapshot 4"). This is the batching of watch events.
  - **umami-fsmut, the keyed second load:** 3 of 6 on runs lack `info_cache_get` records under `packages/mcp/` that all 6 off runs have. The off runs vary in the same section too.
    - **Pre-existing cause:** a keyed (second) resolve-ahead load does not adopt its taken answers' package.json lookups into the real `InfoCache` (only first loads do). Its cache therefore depends on which answers were taken in time. Enumeration and the answers themselves are unaffected.
    - **What S1a changes:** it leaves the resolve-ahead workers started at the second load, which shifts which keys are taken.
    - **Next step:** make keyed loads adopt too. That is a one-line behavior change to the keyed path (`package_json_text` for every load), so it is left for review.
