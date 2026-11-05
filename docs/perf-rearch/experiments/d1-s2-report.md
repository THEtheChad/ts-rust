# D1-S2 report (spike, branch t3/d1-s2-checker-certification, head 2ed12a6bc)

Design: `docs/perf-rearch/design/d1-s2.md` on the spike branch. Data: `~/.cache/perf-rearch/scratch/d1s2/`.

## Verdict
Continue D1.

## Triggers and fallback
- **Monotone limits** fire their group:
  - instantiation depth and count, using a virtual count that ignores Go's
    per-expression reset;
  - the relation budget, bounded after the pass;
  - union, cross-product and tuple sizes; constraint cut; flow depth.
- **Finding-5 triggers:**
  - a store into a later group file held by another sub-checker (0 fires);
  - a #4825 merge site.
- **Cycles:** checked by agreement (D1 §3.4), not by firing; firing would
  cost vscode its whole gain.
- **Fallback:** whole-process re-exec as Go c4, buffered output, from the
  first fire. Per-group fallback differs by under 7% of c4 on zod.

## Soundness data
- 17,850 runs: 66 projects plus 1,719 corpus cases, across c4, c8–16,
  r8–16 and both inner splits.
- Divergences are exactly D1-S1's (zod): every one is caught (6/6 refine,
  9/9 Go-N).
- No warm-only events.
- Counts are monotone for every kind except `deep_true` (57 group-runs).
- zod's divergence is Go's own: Go `--checkers 8` and `--checkers 16`
  give the same bytes.

## Not certifiable
`deep_true`, id tie-breaks, union id keys and the resolving sentinels fire
in 98–100% of groups. They are left to large-scale testing, under Chad's rule
("default if tests pass").

## Expected noemit wall at r16 after fallback

| | wall | vs c4 | vs Go |
|---|---|---|---|
| vscode | 2.86 s | −51% | 0.33× |
| t3-server | 2.69 s | −34% | 0.26× |
| umami | 0.472 s | −32% | 0.37× |
| hono | 0.089 s | −28% | 0.34× |
| zod (always falls back) | 0.48–0.50 s | +9–13% | 0.49× |

About 31% of parity-set runs fall back.

## Validation plan
- About 17 machine-hours: N sweep plus seeded refinements, about 20 more
  repos, holdout variants against Go, the 319 group-splitting corpus cases,
  8 generator families, a mutation test per trigger, and order-perturbation
  runs.
- Kill condition: any miss outside the id-order mechanisms.

## Final shape
- The certified split replaces both FENNEL in the default pool and fast
  mode (C3's deletion list).
- `--checkers N` keeps Go's N-way split.

## Gates
- Parity full OK (`runs/20261104-201514-full-8064577a17`).
- Instrumentation is diagnostics-only.
