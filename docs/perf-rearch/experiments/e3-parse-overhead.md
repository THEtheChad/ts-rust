# E3 spike: parse-worker overhead (2026-11-04)

Lane E3 (Haiku 5.5). Branch `t3/investigate-parse-overhead`, prototype
`da66923f1` (not merged). Measured with per-thread `perf_event_open` scopes
in an instrumented build; the patch is
`~/.cache/perf-rearch/spike/instrumentation.patch`.

## Findings (umami parse-only, user instructions)

- **The audit's suspects are each under 1%:** identifier caches (per-byte
  parse cost is equal on workers), JSDoc adoption (3–4M), diagnostic clones
  (about 1M), specifier copies (about 2M).
- **The overhead is duplicated resolution work, and it scales with worker
  count** (+14% at 1 worker, +27% at the default 15). So the audit's "fixed
  +13%" is wrong.
  - **A race:** the loader takes a worker's parse before the worker
    publishes its prep, then resolves that file's imports again. That is
    15–16k calls, +0.12 to +0.15G.
  - **`package.json` files are parsed in every resolver cache:** 685 parses
    with no workers, 1,002 at 1 worker, 1,771 at default. That costs about
    0.77G more than with no workers, at default.
  - Shared stat-cache lookups (≤0.17G), `ImportNames` (+0.03–0.05G), and
    queue and threads (+0.03G) make up the rest.
- **Prototype `da66923f1`:** `take_prep` waits for a per-job `prep_ready`
  flag. Parity full is OK.
  - It saves 0.18G at 1 worker and nothing at 0 or default.
  - It makes the loader block on a worker, so it needs an idle wall check
    before any merge.

## New lane candidate E3b

**A shared package.json info cache across workers and the loader.**
- **Gain:** about 0.8G of 5.0G default parse-only (about 15%). It also helps
  the LSP per-edit rebuild, where resolution is 54% inclusive.
- **Size:** `Rc` → `Arc` and `RefCell` → locks across about 73 sites in 13
  files (including `Fields`).
- **Overlap:** program audit candidate 3 (one concurrent resolution cache);
  design them together.
