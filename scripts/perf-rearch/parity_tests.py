#!/usr/bin/env python3
"""Test tier of the parity gate: the cargo test suites and go_baselines TestLocal, as CI runs them,
with a result per test name.

usage: parity_tests.py --out DIR [--checkout DIR] [--tier quick|full] [--jobs N] [--shards N]
                       [--runner-jobs N] [--no-build]

It builds (scripts/run-cargo-capped.sh, the edit-loop release build of the checkout), with the
`diagnostics` and `test-hooks` features, so the tests of the diagnostic and verify code and of the
test hooks run too, as in ci.yml's diagnostics and test-hooks step (FEATURES):
  cargo test --release --locked --workspace --features ts_goport/diagnostics,ts_goport/test-hooks --no-run
  cargo test --release --locked -p ts_goport --features ts_goport/diagnostics,ts_goport/test-hooks --test go_baselines --no-run
(ci.yml builds go_baselines with diagnostics only; test-hooks does nothing until a test sets a hook,
and one feature set keeps it to one build of the crates.)
and runs the test binaries as ci.yml's test and test-local jobs do: in the crate dir, with
RUST_TEST_THREADS=1, TS_GO_REPO at the pinned Go checkout (tsc/ of microsoft__TypeScript@3e9236361fa9)
and its own TSCTEST_TMP and COMPILER_RUNNER_TMP per binary:
  - every test binary of the workspace (full; quick: those the quick manifest's binaryPrefixes name
    beside go_baselines, parity-quick-manifest.json), --jobs binaries at a time (each binary is its
    own process with its own temp dirs, so binaries can run side by side; inside a binary one thread);
  - go_baselines, the default set, without TestLocal (`--exact --skip compiler_runner::test_local`);
  - go_baselines TestLocal in --shards shards (COMPILER_RUNNER_SHARD=i/n, COMPILER_RUNNER_JOBS
    --runner-jobs each), every case result from COMPILER_RUNNER_RESULTS.
Cargo doc tests are not run (ci.yml's `cargo test --workspace` runs them; see parity.md).

Items: test/<binary>/<test name> from libtest's --logfile (and --list for names without a result:
UNRUN), test-local/<kind> <key> from the compiler runner rows. Status PASS, FAIL, IGNORED, UNRUN, and
FLAKY: a test of a binary that failed and then passed when it ran once more, alone (good in compare,
and listed).
Output: DIR/results.json, DIR/logs/<suite>.{log,list,results}, DIR/test-local-<i>.tsv. Exit 0
unless the build fails (exit 3).
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # python3 -I leaves the script dir out
import parity_modes as modes  # noqa: E402
# The test builds have the diagnostics feature (crates/ts_goport/Cargo.toml): tests/plain_early_emit.rs
# needs it, and the reuse verify, call log and phase report tests run only with it. test-hooks: the
# panic, stall and write tests of plain_early_emit, the emit panic test of tests/emit_pool.rs and the
# shutdown fault tests of execute/watchmanager run only with it.
FEATURES = 'ts_goport/diagnostics,ts_goport/test-hooks'
GO_REPO = Path.home() / '.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc'


def build(checkout, args):
    """[(suite name, executable, crate dir)] of a cargo test --no-run."""
    cmd = [str(checkout / 'scripts/run-cargo-capped.sh'), 'test', '--release', '--locked', *args, '--no-run',
           '--message-format=json-render-diagnostics']
    r = subprocess.run(cmd, cwd=checkout, stdout=subprocess.PIPE, text=True)
    if r.returncode:
        sys.exit(f'build failed: {" ".join(cmd)}')
    out = []
    for line in r.stdout.splitlines():
        if not line.startswith('{'):
            continue
        d = json.loads(line)
        if d.get('reason') == 'compiler-artifact' and d.get('executable') and d['profile']['test']:
            name = d['target']['name'] + ('' if d['target']['kind'] == ['test'] else '.' + d['target']['kind'][0])
            out.append((name, d['executable'], str(Path(d['manifest_path']).parent)))
    return out


def run_suite(out, name, exe, crate, extra_args=(), extra_env=None, timeout=3600):
    logs = out / 'logs'
    tmp = out / 'tmp' / name
    (tmp / 'tsctest').mkdir(parents=True, exist_ok=True)
    (tmp / 'runner').mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('GOPORT_', 'COMPILER_RUNNER_', 'TRANSPILE_RUNNER_',
                                                                       'TSCTEST_', 'TS_GOPORT_'))}
    env.update(RUST_TEST_THREADS='1', TS_GO_REPO=str(GO_REPO), TSCTEST_TMP=str(tmp / 'tsctest'),
               COMPILER_RUNNER_TMP=str(tmp / 'runner'), CARGO_MANIFEST_DIR=crate, **(extra_env or {}))
    with open(logs / f'{name}.list', 'w') as f:
        subprocess.run([exe, *extra_args, '--list'], cwd=crate, env=env, stdout=f, stderr=subprocess.STDOUT)
    start = time.monotonic()
    with open(logs / f'{name}.log', 'w') as f:
        try:
            rc = subprocess.run([exe, *extra_args, '--logfile', str(logs / f'{name}.results')], cwd=crate, env=env,
                                stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 'timeout'
        f.write(f'\nexit={rc}\n')
    secs = round(time.monotonic() - start, 1)
    listed = [l[:-len(': test')] for l in (logs / f'{name}.list').read_text(errors='replace').splitlines()
              if l.endswith(': test')]
    got = {}
    res = logs / f'{name}.results'
    if res.exists():
        for l in res.read_text(errors='replace').splitlines():
            word, _, rest = l.partition(' ')
            status = {'ok': 'PASS', 'failed': 'FAIL', 'ignored': 'IGNORED', 'ignored:': 'IGNORED'}.get(word)
            if status:
                got[rest.split(' ')[-1]] = status
    items = {f'test/{name}/{t}': got.get(t, 'UNRUN') for t in listed}
    for t, s in got.items():
        items.setdefault(f'test/{name}/{t}', s)
    print(f'{name}: exit={rc} {dict(Counter(items.values()))} {secs}s', flush=True)
    return items, {'exit': rc, 'secs': secs}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--checkout', default=str(HERE.parents[1]))
    ap.add_argument('--tier', default='full', choices=('quick', 'full'))
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--shards', type=int, default=4)
    ap.add_argument('--runner-jobs', type=int, default=8)
    ap.add_argument('--no-build', action='store_true', help='use the binaries of the last build (build list in DIR)')
    args = ap.parse_args()
    out = Path(args.out)
    (out / 'logs').mkdir(parents=True, exist_ok=True)
    checkout = Path(args.checkout).resolve()
    t0 = time.monotonic()
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=checkout,
                           capture_output=True, text=True).stdout.strip() != ''
    if args.tier == 'quick':  # the quick manifest's binaries (go_baselines is always built below)
        prefixes = modes.working_manifest().test_prefixes
        suites = [] if set(prefixes) <= {'go_baselines'} else [
            s for s in build(checkout, ['--workspace', '--features', FEATURES]) if s[0].startswith(prefixes) and s[0] != 'go_baselines']
    else:
        suites = build(checkout, ['--workspace', '--features', FEATURES])
    gb = build(checkout, ['-p', 'ts_goport', '--features', FEATURES, '--test', 'go_baselines'])
    gb = [s for s in gb if s[0] == 'go_baselines']
    assert len(gb) == 1, gb
    _, gb_exe, gb_crate = gb[0]
    build_secs = round(time.monotonic() - t0, 1)
    print(f'build {build_secs}s: {len(suites)} workspace test binaries and go_baselines', flush=True)
    items, meta, lock = {}, {}, threading.Lock()

    def job(spec):
        name, exe, crate, extra_args, extra_env = spec
        it, m = run_suite(out, name, exe, crate, extra_args, extra_env)
        if name.startswith('go_baselines_local-'):
            tsv = Path(extra_env['COMPILER_RUNNER_RESULTS'])
            rows = tsv.read_text(errors='replace').splitlines() if tsv.exists() else []
            m['cases'] = 0
            for row in rows:
                parts = row.split('\t')
                if parts[0] in ('pass', 'fail', 'skip') and len(parts) >= 3:
                    it[f'test-local/{parts[1]} {parts[2]}'] = {'pass': 'PASS', 'fail': 'FAIL', 'skip': 'IGNORED'}[parts[0]]
                    m['cases'] += 1
        with lock:
            items.update(it)
            meta[name] = m

    specs = []
    for i in range(args.shards):
        specs.append((f'go_baselines_local-{i}', gb_exe, gb_crate, ['--exact', 'compiler_runner::test_local'],
                      {'COMPILER_RUNNER_SHARD': f'{i}/{args.shards}', 'COMPILER_RUNNER_JOBS': str(args.runner_jobs),
                       'COMPILER_RUNNER_RESULTS': str(out / f'test-local-{i}.tsv')}))
    specs.append(('go_baselines', gb_exe, gb_crate, ['--exact', '--skip', 'compiler_runner::test_local'], {}))
    specs += [(name, exe, crate, [], {}) for name, exe, crate in suites]
    # The shards first (the longest), then the rest; --jobs suites at a time beside the shards.
    with ThreadPoolExecutor(args.shards + args.jobs) as pool:
        list(pool.map(job, specs))
    # A failed test of a test binary runs once more, alone (timing tests such as
    # tsgo_stdio_end/watch_ends_soon_after_sigint_or_sigterm fail under load): FLAKY when it passes then.
    by_name = {spec[0]: spec for spec in specs}
    for k in sorted(k for k, v in items.items() if v == 'FAIL' and k.startswith('test/')):
        suite, test = k[len('test/'):].split('/', 1)
        name, exe, crate, _, extra_env = by_name[suite]
        retry, _ = run_suite(out, f'{suite}.retry', exe, crate, ['--exact', test], extra_env)
        if retry.get(f'test/{suite}.retry/{test}') == 'PASS':
            items[k] = 'FLAKY'
    counts = Counter(items.values())
    summary = {'tier': args.tier, 'checkout': str(checkout), 'head': head, 'dirty': dirty, 'goRepo': str(GO_REPO),
               'buildSecs': build_secs, 'wallSecs': round(time.monotonic() - t0, 1), 'counts': dict(counts),
               'suites': meta, 'items': {k: {'status': v} for k, v in sorted(items.items())}}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'tests: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
