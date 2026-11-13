# LSP while typing in T3 Code: where the fork spends time, against tsc 7

Profiling lane, 2026-11-12, AMD MS-A2 (WSL2, 32 threads), branch `perf/lsp-t3code-profile` on fork main
`1b6aba1a8`. This lane measures. It changes no compiler code.

## Summary

- **Semantic tokens and inlay hints are fast in the fork.** Their own work is 2.0–2.5 ms (tokens) and
  0.6 ms (hints). Go's is 4.3–7 ms. The fork's ~41 ms (pulls=no-diagnostics) and ~75 ms (pulls=all)
  is **queue wait on the dispatch thread**:
  - In pulls=no-diagnostics, half the token/hint pulls queue behind a hover (35 ms of its own work).
    The bench sends that hover at the same keystroke, just before them.
  - In pulls=all they also queue behind the edited file's diagnostic pull (38–42 ms of its own work).
  - With nothing queued ahead, fork tokens answer in 11.2 ms and hints in 12.1 ms. Go answers in
    18.9 and 18.1 ms.
- **Go's global completions do not reuse anything when diagnostics run alongside.** Go's completion
  work is the same in both modes (42.5 vs 41.7 ms CPU). What changes is Go's JSON marshalling of the
  4 MB answer: 37 ms CPU when the server is otherwise idle, 13 ms when diagnostics keep it busy.
  - Most of that difference is `runtime.memmove` (13.6 vs 2.7 ms), so the likely cause is cold memory
    (page faults on freed heap) and a cold CPU. Mechanism: a guess.
  - The fork's marshal takes 25 ms in both modes.
  - The fork's real gap on "c" is its own completion work: 78 ms against Go's 45. Of the 30 ms
    difference, 22 ms is the auto-import fix sort.
- **First completion of a session:** the fork builds the auto-import registry inside the completion on
  every run (96–126 ms). Go's warm starts at once on a goroutine. It beat the first keystroke in 5 of
  15 Go runs, and those first completions took 119–129 ms.
  - The fork's warm is idle work: it waits for 50 ms of quiet on the dispatch thread, so at 120 ms
    per key it never finishes.
  - The fork's first completion also waits 7–17 ms for the cancelled warm to yield the thread.
- **Worker threads would remove 60–70 ms of the 72–75 ms that tokens, hints and other-file
  diagnostics wait in pulls=all.** They would not remove the snapshot clone (~4 ms) or checker
  creation (~3 ms). Go serializes those too. They would also not remove any request's own work or
  its marshal on the single writer thread.
- **On T3 Code's own files the picture changes, and one finding is severe.**
  - On a `.ts` server file and a small `.tsx` component, the fork is faster than or close to Go,
    except for queued pulls.
  - On `apps/web/src/components/ChatView.tsx` (476 KB) the fork's queue never drains: median 11–24 s
    for completions and 19 s for everything else.
  - Each edited-file diagnostic or token pull costs ~780 ms. The fork runs requests that the editor
    cancelled while they were still queued (10 of 738 diagnostic pulls answered as cancelled; Go:
    330 of 738).
  - The single most valuable fix is to drop requests cancelled while queued.

## Method

- **Binaries:**
  - tsc 7: `~/.local/bin/tsgo-lsp-oracle-3e9236361fa9` (Go, with debug info).
  - fork: `~/.cache/perf-rearch/closeout/bins/tsgo-head-3733fe44b-goport`. This is the fat-LTO goport
    profile, with `.symtab` and no DWARF, so inlined frames fold into their caller. Its compiler code
    is equal to `1b6aba1a8` (`git diff --stat 3733fe44b 1b6aba1a8 -- crates` is empty).
  - No build was needed. Every number below, profiles included, comes from these two binaries.
- **Project:** `~/.cache/perf-rearch/projects/t3code` (read-only).
- **Harness:** `scripts/lspdiff/lspdiff.mjs bench`, as in the brief: 120 ms/key, 4 rounds of
  `"\nconsole.log(Math.max(1, 2).toFixed(1));\n"`, concurrent mode, 5 runs per server unless stated.
  Default files: the edited file is `.repos/alchemy-effect/packages/alchemy/src/AWS/CloudFront/KeyValueStore.ts`.
- **Load:** every run ran under the heavy-run rule (`systemd-run … flock ~/.cache/ts-rust-heavy.lock`),
  one job at a time. Load at start was 0.1–1.1 for every timing run. The `.tsx` runs (E) reached
  load 2–2.7 during the run. Go's parallel program rebuilds and the fork's backlog explain that, but
  read E's absolute numbers with that in mind.
- **Harness additions** (this branch, `scripts/lspdiff/`):
  - `bench --trace`: per run, it writes `trace-<server>-<mode>-<run>.json`. The file holds each request's
    id, send time, first and last byte of the answer, answer size and parse-done time; the keystroke
    times; every `window/logMessage` line with its arrival time; and a `performance.now()` /
    `CLOCK_MONOTONIC` clock pair for aligning with samply.
  - The server gets 10 minutes to exit, so a wrapping profiler can write its file.
  - `client.mjs` copied a big answer once per pipe chunk (`Buffer.concat` per chunk). It now copies it
    once. This took ~20–28 ms off the 4 MB global completion answers for **both** servers.
    `m1-*` runs used the old client, `m2-*` the new. The brief's pitch numbers had the old cost in them.
  - `trace-split.mjs`: per request kind, it splits latency using the servers' own
    `handled method 'M' (id) in D` lines (both servers log them). The columns:
    - `handled`: the server's own time, sync and async parts together.
    - `snapshot`: the part of `handled` that was a snapshot clone. A clone ("Finished cloning snapshot …
      in X") belongs to the earliest-sent request still pending when the clone was logged.
    - `own` = `handled` − `snapshot`.
    - `wait` = latency − `handled`.
    - `behind <kind>`: overlap of the wait with other requests' handled intervals.
    - `preWire` = first answer byte − send − `handled`: queue wait plus the server's marshalling.
    - `read` = pipe transfer plus the client's `JSON.parse`.
    - Completions are labelled by what was typed ("c", "M", "console.", "Math.", "2)."). The session's
      first completion has its own row. Diagnostic pulls are split into the edited file's and the
      other files'.
  - `profile-windows.py`: samply CPU per request kind and thread, over each request's window
    (send…first byte, or the handler interval), using the trace's clock pair. `--under F` breaks down
    one function.
  - No `--edit-file` option was needed: `--file` (repeatable) already makes the first given file the
    edited one.
- **Profiles:** `samply-user record -r 2000 --unstable-presymbolicate`, wrapping the server binary
  (`~/.cache/perf-rearch/lsp-profile1/tools/samply-wrap.sh`). Profiles: pulls=none (3 runs each) and
  pulls=all (2 runs each), both servers. CPU is samply's `threadCPUDelta` (on-CPU ms).
- **Raw data:** `~/.cache/perf-rearch/lsp-profile1/`. Runs: `m1-*`, `m2-*`, `e-*`; profiles: `prof-*`.
  Split tables are `split-*.json` beside each run. One-off helpers are in `tools/`
  (`timeline.mjs`, `first-completion.mjs`, `heavy.sh`).

### A caution on "handled"

- The fork logs `handled … in D` at the end of the async part. D runs from the handler's start, so it
  includes the sync part (snapshot) and the `run_pending` between them.
- Go logs the same way, but its async part runs on its own goroutine, so Go's D can include waiting
  for a checker from the pool.
- For both servers, the log line reaches the client *after* the answer, because the outgoing queue is
  FIFO. Handler end ≈ min(log arrival, answer first byte).

## Tables

All values are medians in ms except the "behind" and "other" columns, which are means (they add up
to "wait mean"). Fork = rs, tsc 7 = go. Runs: `m2-*`, 5 runs per server, same run per table.

### pulls=no-diagnostics (question A)

| kind | server | latency | handled | snapshot | own | wait | wait mean | behind hover | behind completion | behind tokens | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| semanticTokens | rs | 41.4 | 2.5 | 0 | 2.5 | 39.1 | 26.9 | 21.5 | 3.7 | – | 1.7 |
| semanticTokens | go | 19.1 | 7.0 | 0 | 6.7 | 12.4 | 8.1 | 5.8 | 1.0 | – | 1.3 |
| inlayHint | rs | 42.2 | 0.6 | 0 | 0.6 | 41.6 | 33.5 | 21.5 | 3.7 | 6.4 | 1.9 |
| inlayHint | go | 18.4 | 4.6 | 0 | 4.6 | 13.4 | 13.7 | 5.9 | 1.0 | 5.5 | 1.3 |
| hover | rs | 41.9 | 40.4 | 4.4 | 35.2 | 0.9 | | | | | |
| hover | go | 67.0 | 65.8 | 9.8 | 55.1 | 0.9 | | | | | |

The fork's token and hint latencies are bimodal:

| fork, pulls=no-diagnostics | n | latency p50 |
|---|---|---|
| tokens behind a hover (same keystroke) | 105 | 44.1 |
| tokens, nothing ahead | 85 | 11.2 (handled 10.3 = snapshot 4.4 + own 5.4) |
| hints behind a hover | 105 | 45.3 |
| hints, nothing ahead | 85 | 12.1 (own 0.6; waits behind the tokens pull, 10.3) |
| Go tokens / hints, either case | | 18.9 / 18.1 (Go tokens with nothing ahead: snapshot 9.8 + own 7.8) |

### pulls=all (question D)

| kind | server | latency | handled | own | wait mean | behind diag | behind hover | behind completion | behind tokens | other | preWire | read |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| completion "c" | rs | 117.3 | 82.7 | 78.4 | 34.0 | 0 | 0 | 0 | 0 | 34.0 | 25.4 | 8.8 |
| completion "c" | go | 77.4 | 53.6 | 44.8 | 24.7 | 0.2 | 0 | 0 | 0.3 | 24.0 | 12.9 | 11.1 |
| completion "M" | rs | 63.6 | 43.4 | 39.5 | 21.2 | 0 | 0 | 0 | 0 | 21.2 | 14.9 | 5.5 |
| completion "M" | go | 47.3 | 33.6 | 24.0 | 17.5 | 2.5 | 0 | 0 | 0 | 15.0 | 7.9 | 5.5 |
| completion member (3 kinds) | rs | 8.4–8.9 | 7.9–8.0 | 3.4–3.6 | ~0.8 | | | | | | | |
| completion member | go | 15.4–16.6 | 14.6–15.7 | 5.8–6.5 | ~0.8 | | | | | | | |
| hover | rs | 40.6 | 39.4 | 34.7 | 3.2 | 0 | – | 1.7 | 0 | 1.4 | | |
| hover | go | 58.4 | 57.2 | 48.8 | 1.9 | 0 | – | 0.7 | 0 | 1.1 | | |
| diagnostic, edited file | rs | 44.5 | 42.3 | 37.8 | 10.8 | 0.3 | 5.2 | 3.9 | 0 | 1.6 | | |
| diagnostic, edited file | go | 64.7 | 63.1 | 54.7 | 4.0 | 0.1 | 1.3 | 1.2 | 0 | 1.4 | | |
| diagnostic, other files | rs | 71.6 | 0.1 | 0.1 | 64.2 | 38.2 | 20.3 | 3.7 | 0 | 2.0 | | |
| diagnostic, other files | go | 13.2 | 0.4 | 0.4 | 12.1 | 4.9 | 5.2 | 1.0 | 0.1 | 0.9 | | |
| semanticTokens | rs | 74.2 | 2.0 | 2.0 | 65.2 | 39.3 | 20.3 | 3.7 | – | 1.9 | | |
| semanticTokens | go | 17.4 | 5.4 | 5.4 | 12.0 | 4.9 | 5.2 | 0.9 | – | 1.0 | | |
| inlayHint | rs | 75.4 | 0.6 | 0.6 | 68.2 | 39.4 | 20.3 | 3.7 | 2.7 | 2.2 | | |
| inlayHint | go | 16.7 | 4.3 | 4.3 | 12.4 | 6.0 | 5.4 | 0.9 | 0.3 | 0.5 | | |

- For Go, "behind" only means overlap in time: Go runs the async parts at the same time.
- What Go does serialize is the sync part (snapshot clone, ~8–10 ms) on its dispatch loop. Go's
  12 ms wait for tokens and hints is mostly that clone, done by the hover's or diagnostic pull's sync
  part just ahead of them.

### pulls=none (questions B, C)

| kind | server | latency | handled | snapshot | own | preWire | read | answer KB |
|---|---|---|---|---|---|---|---|---|
| completion first | rs | 268.3 | 214.8 | 100.7 | 114.8 | 42.1 | 10.6 | 3992 |
| completion first | go | 224.8 | 190.5 | 117.8 | 80.3 | 23.8 | 11.8 | 3992 |
| completion "c" | rs | 117.9 | 84.1 | 4.2 | 79.7 | 24.6 | 9.0 | 3990 |
| completion "c" | go | 108.8 | 54.2 | 9.3 | 45.2 | 44.3 | 10.1 | 3991 |
| completion "M" | rs | 66.2 | 45.8 | 4.3 | 38.1 | 14.5 | 5.4 | 2252 |
| completion "M" | go | 65.7 | 36.0 | 9.3 | 26.5 | 27.1 | 5.2 | 2254 |
| hover | rs | 42.9 | 41.0 | 4.3 | 35.5 | 0.8 | 0.1 | 0.2 |
| hover | go | 73.2 | 71.2 | 9.1 | 62.9 | 0.8 | 0.1 | 0.2 |

## Profiles (trimmed)

### Fork, global completion "c" (pulls=none, 9 requests, CPU ms per request, dispatch thread `tsgo`)

```
 82.1  dispatch_request
 76.0  LanguageService::get_completions_at_position
 63.6    autoimport::view::View::get_completions          (collect_auto_imports)
 37.5      slices::by_index::sort_indexes_cmp / pdqsort_cmp_func   <- sorting fixes
 37.2        View::compare_module_specifiers_for_ranking
 13.4          fix::is_fix_possibly_re_exporting_importing_file
  7.9            SourceFS::use_case_sensitive_file_names  (5 dyn Fs layers, per comparison)
  5.9            tspath::relative_path_within_directory (+ get_directory_path, allocates)
 11.4          memcmp (self)
  8.7      View::get_fixes
  4.8      View::search
  8.1    completion_info_from_data / get_completion_entries_from_symbols
  3.6  CheckerPool::get_checker -> Checker::new   (first checker of this program)
  3.3  Snapshot::clone_body (program update ~3 ms)
writer thread (`errgroup`): 25.5  json_marshal_dyn   (Message marshal 21.3: write_value_from 12.7,
                                                       JsonDecoder::read_token 7.8 -> re-tokenizes the
                                                       marshalled bytes; append_json_quote_with 6.7 self)
```

### tsc 7, global completion "c" (pulls=none, 9 requests)

```
 42.5  ls.getCompletionsAtPosition
 33.9    autoimport.(*View).GetCompletions
 15.3      slices.pdqsortCmpFunc -> View.CompareFixesForSorting 14.3
  9.0        isFixPossiblyReExportingImportingFile
  6.1      View.GetFixes
  5.1      View.Search
 13.7  Session.getSnapshot (sync part)
 37.1  writeLoop -> json.Marshal (encoding/json/v2; reformatValue 16)    [pulls=all: 13.2]
```

### Fork, first completion (3 requests)

```
118   Snapshot::clone_body -> Registry::clone_ -> RegistryBuilder::update_indexes  (registry build, serial)
 81     ExportExtractor::extract_from_file / SymbolExtractor::extract_from_symbol 74
 25     Checker::get_exports_of_module
110   get_completions_at_position
 47     View::get_fixes   (steady state 8.7)
 37       modulespecifiers::get_module_specifiers_for_file_with_info  (cold)
 34     compare_module_specifiers_for_ranking (sort)
```

- On Go's first completion, `registryBuilder.buildProjectBucket.func1` uses 594 ms CPU, spread over
  32 checkers (`createCheckerPool`).
- Go's GC mark workers use 670 ms CPU in the same windows. They run in parallel, so the wall time is
  far lower.

### Edited-file diagnostic and hover (pulls=all, handler windows)

```
fork diagnostic, edited (321): 42.7 on tsgo
  33.4 get_declaration_diagnostics -> DeclarationTransformer -> serialize_type_for_declaration
  32.4   symbol_accessibility::get_with_alternative_containers
  24.1     Checker::get_exports_of_module          (fresh checker after every edit)
fork hover (42): 41.5 on tsgo
  33.0 hover::get_quick_info_and_documentation_for_symbol -> symbol_to_string_ex -> get_symbol_chain
  32.6   get_with_alternative_containers -> get_exports_of_module 24.0
Go: diagnostic edited 41.6 in getDeclarationDiagnostics; hover 76 CPU in getWithAlternativeContainers
    (plus 57 GC mark)
```

This is the same algorithm in both servers, and the fork is faster at it. It is paid once per new
checker per edit, and it is the work the queued pulls wait behind.

### Per-edit shared work and frees (fork)

- **Snapshot clone:** 4.2–4.4 ms per edit. Program update is 2.6–3.7 ms of it.
- **Checker creation:** `Checker::new` ~3 ms. It goes to the first request that needs a checker of
  the new program: 3.1–3.5 ms in completions and hover, 2.6 in edited diagnostics.
- **Frees:** inside request handler windows, `drop_in_place` costs 0–0.6 ms per request (2.2 ms in
  "c", 5.4 in the first completion). The dispatch thread spends ~395 ms of CPU per pulls=all run
  freeing (`sdallocx`, `arena_ptr_array_flush`, `SparseLinkPage`, `ExternalModuleContainerIndex`),
  but only in idle gaps (`drop_garbage(busy)`). Unexplained wait ("other") is 1–2 ms per request,
  so frees and idle work add at most that.
- **Auto-import warm:** ~73 ms of CPU per pulls=none run of 4 rounds, all of it cancelled.

## Answers

### A. Semantic tokens and inlay hints (pulls=no-diagnostics), fork 41 vs Go 19

- **(a) Queue:** 39 ms median wait (mean 27 tokens, 33.5 hints).
  - Behind a hover sent at the same keystroke: 21.5 ms mean. It is ~36–40 ms whenever it happens,
    which is on 105 of 205 pulls.
  - Behind a completion: 3.7 ms. Hints also wait behind the tokens pull: 6.4 ms.
  - The median lands at ~41 because just over half the pulls are in the hover case.
  - Both kinds land at the same value because hints queue behind tokens, which queue behind the
    hover. The work is not shared per edit.
- **(b) Per-edit shared work:** paid by whichever request comes first after the edit (the hover, or
  the tokens pull when no hover is ahead):
  - snapshot clone 4.3 ms (program update ~3);
  - checker creation ~3 ms;
  - on the hover's checker, the fresh-checker `get_with_alternative_containers` /
    `get_exports_of_module` work (~24–32 ms). This last part is what makes the hover 35 ms.
  - No frees and no idle work show up inside these windows (≤1–2 ms).
- **(c) Own work:** tokens 2.0–2.5 ms, hints 0.6 ms. When the tokens pull comes first after the edit,
  it pays the clone and the checker creation: handled 10.3 ms, latency 11.2.
- **Go:**
  - (a)+(b): the wait is 12–13 ms. It is the serial sync part (snapshot clone 9.8 ms on Go's dispatch
    loop) of the request just ahead.
  - (c): tokens 4.3–7.8 ms and hints 4.6 ms, checker creation included. Go's hover (55–66 ms of its
    own) runs on a goroutine and does not delay them.

### B. Why Go's global completions get faster with diagnostics running, and the fork's don't

- **Go reuses nothing, and the fork serializes nothing here.**
  - The completion is the first request at its keystroke, so in both servers it waits behind nothing
    (behind = 0).
  - Go's completion work is the same with and without diagnostics: "c" handled 54.2 → 53.6 ms,
    `getCompletionsAtPosition` 42.5 → 41.7 ms CPU.
- **What changes is Go's marshalling of the 3.9 MB answer on its write loop:**
  - preWire 44.3 → 12.9 ms ("c") and 27.1 → 7.9 ms ("M");
  - `json.Marshal` CPU 37.1 → 13.2 ms ("c") and → 10.8 ms ("M").
  - Every frame under it gets slower when idle. `runtime.memmove` goes 13.6 → 2.7 ms and
    `slices.Grow` 2.5 → 1.4.
- **Likely cause (a guess):** the cost of cold memory. When the server sits idle between keystrokes,
  Go's scavenger has returned freed heap to the OS, so growing a multi-MB buffer page-faults. Lower
  idle clock speeds add to it. With diagnostics running, the heap and the cores stay warm. I did not
  confirm this with page-fault counters.
- **The fork's marshal** is 25 ms in both modes (jemalloc keeps its memory). Its profile shows a
  second pass: `JsonDecoder::read_token` re-tokenizes the marshalled message (7.8 ms) on top of
  quoting (6.7 ms).
- **So with diagnostics, the fork's "c" (117 vs 77 ms) loses on three things:**
  - its own completion work: +33.6 ms (78.4 vs 44.8);
  - marshal: +12.5 ms;
  - read: −2.3 ms.
- **In the completion work, the auto-import fix sort is 37.5 vs 15 ms.**
  - The fork's comparator calls `program.use_case_sensitive_file_names()` through five dynamic `Fs`
    layers on every comparison (7.9 ms).
  - It builds a directory string and a relative path for every "possibly re-exporting" check
    (6–10 ms). Go does a prefix test (`StartsWithDirectory`).
  - It also sorts through an index indirection (`by_index`).

### C. First completion of a session (fork ~270 vs Go ~220 median)

Median over 15 runs per server (`first-completion.mjs`):

| | fork | Go |
|---|---|---|
| latency | 273.1 | 217.6 (bimodal: 119–129 in 5 runs, 206–347 in 10) |
| didChange handled after send (dispatch thread busy) | 9.7 (6–17) | 0.5 |
| snapshot clone without registry | 3.8 | 8.4 |
| auto-import registry build inside the completion | 110.4 (every run) | 153.4 (0 in the 5 fast runs) |
| completion work | 115.3 | 78.7 (fast runs: 78.7–86.0) |
| preWire (marshal) | 35.8 | 24.0 |
| read | 9.2 | 11.5 |

- **The registry build.**
  - Both servers start a warm after the first snapshot update that changes one open file (Go
    `Session.warmAutoImportCache`, from `UpdateSnapshot`). Here the hover at key 0 triggers it.
  - Go's warm runs at once on a goroutine. Its exports extraction is parallel ("32 created
    checkers"). It finished before the "c" keystroke 120 ms later in 5 of 15 runs. In those runs the
    first completion was 119–129 ms.
  - The fork's warm is idle work (`IdleStart::AfterQuiet`). It starts only after the hover's answer
    and 50 ms of quiet. It runs serially ("1 created checkers") on the dispatch thread, and the next
    didChange always cancels it.
  - So the fork builds the registry inside the first completion every time (96–126 ms). Its registry
    build is as fast as Go's (Go 109–231 ms when it ran inside the completion).
  - The keystroke's didChange then waits 6–17 ms for the cancelled warm to give back the thread.
- **The cold completion work** costs about 35 ms extra in both servers: the fork goes 115 vs 78–80
  steady, Go 79–86 vs 45 steady. In the fork, `View::get_fixes` takes 47 ms instead of 8.7 ms because
  module specifiers are resolved cold. That is not a gap against Go.
- **Marshal** on the first answer is 36–44 ms in the fork against 25 ms steady. Probably cold jemalloc
  pages (a guess).

### D. Queue wait vs own work per request kind (pulls=all), and what worker threads would change

| kind | fork latency | own (handled) | waits behind other requests (removable by worker threads) | not removable by worker threads |
|---|---|---|---|---|
| semanticTokens | 74.2 | 2.0 | ~63: edited diag 39.3, hover 20.3, completion 3.7 | snapshot clone 4.3 + checker creation ~3, when it is first after the edit; own 2.0; writer FIFO (behind a 25 ms completion marshal if one is in flight) |
| inlayHint | 75.4 | 0.6 | ~66: 39.4 + 20.3 + 3.7 + tokens 2.7 | the same, own 0.6 |
| diagnostic, other files | 71.6 | 0.1 | ~62: 38.2 + 20.3 + 3.7 | ~1–2 (other) |
| diagnostic, edited file | 44.5 | 42.3 | ~9 mean (hover 5.2, completion 3.9) | own 37.8 (declaration diagnostics on a fresh checker: `get_with_alternative_containers`) + snapshot 4.1 |
| hover | 40.6 | 39.4 | ~1.7 (completion) | own 34.7 (same fresh-checker work) + snapshot 4.4 |
| completion "c" / "M" | 117.3 / 63.6 | 82.7 / 43.4 | 0 (first in line at its keystroke) | own work 78 / 40; marshal 25 / 15 on the writer thread; read 9 / 5.5 |
| completion member | 8.4–8.9 | ~8 | 0 | all of it (snapshot ~4.2 + own ~3.5) |

- **Worker threads would remove the "behind" columns**, about 60–70 ms of the 72–75 ms for tokens,
  hints and other-file diagnostics. The edited-file diagnostic and the hover would still pay their
  own fresh-checker work, as Go's do on their goroutines.
- **What remains** is the serial sync part, as in Go: snapshot clone ~4 ms and checker creation ~3 ms
  for whichever request comes first after an edit. Requests behind it would wait ~4–8 ms on the
  dispatch thread. Go waits ~12 ms here, because Go's clone is 8–10 ms.
- **Expected tokens/hints latency with worker threads:** ~5–12 ms, against Go's 17. This is a guess
  from the parts.
- **The single writer thread stays.** An answer that finishes while a 4 MB completion is being
  marshalled waits for it, in both servers.
- **Worker threads would not help completions** in this bench. Completions never wait behind
  anything. Their gap is their own work and the marshal.

### E. T3 Code's own files as the edited file (pulls=all, other 5 files unchanged, 3 runs; ChatView 2 runs)

| edited file | kind | fork p50 | Go p50 | notes |
|---|---|---|---|---|
| `apps/server/src/bootstrap.ts` (6.8 KB) | completion "c" / "M" | 53.0 / 36.0 | 47.8 / 30.8 | fork own work 28.5 / 18.6 vs Go 20.7 / 14.0; answers 2.2 / 1.4 MB |
| | completion member | 6.2–7.3 | 10.0–11.6 | |
| | diagnostic, edited | 16.6 | 23.3 | fork own 12.2 vs 17.2 |
| | diagnostic, other / tokens / hints | 17.0 / 27.2 / 29.0 | 6.8 / 21.6 / 22.0 | the fork's are queue waits (17–28 ms); own work 0.0 / 9.8 / 1.1 vs Go 0.3 / 14.9 / 14.8 |
| | program update per edit | 1.7 | 4.3 | |
| `apps/web/src/components/BranchPicker.tsx` (10 KB) | completion "c" / "M" | 86.0 / 55.3 | 335.4 / 272.1 | Go clones the snapshot with a full `.tsx` program rebuild inside the completion |
| | completion member | 11.2–16.1 | 59.8–103.5 | |
| | diagnostic, edited | 65.5 (488 answered) | 168.6 (12 answered; 480 of all 1,107 diagnostic pulls cancelled) | Go cannot keep up at 120 ms/key, so the editor cancels |
| | diagnostic, other / tokens / hints | 67.3 / 88.6 / 89.1 | 58.1 / 99.5 / 88.3 | the fork waits ~66–88 behind the edited diagnostic (55 ms own) |
| | program update per edit | 4.7 | 42.1 (max 134) | the fork reuses the program on `.tsx` edits, Go does not |
| `apps/web/src/components/ChatView.tsx` (476 KB) | completion | 11,042 (p95 24,386) | 107.8 | **the fork's queue never drains** |
| | hover / diagnostic / tokens / hints | 18.7 s / 18.8 s / 19.5 s / 19.6 s | 2.0 / 77.8 / 1,730 / 1,651 | Go answered 330 of 738 diagnostic pulls and 74 of 82 token pulls (74 of 82 hint pulls) as cancelled. The fork: 10 of 738, and 0. |

- **On the server `.ts` file** the picture is the same as on the vendored file, at smaller scale.
  Requests have less own work and the queue waits are shorter.
- **On a small web `.tsx` component the fork is faster than Go for everything except the queued
  pulls.** Go's program update on a `.tsx` edit is 42 ms against the fork's 4.7 ms, because Go's
  ReuseProgram refuses files with a JSX runtime import. Go pays that update inside whichever request
  comes first, often a completion.
- **On ChatView.tsx the fork's single thread collapses:**
  - Each edited-file diagnostic pull costs ~780 ms of the fork's own work (Go's: ~1,675 ms) and each
    tokens pull ~784 ms (Go's: ~1,648 ms). A new pull of each kind arrives every keystroke or every
    500 ms.
  - `$/cancelRequest` only cancels requests that `dispatch_request` has already registered
    (`Server::cancel_request`, server.rs:2091). It looks only in `pending_client_requests`.
  - A request cancelled while it still sits in the queue therefore runs in full later. In Go, the
    dispatch loop registers every request within milliseconds (the sync part is short), so the
    editor's cancels reach the async work, and the async work stops at its next context check.
  - The fork's backlog grows for the whole session.

## Fix candidates, ranked

1. **Drop requests that are cancelled while queued.**
   - Single thread. In the reader, when `$/cancelRequest` names a request still in `request_queue`,
     mark it. Then `dispatch_request` answers `RequestCancelled` without running it.
   - The answer is the one Go gives when its async work sees the cancel. Cancel timing is already
     nondeterministic in Go.
   - Expected gain on ChatView-sized files: from an unbounded backlog (11–35 s) to roughly one
     request's own time (~0.8 s). This is a guess: it assumes the editor keeps cancelling superseded
     pulls, as VS Code and this bench do.
   - On the vendored file it changes little (21 of 1,845 diagnostic pulls were answered as cancelled).
   - A further step for the same files would be to stop the running request at its next context
     check (the checker already checks `ctx`).
2. **Run query requests on worker threads** (tokens, inlay hints, other-file diagnostics, hover,
   completion).
   - Needs threads. Removes ~60–70 ms of the 72–75 ms for tokens, hints and other-file diagnostics in
     pulls=all, and 30–40 ms of the 41 ms in pulls=no-diagnostics. On a small web `.tsx` component it
     removes ~66–88 ms.
   - It does not remove the snapshot clone (~4 ms), checker creation (~3 ms), each request's own
     work, or the writer FIFO.
   - Expected tokens and hints with workers: ~5–12 ms against Go's 17 (a guess from the parts).
   - **Single-thread stopgap (a guess):** when the dispatch thread dequeues, take cheap pulls
     (tokens, hints, other-file diagnostics: under 3 ms of own work here) before the expensive ones
     queued at the same keystroke (the edited-file diagnostic, hover). Expected −35 to −60 ms on the
     cheap pulls, +3 to +5 ms on the expensive ones. Go's answer order is already concurrent, so no
     answer changes, only its timing.
3. **Make the auto-import fix sort cheap.** Single thread.
   - Take `use_case_sensitive_file_names()` and the importing file name out of the comparator. They
     go through five dynamic `Fs` layers on every call.
   - Replace `relative_path_within_directory(get_directory_path(..))` with an allocation-free
     starts-with-directory test, as Go's `CaseSensitivity.StartsWithDirectory` does.
   - Or compute each fix's sort keys once before sorting. The comparator stays the same, so the order
     stays Go's.
   - Expected: the sort goes from 37.5 to ~15 ms. That is about −20 ms on "c" (117 → ~97 with
     diagnostics, against Go's 77) and −8 ms on "M". It also applies on the server `.ts` file (fork
     own work 28.5 vs 20.7).
4. **Marshal big answers in one pass.**
   - This runs on the writer thread, so it delays no other handling. It shortens only the big answer
     itself and the answers queued behind it.
   - `json_marshal_dyn` re-tokenizes the marshalled message (`JsonDecoder::read_token` 7.8 ms,
     `write_value_from` 12.7 ms of 25.5 on a 4 MB answer).
   - Expected −8 to −12 ms on global completions. Go's warm marshal is 13 ms.
5. **Start the auto-import warm earlier.**
   - Today it waits for 50 ms of quiet and runs serially on the dispatch thread, so at typing speed
     it never finishes. Every first completion after an edit builds the registry inline (~110 ms) and
     waits 7–17 ms for the cancelled warm.
   - On the single thread, start it at once (`IdleStart::AtOnce`), since it already preempts on a new
     message. Off the thread, run it on a worker (that needs threads).
   - Expected: up to −110 ms on a first completion after the user pauses briefly. Nothing in steady
     typing. Go's own warm wins this race only some of the time (5 of 15 runs).
6. **Not a gap against Go:**
   - The fresh-checker `get_with_alternative_containers` / `get_exports_of_module` cost (24–33 ms per
     new checker, in hover and edited diagnostics). Go pays it too and is slower at it. Only running
     it off the dispatch thread hides it (item 2).
   - The cold module-specifier work in the first completion (+35 ms in both servers).

## Reproduce

```
O=~/.cache/perf-rearch/lsp-profile1; P=~/.cache/perf-rearch/projects/t3code
systemd-run --user --collect --wait --pipe --slice=app-batch.slice --nice=10 --property=TasksMax=24000 \
  --setenv=PATH=$PATH --setenv=HOME=$HOME --working-directory=$PWD flock ~/.cache/ts-rust-heavy.lock \
  node scripts/lspdiff/lspdiff.mjs bench --project $P --files 6 --runs 5 --interval 120 --pulls all \
  --modes concurrent --trace --go ~/.local/bin/tsgo-lsp-oracle-3e9236361fa9 \
  --rs ~/.cache/perf-rearch/closeout/bins/tsgo-head-3733fe44b-goport --out $O/m2-all
node scripts/lspdiff/trace-split.mjs $O/m2-all/t3code-bench/trace-rs-*.json
# profile: --only rs --rs $O/tools/samply-wrap.sh with SAMPLY_BIN=<fork bin> SAMPLY_DIR=<dir>, then
python3 -I scripts/lspdiff/profile-windows.py --pair <trace.json> <profile.json.gz> --kind 'completion c$' --under pdqsort_cmp_func
```
