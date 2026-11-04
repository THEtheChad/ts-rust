# Audit: language server

Condensed by root from the audit lane's report on 2026-11-03. Full report:
T3 thread "perf-rearch audit: LSP/session". The load stayed at 15–50, so the
lane's load-gated runs are queued under the systemd unit `lsp-audit-all` and
write to `~/.cache/perf-rearch/lsp-audit/`:
- the umami bench with a `.ts` file as the edited file;
- time to first answer;
- per-request costs, cold and warm.

## Main finding

**Most of the single-request gap is the `.tsx` program rebuild, not the
providers.** The bench edits `BoardCloneForm.tsx`, so every key rebuilds the
program.

| | tsc-rs | Go |
|---|---|---|
| hover, total | 97 ms | 75.5 ms |
| of which program update | 90 ms | 69.8 ms |
| provider work | about 7 ms | about 6 ms |
| completion p50 | about 78 ms update + 26 ms provider | about 41 + 27 ms |
| `.ts` edit: first request | 2.7 ms | 9.5 ms |
| `.ts` edit: diagnostics | 3.4 ms | 12 ms |

On `.ts` edits, tsc-rs is already faster than Go.

## Candidates (ranked)

1. **Program reuse for JSX-runtime and importHelpers files.** This is the
   same as program audit #1.
   - **Design:** make a new synthetic specifier node, parented to the new
     root and owned by the new version, then patch both specifier maps.
     Include reasons resolve through the map, so they stay correct.
   - **Parity trap:** the reuse must report `SAME_FILE_NAMES`, not `CLONED`,
     to the consumers that publish config diagnostics and refresh
     auto-imports.
   - **Proof:** a verify mode that does the full load on each reuse and
     compares files, resolutions, specifiers, explainFiles text,
     diagnostics and meta. Then lspdiff, plus a comparison of the
     publishDiagnostics stream.
   - **Gain:** `.tsx` update 78 → about 1 ms; first request 82 → 3–10 ms;
     no 27 ms free; no 145 ms of worker CPU. Below Go at 250 and 500 ms/key,
     and it probably removes most of the 120 ms/key saturation.
   - **Size:** 200–400 lines, 1–2 weeks.
2. **Preemptible diagnostics on the dispatch thread** with stackful
   coroutines (corosensei). An alternative to #116's thread plan.
   - **Design:** a diagnostics pull's async half yields at the checker's
     per-statement context poll when a completion, hover or similar request
     is queued (an atomic flag set by the reader thread) and no print scope
     is open.
   - **Why:** nothing needs to be `Send`, there is no WorkerSeed copy, ids
     stay shared, and serial traffic never yields (so lspdiff stays
     byte-identical).
   - **Gain:** 120 ms/key p95 toward the no-diagnostics bound.
   - **Size:** 0.8–1.5k lines, 2–4 weeks; the prototype takes 2–3 days.
   - **Kill condition:** completion p95 stays at or above 300 ms, or borrow
     conflicts can't be fenced off.
3. **Answer cancelled queued requests with -32800** (revive stage 1a,
   `fix/lsp-cancel-queued` `64dfef99a`). Now allowed, since every answer is
   one Go can give. Add priority ordering, and fold it into #2.
4. **Diagnostics on a real slot-0 thread** (#116 stage 1), only if the
   dispatch thread is still CPU-bound after #1–#3. Time `WorkerSeed::take`
   per version first.
5. **Trust the previous load's resolutions** when the only changes are
   overlay edits to files other than package.json. Saves 54–63 ms of
   checking and replaying, plus 145 ms of worker CPU; 300–600 lines.
6. **Incremental loader for import-changing edits:** 78 → about 30 ms.
   1.5–3k lines, risk medium to high.
7. **O(1) free of the task graph** (a bump arena): 27 ms. Comes free with
   program audit #2.
8. **First empty-prefix global completion:** 1,225 ms with 28,944 items in
   tsc-rs, 487 ms warm. Only a candidate if Go turns out much faster
   (pending `probes.log`). Possible fix: start the auto-import registry
   right away.
9. **Speculative idle diagnostics:** low priority.

## Not worth it

- Caching answers across versions: type ids order union members, so it
  can't be proven byte-identical.
- A `Send`/`Sync` language-service layer.
- Speculative updates on `didChange`: they would shift global symbol ids.
- Checker creation (about 1 ms), semantic tokens and inlay hints (already
  faster than Go), the snapshot clone (0.3 ms).
- Cross-project threads, the watch registration round trip.

## First three

#1, the #2 spike, #3 folded into #2.
