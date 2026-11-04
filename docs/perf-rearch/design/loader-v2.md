# Loader v2: serial commit over a frozen per-load file-system view

Design lane, 2026-11-04. Read-only on the code; inputs: [design/loader.md](loader.md) (v1),
[Astra's review](../reviews/2026-11-04-astra-loader-design.md), plan.md "Timing window 1",
and the S1a write-up (`t3/speed-umami-lsp-ttfd:docs/perf-rearch/experiments/s1a-first-load-ahead.md`).
Each number is marked **[measured]**, **[est.]**, **[read]** (from code) or **[inferred]**.

**Summary**
- **What the loader thread actually spends:** about half of its time is kernel time, not CPU work. During the umami first load, the loader thread used 120–160 ms of user time and 130–170 ms of system time **[measured]**. Those 27,985 OS operations alone cost 90–98 ms when replayed on one thread **[measured]**. The S1a profile hides most of this, because samply spreads kernel time over the user frames that run after each system call.
- **Why S1a hit a wall:** answer-level reuse still has to repeat each uncached lookup as a real OS call on the loader thread.
  - **v2 changes the layer instead:** the serial loader keeps running unchanged and stays the only code that changes the snapshot. Under the snapshot's own caches sits a **per-load, frozen, concurrent OS view (V)**, which the parse workers fill in parallel.
  - **Exactness comes by construction:** the snapshot's precedence, tracking, caching and order are produced by the serial code itself. Only the OS backend changes.
  - **Discovery becomes advisory:** it warms V and a store of products keyed by all their inputs. It never writes snapshot state.
- **Findings 1–3 and 5** are answered by construction for stages L1–L3. Finding 4 matters only from L3 on, and L4 uses a replay commit that produces every observable item itself instead of comparing sets. Finding 7 is handled in §4.
- **Expected effect:** L1 (V) is about −100 to −160 ms of loader time, which puts ttfd at about 265–300 ms against Go's 283–295. L2 (bind in workers) takes another −25 to −30 ms. These are **[est.]**, with the measured basis in §0.

---

## 0. New measurements (S1a bin `98f94361a`, umami, open `BoardCloneForm.tsx`)

The machine never dropped below load 1.5 during this lane (1-minute load 1.3–5.0). So these are **CPU splits and counts, not wall claims**.

| What | Result | Method |
|---|---|---|
| Loader-thread time in the load window (main `tsgo` thread, from `load-times` start to end) | utime **120–160 ms**, stime **130–170 ms**, minor faults 32.5k–36.4k (4 runs, S1a on and off alike) **[measured]** | `/proc/<pid>/task/<tid>/stat` sampled every 2 ms (`tt_sampler.py`) |
| Distinct OS operations of one first load (from the load dump's snapshot section) | 13,971 `file_exists` (8,757 false), 4,217 `directory_exists`, 4,340 `realpath`, 5,102 reads (26.3 MB), 355 `readdir`; 18.6k distinct paths **[measured]** | `~/.cache/perf-rearch/s1a/dumps4/umami-open-0a.dump.gz` |
| Cost of those operations replayed on one thread | **90–98 ms**: stat 2.0–2.3 µs, realpath (O_PATH open + readlink of `/proc/self/fd` + close) **6.3–6.4 µs**, read 4.9–5.3 µs, readdir 7.3 µs **[measured]** | `fsbench.c`, warm dentries, 4 runs |
| The same operations on 15 threads | **14–15 ms** wall (4 threads: 41 ms) **[measured]** | `fsbench` |
| Huge pages | With the guard default: THP off (WSL2 `page_reporting_order 5`), AnonHugePages 0, RSS 549 MB. With `GOPORT_THP_GUARD=0`: loader faults drop from 35k to 1.5k, but stime (≈170 ms) and loader time (313/318 ms against 309–323) don't change **[measured]** | `smaps_sampler.py`, 3+3 runs |
| Text copies per loaded file on the loader | Three: the read `String`, then `String`→`Arc<str>` (`project/overlayfs.rs:113-115`, which also hashes), then `fh.content()`→`String` in the parse-cache closure (`overlayfs.rs:23-25`, `project/parsecache.rs:185-191`). `cache_source_file` hashes again (`project/snapshotfs.rs:672-674`) **[read]** | code |

**Conclusions**
- **Page faults aren't the lever.** Turning THP on removes 95% of the faults and doesn't change stime.
- **About 90 of the roughly 150 ms of kernel time is the file-system calls [est.].** The other roughly 60 ms is unattributed: possibly fd-table and dentry contention with 15 workers doing the same calls, futex, or mmap. L0 measures it.
- **"One statx per path" saves 0 ms.** `file_exists` and `directory_exists` are already one `stat` each (`frontend/vfs/mod_impl.rs:357-366`), and no path asks for both in this load.
  - Merging stat + realpath + read for the 3,367 paths that need all three would save about 14 ms of CPU **[est.]**, but only on the workers once V exists.
- **Overlay-direct lookups save about 0 ms:** ttfd has one overlay.
- **Realpath is the most expensive operation per call** (28 ms over 4,340 calls; `frontend/vfs/osvfs.rs:562-584`, `frontend/nativepath.rs:41-63`). It belongs off the critical path, which V does.
- **Profile caveat:** the S1a breakdown's "OS calls" undercount the system calls. The leaf frames `stat`, `file_exists` and `directory_exists` total about 8 ms, while `/proc` shows 130–170 ms of stime.

---

## 1. The principle

S1a reused worker *answers* and then had to prove each one by repeating its uncached lookups through the snapshot, in serial call order, against the live OS. Most of the cost is the OS calls themselves.

v2 reuses worker *observations of the OS*, at the layer **below** every snapshot cache:

```
serial loader (unchanged code, one thread, serial order)
  → sourceFS / snapshot builder:  A cached files · C lookup cache · D aliases · G seen · H missing dirs
      → OverlayFS (overlays)
          → V: per-load frozen OS view (Sync, sharded; first answer wins; texts carry their hash)
              → OsFs (live OS)          ← parse workers fill V in parallel (they call V directly)
```

V is the CLI's existing idea, moved under the snapshot.
- The CLI already answers OS lookups from a per-load concurrent cache that the workers fill (`SharedStatCache`, `frontend/compiler/files_parser.rs:4978-5000`). Its accepted argument: "the load writes no file, so the output of the load is the same".
- The snapshot host is excluded today by `is_plain_os_fs()` (`frontend/compiler/file_loader.rs:362-369`).
- The `AheadLookupLayer` already sits exactly where V goes, under C (`project/snapshotfs.rs:49-60`, `:62-158`). S1a didn't attach first-load jobs there, because S1a's acceptance skipped calls at the answer level. v2 never skips a call.

**V's operations** are the OS-level `Fs` methods and nothing else:
- `stat` answers both `file_exists` and `directory_exists`, as in `Common`;
- `read_file` returns `Arc<str>` and the xxh3 hash, or a failure;
- `realpath`;
- `get_accessible_entries`.

**V's key** is the exact path string the snapshot passes, so each casing is its own key. A case-insensitive file system therefore needs no fallback.

**What the serial loader still does on its thread:**
- it makes every call it makes today, through A, C and the overlays. A C miss now costs a V hit (sharded map, about 0.1–0.3 µs) instead of an OS call;
- `get_file_by_path` (`project/overlayfs.rs:504-526`) gets the worker's `Arc<str>` and hash. The cached file is built with that hash (no rehash, no copy). `take_prefetched` compares by `Arc` pointer before falling back to `memcmp`.

---

## 2. Snapshot effects (findings 1, 2, 3, 5)

### 2.1 What a load mutates

Full inventory: Go `project/snapshotfs.go` (G), Rust `project/snapshotfs.rs` (R).

| State | Lifetime | Order-sensitive? |
|---|---|---|
| **A** cached files (text, hash, `needsReload`, tombstones). First writer wins (G 359-370, R 672-686) | carries over | Yes. Whether a `FileExists` lands in C depends on whether A already has the file (G 323-334, R 1176-1187). This is the order dependence that breaks v1's "union" argument |
| **C** lookup cache: FileExists, DirectoryExists, Realpath, Stat, entries. `ReadFile` is never cached (cachedvfs.go:97, cachedvfs.rs:156-158) | per snapshot (the next snapshot's C has 11 entries in the dump) | yes, through A |
| **D** node_modules realpath aliases. Recorded on the first A insert, calls Realpath into C (G 386-401, R 722-746) | carries over | yes |
| **E** cached directories, written only in `Finalize` from A's changes (G 219-261, R 546-605) | carries over | derived |
| **G** seen files: Track on every GetFile, GetFileByPath and FileExists, **including cache hits**; the last spelling wins (G 802-807, 839-862; R 1516-1535, 1610-1616) | per host | spelling only |
| **H** missing directories: every false DirectoryExists, **including hits** (G 850-856, R 1658-1668) | per host | no |
| **I** parse-cache refcounts (G compilerhost.go:95-101, R compilerhost.rs:312-326) | store | no |
| **J** package.json InfoCache: its calls go through sourceFS and so fill A, C, G and H | per program | yes |

### 2.2 Answers by construction

- **Finding 1 (tracking, cached hits, reads):** no worker log is ever replayed into the snapshot in L1 and L2. G and H are written by the serial code's own calls, on hits and misses alike, exactly where serial writes them. Reads are the snapshot's own `get_file_by_path`.
- **Finding 2 (cached content and precedence):** precedence is decided by unchanged snapshot code: A live/`NoValue`/`needsReload`/tombstone, then overlays, then C. V is reached only where serial would reach the OS.
  - **needsReload:** an entry marked `needsReload` re-reads from V. V is created when the program load starts inside the builder, after the change set that set `needsReload` was applied, so every V sample postdates it.
- **Finding 3 (adoption and persistence):** A, D and C get only what the serial code demands, at its point in the order. Speculative worker texts that the serial loader never asks for die with V at the end of the load.
  - **Auto-import** runs after the load. It never sees V and keeps its untracked, uncached reads (G autoimport.go:35-49, R autoimport.rs:51-80).
- **Finding 5 (clean fallback):** discovery writes only V and its private product store. The pre-load snapshot state is never touched by speculation, because the only writer is the serial commit, which *is* the serial loader. There is no whole-load fallback in L1–L3, so there is nothing to roll back. A product or answer that doesn't fit is recomputed at that point by serial code.
  - **Exception:** the strict mode's retry (§2.3) needs a load transaction.
- **Side benefit:** keyed (second) loads today accept answers through `agrees()` without a call through C (S1a branch `compilerhost.rs` `check_ahead_call`; inventory §6). That is the pre-existing cause of their lookup-cache differences against serial, and of their run-to-run variation (umami-edit, hono-edit). L1 removes it by making every call through C → V.

### 2.3 Disk changes during a load

Go and the serial loader both sample the disk at arbitrary times. Go fixes only successful reads (in A), and its lookups race. v2 gives:

- **G1 (default):**
  - **No change in the window:** if nothing the load observes changes between V's creation and the end of the load, the result is byte-identical to the serial loader.
  - **Otherwise:** the result is identical to *the serial loader run against V*. V is deterministic and recordable, so this is testable.
  - **What V's samples are:** each sample is a real disk state from inside the load window, and repeated calls in one load agree. That is more consistent than serial, which can see `FileExists=false` and then read the file successfully.
  - **Recovery:** the watch registrations equal serial-on-V's, so a change produces the same watch event and invalidation as it would after a serial load. A path whose observation wasn't tracked (DirectoryExists true, Realpath, Stat) is untracked in serial too.
- **G2 (strict mode, `GOPORT_LOAD_STRICT=1`, on in tests):**
  - **Revalidation:** at the end of the load, the workers revalidate every V entry in parallel:
    - stat → kind;
    - read → `(dev, ino, size, mtime_ns, ctime_ns)` recorded at read time;
    - realpath → repeated;
    - entries → directory mtime.
    - Cost: about 28k operations, which is about 15 ms on 15 threads, measured in `fsbench`; with `stat` in place of reads, probably ≤ 5 ms **[est.]**.
  - **If it passes:** V equals the disk at the end of the load on every observed path. The result then equals an instantaneous serial load at commit time, which is a consistent disk view (modulo ABA within the window, which ctime narrows).
  - **If it fails:** the load transaction is dropped and the load reruns with a fresh V, at most twice, then once on the live OS.
  - **The load transaction:**
    - A and D get a child `dirty` layer over the builder's maps (the dirty maps already layer over a base, `snapshotfs.rs:511`);
    - C gets a child `CachedFs` that falls through to the parent read-only;
    - G and H are already fresh per host attempt;
    - parse-cache acquires are released on drop;
    - commit replays the child's insertion journal in order into the parent, so `Finalize`'s iteration order is unchanged.
  - **Default:** turn strict on by default for the LSP only if L0 measures its cost at ≤ 3 ms on umami.
- **What changes against today:** only *when* each OS answer is sampled (earlier, by a worker). L1 must state this and test it (§6).

---

## 3. Discovery equivalence (finding 4)

L1 and L2 don't need it: the serial task machine decides arrivals, `LoadOrStore` representatives, lib against import, depth, elision, `packageId` and counters, unchanged (`files_parser.rs:707-765`). Products are taken **only by exact key**:
- **Parse product key:** path, file-name casing, text hash, script kind, the `ExternalModuleIndicatorOptions` derived from the metadata, and lib-ness.
- **Consequence:** a lib-arrival product can't stand in for an import arrival, and a wrong guess about the metadata is a miss.

**L3, memo frames** (generalizing S1a's checked answers):
- **What a frame records:** every memoized resolver computation records a frame of ordered events:
  - `Fs(op, path, observed answer)` at the **sourceFS level**, not the OS level;
  - `Memo(layer, key)` for each sub-memo it hit or created. The layers are module, type-ref, package.json `InfoCache`, package scope and parsed patterns, and every Rust-only cache in `Caches` (`frontend/module/cache.rs:277-300`).
- **Commit order:** commit runs the serial code. Where serial would call the resolver for K:
  - **The real per-load cache has K:** it's a hit, exactly as in serial.
  - **Otherwise, a frame exists:** it is replayed. Each `Fs` event is executed against the real snapshot (so C, A, D, G and H change exactly as in serial, hits included) and its answer is compared with the observed one. Each `Memo` event recurses the same way. The frame's result is installed only if every observation matched.
  - **On a mismatch** (stale A, `needsReload`, a tombstone, an overlay the worker didn't see): the serial resolver computes K for real from that point. The executed prefix is exactly a serial prefix, as in S1a.
- **Why it equals serial:** the commit-side memo state is serial's at every point, so hits and misses are serial's. The resolver is deterministic given (key, observations, sub-memo results), so the replayed event sequence is serial's call sequence.
  - Frames are per memo entry, not per answer, so a hit inside a worker never hides effects. A frame carries no package.json entries; those come only from `Memo(InfoCache, dir)` frames replayed on demand.
- **Duplicate frames:** two workers that computed the same key on V produce identical frames. Keep the first.

**L4, compact task machine and parallel discovery** (v1 §3.2–3.5, with the commit replaced):
- **Records are keyed per arrival class:** `(path, casing, class)`, where the class is lib (with its lib id), import or reference, auto-types, content-mapper supplemental, failed lookup, or redirect target. These are the arrival-specific fields of `ParseTask` (`files_parser.rs:22-66`).
- **The replay owns every observable item:**
  - the serial state machine over `Arrival`s picks the representative at `LoadOrStore` in serial order (Go `filesparser.go:279`, `:289-296`);
  - it sets the metadata (fixed CommonJS for lib, which skips the package.json lookups; Go `filesparser.go:109-116`), the counters, `packageId`, `lowestDepth`, `startedSubTasks` and elision.
- **A record whose class has no discovery product** is loaded on the commit thread by serial code ("repair", not fallback).
- **Edges** don't depend on depth; whether they *start* does, and the replay decides that.
- **Effects:** each record's load() frame (read, metadata `Memo(scope)`, parse-cache acquire, resolution `Memo`s in Go's subtask order, synthetic tslib/JSX creation) is replayed in replay load order. Then the unchanged walk (`files_parser.rs:965-1600`) runs.
- **No set comparison:** loaded set, started set and depths are *outputs* of the replay, not compared with discovery's. That removes v1's "S3 on serial records proves S4" gap.

---

## 4. Type-reference cache (finding 7)

- **Go:** the key has 5 fields, including `fromInferredTypesContainingFile` (`module/cache.go:31-37`), and `Set` is `Store` (`:47-49`). The flag changes the result: secondary lookup is skipped when `fromConfig && fromInferred` (`resolver.go:643`). The auto-types task resolves from `__inferred type names__.ts`, while a `/// <reference types>` doesn't.
- **Rust already matches:** key at `frontend/module/cache.rs:114-122`; `Store` at `:142-151`, and in the shared map at `:1430-1438`.
- **v2:**
  - **L1 and L2:** the serial code owns the cache.
  - **L3:** a frame is installed at the exact point serial would `Set`, once per miss, in serial order. Serial sets a key twice only under `traceResolution`, where `Get` is skipped (`resolver.go:440-444`), and frames are off under traces. So `Store` against `LoadOrStore` never differs observably.
  - **Discovery side:** frames are keyed by all 5 fields, and true/false for `from_inferred` are never merged. Since package.json effects travel as `Memo` events, type-ref frames *can* be shared across workers. That lifts S1a's "type-ref answers stay per worker".
- **Pointer identity:** the only `ptr_eq` on resolutions is a debug assertion on module commits (`file_loader.rs:1905`), so identity isn't observable.

---

## 5. Bind in the parse workers (S7)

**What doesn't block:** symbol and table ids don't depend on when a file is bound.
- Each file binds into its own arena (`program.rs:2674-2675`), and arenas join in file order with `ArenaOffsets` (`program.rs:1859-1861`, `:2708-2730`; `core.rs:3279-3292`, `:3600`).
- **Private-name class ids:** the binder only records the classes (`binder_p1.rs:1121-1128`). The ids come from `NEXT_SYMBOL_ID` in reverse file order after all binds (`program.rs:1926-1948`). That stays in `bind_all`.
- **Lazy global node and symbol ids:** the binder takes none, except `get_node_id(attributes)` for a pattern ambient module (`binder_p1.rs:1098-1103`). `bind_thread_fingerprint` (`program.rs:2001-2008`) already catches that and rebinds serially (`:2670-2681`).
- **The lib blobs** already encode bind output without the file index and add `file_index << 32` at decode (`lib_snapshot.rs:20-22`, `:730-733`).

**What blocks:**
1. The binder needs a published store with its real id: `go_file` (`binder_p1.rs:156`, `:244`, `:455-456`), `bind_records` panics if the store is unpublished (`store.rs:5830-5834`), and the registry reads (`store.rs:6816`, `:7060`).
2. The output embeds the file id: `FlowNodeId` (`binder_p1.rs:1550-1551`), and the `Node` handles in declarations, flow nodes, diagnostics, `private_name_classes` and the CommonJS indicator (`core.rs:1269-1278`, `:3640-3645`, `:3885-3913`).
3. Today's LSP parse cache refuses to bind on purpose (`parsecache.rs:227-233`). With the positional join, that reason no longer holds **[inferred]**.

**Design:**
- **(a) Preferred:** publish each store when it is adopted (the real id is assigned on the loader at adopt, `store.rs:4413-4458`). Then queue a bind job at below-parse priority. No remap is needed.
- **(b) If incremental publish is too invasive:** bind detached with file-relative handles, as the lib blob does. A parallel decode in `bind_all` adds the real index, at O(symbols + flow nodes). `StoreRemap::node` exists (`store.rs:4400-4406`).
- **Either way:**
  - the join stays in file order in `bind_all`;
  - private-name ids stay there;
  - a bind that moved a counter is dropped;
  - the `start_early_bind` gates apply: syntactic diagnostics, traces, single-threaded (`program.rs:2807-2836`).
- **Ids:** identical to today's by construction, since they're positional.
- **Observable id orders that must stay unchanged** (they do, since adopt order and the global counters don't change): `compare_symbols_worker` tie-break (`checker/utilities_p1.rs:720-722`), `FlowNodeId` sort (`flow_skip.rs:1239-1252`), `pattern@<nodeId>` names, and private-name Go ids in the API.

**Gain:**
- **ttfd:** the 33 ms bind becomes about 3–5 ms of join, so −25 to −30 ms **[est.]**. The workers sit idle once the loader is the bottleneck: 206 ms of bind CPU over 15 workers is about 14 ms.
- **CLI cold (vscode):** 0 to −100 ms **[est., uncertain]**. Workers are saturated from 100 to 425 ms, so binding there competes with parsing. The low-priority queue means leftover binds run in `bind_all` as today, which caps any regression. Measure before claiming anything.

---

## 6. Staged plan (ranked by ttfd)

Baseline: S1a fixed, loader about 300–320 ms, ttfd about 400–410 ms idle (window 1 and S1a manifests). Go: 283–295 ms.

| Stage | What | Size [est.] | ttfd | CLI cold | Proof | Kill condition |
|---|---|---|---|---|---|---|
| **L0** | `GOPORT_LOAD_TIMES` adds per-phase `RUSAGE_THREAD` (utime, stime, minflt) for setup, run_queue, collect, publish, bind; V hit/miss/late counters; `GOPORT_LOAD_VIEW_DUMP` | 150–300 lines | 0 | 0 | output unchanged (stderr or separate file only) | – |
| **L1** | **V under A, C and the overlays for every snapshot-host load.** Workers' `AheadFs` calls V. Read returns `Arc<str>`+hash. One copy per text, no rehash, `take_prefetched` by `Arc` pointer. Keyed loads stop calling `agrees()` without a call | 600–1,000 lines (reuses `StatMap`, `WorkerStats`, `AheadLookupLayer`) | loader −100 to −160 ms → **ttfd ≈ 265–300** | ≈0 (CLI already has `SharedStatCache`) | (1) load dump identical with V on and off in the 18 S1a scenarios (2 runs each plus serial with no ahead): every section, including C, A, D, InfoCache, seen, missing; (2) byte-identical protocol (`load-dump-session.mjs`, `compare-requests.py`); (3) parity full, including the LSP watch and fs-event sessions; (4) **V-replay oracle:** rerun the serial loader with OS = the recorded V and compare dumps, under a mutating disk; (5) strict-mode tests using `resolveahead_test.rs`'s `Mode::Force` hooks (package appears, file deleted, symlink repointed): detect, retry, equal serial on the post-change disk | loader not ≥ 60 ms lower (median of 5 idle runs); V hit rate < 90%; any first-load dump difference without a disk change |
| **L2** | Bind in workers, §5 (a), or (b) as fallback | 1–2k lines | −25 to −30 → **≈ 240–275** | 0 to −100 (vscode) | per-file bind digest A/B (`GOPORT_BIND_VERIFY`: symbols, tables, flow, diagnostics, `private_name_classes`) on corpus 12,727 + parity projects; lib-blob snapshot tests; parity full | join/remap on the loader > 5 ms; fingerprint rebinds > 1% of files; CLI vscode regresses > 1% (icount and wall) |
| **L3** | Memo frames, §3: S1a's answers on V, generalized per memo entry, with metadata scope frames. `ImportNames::of` moves into the parse product | 1.5–2.5k lines | −30 to −60 → **≈ 200–240** | ≈0 | L1's proofs plus mutation sensitivity (drop or reorder one event, and the dump must differ); mismatch counter | frame replay on the commit thread > 30 ms on umami; mismatch rate > 2% on real projects |
| **L4** | Compact `LoadGraph` and parallel discovery with the replay commit, §3 (v1 C1/C2) | 4–6k changed, 3–5k deleted | −20 to −40 | vscode main-thread floor 312 → ~70–120 ms | L1's proofs + `GOPORT_PARSE_THREADS` 1/2/4/32 × discovery shuffle seeds; repair counter | vscode load not ≥ 20% better; repairs > 1% of records |
| **L5** | Collect and publish overlapped, O(1) free (v1 S5) | 0.8–1.5k | −5 to −15 | vscode collect 26 → ~10 | parity full | < 10 ms total |

How the L1 estimate decomposes (loader thread):
- answer checks plus OS lookups: 110 → ~25 ms (22.5k calls at ~0.3 µs instead of 2–6 µs);
- reads and hashes: 49 → ~8;
- parse-cache acquire: 30 → ~20.

That is about −135 ms if the unattributed ~60 ms of kernel time is contention around these calls, and about −100 ms if it isn't.

v1's S2 (process-wide package.json *content* cache, E3b) is still worth doing for the CLI and is independent of all of this.

---

## 7. Risks and what stays empirical

- **The disk sampling time changes.** V samples at the worker's first call, earlier than serial would. G1 is the honest default guarantee and G2 the strict one. Astra should rule whether G1 is enough for the default, or whether strict must be on by default.
- **Any read during the load that bypasses V** reaches the OS live. That's harmless, since it is serial behavior, but it weakens G2's "consistent view". The config registry already goes through the builder's C. L0 adds a debug counter of live OS calls inside the load scope.
- **Workers staying ahead:** a faster loader might outrun them. A V miss is simply a serial OS call, so the risk is lost gain, not correctness. L0's "late" counter measures it.
- **The roughly 60 ms of unattributed kernel time** (and tick accounting at ±10 ms) is the main uncertainty in L1's range. L0's `RUSAGE_THREAD` per phase settles it.
- **Memory:** V's texts are the same `Arc`s that A keeps. Unused texts are freed when the load ends, on a worker as `AheadLookupLayer::drop` does today (`snapshotfs.rs:103-110`).
- **L2:**
  - thread-local seeds (`program.rs:2856`, `:3013`) and deep-file stack limits;
  - parses that are taken and then rejected waste their bind;
  - the CLI gain is unknown.
- **L3** must cover *every* memo layer of the resolver. A missed layer shows as a frame mismatch, which costs gain, not correctness. The mutation tests guard the replay code itself.
- **Empirical:**
  - V hit rate;
  - strict-mode cost;
  - worker-bind gain on vscode;
  - Go's run-to-run spread (395–462 ms bursts seen);
  - all ttfd numbers above are **[est.]** until idle A/B runs.

---

## 8. Sample manifest (CPU-split runs; not wall claims)

| set | runs | 1-min load | per-run result |
|---|---|---|---|
| sampler, load window | `S1a off` ×2, `S1a on` ×2 | 1.96–2.73 | u/s ms 160/130, 120/170, 120/150, 140/150; faults 35.6k, 36.4k, 33.1k, 32.5k |
| thread lifetime (earlier sampler) | 6 runs | 1.57–5.03 | 2 excluded (ttfd 628 and 1,981 ms); kept u/s 200/150, 160/160, 170/190, 180/160 |
| THP A/B | guard=0 ×3, default ×3 | 1.32–2.59 | loader 427 (outlier)/313/318 against 315/323/309; main-thread faults ~1.5k against ~35k |
| fsbench, 1 thread | 4 runs | 1.87–2.53 | 89.7–98.1 ms |
| fsbench, 15 threads | 2 runs | same | 14.0 and 15.0 ms |

## Artifacts

Tools, in [`scripts/perf-rearch/loader-v2/`](../../../scripts/perf-rearch/loader-v2/) (usage in its README.md):
- `fsbench.c`: replays a load's OS calls, per kind, on 1 or N threads;
- `tt-wrap.sh` + `tt_sampler.py`: per-thread utime, stime and faults, with a timeline;
- `sm-wrap.sh` + `smaps_sampler.py`: huge-page check (`smaps_rollup`);
- `classify.py`, `leaves.py`, `callers.py`: loader-thread views of a samply profile.

Data, in `~/.cache/perf-rearch/scratch/loader-v2/`:
- `umami-calls.tsv`: the call list from `dumps4/umami-open-0a.dump.gz`;
- run outputs: `tt-*.tsv`, `ttfd-*.json` (sampler runs), `thp-*` (THP A/B), `smaps.txt`.
- Profiles used: `~/.cache/perf-rearch/s1a/prof/fix3-{off,on}-*.json.gz`.

Bin: `~/.cache/perf-rearch/s1a/tsgo-s1a-98f94361a-goport` (`GOPORT_FIRST_LOAD_AHEAD=0/1`). I did not edit or commit any source.
