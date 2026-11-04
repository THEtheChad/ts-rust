# Audit: front end (scanner, parser, AST, lib snapshots)

Condensed by root from the audit lane's report on 2026-11-03. Full report:
T3 thread "perf-rearch audit: scanner/parser/AST". Scratch projects are in
`~/.cache/perf-rearch-audit-fe/`.

**Metric:** user-space instructions (`scripts/perf-rearch/icount.py`), which
are load-independent. The load was 15 to 64 throughout, so wall times were
unusable.

## Facts (single-threaded, user instructions)

| | tsc-rs | Go |
|---|---|---|
| umami parse only | 3.69G | 6.35G |
| umami parse + bind | 4.70G | 7.40G |
| umami full check | 16.5G | 27.8G |
| umami check + emit with declarations | 20.8G | 35.8G |

- **Parse share:** about 22% of single-threaded check work; bind about 6%.
- **Per-unit cost:** source code costs about 300 instructions per byte,
  roughly the same as Go: about 800 per node, in Go's recursive-descent
  shape plus store work. `.d.ts` files are cheaper than in Go.
- **Fixed cost:** about 85k instructions per tiny file (Go 103k).
- **Lib snapshot:** loading lib.dom costs 101M against 164M for a live
  parse, only 1.6× cheaper. Only dom and es5 have snapshots; about 95 es20xx
  libs parse live (about 37M).
- **Parse workers:** any worker adds a fixed +13% parse overhead
  (`GOPORT_PARSE_THREADS` 0: 3.70G; 1: 4.19G; 15: 4.24G). It isn't discarded
  work.
- **Note for the checker lane:** a tiny project without `skipLibCheck`
  spends about 810M checking lib.dom on one checker, the critical path for
  small projects.

## Candidates (ranked)

1. **Parser cost per node** (about 800 instructions per node).
   - **Design:** hold the store by `&mut` during the parse; write parents
     and links when the node is created; keep the parse-error flag in a
     plain field; memoize tokens across rewinds.
   - **Gain:** −25 to −40% of parse, −5 to −9% of the single-threaded CLI.
   - **Size:** 1.5–3k lines, 3–5 weeks.
   - **Gate:** profile first, and drop it if store work is under 15% and
     rescans under 1.3×.
   - **Parity:** a dump of every store slot, compared byte for byte.
2. **Remove the +13% worker parse overhead.** *Corrected by the E3 spike
   (`experiments/e3-parse-overhead.md`): it isn't parse work and isn't
   fixed. It is duplicated resolution (a race, plus package.json parsed per
   worker cache), +27% at default workers.* Suspects: cold identifier
   caches, `adopt_detached_store` rebuilding the JSDoc cache, diagnostics
   cloned twice, strings copied.
   - **Gain:** up to −11% of parse CPU in every multi-threaded run.
   - **Size:** 200–600 lines, 1–2 weeks. **Risk:** low.
3. **An arena for LSP owned nodes, with unpinned reads.** Today each node of
   an edited file is its own `Box`.
   - **Gain:** −2.5 to −4% of session instructions, −0.5 to −2 ms per edit.
   - **Size:** 0.8–1.5k lines, 2–3 weeks.
4. **Cheaper registry lookup on node reads.** Today a read is 4–5 dependent
   loads plus an acquire load that stops LLVM merging lookups. Use a
   publish-once table with plain loads and thin-pointer columns.
   - **Gain:** −1 to −4% of check instructions.
   - **Size:** 150–400 lines, 1 week.
   - **Experiment:** change only `file_block`, count umami instructions, and
     go ahead if it saves ≥1%.
5. **Lib snapshot v2:** fixed-width columns, all bundled libs, optionally the
   bind snapshot in the same image.
   - **Gain:** −100 to −130M instructions per run (about 10–14% of a tiny
     project, about 8–12 ms of startup).
   - **Size:** 0.8–1.2k lines, 2 weeks.
6. **Fixed per-file cost:** reuse parse scaffolding, create maps lazily.
   −3 to −8% of parse, about 1 week.
7. **Synthetic node reads without `Rc` or thread-local lookups** (emit,
   declaration emit, hover). −2 to −5% of emit, unverified; 1–2 weeks.
8. **Copies at freeze, adopt and publish** (`shrink_to_fit`, `parser_flags`,
   the JSDoc cache, diagnostic clones). −1 to −3% of parse, a few days.
9. **Persistent parse cache for node_modules `.d.ts`.** −40 to −60% of parse
   on repeat runs, but it writes a cache directory, which is a policy
   decision. Only worth it if snapshot v2 loads at least 5× faster than a
   live parse.

## Not worth it

- The scanner core (perfect-hash keywords, SIMD-ish identifiers, memchr).
- Line maps, file reading, resolution during load (4M instructions).
- Identifier interning, lazy JSDoc.
- Pointer-based node handles (#4 gets most of the benefit).
- Compacting the AST (13.4 MB against Go's 13.0 MB for lib.dom).
- Incremental reparse in the LSP (a 34 KB `.tsx` file costs 2–3 ms; the
  rebuild is the gap).
- Splitting the parse of one big file.

## First three

#2, #4 (a one-function experiment), #5. Profile #1 before investing.
