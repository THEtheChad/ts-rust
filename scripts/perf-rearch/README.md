# perf-rearch tools

Timing and profiling tools for the `perf/first-principles` lanes (brief:
`docs/perf-rearch/brief.md`, baseline results: `docs/perf-rearch/baseline.md`).
Every tool prints its usage with `help` (or `--help`).

| Tool | What it does |
|---|---|
| `setup-projects.sh` | Clones the benchmark projects at their pins into `~/.cache/perf-rearch/projects` and installs their dependencies without install scripts. Pins and configs: `projects.tsv`. |
| `bench.sh` (`bench.py`) | Times CLI and LSP scenarios for labelled binaries, load-gated and interleaved; writes `runs.jsonl`, `results.json` and `results.md`; `bench.sh table` merges result dirs into one gap table. |
| `baseline.sh` | The whole baseline set for one tsc-rs binary against Go (icount, CLI wall, LSP, THP check), resumable; ends with `gap.md`. |
| `lsp-ttfd.mjs` | Time to first diagnostics: spawn the server, initialize, open one file, pull its diagnostics. |
| `quality.sh` (`quality.py`) | Code-quality report for `crates/` at a revision, with a delta against a base: escape hatches (`GOPORT_*`/`TSC_RS_*`/`TSGO_*` switches, from rustc's expanded source), per-function complexity (rust-code-analysis), duplication, churn and hygiene, plus clippy in `report` (repo config on CI's toolchain, forced audit on stable); `history` walks the branch's merges; `gating` lists diagnostics code still in the shipped build; `report` and `ranking` add the Go-relative ranking (port bloat against Go at the pin, own code over cog 25). Report only (`docs/perf-rearch/quality.md`). |
| `qexpand.py`, `qexpand_wrapper.py` | The source a build compiles, from rustc: `cargo check --release` with a `RUSTC_WORKSPACE_WRAPPER` that reruns each workspace crate's rustc command with `-Zunpretty=expanded`, cut into one piece per module file (rustc's module rules, checked against its dep-info). Feeds `quality.sh`'s escape hatches and `gating`. |
| `qgating.py`, `gating-allow.txt` | `quality.sh gating`: the rules (env, module, topic, dead, name) and the reviewed exceptions (`docs/perf-rearch/quality.md` section 9). |
| `quality_test.py`, `quality_fixture/` | Self-test of `qexpand.py`, the inventory and `gating` on a fixture crate (`python3 -I scripts/perf-rearch/quality_test.py`, about 5 s). |
| `gorel.py`, `gocog/` | Go-relative complexity for `quality.sh`: `gocog` scores Go functions with rust-code-analysis's rules (`go/ast` only), `gorel.py` maps each Rust function to its Go counterpart (`// Go:` markers, prose, names) and ranks. `gorel_test.py` tests both (`python3 -I scripts/perf-rearch/gorel_test.py`). |
| `samply_top.py` | Summarizes a samply profile: on-CPU time per thread, per phase and phase × thread, top self and inclusive frames, callers of a function. |
| `../lspdiff/` | The LSP harness (`lspdiff.mjs bench`, `probe-edit.mjs`), from branches `perf/lsp-concurrency` and `perf/lsp-edit-cost`. `bench.sh lsp` wraps it. |

## Projects

```sh
scripts/perf-rearch/setup-projects.sh            # all of projects.tsv (about 12 GB, 10 minutes)
scripts/perf-rearch/setup-projects.sh hono zod   # some
```

`projects.tsv` names each project, its repo and commit, its tsconfig and its
installer. Several rows can share a repo (T3 Code's web and server apps).
Lockfiles decide the installer: pnpm, bun (Hono, with
`prepare-hono-inputs.mjs`'s bun 1.2.20 and flags), or `npm ci` for the two
`package-lock.json` repos (ts-pattern, VS Code), as `scripts/bench-apps` does,
so the dependency tree is the lockfile's. umami is a symlink to the LSP
harness's copy (`~/.cache/lspdiff/projects/umami`). The setup also applies
`scripts/bench-apps`' fixes: electron typings for VS Code, and T3 Code configs
without their Effect plugins (`tsconfig.noeffect.json`; Go ignores plugins,
tsc-rs runs the Effect diagnostics built in, so with plugins the two sides
would not do the same work).

The projects are inputs: nothing writes into them. `bench.sh` sends emit
output and build info to its scratch dir, and stops if `git status` of an
input changes during a scenario. Emit and `-b` run on a scratch copy of the
repo (git files, the top-level `node_modules` linked, package `node_modules`
copied with their links, so workspace packages stay inside the copy). Emit
also gets `--rootDir` at the copy's root: TypeScript 7 stops at TS5011 or
TS6059 before checking when outDir is set without a fitting rootDir. And
T3 Code's web config makes both compilers write JS next to sources outside
its root. The emit copy drops files a run added before the next run.

## Timing

```sh
# all CLI scenarios on every project, tsc-rs against Go (defaults: the shipped main build and the Go oracle)
scripts/perf-rearch/bench.sh cli --bin rs=PATH/tsgo --bin go=~/.local/bin/tsgo-oracle-3e9236361fa9 \
  --scenarios noemit,emit,single,incr,build --runs 3 --out ~/.cache/perf-rearch/runs/my-lane
# two tsc-rs builds against each other: make one of them the reference
scripts/perf-rearch/bench.sh cli --bin base=A/tsgo --bin new=B/tsgo --ref base --projects vscode --scenarios noemit
# LSP: time to first diagnostics, per-edit probe (.tsx and .ts), concurrent typing at 120/250/500 ms/key
scripts/perf-rearch/bench.sh lsp --bin rs=PATH/tsgo --bin go=~/.local/bin/tsgo-lsp-oracle-3e9236361fa9
# one table from several result dirs
scripts/perf-rearch/bench.sh table ~/.cache/perf-rearch/baseline/cli ~/.cache/perf-rearch/baseline/lsp --out gap.md
```

Run anything longer than 10 minutes under systemd, so a session end does not
kill it, in `app-batch.slice` at nice 10 so it does not outrank the agents for
CPU, with a log that ends `DONE` or `FAIL rc=N`:

```sh
systemd-run --user --collect --slice=app-batch.slice --nice=10 \
  -p MemoryMax=20G -p MemorySwapMax=0 --unit=pr-bench \
  --working-directory=$PWD --setenv=PATH=$PATH --setenv=HOME=$HOME \
  bash -c 'scripts/perf-rearch/bench.sh cli ... --out D > D.log 2>&1 && echo DONE >> D.log || echo "FAIL rc=$?" >> D.log'
```

A rerun with the same `--out` skips the runs already in `runs.jsonl`, so an
interrupted run resumes.

Method (the brief's): each run starts only when the 1-minute load is under
1.5 (`MAX_LOAD`); the sides alternate run by run, and the order rotates; each
(scenario, project, side) gets one unrecorded warm-up run and `--runs`
recorded ones; the table gives the median and the min..max. The load before
and after each run is recorded. A run is repeated (twice at most, then
flagged `(load)`) when the load after it is above 3 (`REPEAT_LOAD`) plus what
the run itself adds to the 1-minute average (its CPU/wall times
1 − e^(−wall/60)): a 16-thread compile of 10 s raises the load by about 2.5
on its own, so a bare "above 3" rule would repeat every large run.
`other_cpu` in `runs.jsonl` is the CPU time the rest of the machine used
during the run (from `/proc/stat`), a direct noise check.

CPU and RSS: `bench.py` is a child subreaper, so it reaps tsc-rs's worker
process when the launcher exits first, and adds the worker's `wait4` rusage
(user+sys, max RSS) to the launcher's. `GOPORT_LAUNCH` stays at its default,
so the wall time is what a user waits for (the launcher exits when the worker
sends its exit code, before the worker unmaps its memory); `drain` is the
time until every process has exited. `PR_LAUNCH=0` runs with
`GOPORT_LAUNCH=0` instead.

Scenarios: see `bench.sh help`. Timing builds are stable builds
(`TS_CARGO_NIGHTLY=0 TS_CARGO_INCREMENTAL=0 RUSTUP_TOOLCHAIN=1.95.0`) or
`--profile goport`; never compare an edit-loop build.

## Profiling

### samply for user space only (`~/.local/bin/samply-user`)

`perf_event_paranoid` is 2 and there is no sudo. Stock samply 0.13.1 always
asks for kernel samples and fails; user-space-only sampling of your own
processes works at level 2. Build it from the crates.io source, checked
against the index's sha256, with the three `.sample_kernel()` calls removed:

```sh
mkdir -p ~/.cache/perf-rearch/src/samply-dl && cd ~/.cache/perf-rearch/src/samply-dl
curl -fsSL -o samply-0.13.1.crate https://static.crates.io/crates/samply/samply-0.13.1.crate
curl -fsSL https://index.crates.io/sa/mp/samply | grep '"vers":"0.13.1"'   # cksum field
sha256sum samply-0.13.1.crate   # must equal it: 4522bce933096db8be451224bde3dbb1ce640748170dce8aca1edbdad7c82fff
cd .. && tar xzf samply-dl/samply-0.13.1.crate && cd samply-0.13.1
sed -i 's/^\(\s*\)\.sample_kernel()$/\1\/\/ .sample_kernel() \/\/ user-space only (perf_event_paranoid=2)/' \
  src/linux/perf_group.rs
grep -c '// .sample_kernel()' src/linux/perf_group.rs   # 3
cargo +1.95.0 build --release --locked -j 8
cp target/release/samply ~/.local/bin/samply-user
```

The 2026-11-03 build's sha256 is
`3f2f310b8c1b93af63fd98ebd724687ab8a066d88eab6029f914d1fa6e8533b9`.

### A tsc-rs profiling build

Stable, release, line tables (frames keep their inlined callers):

```sh
TS_CARGO_JOBS=16 TS_CARGO_NIGHTLY=0 TS_CARGO_INCREMENTAL=0 RUSTUP_TOOLCHAIN=1.95.0 \
  CARGO_PROFILE_RELEASE_DEBUG=line-tables-only scripts/run-cargo-capped.sh build --release -p ts_goport --bin tsgo
```

The main @ 91f3d021c build is `~/.cache/perf-rearch/bins/tsgo-main-91f3d021c-prof`.

### Record and summarize

```sh
cd ~/.cache/perf-rearch/projects/vscode
GOPORT_LAUNCH=0 samply-user record --save-only --unstable-presymbolicate -r 2000 \
  -o ~/.cache/perf-rearch/profiles/vscode-noemit-rs.json.gz -- \
  ~/.cache/perf-rearch/bins/tsgo-main-91f3d021c-prof -p src/tsconfig.json --noEmit --incremental false --pretty false
python3 -I scripts/perf-rearch/samply_top.py ~/.cache/perf-rearch/profiles/vscode-noemit-rs.json.gz --top 25
```

- `GOPORT_LAUNCH=0`: profile the compiler, not a launcher waiting on a worker.
- `--unstable-presymbolicate` writes the `.syms.json` sidecar that
  `samply_top.py` reads (inlined frames included). `samply load FILE` opens
  the profile in the Firefox Profiler UI.
- Samples are weighted by on-CPU time (`threadCPUDelta`), so idle waits do not
  count. Phases come from the innermost frame whose module names one (the
  `PHASES` table in `samply_top.py`): a parse the checker triggers counts as
  parse, a checker query from declaration emit counts as check.
- LSP: `node scripts/lspdiff/probe-edit.mjs --window W.json ...` under
  `samply-user record`, then `samply_top.py --window W.json` keeps only the
  edit loop (samply's sample clock is `CLOCK_MONOTONIC`, as is node's
  `process.hrtime`).
- The Go oracle has no symbols table samply can split by package as well as
  pprof; for Go, `probe-edit.mjs --cpu-profile DIR` and `go tool pprof`.
