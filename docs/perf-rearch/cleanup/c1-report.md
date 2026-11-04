# C1 report (lane `t3/remove-old-env-paths`, head 3976fc415, base ba5d3f8b7)

Full lane log: `docs/perf-rearch/cleanup/c1.md` on the lane branch.

- **Removed (15):** `GOPORT_DTS_TWIN`, `EARLY_EMIT`, `PLAIN_EARLY_EMIT`,
  `REUSE_SYNTHETIC`, `EARLY_BIND`, `KEEP_VERSION_TABLES`, `SYNTHETIC_OWNERS`,
  `OWNED_NODES`, `FREE_FILE_VERSIONS`, `FLOWSKIP` (`=0`), `N1`, `LIB_SNAPSHOT`,
  `LIB_PARSE_SNAPSHOT`, `DTS_WALK_FALLBACK`, `JEMALLOC_LAYOUT`.
- **Kept:**
  - `GOPORT_LAZY_MEMBERS`: a fast-mode part; belongs to C3.
  - `THP_GUARD` and `THP_GUARD_MIB`: operational knobs.
  - `LAUNCH`: `=0` is needed for wait4 RSS and CPU measurements.
- **Non-shipped paths that look faster:**
  - `THP_GUARD=0`: zod minor faults drop from 128k to 4k, about −6% CPU.
  - `LAZY_MEMBERS=1`: −4–15% peak memory, but the Symbols count differs from Go.
- **diagnostics cargo feature:** gates `VERIFY_REUSE*`, `REUSE_STATS`,
  `CHECKER_CALL_LOG`, `EMIT_PHASE_TIMES`, `DTS_TWIN_CHECK`, `FLOWSKIP=verify`
  and the lib snapshot `trace`. `parity_tests.py` builds the tests with it.
- **Other cleanup:**
  - `fs_write_through!` removes the duplication between trackingvfs and
    reuse_verify.
  - Repo clippy warnings: 5 → 1, a toolchain `deprecated` warning in
    generated code.
- **Lines:** prod net −152; tests −76; Go fixtures +1,186.
- **Parity:**
  - Full #2 (`runs/20261104-160759-full-5485734971`): 0 lost, apart from 3
    tests deleted on purpose.
    - `file_version_test::flag_off_keeps_every_file_version_static`
    - `file_version_test::owned_nodes_off_keeps_node_data_leaked`
    - `print_scope_trace_types_do_not_change`, renamed `print_scope_trace_types_are_go_types`.
  - PARITY OK against `~/.cache/perf-rearch/c1/base-minus-removed`.
  - Full #1 lost `lsp/hono-f12-p6/0057`, an existing `rs-schedule`
    nondeterminism that the base also shows.
  - Flaky test, failing in both full runs and passing alone:
    `tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm`.
- **Instructions:** goport profile on both sides, query, umami and hono
  st/mt. Every cell is within noise (−0.32% … +0.04%).
- **Bins:** `tsgo-c1base-ba5d3f8b7-goport`, `tsgo-c1head-c07b531f1-goport`.
- **Open:**
  - the pure `--noEmit` d.ts-walk build info is not compared with Go (asked of
    Astra);
  - the `fast_mode.rs` comment about per-part switches (C3).
