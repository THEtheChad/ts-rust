# Astra review of the loader design (2026-11-04)

Reviewer: Codex `gpt-6-astra`, effort high. Read-only, at perf `32def1dbb`.
Full text: T3 thread "Astra review: loader design + Go-model check".

**Verdict:** S1 rework. S3–S4 rework; S3 as a shadow experiment is
reasonable.

1. **High:** S1's lookup logs can't reproduce LSP tracking.
   - `worker_lookup` doesn't log cached hits (`files_parser.rs:4907–4911`),
     and `StatLookup` has no reads or values (`module/cache.rs:1065–1072`).
   - Go tracks `FileExists` even on cache hits, and tracks reads
     separately (`project/snapshotfs.go:839–873`). These feed watchers and
     invalidation (Go `project/project.go:606–607`; Rust
     `projectcollectionbuilder.rs:2748`).
   - **Change:** record complete, transitive SourceFS-level effects,
     including cached hits and reads.
2. **High:** equal cached lookups don't prove equal cached content.
   - Old text, nil entries and `needsReload` take precedence over disk
     (Go `snapshotfs.go:323–344`). `FilePrep::fits` doesn't check
     file-system validity.
   - **Change:** extend the resolve-ahead acceptance path
     (`project/compilerhost.rs:672`), which checks cached-file state and
     hashes. Validate metadata dependencies before adopting anything.
3. **High:** text adoption and delayed publication can change future
   snapshots.
   - Unused speculative texts would persist where Go deliberately avoids
     caching auto-import-only reads (Go `project/autoimport.go:35–49`).
   - Adoption also records node_modules symlink aliases and directory
     membership (Go `snapshotfs.go:359–399`, `263–275`).
   - **Change:** adopt only demanded reads, with snapshot precedence and
     aliases, consistently during the load; discard unused speculation.
4. **High:** equal loaded, started and depth sets don't prove replay
   equivalence.
   - The representative task is chosen at `LoadOrStore`
     (Go `filesparser.go:247–250`, `279`). Arrivals can differ in lib
     status, which changes metadata, counters and lib classification
     (`:110–116`, `:523–527`).
   - **Change:** preserve arrival-specific properties, and validate
     metadata, resolutions, edges, diagnostics and effects. S3 on
     serial-generated records can't prove S4.
5. **High:** fallback isn't clean. Discovery has already mutated the
   shared snapshot (file cache, lookup cache, directories, aliases).
   **Change:** transactional isolation or explicit rollback. A verify
   child needs the same overlays and prior snapshot state.
6. **High:** the parity proof omits protocol behavior. The client
   acknowledges server requests without recording them
   (`vendor/lspdiff/client.mjs:49–52`).
   - **Change:** compare registration and unregistration payloads,
     publishDiagnostics, refreshes and invalidation.
   - **Exercise:** create, delete and rename; package.json changes;
     symlinks; open and close; overlays absent on disk; second projects;
     auto-import completion and resolve, across worker schedules.
7. **Medium:** the S4 type-reference cache contract is wrong. Go's key
   includes `fromInferredTypesContainingFile`, and its setter uses
   `Store` (Go `module/cache.go:31–48`).
8. **Medium:** the timing claim isn't reproducible from its named
   sample. `g-rs-{1..5}` median is 435.89 ms and `g-go-{1..5}` 395.27 ms,
   not 427/294.
   - **Change:** publish a sample manifest with exclusions.
   - Treat "S1 fixes this alone" as a hypothesis. Start by feeding
     first-load answers through the existing acceptance machinery and
     measure.

## Part A (Go model in design §2)

- **Mostly correct.**
- **Wrong:**
  - line 101: only a **new casing** copies `startedSubTasks`;
  - line 160: missing package.json entries enter buildinfo only under
    node_modules, and realpath applies only to existing ones;
  - line 163: the union argument is not a general proof.
- **Imprecise:**
  - lines 85 and 87: guards on root FileExists and the auto-types task;
  - line 94: Go iterates casings in map order, Rust in insertion order;
  - line 125: binding allocates lazy global node and symbol ids;
  - lines 144–146, 157 and 161: race details, trace bypass excluding
    package.json reads, seen-file values overwritten.
