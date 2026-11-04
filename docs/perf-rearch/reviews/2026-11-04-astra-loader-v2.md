# Astra review of loader design v2 (2026-11-04)

Reviewer: Codex `gpt-6-astra`, effort high. Read-only, at `814bae76f`.
Full text: T3 thread "Astra review: loader design v2 (frozen OS view)".

**Verdicts:** L1 go with changes. L2 go with changes. L3 rework. L4 rework.

1. **High:** the cited insertion point sits above overlays, and reads bypass
   it. `AheadLookupLayer` wraps the layered FS, and
   `CachedLayeredFileSystem::get_file_by_path` delegates past it
   (`snapshotfs.rs:49`, `:117`, `:166`).
   - **Change:** put V under `OverlayFS.host`, covering both `Fs` and
     file-handle reads, and keep bundled and custom FS behavior.
     Referenced-config acquisition (`compilerhost.rs:292`) must share the
     same backend.
   - Detach V before later loads and auto-import work.
2. **High:** G1 proves serial-on-V, not Go equivalence or recovery. Watches
   are registered after the snapshot is built, with no retrospective events
   (Go `session.go:1667`, Rust `watch.rs:59`). A package that appears
   between a worker's observation and watch registration may never send an
   event. Independently sampled stat, read and realpath answers need not
   describe one disk state.
   - **Change:** state stable-input equivalence separately, and define an
     event-generation or reconciliation protocol for concurrent change.
     Test mutations before watch registration.
3. **High:** G2's revalidation isn't snapshot validation.
   - Accessible entries depend on symlink targets (`mod_impl.rs:411`), so a
     directory mtime misses them.
   - Metadata after a read can describe replacement bytes; there's a
     check-to-commit race and ABA.
   - **Change:** call it best-effort change detection unless a real
     snapshot exists.
4. **High:** G2's rollback inventory is incomplete. It misses store id
   allocation (`store.rs:4419`), parse-cache registration
   (`parsecache.rs:226`), the referenced-config registry, resolver caches,
   counters and notifications. **Change:** define the transaction boundary
   before implementing it, and journal updates, deletes and order.
5. **High:** L2's id argument is confirmed (`program.rs:1926`, `:2670`,
   `:2708`), but the early-execution safety argument is incomplete.
   - Detached binding writes shared node records (`binder_p1.rs:166`).
   - The CLI gate checks all files for syntax errors first
     (`program.rs:2807`).
   - **Change:** per-file ownership, reader barriers, pins and cleanup.
     Keep the whole-program CLI gate, and treat LSP (where Go binds in the
     parse cache, `parsecache.go:80`) separately from CLI.
6. **High:** L3 doesn't validate sub-memo values. Package-cache hits return
   without FS reads (`resolver_p2.rs:1108`). **Change:** record and validate
   consumed memo results and resolver context; mismatch needs a continuation
   or rollback.
7. **High:** L4's arrival classes omit diagnostic provenance:
   unsupported-extension diagnostics capture the arrival's `includeReason`
   (`files_parser.rs:111`). **Change:** keep complete arrivals, and build
   arrival-dependent diagnostics in the replay.
8. **Medium:** the measurements justify a prototype, not the range.
   - `fsbench.c:18` has a data race and measures wall, not kernel CPU; its
     input is cache contents rather than a call trace (96 of the 5,102 read
     paths aren't absolute).
   - **Change:** per-thread sinks, a real call trace, user and sys
     separately, in-process phase timestamps, idle end-to-end A/B.
   - −100 ms is plausible; −160 ms and Go parity are not established.

**OS boundary:** preserve the exact collapsed results: `ENOENT`/`EACCES` as
false/None, partial reads discarded, BOM decoding, realpath returning the
original path on failure, full `FileInfo`, entry order and symlink metadata.

**Defaults (Astra):**
- G1 is reasonable for the one-shot CLI under an explicit stable-input
  contract.
- Keep the current LSP behavior as the default until the concurrent-change
  and recovery contract is resolved and tested.
- Don't pick G2 just because it's cheap.
