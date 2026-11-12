# Close-out of the code-quality phase (lane C2, 2026-11-12)

The code-quality phase ran from `e81a14807` (the end of the perf phase: C2 follow-up merged) to the merged head
`3733fe44b` (`54a2a38d5` plus a plan note; all lanes in, B10 last). This report has the final evidence:
- output is still identical to Go at the pin `3e9236361fa9`;
- every timing is still ≤ Go;
- the quality before and after.

No compiler source changed in this lane. The one tool fix is listed under "Timing".

## Parity

- **Run:** `scripts/perf-rearch/parity.sh full <bin> --base base-current`.
  - bin: `tsgo-head-3733fe44b-rel`, a release build on nightly, as the brief says, under the heavy-run rule.
  - base: `b8-e2941610d-full`.
  - out: `~/.cache/perf-rearch/parity/runs/c2close-3733fe44b-full`.
- **Result: PARITY OK.**

  > compared 188,055 items (186,324 base items that a full run must have): 0 lost, 0 not run, 0 known-bad not as
  > pinned, 0 new items not good, 0 crashes on good items, 36 grandfathered, 0 stale grandfather entries.

| tier | result |
|---|---|
| cli | 179 MATCH |
| tests | 140,037 PASS, 1,731 IGNORED (141,768 names) |
| lsp | 3,316 MATCH, 29 GO-BROKEN |
| api | 73 MATCH |
| fuzz | 29,955 MATCH, 8 GO-BROKEN, 1 JS-AGREES, 36 DIFF (all grandfathered: Go and tsc-rs both time out on deep nesting, or tsc-rs reproduces Go's crash #65078/#65079 on purpose) |
| corpus | 12,727 MATCH |

**New items since base-current** (37, all good):
- **api, 10 MATCH:**
  - B10: `ms-paths-fold-insensitive`, `ms-imports-fold-insensitive` and `ms-exports-fold-insensitive`, plus the
    controls `ms-paths-fold-sensitive` and `ms-imports-fold-sensitive`;
  - B9: `batch-page-cap-int64`, `format-insertion-past-int32`, `positions-index-int64`,
    `raw-options-target-int32` and `typeacq-empty-include`.
- **cli, 6 MATCH:**
  - F5: `resolution-mode-source-phase/cts/{noemit,emit,decl}`;
  - B9: `buildinfo-int-range/code-int32/{noemit,emit,incr}`.
- **tests, 21 PASS:** B9's `go_baselines` int-width and build-info tests, such as
  `api_session_int_width_test::format_node_for_insertion_position_past_i32` and
  `build_info_corrupt::file_id_past_int32_panics_index_out_of_range`.

No item left MATCH for a referee status in this run: GO-BROKEN 0, GO-PARTITION 0 and JS-AGREES 0 newly good.

Root moves `base-current` to this run.

## Timing (window 2): Go, phase start, head

### Result

**Every cell is at or under Go: 90 cells against Go plus the 3 THP-guard cells, 0 FAIL.** The head is flat
against the phase start (`e81a14807`) everywhere, within the spreads. One cell is "worse" beyond its spread, by
less than 1%, with equal instructions (see below). Every output is the same on all sides.

| group | head/Go | e81a14807/Go |
|---|---|---|
| CLI noemit | 0.36–0.65× | 0.35–0.66× |
| CLI emit | 0.37–0.68× | 0.37–0.68× |
| CLI emit-js | 0.40–0.66× | 0.36–0.67× |
| CLI emit-cjs | 0.50–0.59× | 0.49–0.60× |
| CLI `--singleThreaded` | 0.48–0.76× | 0.49–0.77× |
| icount part (wall, single-threaded) | 0.41–0.61× | 0.41–0.61× |
| `-b` cold | 0.41–0.68× | 0.41–0.67× |
| `-b` warm | 0.21–0.93× | 0.22–1.14× (query, 8.2 ms against 7.1 ms; 7.0 ms against 7.5 ms in qbuild, n=9) |
| incremental warm | 0.35–0.54× | 0.35–0.54× |
| incr-edit | 0.45–0.68× | 0.45–0.73× |
| LSP concurrent typing (120/250/500 ms per key) | 0.28–0.68× | 0.28–0.69× |
| LSP probes (.ts, .tsx) | 0.13–0.26× | 0.13–0.27× |
| LSP ttfd (umami) | **0.95×** (280.6 against 293.9 ms) | 0.96× |
| open12 (hono) | **0.90×** (104.1 against 116.1 ms) | 0.91× |
| THP guard part (noemit, guard on) | 0.37–0.63× | — |

The closest cells to Go were already the closest at the end of the perf phase:
- ttfd: 0.93× then, 0.95× now;
- open12: 0.88× then, 0.90× now;
- `-b` warm query: 0.82× in P3, 0.92–0.93× now.

The phase-start side gets the same ratios in this run (0.96×, 0.91× and 0.93×), so the drift is the run's Go
times, not the head.

### Method

- **Bins:** both tsc-rs sides were built by this lane with `--profile goport` on rustc 1.95.0 (`.comment`
  checked): `tsgo-e81-e81a14807-goport` (from a detached worktree, since removed) and `tsgo-head-3733fe44b-goport`.
  Go is the oracle at the pin.
- **Run:** `window2.sh <e81 bin> <head bin> ~/.cache/perf-rearch/closeout/w2`, all parts, `RUNS=3`, under the
  heavy-run rule.
  - Every run waits for a 1-minute load under 1.5 and starts at least 300 s after boot.
  - Sides are interleaved run by run, and a run is repeated when the load or the other processes' CPU goes over
    the limit.
- **Top-up to 5 runs** (`topup.sh` in the lane dir) on every cell where head was within 10% of Go or slower than
  `e81a14807` by more than its spread:
  - emit-js on hono, query and t3-server;
  - `-b` on zod, query and t3-server;
  - emit-cjs and incr on umami;
  - ttfd;
  - open12, which was at 0.90×;
  - THP on zod and vscode.

  qbuild already has 9 runs per side.
- **Verdicts:** `verdict.py` in the lane dir.
  - Against Go: head's wall median ≤ Go's.
  - Against `e81a14807`: "worse" means head's min is above `e81a14807`'s max, and "better" means head's max is
    below its min.
- **Raw data:** `~/.cache/perf-rearch/closeout/w2/<part>/runs.jsonl`. The table is `verdict-final.md` and the
  bench's own table is `w2/gap.md`.

### Three tool bugs, fixed in `bench.py` before these numbers

1. **`3c6a743fe`, read-only inputs.** Since the inputs were made read-only (2026-11-10), `scratch_copy`'s
   `rsync -a`/`cp -a` copied the read-only modes. The first emit cell then failed with EACCES inside the copy.
   Copies are now owner-writable.
2. **`91861a5bc`, repeats skipped the untimed setup.** `gated_run` repeated a run without redoing the scenario's
   setup, so a repeated `build-cold` ran on the copy the first attempt had built.
   - Effect: a no-op `-b`. hono measured 34–86 ms against 176/333 ms, which first showed as a false FAIL (head
     2.05× Go); query measured 7 ms.
   - A repeated incr-edit or emit likewise ran on the state left behind.
   - Setup now runs before each repeat. The 24 affected records (repeats of build-cold, incr-edit and emit) were
     dropped and measured again; the old files are kept as `runs.jsonl.before-repeat-fix`.
   - The perf-phase window 2 (`~/.cache/perf-rearch/window2/run`) has the same bug but was barely affected.
     - Its one repeated build-cold is ts-pattern main: 28 ms against 30 and 30 ms, because ts-pattern's `-b`
       rebuilds anyway.
     - Its 14 repeated incr-edit runs are in line with the unrepeated ones (umami go 1,620/1,528 ms against
       1,332–1,349 ms; hono head 210 against 158–161 ms), so none was a no-op.
     - Its conclusions stand.
3. **`efd98d2d3`, incr-edit resume.** incr-edit kept the edited file's original text only when it created the
   copy, so a resumed bench died with KeyError. The original now comes from the read-only input.

### Cells over 2% against the phase start, or "worse"

- **`-b` warm zod is "worse":** 473.8 ms (471.4..487.8) against 469.4 ms (464.7..470.7), n=5, +0.9%.
  - Instructions are equal (14.456G against 14.455G, +0.007%), and the cold build is flat (+0.7%, overlapping).
  - This "warm" build rechecks everything because zod's `-b` reports errors (rc 2), so it repeats the cold
    build's work. The +0.7 ms gap between the ranges is wall noise, not work.
  - No bisect: it does not reproduce in instructions.
- **Over 2% by median, all flat with overlapping ranges:**
  - t3-server emit-js +9.9% and noemit +4.1%, t3-web noemit +9.3%, t3-server `-b` warm +3.0%;
  - effect incremental warm +3.2%, ts-pattern single +2.1%.

  Each has equal or lower instructions (head/e81 0.9985–1.0006). The T3 Code cells are bimodal in wall time
  (about 2.0 s or 2.2 s for t3-web noemit, 3.9 s or 4.3 s for t3-server) on both sides.
- **THP guard:** guard on is 2–4% slower than guard off on vscode and zod (5.445 against 5.305 s; 460.8 against
  443.8 ms). This was already the case at the end of the perf phase (vscode 6.29 against 5.82 s), and THP is off
  on this VM. It is not a phase change, and both sides are well under Go.

### Full table

| part | scenario | project | Go | e81a14807 | head | head/Go | head/e81 | verdict | vs e81 | output |
|---|---|---|---|---|---|---|---|---|---|---|
| cli-core | emit | effect | 1.438 s (1.437 s..1.465 s) n=3 | 762.3 ms (757.3 ms..767.2 ms) n=3 | 761.2 ms (760.5 ms..779.0 ms) n=3 | 0.53× | 0.999× | OK | flat | same |
| cli-core | emit | hono | 324.2 ms (323.1 ms..325.8 ms) n=3 | 163.6 ms (162.5 ms..175.3 ms) n=3 | 165.1 ms (164.5 ms..165.5 ms) n=3 | 0.51× | 1.009× | OK | flat | same |
| cli-core | emit | query | 96.7 ms (95.5 ms..96.9 ms) n=3 | 46.9 ms (46.4 ms..47.5 ms) n=3 | 46.9 ms (46.0 ms..46.9 ms) n=3 | 0.48× | 0.999× | OK | flat | same |
| cli-core | emit | t3-server | 11.535 s (11.257 s..11.705 s) n=3 | 4.257 s (4.137 s..4.355 s) n=3 | 4.289 s (4.254 s..4.416 s) n=3 | 0.37× | 1.008× | OK | flat | same |
| cli-core | emit | t3-web | 4.366 s (4.243 s..4.539 s) n=3 | 2.281 s (2.238 s..2.340 s) n=3 | 2.296 s (2.223 s..2.418 s) n=3 | 0.53× | 1.007× | OK | flat | same |
| cli-core | emit | ts-pattern | 107.2 ms (102.2 ms..110.0 ms) n=3 | 63.4 ms (62.1 ms..63.7 ms) n=3 | 61.3 ms (60.0 ms..61.9 ms) n=3 | 0.57× | 0.966× | OK | better | same |
| cli-core | emit | umami | 1.408 s (1.405 s..1.436 s) n=3 | 848.0 ms (844.8 ms..858.8 ms) n=3 | 842.2 ms (831.0 ms..844.6 ms) n=3 | 0.60× | 0.993× | OK | better | same |
| cli-core | emit | vscode | 11.729 s (11.648 s..12.017 s) n=3 | 8.032 s (7.976 s..8.039 s) n=3 | 8.001 s (7.677 s..8.035 s) n=3 | 0.68× | 0.996× | OK | flat | same |
| cli-core | emit | zod | 1.062 s (1.047 s..1.073 s) n=3 | 490.6 ms (487.9 ms..493.7 ms) n=3 | 492.5 ms (482.5 ms..497.6 ms) n=3 | 0.46× | 1.004× | OK | flat | same |
| cli-core | emit-js | effect | 1.223 s (1.216 s..1.227 s) n=3 | 652.0 ms (641.3 ms..689.6 ms) n=3 | 647.8 ms (647.2 ms..658.8 ms) n=3 | 0.53× | 0.994× | OK | flat | same |
| cli-core | emit-js | hono | 298.8 ms (293.9 ms..315.5 ms) n=5 | 146.4 ms (144.9 ms..148.1 ms) n=5 | 148.5 ms (144.0 ms..159.9 ms) n=5 | 0.50× | 1.015× | OK | flat | same |
| cli-core | emit-js | query | 95.7 ms (85.6 ms..99.6 ms) n=5 | 47.2 ms (45.1 ms..49.1 ms) n=5 | 46.7 ms (45.1 ms..49.1 ms) n=5 | 0.49× | 0.990× | OK | flat | same |
| cli-core | emit-js | t3-server | 10.839 s (10.252 s..11.299 s) n=5 | 3.944 s (3.884 s..4.363 s) n=5 | 4.335 s (3.870 s..4.427 s) n=5 | 0.40× | 1.099× | OK | flat | same |
| cli-core | emit-js | t3-web | 4.042 s (3.911 s..4.055 s) n=3 | 2.053 s (2.030 s..2.058 s) n=3 | 2.056 s (2.020 s..2.096 s) n=3 | 0.51× | 1.001× | OK | flat | same |
| cli-core | emit-js | ts-pattern | 107.3 ms (104.9 ms..111.2 ms) n=3 | 59.0 ms (58.5 ms..60.9 ms) n=3 | 59.4 ms (58.6 ms..60.3 ms) n=3 | 0.55× | 1.008× | OK | flat | same |
| cli-core | emit-js | umami | 1.407 s (1.406 s..1.409 s) n=3 | 806.0 ms (799.0 ms..807.2 ms) n=3 | 801.5 ms (796.8 ms..805.7 ms) n=3 | 0.57× | 0.994× | OK | flat | same |
| cli-core | emit-js | vscode | 11.643 s (11.572 s..11.683 s) n=3 | 7.743 s (7.282 s..7.896 s) n=3 | 7.667 s (7.513 s..7.670 s) n=3 | 0.66× | 0.990× | OK | flat | same |
| cli-core | emit-js | zod | 1.060 s (1.047 s..1.071 s) n=3 | 498.8 ms (491.4 ms..512.9 ms) n=3 | 496.6 ms (494.6 ms..501.6 ms) n=3 | 0.47× | 0.996× | OK | flat | same |
| cli-core | noemit | effect | 1.136 s (1.133 s..1.172 s) n=3 | 647.0 ms (620.2 ms..662.0 ms) n=3 | 634.9 ms (633.2 ms..638.7 ms) n=3 | 0.56× | 0.981× | OK | flat | same |
| cli-core | noemit | hono | 278.7 ms (274.7 ms..285.9 ms) n=3 | 122.8 ms (121.1 ms..126.6 ms) n=3 | 122.9 ms (121.8 ms..123.0 ms) n=3 | 0.44× | 1.001× | OK | flat | same |
| cli-core | noemit | query | 91.6 ms (87.0 ms..95.9 ms) n=3 | 42.6 ms (42.1 ms..45.2 ms) n=3 | 43.3 ms (42.3 ms..43.9 ms) n=3 | 0.47× | 1.018× | OK | flat | same |
| cli-core | noemit | t3-server | 11.157 s (10.928 s..11.236 s) n=3 | 3.909 s (3.832 s..4.192 s) n=3 | 4.071 s (4.038 s..4.115 s) n=3 | 0.36× | 1.041× | OK | flat | same |
| cli-core | noemit | t3-web | 4.068 s (3.994 s..4.216 s) n=3 | 2.007 s (2.006 s..2.202 s) n=3 | 2.194 s (2.021 s..2.202 s) n=3 | 0.54× | 1.093× | OK | flat | same |
| cli-core | noemit | ts-pattern | 109.7 ms (105.4 ms..111.2 ms) n=3 | 55.9 ms (55.4 ms..58.5 ms) n=3 | 56.7 ms (56.6 ms..58.9 ms) n=3 | 0.52× | 1.013× | OK | flat | same |
| cli-core | noemit | umami | 1.313 s (1.296 s..1.344 s) n=3 | 708.3 ms (708.1 ms..729.7 ms) n=3 | 720.1 ms (703.8 ms..733.7 ms) n=3 | 0.55× | 1.017× | OK | flat | same |
| cli-core | noemit | vscode | 9.124 s (9.061 s..9.173 s) n=3 | 6.053 s (5.977 s..6.076 s) n=3 | 5.926 s (5.597 s..6.021 s) n=3 | 0.65× | 0.979× | OK | flat | same |
| cli-core | noemit | zod | 1.011 s (999.8 ms..1.025 s) n=3 | 455.1 ms (453.9 ms..462.6 ms) n=3 | 455.6 ms (450.5 ms..462.9 ms) n=3 | 0.45× | 1.001× | OK | flat | same |
| cli-rest | build-cold | effect | 1.460 s (1.437 s..1.488 s) n=3 | 855.9 ms (842.9 ms..872.3 ms) n=3 | 868.8 ms (845.5 ms..869.8 ms) n=3 | 0.60× | 1.015× | OK | flat | same |
| cli-rest | build-cold | hono | 332.8 ms (323.1 ms..333.4 ms) n=3 | 177.2 ms (177.1 ms..180.8 ms) n=3 | 177.4 ms (176.1 ms..181.4 ms) n=3 | 0.53× | 1.001× | OK | flat | same |
| cli-rest | build-cold | query | 99.7 ms (97.9 ms..102.9 ms) n=5 | 50.8 ms (50.2 ms..65.4 ms) n=5 | 50.0 ms (49.3 ms..52.8 ms) n=5 | 0.50× | 0.985× | OK | flat | same |
| cli-rest | build-cold | t3-server | 10.396 s (10.235 s..11.328 s) n=5 | 4.297 s (3.867 s..4.537 s) n=5 | 4.272 s (3.948 s..4.527 s) n=5 | 0.41× | 0.994× | OK | flat | same |
| cli-rest | build-cold | t3-web | 4.265 s (4.250 s..4.406 s) n=3 | 2.183 s (2.166 s..2.186 s) n=3 | 2.175 s (2.175 s..2.175 s) n=3 | 0.51× | 0.996× | OK | flat | same |
| cli-rest | build-cold | ts-pattern | 49.1 ms (48.8 ms..50.8 ms) n=3 | 28.7 ms (28.4 ms..29.1 ms) n=3 | 29.1 ms (27.8 ms..30.3 ms) n=3 | 0.59× | 1.016× | OK | flat | same |
| cli-rest | build-cold | umami | 1.330 s (1.314 s..1.333 s) n=3 | 791.1 ms (767.1 ms..808.1 ms) n=3 | 793.2 ms (775.5 ms..798.2 ms) n=3 | 0.60× | 1.003× | OK | flat | same |
| cli-rest | build-cold | vscode | 11.765 s (11.451 s..11.832 s) n=3 | 7.898 s (7.878 s..8.007 s) n=3 | 7.986 s (7.648 s..8.167 s) n=3 | 0.68× | 1.011× | OK | flat | same |
| cli-rest | build-cold | zod | 994.3 ms (993.1 ms..1.031 s) n=5 | 467.1 ms (465.1 ms..476.7 ms) n=5 | 470.2 ms (465.7 ms..474.1 ms) n=5 | 0.47× | 1.007× | OK | flat | same |
| cli-rest | build-warm | effect | 176.1 ms (162.4 ms..178.1 ms) n=3 | 69.5 ms (68.8 ms..70.2 ms) n=3 | 69.5 ms (69.0 ms..76.9 ms) n=3 | 0.39× | 1.000× | OK | flat | same |
| cli-rest | build-warm | hono | 90.6 ms (86.5 ms..92.5 ms) n=3 | 33.0 ms (32.6 ms..34.0 ms) n=3 | 33.4 ms (33.1 ms..33.9 ms) n=3 | 0.37× | 1.011× | OK | flat | same |
| cli-rest | build-warm | query | 7.1 ms (6.8 ms..7.9 ms) n=5 | 8.2 ms (6.9 ms..8.7 ms) n=5 | 6.7 ms (6.6 ms..7.9 ms) n=5 | 0.93× | 0.818× | OK | flat | same |
| cli-rest | build-warm | t3-server | 11.298 s (10.247 s..11.359 s) n=5 | 3.976 s (3.789 s..4.310 s) n=5 | 4.094 s (3.866 s..4.449 s) n=5 | 0.36× | 1.030× | OK | flat | same |
| cli-rest | build-warm | t3-web | 279.6 ms (274.3 ms..282.5 ms) n=3 | 61.3 ms (60.7 ms..62.3 ms) n=3 | 59.9 ms (59.3 ms..60.1 ms) n=3 | 0.21× | 0.976× | OK | better | same |
| cli-rest | build-warm | ts-pattern | 51.1 ms (48.6 ms..52.0 ms) n=3 | 29.9 ms (27.9 ms..30.1 ms) n=3 | 29.9 ms (28.8 ms..30.9 ms) n=3 | 0.59× | 0.999× | OK | flat | same |
| cli-rest | build-warm | umami | 1.335 s (1.329 s..1.354 s) n=3 | 794.6 ms (785.1 ms..800.5 ms) n=3 | 799.9 ms (792.2 ms..801.5 ms) n=3 | 0.60× | 1.007× | OK | flat | same |
| cli-rest | build-warm | vscode | 11.379 s (11.131 s..11.529 s) n=3 | 7.875 s (7.677 s..8.003 s) n=3 | 7.900 s (7.825 s..8.038 s) n=3 | 0.69× | 1.003× | OK | flat | same |
| cli-rest | build-warm | zod | 998.1 ms (991.3 ms..1.021 s) n=5 | 469.4 ms (464.7 ms..470.7 ms) n=5 | 473.8 ms (471.4 ms..487.8 ms) n=5 | 0.47× | 1.009× | OK | worse | same |
| cli-rest | emit-cjs | hono | 280.5 ms (278.0 ms..281.4 ms) n=3 | 146.4 ms (143.3 ms..147.6 ms) n=3 | 140.2 ms (136.2 ms..141.7 ms) n=3 | 0.50× | 0.958× | OK | better | same |
| cli-rest | emit-cjs | query | 98.5 ms (96.0 ms..98.5 ms) n=3 | 48.4 ms (46.0 ms..49.2 ms) n=3 | 48.8 ms (46.9 ms..54.4 ms) n=3 | 0.50× | 1.008× | OK | flat | same |
| cli-rest | emit-cjs | t3-web | 3.998 s (3.988 s..4.102 s) n=3 | 2.302 s (2.292 s..2.304 s) n=3 | 2.291 s (2.276 s..2.293 s) n=3 | 0.57× | 0.995× | OK | flat | same |
| cli-rest | emit-cjs | ts-pattern | 101.8 ms (101.8 ms..104.2 ms) n=3 | 60.7 ms (59.9 ms..64.5 ms) n=3 | 59.9 ms (57.5 ms..61.5 ms) n=3 | 0.59× | 0.985× | OK | flat | same |
| cli-rest | emit-cjs | umami | 1.477 s (1.472 s..1.503 s) n=5 | 814.0 ms (810.0 ms..820.3 ms) n=5 | 827.8 ms (808.9 ms..831.1 ms) n=5 | 0.56× | 1.017× | OK | flat | same |
| cli-rest | incr-warm | effect | 159.8 ms (155.8 ms..189.8 ms) n=3 | 61.7 ms (60.3 ms..64.5 ms) n=3 | 63.7 ms (61.3 ms..63.9 ms) n=3 | 0.40× | 1.032× | OK (load) | flat | same |
| cli-rest | incr-warm | hono | 87.4 ms (79.7 ms..93.2 ms) n=3 | 30.4 ms (29.6 ms..39.8 ms) n=3 | 30.8 ms (30.5 ms..32.4 ms) n=3 | 0.35× | 1.014× | OK (load) | flat | same |
| cli-rest | incr-warm | query | 63.7 ms (58.8 ms..67.3 ms) n=3 | 23.9 ms (22.6 ms..26.6 ms) n=3 | 24.3 ms (24.2 ms..24.9 ms) n=3 | 0.38× | 1.016× | OK | flat | same |
| cli-rest | incr-warm | t3-server | 579.8 ms (566.4 ms..582.0 ms) n=3 | 248.8 ms (245.5 ms..250.6 ms) n=3 | 244.6 ms (241.6 ms..247.2 ms) n=3 | 0.42× | 0.983× | OK | flat | same |
| cli-rest | incr-warm | t3-web | 587.9 ms (578.8 ms..688.3 ms) n=3 | 284.4 ms (281.6 ms..314.7 ms) n=3 | 280.3 ms (276.4 ms..314.8 ms) n=3 | 0.48× | 0.986× | OK | flat | same |
| cli-rest | incr-warm | ts-pattern | 40.9 ms (40.8 ms..42.9 ms) n=3 | 22.1 ms (22.0 ms..22.3 ms) n=3 | 22.2 ms (22.0 ms..22.9 ms) n=3 | 0.54× | 1.007× | OK | flat | same |
| cli-rest | incr-warm | umami | 432.3 ms (416.0 ms..435.2 ms) n=5 | 213.1 ms (209.7 ms..225.3 ms) n=5 | 212.0 ms (209.2 ms..221.7 ms) n=5 | 0.49× | 0.995× | OK | flat | same |
| cli-rest | incr-warm | vscode | 2.010 s (1.920 s..2.023 s) n=3 | 814.2 ms (776.3 ms..850.6 ms) n=3 | 809.5 ms (767.2 ms..823.2 ms) n=3 | 0.40× | 0.994× | OK (load) | flat | same |
| cli-rest | incr-warm | zod | 125.4 ms (125.0 ms..133.4 ms) n=3 | 51.7 ms (47.3 ms..52.7 ms) n=3 | 50.4 ms (50.2 ms..53.4 ms) n=3 | 0.40× | 0.976× | OK | flat | same |
| cli-rest | single | effect | 2.559 s (2.466 s..2.582 s) n=3 | 1.719 s (1.709 s..1.725 s) n=3 | 1.720 s (1.698 s..1.740 s) n=3 | 0.67× | 1.001× | OK | flat | same |
| cli-rest | single | hono | 397.3 ms (393.3 ms..407.5 ms) n=3 | 240.6 ms (238.8 ms..244.1 ms) n=3 | 238.6 ms (236.7 ms..247.8 ms) n=3 | 0.60× | 0.992× | OK | flat | same |
| cli-rest | single | query | 135.9 ms (135.6 ms..139.6 ms) n=3 | 93.3 ms (91.5 ms..93.9 ms) n=3 | 92.5 ms (92.3 ms..93.7 ms) n=3 | 0.68× | 0.991× | OK | flat | same |
| cli-rest | single | t3-server | 14.792 s (14.687 s..14.808 s) n=3 | 7.203 s (7.170 s..7.397 s) n=3 | 7.140 s (7.112 s..7.241 s) n=3 | 0.48× | 0.991× | OK | flat | same |
| cli-rest | single | t3-web | 7.564 s (7.381 s..7.679 s) n=3 | 4.839 s (4.802 s..4.935 s) n=3 | 4.877 s (4.829 s..4.889 s) n=3 | 0.64× | 1.008× | OK | flat | same |
| cli-rest | single | ts-pattern | 113.9 ms (113.7 ms..114.0 ms) n=3 | 69.5 ms (68.7 ms..71.1 ms) n=3 | 71.0 ms (69.6 ms..72.0 ms) n=3 | 0.62× | 1.021× | OK | flat | same |
| cli-rest | single | umami | 2.422 s (2.385 s..2.462 s) n=3 | 1.602 s (1.578 s..1.631 s) n=3 | 1.614 s (1.590 s..1.646 s) n=3 | 0.67× | 1.008× | OK | flat | same |
| cli-rest | single | vscode | 26.580 s (26.470 s..27.440 s) n=3 | 20.463 s (20.398 s..20.532 s) n=3 | 20.235 s (20.173 s..20.593 s) n=3 | 0.76× | 0.989× | OK | flat | same |
| cli-rest | single | zod | 2.215 s (2.162 s..2.229 s) n=3 | 1.225 s (1.216 s..1.271 s) n=3 | 1.242 s (1.221 s..1.260 s) n=3 | 0.56× | 1.014× | OK | flat | same |
| icount | icount | effect | 3.123 s (3.025 s..3.222 s) n=2 | 1.762 s (1.740 s..1.784 s) n=2 | 1.733 s (1.733 s..1.734 s) n=2 | 0.56× | 0.984× | OK | better | same |
| icount | icount | hono | 501.2 ms (493.3 ms..509.1 ms) n=2 | 239.3 ms (236.6 ms..242.0 ms) n=2 | 241.3 ms (236.5 ms..246.1 ms) n=2 | 0.48× | 1.009× | OK | flat | same |
| icount | icount | query | 151.6 ms (150.3 ms..153.0 ms) n=2 | 92.2 ms (90.6 ms..93.7 ms) n=2 | 91.4 ms (91.1 ms..91.6 ms) n=2 | 0.60× | 0.991× | OK | flat | same |
| icount | icount | t3-server | 17.482 s (17.125 s..17.838 s) n=2 | 7.244 s (7.117 s..7.371 s) n=2 | 7.133 s (7.129 s..7.136 s) n=2 | 0.41× | 0.985× | OK | flat | same |
| icount | icount | t3-web | 9.461 s (9.144 s..9.778 s) n=2 | 4.767 s (4.759 s..4.775 s) n=2 | 4.769 s (4.757 s..4.781 s) n=2 | 0.50× | 1.000× | OK | flat | same |
| icount | icount | ts-pattern | 139.3 ms (131.3 ms..147.3 ms) n=2 | 75.8 ms (72.8 ms..78.8 ms) n=2 | 71.4 ms (70.3 ms..72.4 ms) n=2 | 0.51× | 0.942× | OK | better | same |
| icount | icount | umami | 3.126 s (3.100 s..3.152 s) n=2 | 1.618 s (1.616 s..1.620 s) n=2 | 1.606 s (1.604 s..1.609 s) n=2 | 0.51× | 0.993× | OK | better | same |
| icount | icount | vscode | 31.173 s (30.813 s..31.533 s) n=2 | 19.146 s (19.143 s..19.148 s) n=2 | 19.095 s (18.742 s..19.448 s) n=2 | 0.61× | 0.997× | OK | flat | same |
| icount | icount | zod | 2.831 s (2.793 s..2.870 s) n=2 | 1.260 s (1.249 s..1.271 s) n=2 | 1.247 s (1.222 s..1.272 s) n=2 | 0.44× | 0.990× | OK | flat | same |
| incr-edit | incr-edit | hono | 313.7 ms (310.8 ms..324.7 ms) n=3 | 168.0 ms (164.6 ms..168.6 ms) n=3 | 167.3 ms (167.0 ms..172.3 ms) n=3 | 0.53× | 0.996× | OK | flat | same |
| incr-edit | incr-edit | query | 103.4 ms (102.3 ms..110.0 ms) n=3 | 46.6 ms (46.6 ms..47.3 ms) n=3 | 46.9 ms (46.5 ms..47.6 ms) n=3 | 0.45× | 1.006× | OK | flat | same |
| incr-edit | incr-edit | synth85 | 51.8 ms (51.6 ms..53.2 ms) n=3 | 37.6 ms (35.6 ms..38.5 ms) n=3 | 35.4 ms (35.4 ms..38.1 ms) n=3 | 0.68× | 0.943× | OK | flat | same |
| incr-edit | incr-edit | umami | 1.301 s (1.300 s..1.333 s) n=3 | 683.5 ms (678.1 ms..685.9 ms) n=3 | 682.2 ms (678.9 ms..691.9 ms) n=3 | 0.52× | 0.998× | OK | flat | same |
| lsp | concurrent-120 | umami | 333.8 ms (328.9 ms..366.4 ms) n=3 | 92.9 ms (90.1 ms..93.0 ms) n=3 | 91.9 ms (89.8 ms..93.3 ms) n=3 | 0.28× | 0.990× | OK (load) | flat | same |
| lsp | concurrent-250 | umami | 145.9 ms (139.4 ms..151.4 ms) n=3 | 99.3 ms (99.2 ms..100.9 ms) n=3 | 95.0 ms (94.8 ms..97.2 ms) n=3 | 0.65× | 0.957× | OK | better | same |
| lsp | concurrent-500 | umami | 143.7 ms (143.1 ms..146.7 ms) n=3 | 99.5 ms (96.5 ms..99.8 ms) n=3 | 97.3 ms (94.8 ms..99.5 ms) n=3 | 0.68× | 0.978× | OK | flat | same |
| lsp | probe-ts | umami | 12.2 ms (11.7 ms..14.3 ms) n=3 | 3.3 ms (3.1 ms..3.4 ms) n=3 | 3.2 ms (2.9 ms..3.6 ms) n=3 | 0.26× | 0.970× | OK | flat | same |
| lsp | probe-tsx | umami | 58.3 ms (50.3 ms..59.9 ms) n=3 | 7.7 ms (7.4 ms..7.8 ms) n=3 | 7.5 ms (7.5 ms..7.9 ms) n=3 | 0.13× | 0.974× | OK | flat | same |
| lsp | ttfd | umami | 293.9 ms (277.7 ms..337.5 ms) n=5 | 282.5 ms (276.3 ms..308.3 ms) n=5 | 280.6 ms (268.1 ms..300.2 ms) n=5 | 0.95× | 0.993× | OK | flat | same |
| open12 | open12 | honojs__hono | 116.1 ms (111.1 ms..131.2 ms) n=5 | 105.7 ms (98.5 ms..111.6 ms) n=5 | 104.1 ms (101.1 ms..111.0 ms) n=5 | 0.90× | 0.984× | OK | flat | same |
| qbuild | build-cold | query | 100.8 ms (99.0 ms..104.3 ms) n=9 | 50.9 ms (48.1 ms..52.7 ms) n=9 | 51.8 ms (49.3 ms..64.2 ms) n=9 | 0.51× | 1.019× | OK | flat | same |
| qbuild | build-warm | query | 7.5 ms (7.2 ms..9.6 ms) n=9 | 7.0 ms (6.6 ms..7.8 ms) n=9 | 6.9 ms (6.3 ms..7.6 ms) n=9 | 0.92× | 0.980× | OK | flat | same |

THP guard (part thp, noemit; the guard-off side is the same head binary with GOPORT_THP_GUARD=0):

| project | Go | head, guard off | head | head/Go | verdict |
|---|---|---|---|---|---|
| t3-server | 10.466 s (10.177 s..10.603 s) n=3 | 3.824 s (3.818 s..3.965 s) n=3 | 3.822 s (3.821 s..3.826 s) n=3 | 0.37× | OK |
| vscode | 8.618 s (8.266 s..8.819 s) n=5 | 5.305 s (5.268 s..5.381 s) n=5 | 5.445 s (5.233 s..5.599 s) n=5 | 0.63× | OK |
| zod | 1.021 s (1.008 s..1.055 s) n=5 | 443.8 ms (397.4 ms..474.5 ms) n=5 | 460.8 ms (452.5 ms..482.6 ms) n=5 | 0.45× | OK |

## icount, phase start against end

- **Method:** `icount.py` counts user-space instructions. Both sides are `--profile goport` builds on rustc 1.95.0
  (`tsgo-e81-e81a14807-goport`, `tsgo-head-3733fe44b-goport`) with fresh lib blobs (`lib-blobs.sh stale`: clean at
  both revisions).
- **Settings:** a plain program (`--incremental false --composite false --pretty false`), and emit to a scratch
  `--outDir`. 3 runs per side, interleaved.
- **Script:** `scripts/perf-rearch/cleanup/icount_ab.sh` plus a `noemit-mt` scenario (the copy is in the lane dir,
  `~/.cache/perf-rearch/closeout/icount_ab4.sh`). Data: `~/.cache/perf-rearch/closeout/icount.tsv`.

| project | scenario | e81a14807 median (min..max) | head median (min..max) | Δ |
|---|---|---|---|---|
| query | noemit-st | 0.6837G (0.6836G..0.6837G) | 0.6837G (0.6837G..0.6837G) | +0.009% |
| query | emit-st | 0.7193G (0.7193G..0.7193G) | 0.7194G (0.7194G..0.7194G) | +0.015% |
| query | noemit-mt | 0.7772G (0.7766G..0.7774G) | 0.7770G (0.7764G..0.7778G) | -0.019% |
| query | emit-mt | 0.8181G (0.8158G..0.8182G) | 0.8180G (0.8164G..0.8186G) | -0.015% |
| umami | noemit-st | 16.4933G (16.4932G..16.4950G) | 16.4958G (16.4957G..16.4958G) | +0.015% |
| umami | emit-st | 19.0088G (19.0085G..19.0088G) | 19.0177G (19.0176G..19.0178G) | +0.047% |
| umami | noemit-mt | 19.5639G (19.5056G..19.5801G) | 19.6265G (19.5252G..19.6579G) | +0.320% |
| umami | emit-mt | 22.3403G (22.3139G..22.3407G) | 22.2767G (22.2499G..22.3011G) | -0.285% |
| hono | noemit-st | 2.1402G (2.1402G..2.1403G) | 2.1390G (2.1389G..2.1390G) | -0.055% |
| hono | emit-st | 2.8544G (2.8544G..2.8544G) | 2.8541G (2.8540G..2.8541G) | -0.010% |
| hono | noemit-mt | 2.9374G (2.9363G..2.9387G) | 2.9349G (2.9339G..2.9366G) | -0.082% |
| hono | emit-mt | 3.7026G (3.7023G..3.7030G) | 3.6994G (3.6986G..3.6995G) | -0.086% |

- **Single-threaded: every cell is within +0.3%.** The largest is umami emit at +0.047%; hono is −0.01% to
  −0.06%.
- **Multi-threaded:** these counts include the work of waiting, which depends on timing.
  - umami noemit is +0.32%. Its run ranges overlap (e81 19.506..19.580G, head 19.525..19.658G), and its
    single-threaded twin is +0.015%. That is umami's known default-thread variation of about ±0.7%, not a cost.
  - umami emit is −0.29%. Every other multi-threaded cell is −0.02% to −0.09%.

## Quality, phase start against end

- **Run:** `quality.sh report HEAD --base e81a14807` and `quality.sh ranking HEAD --top 60`, both under the
  heavy-run rule.
- **Output:** `~/.cache/perf-rearch/closeout/quality-report/` and `quality-ranking.md`. There is also a
  `--no-clippy` snapshot of `e81a14807` in `quality-e81/`.
- **Baseline:** the phase-start baseline `q0-f853c4dea` measures the same `crates/` tree as `e81a14807`;
  `f853c4dea` is a docs commit on top of it.

| measure | phase start (`q0-f853c4dea` = `e81a14807`) | head `3733fe44b` |
|---|---|---|
| prod functions | 21,871 | 22,294 |
| cognitive > 25 | 713 | **541** |
| cognitive > 50 | 218 | 174 |
| cognitive max / sum | 509 / 93,244 | 456 / 86,980 |
| cyclomatic > 25 | 408 | 348 |
| port bloat: rows with Δcog ≥ 10 | 166 | **10** |
| of those, Δ less residue ≥ 10 | 145 | 4 |
| Δcog ≥ 25 | 26 | 1 |
| Σ positive Δcog | 7,084 | 4,135 |
| own code over cognitive 25 | 117 (Σ excess 1,845) | **0** |
| duplication | 3.58% (17,099 lines, 698 blocks) | **3.16%** (15,033 lines, 637 blocks) |
| clippy, repo config (CI toolchain 1.95.0) | 1 in q0 (`deprecated`), 0 when re-measured on 1.95.0 | **0** |
| clippy, `--force-warn clippy::all` (stable 1.99.1) | 2,120 | **1,132** |
| env names read in shipped code | 21 in q0 (old cfg parser); 19 by rustc's expanded source on the same tree | **11** |
| of them, switches | 8 | 4 |
| unresolved `// Go:` markers (name nothing in any Go tree) | 1,257 | 1,153 |
| hygiene (prod) | unwrap 265, expect 621, panic! 551, TODO 237, allow attr 124 | unwrap 262, expect 620, panic! 541, TODO 233, allow attr 136 |
| dups (Go functions with Rust ports in more than one file family: `b3_markers.py calls` + `f5/dups.py`) | 288 | **244** |

The 10 port-bloat rows with Δcog ≥ 10 at the head (Rust cognitive minus Go's, closures and splits summed into
their row):

| Δcog | Δ less residue | residue | Rust cog | Go cog | Rust function | Go function |
|---:|---:|---:|---:|---:|---|---|
| +34 | +5 | 29 | 103 | 69 | `checker/inference_p1.rs:1784` infer_to_template_literal_type (+ closure choose_template_literal_placeholder_type 39) | `checker/inference.go:577` inferToTemplateLiteralType |
| +17 | +2 | 15 | 156 | 139 | `checker/utilities_p1.rs:847` compare_types | `checker/utilities.go:418` CompareTypes |
| +14 | +0 | 14 | 18 | 4 | `ls/displaypartswriter.rs:269` classification_for_symbol | `ls/displaypartswriter.go:168` classificationForSymbol |
| +14 | +11 | 3 | 15 | 1 | `frontend/json.rs:1907` json_check_nesting_depth | `std/encoding/json/jsontext/state.go:302` stateMachine.pushObject |
| +13 | +13 | 0 | 66 | 53 | `checker/emit_resolver_p1.rs:1341` create_late_bound_index_signatures (+ split `_locked` 66) | `checker/emitresolver.go:746` CreateLateBoundIndexSignatures |
| +13 | +13 | 0 | 65 | 52 | `ast/node.rs:5169` subtree_facts_of_data | `ast/ast.go:1169` computeSubtreeFacts |
| +10 | +10 | 0 | 149 | 139 | `ls/autoimport/registry.rs:1283` RegistryBuilder::update_indexes | `ls/autoimport/registry.go:796` updateIndexes |
| +10 | +0 | 18 | 74 | 64 | `checker/checker_p34.rs:218` get_type_facts_worker (+ splits 50, 18) | `checker/checker.go:31626` getTypeFactsWorker |
| +10 | +0 | 10 | 31 | 21 | `api/proto.rs:3716` new_type_response | `api/proto.go:1242` newTypeResponse |
| +10 | +0 | 10 | 15 | 5 | `checker/checker_p24.rs:753` get_apparent_type | `checker/checker.go:22191` getApparentType |

- **Rows that remain Δ ≥ 10 once their tagless-switch residue is paid for (4):** json_check_nesting_depth,
  create_late_bound_index_signatures, subtree_facts_of_data and update_indexes. The other 6 rows' Δ is the
  if/else-if cost of Go's `switch {`.
- **Own code over 25:** none (117 at phase start). The largest drops:
  - `effect/layergraph.rs` extract_layer_graph, 145 → 4;
  - `ast/utilities_p1.rs` merge_symbol_ids, 97 → 1;
  - `effect/typeparser/piping_flow.rs` compute_piping_flows, 95 → 10.
- **The largest functions are still Go-sized ports.** structured_type_related_to_worker is cog 456 against
  Go's 641-line function; check_grammar_modifiers is 302; resolve_name is 272.
- **Env names read in the default build (11):**
  - behavior (4): `GOPORT_LAUNCH`, `GOPORT_THP_GUARD`, `TSC_RS_EFFECT` (Effect rules, off by default) and
    `TSGO_EFFECT_API`;
  - tuning (7): `GOPORT_BIND_THREADS`, `GOPORT_EMIT_THREADS`, `GOPORT_MAPPED_THREADS`, `GOPORT_PARSE_THREADS`,
    `GOPORT_RESOLVE_AHEAD_THREADS`, `GOPORT_THP_GUARD_MIB` and `GOPORT_BUILD_VERSION` (compile time).
- **Gone from the shipped build since phase start:**
  - the five `GOPORT_FLOWSKIP_*` tuning reads;
  - `GOPORT_CHECK_VERSION_TABLES`, `GOPORT_TRACE` and `GOPORT_THP_GUARD_DEBUG`, now in the
    diagnostics/test-hooks build only. In total 32 names are read only there.
- **Clippy:** the top `clippy::all` lints still left are needless_return 292, identity_op 182, type_complexity 139,
  too_many_arguments 134 and match_single_binding 120. Fixed since phase start: collapsible_if −409,
  needless_late_init −118, len_zero −64, needless_borrow −49 and assign_op_pattern −40.
- **Churn since phase start:** prod +23,777/−24,257 lines (net −480), test net +3,804, 597 files.

## Correctness fixes found during the phase

The quality phase was meant to be behavior-neutral. Its audits and parity-tier work still found real output
differences from Go, and each was fixed to Go's answer and pinned by a parity item or test. None of them was in
checker logic (`plan.md` B9 notes). Sources: `plan.md` lines ~1530–2505 and the reviews it names.

| lane (merge) | what it was | real differences from Go found and fixed |
|---|---|---|
| B2 (`eaf77bac4`) | API path case sensitivity | Emit took case sensitivity from the OS, not the program's file system (D2). The common source directory folded case on dynamic names; Go emits `outDir/A/x.*` and `outDir/a/y.*` (D1, Go's `CommonDirectoryOfFiles` ported). Follow-on fixes: the source-map `sources` path, the build-info `root`, a TS5056 duplicate-output key, `x.TS` TS6054, and config `include` dropping a case twin. The api tier went from 11 DIFF to all MATCH; 14 pins were removed. |
| B3 (`9a4c34ea6`) | audit of stale pre-pin ports | Audit only: it confirmed D1 and D2 (fixed by B2) and listed 7 suspect call sites. |
| B4 (`9513cf308`) | LSP refresh-count variation | No difference: lspdiff's pacing caused it, and Go coalesces the same refreshes when starved. Go's refresh sequence is pinned by a test; `tsgo` is byte-identical. |
| B5 (`e218fc92b`) | direct tests for B2's risks 8, 9, 11 and 14 | No difference and no production change. The expected answers come from Go probes, and each test fails only with its fix reverted. |
| B6 (`a983b2618`) | Go's generated LSP preferences parser | `maximumHoverLength` 4294967306 wrapped in an `i32` (Go wraps on a 64-bit `int`). Formatter sizes were `i32` (`tabSize` 2^32). `i64` values went through `f64`. Empty and nil slices differed in `MarshalJSONTo`. A 2^62 indent aborted the process, where Go gives a recoverable InternalError. The prefs battery went from 7 DIFF to 0. |
| B7 (`e449b2c7a`) | project-reference mapper and symlinks | No symlink difference, but three base-directory differences: a programmatic `paths` without `pathsBasePath` gave TS2307; automatic types and lib replacement resolved from the host directory; declaration emit wrote `import("../shared/value").V` where Go writes `import("alias").V`. |
| B8 (`d9b0d013a`) | checker workers read the OS disk (`WORKER_FS`) | API emit over a virtual file system read module specifiers, TS2835 and package.json from the disk. `tsc -w` missed a rebuild when a probed-only file was created. Callback-FS builds used the disk (TS2835 against TS2834). The incremental d.ts mtime restore bypassed the host. `outputs_overlap` made stat and realpath callbacks Go never makes. A nested API build could deadlock (Go's `buildMu` ported). |
| B9 (`9429e8a57`) | A1 audit: Go `int` width, nil against empty | API `maxResponseBytesPerPage` was `i32`. An empty `TypeAcquisition.include/exclude` was lost. Float-to-`int32` enum options saturated where Go wraps. `formatNodeForInsertion` panicked at positions ≥ 2^31. The build-info reader wrapped ids and `int32` fields where Go rejects or keeps them. One case is better than Go (the A5 root-range overflow: Go hangs, tsc-rs answers). |
| B10 (`be997ed55`) | one Go port of HasPrefix/HasSuffix | The `deps.rs` copies folded ASCII only, where Go uses Unicode simple folding. With non-ASCII `paths`, package.json `imports` or `exports` on a case-insensitive host, Go picks the non-relative specifier and tsc-rs fell back to a relative path or TS2883. Pinned by 3 api items and an 18-row go1.27.1 unit test. |
| F5 (`8a868f3bf`) | one port per Go function on output paths | `program.rs` had a stale resolution-mode copy without ts#63915: a `.cts` file with `import.source("a")` gave `import("a").X` where Go gives `import("b").X` (cli item `resolution-mode-source-phase`). |
| H7 (`11dcc9470`) | api parity tier (sealed `tsgo --api` sessions) | Harness: it pinned the 14 api differences that B2 fixed, and it fixed a false ERROR in the H5 tripwires (an inherited fd closing after the watch started). |
| H8 (`2e08cbde7`) | lsp parity tier hardening | Harness only: the `initialize` answer is now compared, number parsing is lossless, home writes are seen, and an exec failure no longer counts as GO-BROKEN. No tsc-rs difference (its nondeterminism lead went to B4 and was harness pacing). |
| H9 (`890053a7c`) | lspdiff paced by the server, not the clock | Harness only: notification and ATA-progress barriers. Refresh counts under CPU starvation are now stable on both sides (Go 12 in 8 of 8, tsc-rs 12 in 50 of 50). No tsc-rs difference. |
| C1 (`1d325e66b`) | content-mapper watch flake | Test only: a clock step made the build info older than its config. 4 sibling tests fixed; `tsgo` is byte-identical. |
