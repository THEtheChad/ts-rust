# Audit: program construction (load, resolve, bind)

Condensed by root from the audit lane's report on 2026-11-03. Full report:
T3 thread "perf-rearch audit: program load/resolve/bind". The scratch tools
are in `~/.cache/perf-rearch/scratch/audit-program/`. Measured at load 10 to
52, so wall numbers are directional only.

## Facts

- **CPU:** tsc-rs already uses less CPU than Go in config, include walk,
  `--listFilesOnly` (vscode 1.2–1.4 s against 2.5–5.2 s), parse and bind.
  Sys time is page faults, not syscalls.
- **The serial loader thread is the cold-load wall,** at about 40–45 µs per
  file. vscode's parse phase stays at about 0.42 s from 15 to 24 parse
  workers while the loader never waits; the parallel floor is about 0.26 s.
  umami's workers are only 40–62% busy.
- **LSP `.tsx` edits** in `react-jsx` projects always rebuild the program,
  because of the synthetic JSX import specifier (umami has 536 `.tsx` to 716
  `.ts` files). The same happens for every module with `importHelpers` and
  every `.js` file with `allowJs`.
- **Order-dependent outputs a redesign must reproduce:**
  - file id order (the serial load order);
  - include reasons (the synthetic node's identity);
  - which path casing is kept first;
  - depth restarts and `maxNodeModuleJsDepth` elision;
  - the first module resolution error kept;
  - the cap of 5 content-mapper failures;
  - the set of `packageJsons` in `.tsbuildinfo` (only package.json files
    from resolutions actually used).
- **Order-independent:** `get_processed_files` file order (a tree walk),
  traces, the resolution cache.

## Candidates (ranked)

1. **ReuseProgram for JSX-runtime, importHelpers and allowJs files**
   (`program_new.rs` `reuse_program` 595–760, refusals at 663–671 and 683–693).
   - **Gain:** 78 ms → about 1 ms per `.tsx` edit, beating Go's 41 ms.
   - **Size:** 200–400 lines, 1–2 weeks. **Risk:** low to medium.
   - **Proof:** a differential of reuse against a forced rebuild on every
     `.tsx`/`.js` file in umami, effect, hono and the fixtures, then lspdiff.
2. **A compact load graph with a deterministic serial replay**
   (`files_parser.rs`, `file_loader.rs`, `file_include.rs`).
   - **Design:** `u32` path ids, one record per file, flat edge `Vec`s
     (about 16 bytes each), and a replay that reproduces every
     order-dependent rule.
   - **Gain:** serial work from about 40 µs to a few µs per file (umami
     about 200 ms → 20–40 ms), and freeing becomes trivial.
   - **Size:** 3–4k lines, 3–4 weeks. **Risk:** medium. This is the base
     for candidates 3, 4, 5 and 7.
   - **Experiment:** replay a graph parsed from `--explainFiles` in a
     standalone Rust program; kill or rethink above 5 ms per 10k files.
3. **Parallel discovery:** workers run the whole parse task, resolution
   goes through a concurrent cache, and candidate 2's replay merges the
   results. About 3–5k lines of speculation and checking can be deleted.
   - **Gain:** umami load 0.22 → about 0.07 s, vscode 0.42 → about 0.28 s,
     LSP rebuild 78 → 20–30 ms.
   - **Size:** 4–6 weeks after candidate 2. **Risk:** medium to high.
   - **Proof:** output identical across `GOPORT_PARSE_THREADS` 1, 2, 4 and 32.
4. **Incremental LSP rebuild:** keep unchanged files' load records across
   snapshots. All 9,393 resolution keys are redone per rebuild today, all
   with the same answer.
   - **Gain:** 78 → under 10 ms for import edits.
   - **Size:** 1.5–2k lines, 3 weeks. **Risk:** disk changes without watch
     events.
5. **`-b` project loads run serially** (`execute/build/orchestrator.rs`).
   Overlap them after candidate 3. Unmeasured, plausibly 20–40%.
6. **Shared path ids in the resolver and caches** (`tspath::Path(String)`,
   `ModeAwareCacheKey`, cachedvfs). 72 ms of string hashing and comparing
   per LSP edit. About 20–30% of resolution CPU, 1–2k lines.
7. **Free the old load without blocking the dispatch thread:** 27 ms per
   LSP edit. Comes with candidate 2.
8. **On-disk parse and bind cache for node_modules `.d.ts` files,** keyed by
   content hash. Writes a cache directory outside the project, so it needs a
   decision. Overlaps the frontend audit.
9. **Share bare-specifier resolutions across sibling directories.**
   Moderate gain, medium risk (failed lookups feed watch registrations).

## Not worth it

- Config parsing and globbing (already faster than Go).
- Syscalls and realpath.
- The binder (already parallel and faster).
- `get_processed_files`.
- Skipping the load on a no-change incremental run (unsafe: new
  node_modules files would go unseen).

## First three

Candidate 1, then 2, then 3.
