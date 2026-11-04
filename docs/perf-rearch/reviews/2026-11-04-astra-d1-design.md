# Astra review of the D1 design (2026-11-04)

Reviewer: Codex `gpt-6-astra`, effort high. Read-only, at `8ac837045`.
Full text: T3 thread "Astra review: D1 certified checking design".

**Verdict:** overall rework. **S1: go with changes**, as an explicitly
uncertified, opt-in experiment. It keeps the exact default, disables lazy
members and never-order, and measures the real performance of the
refinement before anything more is built.

1. **Blocker:** colder doesn't imply safer limits.
   - The relation budget starts at (16M − cache size)/8
     (`relater_p1.rs:964`, Go `relater.go:369`), so a cold checker gets a
     bigger budget and Go can overflow alone.
   - Instantiation counters reset in a cold path (`check_expression`) that
     a warm cache hit skips (`checker_p19.rs:269`, `checker_p09.rs:50`).
   - **Change:** withdraw corollary 2 and hard-limit completeness; model
     cache-size budgets and reset boundaries.
2. **Blocker:** `deep_true` recreates finding 8. Stack ids `[30,20,10]`
   count once and `[10,20,30]` three times (`relater_p1.rs:2023`). When
   both sides expand, a cold run returns MAYBE instead of checking structure
   (`relater_p4.rs:780`). **Change:** treat it as an uncovered mechanism;
   add reverse-creation fixtures and an effective trigger or replay.
3. **Blocker:** the tie filter admits distinguishable types. Anonymous
   binding-pattern types `{a}` and `{b}` reach the id fallback
   (`checker_p20.rs:405`, `utilities_p1.rs:980`). **Change:** compare
   members, signatures, index info and provenance, or fall back.
4. **Major:** lemma R omits execution state.
   - Deferred nodes are an insertion-ordered queue (`checker_p03.rs:632`).
   - `resolution_start` is reset to suppress contextual circularity
     (`checker_p09.rs:1553`).
   - Declaration-only cycle keys collapse instantiations.
   - **Change:** audit queues, sentinels and guards, and use
     instantiation-aware identities.
5. **Blocker:** the diagnostic union doesn't reproduce Go's collection.
   - Diagnostics are collected per file at visit time (`program.rs:5267`,
     Go `program.go:752`), and directives are filtered per collection.
   - #4825 mutates existing diagnostics in place, including related info and
     `skipped_on_no_emit` (`checker_p16.rs:29`).
   - **Change:** collection epochs, mutation tracking, and all observable
     fields.
6. **Major:** a saved WorkerSeed isn't a fresh snapshot. The shared
   `Arc<VersionTables>` holds `declaration_diagnostic_cache`
   (`program.rs:5855`), and B1's sink has a single open bit
   (`program_emit.rs:296`). **Change:** isolate speculative caches, tag
   results by generation, and open only after everything is certified;
   buffer stderr too.
7. **Major:** the `9·n` late-bound slack is incomplete. Symbol ids are
   64-bit, and serialized-type cache hits replay `added_length` without
   revisiting names (`nodebuilder_impl_p4.rs:134`).
8. **Major:** the campaign can't justify `1e-5`. D1 lists about 237k runs,
   not 300k; correlated partitions; no direct Go comparison on new inputs;
   whole-trigger mutants test presence only. **Change:** holdout inputs,
   direct pinned-Go comparisons, mechanism coverage and weakening mutants.
   Report observed zeros without a probability claim.
9. **Moderate:** the gain is credible for today's splitting, not yet for
   D1. The refinement simulation borrows another cache history, uses oracle
   scheduling, and counts missing files as zero. Its instantiation proxy
   already predicts a 23% worse vscode critical path.

**Simpler next design:** a static refinement with no stealing or
fingerprints, whole-process exact fallback, and differential validation,
keeping the exact default.
