# Astra review of the phase 2 draft plan (2026-11-03)

Reviewer: Codex `gpt-6-astra`, reasoning effort high. Read-only, at 91f3d021c.
Full text: T3 thread "Astra review: perf-rearch phase 2 plan".

**Verdict: go with changes.**

1. **Blocker, A1:** include reasons keep `ref_.synthetic` directly
   (`file_include.rs:218`, `:460`; `include_processor.rs:127`), and the
   helper-vs-JSX wording compares that node with the helper map.
   - Remap synthetic references in reasons and processing diagnostics
     without mutating older snapshots.
   - Require equal synthetic import names, presence and resolution modes;
     otherwise rebuild.
   - Test `@jsxImportSource`, automatic/classic switches, missing runtimes,
     helper errors and supplemental files.
2. **Major, A1:** keep "reused storage" separate from the "Go-observable
   update kind".
   - Config diagnostics need `> CLONED` and auto-import refresh needs
     `!= CLONED`, but `CLONED` also copies `seen_files`
     (`session.rs:3473`, `snapshot.rs:1075`,
     `projectcollectionbuilder.rs:2650`).
   - Run the shadow-rebuild verify in an isolated process, so it can't
     perturb global ids.
   - `allowJs` alone doesn't force a rebuild (`file_loader.rs:2845`).
3. **Blocker, A2:** a suspended coroutine leaves thread-local
   current-program and synthetic-owner scopes live, and a second diagnostics
   acquisition panics on a full semaphore (`ls_program.rs:329`,
   `synthetic.rs:1154`, `checkerpool.rs:58`).
   - Needs coroutine-local state restore, snapshot ownership, exclusive
     leases and scheduler-aware waits. Keep synchronous dispatch FIFO.
   - Blanket queued-cancel changes pending-request semantics
     (`server.rs:2086`).
   - Needs forced-yield tests. Statement polling doesn't bound one
     expensive statement.
4. **Major, B1:** plain programs run a post-check global-diagnostics sweep
   before emit; incremental programs skip it (`program.rs:6345`, Go
   `program.go:2088`).
   - Queue check → global-diagnostics snapshot → emit per checker.
   - Add parity cases with syntax, option and global errors, `noCheck`,
     output collisions, write failures and printed counts.
5. **Major, B2:** more checker queries are involved (decorator
   serialization `type_serializer.rs:354`, CommonJS
   `commonjs_module_p2.rs:752`, class fields), and transforms share
   emit-context effects (`emitter.rs:173`).
   - `verbatimModuleSyntax` doesn't move every file off the checker
     (`emitter.rs:1003`).
   - Instrument routing and resolver cost first, and compare the full
     per-checker call sequence (arguments, answers, context mutations).
6. **Blocker, E2:** the acquire load gives visibility of the initialized
   `FileBlock`, and the low and high tables can publish concurrently
   (`store.rs:1739`, `:1818`, `:1864`). Use an already-acquired
   per-program registry view or hoisted block handles instead; byte-equal
   tests can't certify the absence of a race.
7. **Major, E1:** the THP guard is a deliberate protection (`thp_guard.rs:9`,
   `:31`, `:57`). Measure wall, sys, faults and RSS on fresh and fragmented
   memory and in long sessions. Prefer a selective arena policy.
8. **Blocker, D1:** "no monitor event" isn't certification: the fast run can
   skip the computation that makes Go hit a limit (`checker_p24.rs:1092`).
   Id tie-break monitoring must also cover the ordered-id recursion test
   (`relater_p1.rs:2023`). **Recommendation:** reject D1 as the exact
   default; keep D0 as research or an explicit non-parity mode.
9. **Major:** the ranking overstates the measurements.
   - The emit numbers are emit-phase time under load, not end to end.
   - `lsp-audit/probes.log` has `node: command not found` despite ending
     `DONE`.
   - Require successful commands, binary provenance and unloaded paired
     medians.
10. **Minor, B4:** the duplicate declaration pass is absent with
    `noEmit`/`noEmitOnError` off (`program.rs:6352`,
    `program_emit.rs:1551`). Drop its claimed gain.
11. **Major, loader:** C1/C2 don't make A3 or C3 redundant, and A3 and the
    `-b` overlap don't need C1. The `--explainFiles` replay experiment omits
    negative lookups and discovery effects. Do A1, then remeasure, then
    decide A2.
12. **Major, shipped build:** `build-release.sh` applies PGO and BOLT with
    editor training and embedded allocator settings; `--profile goport` is
    not the shipped pipeline. A non-embedded allocator config can add a
    startup exec (`tsgo.rs:194`).
    - Measure the shipped pipeline first, then retrain on representative
      workloads. No `panic=abort`.
    - Missed candidate: per-file reclamation of emitter-only synthetic
      nodes (`emitter.rs:680`).
13. **Major, Effect:** this is a parity difference
    (`parsing_helpers.rs:493`, `checker_p03.rs:287`). **Recommendation:**
    disable it in the Go-parity mode and keep it as an explicit extension
    mode. Settle this before baseline timing.
14. **Minor, caches:** opt-in only, keyed by compiler and schema version
    plus all inputs. Remap identities, and only after snapshot v2 proves the
    decode is fast.

## Revised first wave

1. Finish successful parity and baseline runs, settle Effect, and measure
   the shipped build.
2. A1, with corrected reason ownership, observable update classification
   and an isolated verify.
3. B1, with the global-diagnostics boundary, plus emit phase and routing
   instrumentation.
4. E1, a synchronization-safe E2, the E3 investigation and the corrected B2
   experiment.
5. Remeasure LSP saturation before A2. D0 stays a separate research lane.
