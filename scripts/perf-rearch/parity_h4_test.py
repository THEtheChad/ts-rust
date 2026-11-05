#!/usr/bin/env python3
"""Negative controls of lane H4 (Python side): Astra's H3 re-review (docs/perf-rearch/reviews/
2026-11-05-astra-h3.md), one check per false-good path, run as the reviewer ran it where there is a
probe (~/.cache/perf-rearch/scratch/review-h3-astra/), plus a check for each new mechanism's own
bypass (a shrunk manifest, a stale grandfather entry, a staged cache entry of a run that is then
invalidated). The JS side is vendor/lspdiff/selftest/h4-selftest.mjs.

usage: python3 -I scripts/perf-rearch/parity_h4_test.py [--keep] [--only SUBSTRING]

Each check prints `ok` or `FAIL`; the last line is `H4 SELFTEST OK` or `H4 SELFTEST FAIL: ...`. The
checks run against older harness versions too (copy this file into `git archive <sha>
scripts/perf-rearch`): they use the old entry points where the new ones are missing, so that a
false good of the old harness shows as a FAIL there and not as an error of the test. About 1 minute.
"""
import argparse
import contextlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'vendor/difffuzz'))
import parity_compare as C  # noqa: E402
import parity_modes as M  # noqa: E402

FAILS = []
NEW = hasattr(C, 'Accepted')  # the lane H4 compare (accepted artifacts)


def check(name, ok, got=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else f": got {str(got)[:1500]!r}"}')
    if not ok:
        FAILS.append(name)


def script(path, text):
    path.write_text('#!/usr/bin/env python3\n' + text)
    path.chmod(0o755)
    return path


# ---- compare: known-bad items, grandfather entries, the manifest (findings 3, 4, 7, 12) --------

ROW_795 = {'status': 'DIFF', 'rsFailure': 'crash', 'sig': 'oracle:crash',
           'detail': 'go-broken (GO-FAIL: Go crash: panic: runtime error: invalid memory address or nil pointer dereference), '
                     'tsc-rs crash: panic: runtime error: invalid memory address or nil pointer dereference; attempts: run1 crash '
                     '(0.01 s of 60), run2 crash (0.01 s of 60)'}
PRINT = {'outcome': 'crash', 'code': 2, 'stdout': '0' * 16, 'stderr': '1' * 16, 'written': '2' * 16, 'inputs': 'intact'}
GO_795 = {'status': 'GO-FAIL', 'outcome': 'crash', 'panic': 'panic: runtime error: invalid memory address or nil pointer dereference',
          'stdout': '4' * 16, 'written': '5' * 16}
ENTRY_795 = {'status': 'DIFF', 'rsFailure': 'crash', 'reason': 'tsc-rs reproduces Go\'s crash on purpose', 'input': 'a' * 16,
             'go': GO_795, 'attempt': PRINT, 'attempts': {'full': ['run1', 'run2'], 'quick': ['run1']}, 'evidence': 'selftest'}
OLD_795 = {'status': 'DIFF', 'rsFailure': 'crash', 'reason': 'tsc-rs reproduces Go\'s crash on purpose',
           'detailContains': 'tsc-rs crash: panic: runtime error: invalid memory address or nil pointer dereference'}
ROW_25496 = {'status': 'DIFF', 'rsFailure': 'timeout',
             'detail': 'tsc-rs timeout in an attempt: run1 timeout (60.14 s of 60), run1 retry exit 2 (86.66 s of 120)'}


def full_row(row, prints, labels=('run1', 'run2')):
    return {**row, 'input': 'a' * 16, 'go': GO_795,
            'attempts': [{'why': w, 'secs': 0.01, 'timeout': 60, **p} for w, p in zip(labels, prints)]}


def run_compare(root, name, base, cur, mode='full', grand=None, old_grand=None, manifest=None, raw_grand=None):
    """(exit code, output) of parity_compare on two result dirs, with the accepted artifacts given (the
    lane H4 compare) or the old grandfather file (an older compare)."""
    d = root / name
    for sub, rows, m in (('base', base, 'full'), ('cur', cur, mode)):
        (d / sub).mkdir(parents=True)
        (d / sub / 'results.json').write_text(json.dumps({'meta': {'mode': m}, 'tiers': {'fuzz': {'items': rows}}}))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        if NEW:
            acc = d / 'accepted'
            acc.mkdir()
            m = manifest or json.loads((HERE / 'parity-quick-manifest.json').read_text())
            (acc / 'parity-quick-manifest.json').write_text(json.dumps(m))
            g = raw_grand if raw_grand is not None else {'about': 'selftest', 'version': 2, 'items': grand or {}}
            (acc / 'parity-grandfathered.json').write_text(json.dumps(g))
            rc = C.compare(d / 'base', d / 'cur', C.Accepted(directory=acc))
        else:
            gf = d / 'grandfathered.json'
            gf.write_text(json.dumps(old_grand if old_grand is not None else {'items': {}}))
            rc = C.compare(d / 'base', d / 'cur', gf)
    return rc, out.getvalue()


def compare_checks(root):
    k = 'fuzz/1:25496'
    entry = {**ENTRY_795, 'rsFailure': 'timeout', 'go': {'status': 'ok', 'outcome': 'exit 2', 'panic': None, 'stdout': '6' * 16, 'written': '7' * 16},
             'attempt': {**PRINT, 'outcome': 'timeout', 'code': 'timeout'}}
    old = {'items': {k: {'status': 'DIFF', 'rsFailure': 'timeout', 'reason': 'x', 'detailContains': ''}}}
    rc, out = run_compare(root, 'nondet', {k: ROW_25496}, {k: {'status': 'DIFF', 'sig': 'rs-nondet:diag:+TS2322'}},
                          grand={k: entry}, old_grand=old)
    check('finding 3: a known-bad item that becomes nondeterministic fails (probe known-timeout-becomes-nondeterministic)', rc == 1, out)
    rc, out = run_compare(root, 'gone', {k: ROW_25496}, {}, grand={k: entry}, old_grand=old)
    check('finding 3: a known-bad item that disappears fails (probe known-timeout-disappears)', rc == 1 and 'NOT RUN' in out, out)
    rc, out = run_compare(root, 'other-failure', {k: ROW_25496}, {k: {**ROW_25496, 'rsFailure': 'crash', 'detail': 'tsc-rs crash'}},
                          grand={k: entry}, old_grand=old)
    check('finding 3: a known-bad item that fails another way fails', rc == 1, out)
    rc, out = run_compare(root, 'unpinned', {k: ROW_25496}, {k: {'status': 'DIFF', 'sig': 'diag:+TS2322'}}, grand={}, old_grand={'items': {}})
    check('finding 3: a known-bad item that no entry pins fails', rc == 1, out)
    # Root's case (fuzz/1:5391 in the P1 run): a base item the old harness called GO-UNSTABLE that is
    # now an output DIFF of tsc-rs.
    rc, out = run_compare(root, 'go-unstable', {'fuzz/1:5391': {'status': 'GO-UNSTABLE'}},
                          {'fuzz/1:5391': {'status': 'DIFF', 'sig': "diag:File '_' does not exist according to ear>File '_' does not exist."}})
    check('finding 3: a GO-UNSTABLE base item that is a DIFF now fails unless pinned (fuzz/1:5391 in the P1 run)',
          rc == 1 and 'KNOWN-BAD' in out, out)
    rc, out = run_compare(root, 'stale', {k: ROW_25496}, {k: {'status': 'MATCH'}}, grand={k: entry}, old_grand=old)
    check('bypass: a stale grandfather entry (its item is good now) fails the gate', rc == 1 and 'STALE GRANDFATHER' in out, out)

    k = 'fuzz/1:795'
    base = {k: {'status': 'GO-FAIL'}}
    ok_row = full_row(ROW_795, [PRINT, PRINT])
    rc, out = run_compare(root, 'pinned', base, {k: ok_row}, grand={k: ENTRY_795}, old_grand={'items': {k: OLD_795}})
    check('finding 4: control: the exact pinned failure is excused', rc == 0 and 'GRANDFATHERED fuzz/1:795' in out, out)
    extra = {**full_row(ROW_795, [PRINT, {**PRINT, 'code': -11, 'stderr': '3' * 16}]),
             'detail': ROW_795['detail'] + '; NEW FAILURE: crash:signal-11'}
    rc, out = run_compare(root, 'additional-crash', base, {k: extra}, grand={k: ENTRY_795}, old_grand={'items': {k: OLD_795}})
    check('finding 4: a different crash with the same panic text fails (probe grandfather-additional-crash)', rc == 1, out)
    no_detail = {kk: v for kk, v in OLD_795.items() if kk != 'detailContains'}
    loose = {kk: v for kk, v in ENTRY_795.items() if kk != 'attempt'}
    rc, out = run_compare(root, 'loose-entry', base, {k: {**ok_row, 'detail': 'anything at all'}}, grand={k: loose},
                          old_grand={'items': {k: no_detail}})
    check('finding 4: an entry without its evidence matches nothing (the file is invalid)', rc == 1 and 'INVALID' in out, out)
    other_input = full_row(ROW_795, [PRINT, PRINT])
    other_input['input'] = 'b' * 16
    rc, out = run_compare(root, 'other-input', base, {k: other_input}, grand={k: ENTRY_795}, old_grand={'items': {k: OLD_795}})
    check('finding 4: the same failure on another input (a changed case) fails', rc == 1, out)

    k = 'fuzz/1:25496'
    misplaced = {'about': 'x', 'version': 2, 'items': {}, k: entry}
    rc, out = run_compare(root, 'misplaced', {k: ROW_25496}, {k: ROW_25496}, raw_grand=misplaced,
                          old_grand={'items': {}, k: {'status': 'DIFF', 'rsFailure': 'timeout', 'reason': 'x'}})
    check('finding 12: an entry outside "items" fails loudly (the file is invalid)', rc == 1 and 'GRANDFATHER INVALID' in out, out)
    typo = {'about': 'x', 'version': 2, 'items': {k: {**entry, 'detailContain': 'x'}}}
    rc, out = run_compare(root, 'typo', {k: ROW_25496}, {k: ROW_25496}, raw_grand=typo,
                          old_grand={'items': {k: {'status': 'DIFF', 'rsFailure': 'timeout', 'reason': 'x', 'detailContain': 'x'}}})
    check('finding 12: an unknown key in an entry fails loudly', rc == 1 and 'GRANDFATHER INVALID' in out, out)

    # Finding 7: the reviewer shrank the quick fuzz count of the candidate's own definition.
    k = 'fuzz/1:1499'
    saved = json.loads(json.dumps(M.MODES))
    try:
        M.MODES['quick']['fuzz_count'] = 1000  # the probe (an older harness reads this)
        shrunk = json.loads((HERE / 'parity-quick-manifest.json').read_text()) if (HERE / 'parity-quick-manifest.json').exists() else None
        patch = contextlib.nullcontext()
        if shrunk is not None and hasattr(M, 'working_manifest'):  # the candidate's manifest shrunk too
            shrunk['fuzz'] = {**shrunk['fuzz'], 'count': 1000}
            own = M.load_manifest(json.dumps(shrunk), 'candidate')
            patch = _patched(M, 'working_manifest', lambda: own)
        with patch:
            rc, out = run_compare(root, 'quick-shrink', {k: {'status': 'MATCH'}}, {}, mode='quick')
    finally:
        M.MODES.clear()
        M.MODES.update(saved)
    check('finding 7: a quick run against a shrunk definition still needs the accepted quick items (probe quick-after-shrink)',
          rc == 1 and 'NOT RUN' in out, out)
    check('bypass: a shrunk manifest in the candidate is printed, item by item', 'MANIFEST SHRINK' in out and 'fuzz/1:1499' in out, out)


@contextlib.contextmanager
def _patched(mod, name, value):
    old = getattr(mod, name)
    setattr(mod, name, value)
    try:
        yield
    finally:
        setattr(mod, name, old)


# ---- fuzz tier: attempts and inputs (findings 2, 8) --------------------------------------------

def fuzz_args(bins, root, **kw):
    return SimpleNamespace(corpus=True, seed=1, go=str(bins['go']), rs=str(bins['rs']), js=str(bins.get('js', bins['go'])),
                           timeout=kw.get('timeout', 0.5), rs_runs=2, referees=True, referee_all=False, js_cache=None,
                           go_sha='h4-selftest', js_identity=None)


def run_case(root, args, case):
    import parity_fuzz as F
    F.WORK = root / 'fuzz-work'
    cache, out = root / f'go-cache-{case["name"]}', root / 'fuzz-out'
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=True)
    params = inspect.signature(F.run_case).parameters
    if 'stage' in params:
        import parity_inputs
        stage = parity_inputs.Stage('h4-selftest')
        try:
            return F.run_case(args, [case], cache, out, 0, stage)[1]
        finally:
            stage.discard()
    return F.run_case(args, [case], cache, out, 0)[1]


def fuzz_checks(root):
    import parity_referees_test as T
    T.SPEC['h4-retry-crash'] = {'go': {'*': {'code': 70, 'err': 'panic: boom\n'}},
                                'rs': {'*': {'code': 70, 'err': 'panic: boom\n', 'sleep_first': 1.5}}}
    r = root / 'retry-crash'
    r.mkdir()
    bins, _ = T.write_fakes(r)
    case = {'name': 'h4-retry-crash', 'files': {'a.ts': 'export {};', 'scenario': 'h4-retry-crash'}, 'options': {'noEmit': True}, 'args': []}
    row = run_case(r, fuzz_args(bins, r, timeout=1.0), case)
    check('finding 2: a tsc-rs timeout, then an exact copy of Go\'s crash, is not MATCH (probe retry-crash)',
          row['status'] == 'DIFF' and row.get('rsFailure') == 'timeout', row)

    import oracle
    mut = root / 'mutate-input'
    case = {'name': 'h4-mutate', 'files': {'a.ts': 'export const a=1;'}, 'options': {'noEmit': True}, 'args': []}
    inputs = oracle.write_project(case, mut)
    binary = script(root / 'mutator', 'from pathlib import Path\nPath("a.ts").write_text("export const a=2;")\n')
    res = oracle.run(str(binary), mut, inputs, 5)
    check('finding 8: a compiler that overwrites its input is seen (probe mutate-input)', bool(getattr(res, 'input_changes', None)),
          (res.key(), getattr(res, 'input_changes', 'no input check')))

    go = script(root / 'go-ok', 'print("ok")\n')
    rs = script(root / 'rs-transient', 'from pathlib import Path\np = Path("a.ts"); t = p.read_bytes()\n'
                                       'p.write_text("export const tampered = 1;\\n"); p.write_bytes(t)\nprint("ok")\n')
    case = {'name': 'h4-transient', 'files': {'a.ts': 'export const a = 1;\n'}, 'options': {'noEmit': True}, 'args': []}
    row = run_case(root, fuzz_args({'go': go, 'rs': rs}, root, timeout=10), case)
    check('finding 8: a compiler that changes its input and puts it back is a failure, not MATCH',
          row['status'] == 'DIFF' and row.get('rsFailure') == 'input', row)
    rs = script(root / 'rs-delete', 'import os\nos.remove("a.ts") if os.path.exists("a.ts") else None\nprint("ok")\n')
    case = {**case, 'name': 'h4-delete'}
    row = run_case(root, fuzz_args({'go': go, 'rs': rs}, root, timeout=10), case)
    check('finding 8: a compiler that deletes its input is a failure, and the next attempt gets it back',
          row['status'] == 'DIFF' and row.get('rsFailure') == 'input'
          and [a.get('inputs', '')[:7] for a in row.get('attempts', [])] == ['changed', 'changed'], row)


def fuzz_input_checks(root):
    """The input check's own false results: parallel writers and forks (an fd inherited across fork
    and closed at exec), and an emit that overwrites an input as Go's does."""
    import oracle
    from concurrent.futures import ThreadPoolExecutor
    # The mechanism of the 34 false input changes of the first H4 full run: another thread's child
    # holds a copy of a write fd it inherited at fork and closes it later (here: a forked child that
    # exits after the watch started), so the file gets its last close, IN_CLOSE_WRITE, while the run
    # that never touched it is watched.
    case = {'name': 'h4-race', 'files': {'a.ts': 'export const a = 1;\n'}, 'options': {'noEmit': True}, 'args': []}
    d = root / 'race'
    inputs = oracle.write_project(case, d)
    fd = os.open(d / 'a.ts', os.O_WRONLY)
    pid = os.fork()
    if pid == 0:  # the child: holds the inherited fd for a moment, then exits (its last close)
        import time
        time.sleep(0.3)
        os._exit(0)
    os.close(fd)
    res = oracle.run(script(root / 'sleeper', 'import time\ntime.sleep(0.8)\n'), d, inputs, 10)
    os.waitpid(pid, 0)
    check('inputs: a late close of an fd another thread\'s child inherited is no input change', res.input_changes == [], res.input_changes)
    js = {'name': 'h4-overwrite', 'files': {'a.js': 'var a = 1;\n'}, 'options': {'allowJs': True}, 'args': []}
    go = script(root / 'go-overwrite', 'open("a.js", "w").write("var a = 2;\\n")\nprint("emitted")\n')
    row = run_case(root, fuzz_args({'go': go, 'rs': go}, root, timeout=10), js)
    check('inputs: an emit that overwrites an input exactly as Go\'s does is MATCH', row['status'] == 'MATCH', row)
    other = script(root / 'rs-overwrite-other', 'open("a.js", "w").write("var a = 3;\\n")\nprint("emitted")\n')
    row = run_case(root, fuzz_args({'go': go, 'rs': other}, root, timeout=10), {**js, 'name': 'h4-overwrite-2'})
    check('inputs: overwriting an input otherwise than Go is a failure', row['status'] == 'DIFF' and row.get('rsFailure') == 'input', row)
    plain = script(root / 'rs-no-overwrite', 'print("emitted")\n')
    row = run_case(root, fuzz_args({'go': go, 'rs': plain}, root, timeout=10), {**js, 'name': 'h4-overwrite-3'})
    check('inputs: not making Go\'s overwrite is a DIFF', row['status'] == 'DIFF', row)


@contextlib.contextmanager
def _slow_reader(delay):
    """The reviewer's watch-race: the reader thread has read events but not parsed them yet when the
    run's final check comes (every parse on the reader thread waits `delay` first)."""
    import threading
    import parity_watch as W
    orig = W.Watch._parse

    def slow(self, data):
        if threading.current_thread() is getattr(self, 'thread', None):
            import time
            time.sleep(delay)
        return orig(self, data)
    W.Watch._parse = slow
    try:
        yield
    finally:
        W.Watch._parse = orig


def _fds():
    return len(os.listdir('/proc/self/fd'))


def watch_checks(root):
    import oracle
    import parity_watch as W
    case = {'name': 'h4-rewrite', 'files': {'a.ts': 'export const a = 1;\n'}, 'options': {'noEmit': True}, 'args': []}
    rewrite = script(root / 'rewrite', 'from pathlib import Path\nimport time\np = Path("a.ts"); p.write_bytes(p.read_bytes()); time.sleep(.15)\n')
    d = root / 'race'
    with _slow_reader(0.5):
        res = oracle.run(str(rewrite), d, oracle.write_project(case, d), 10)
    check('round 3: finding 1: a same-byte rewrite is seen while the reader is still parsing (probe watch-race)',
          bool(res.input_changes), (res.input_changes, res.inputs_after))
    go = script(root / 'go-plain', 'import time\ntime.sleep(.15)\n')
    with _slow_reader(0.5):
        row = run_case(root, fuzz_args({'go': go, 'rs': rewrite}, root, timeout=10), {**case, 'name': 'h4-watch-gate'})
    check('round 3: finding 1: and the case is a failure, not MATCH (probe watch-gate)', row['status'] == 'DIFF' and row.get('rsFailure') == 'input', row)

    # Finding 6: the watch cannot be built (the watch limit): nothing leaks, and the run falls back.
    orig = W._libc.inotify_add_watch
    calls = {'n': 0}

    def failing(fd, path, mask):
        calls['n'] += 1
        if calls['n'] >= 2:
            import ctypes
            ctypes.set_errno(28)  # ENOSPC: the watch limit
            return -1
        return orig(fd, path, mask)
    tree = root / 'tree'
    (tree / 'sub').mkdir(parents=True)
    before = _fds()
    W._libc.inotify_add_watch = failing
    try:
        try:
            W.Watch([str(tree)])
            raised = False
        except W.WatchError:
            raised = True
        after = _fds()
        calls['n'] = 0
        case2 = {'name': 'h4-limit', 'files': {'a.ts': 'export const a = 1;\n', 'sub/b.ts': 'export const b = 2;\n'},
                 'options': {'noEmit': True}, 'args': []}
        d2 = root / 'limit'
        res = oracle.run(str(rewrite), d2, oracle.write_project(case2, d2), 10)
    finally:
        W._libc.inotify_add_watch = orig
    check('round 3: finding 6: a watch that cannot be built leaks no fd (probe watch-limit)', raised and after == before, (raised, before, after))
    check('round 3: finding 6: the run falls back to the stat check and still sees the rewrite',
          bool(res.input_changes) and bool(getattr(res, 'watch_error', None)), (res.input_changes, getattr(res, 'watch_error', None)))


# ---- cli tier: snapshots and staged caches (finding 1) ------------------------------------------

def cli_project(root, name):
    proj = root / name
    proj.mkdir()
    (proj / 'package.json').write_text('{}')
    (proj / 'tsconfig.json').write_text('{}')
    (proj / 'a.ts').write_text('A')
    variants = ('noemit', 'emit', 'decl', 'incr', 'listfiles', 'extdiag', 'pretty', 'showconfig', 'build')
    spec = root / f'{name}.json'
    spec.write_text(json.dumps({'projects': [{'name': 'h4', 'dirs': [str(proj)], 'configs': [
        {'label': name, 'config': 'tsconfig.json', 'skipVariants': [v for v in variants if v != 'noemit']}]}]}))
    return proj, spec


def run_cli(root, spec, go, rs, out, env):
    help_text = subprocess.run([sys.executable, '-I', str(HERE / 'parity_cli.py'), '--help'], capture_output=True, text=True).stdout
    cmd = [sys.executable, '-I', str(HERE / 'parity_cli.py'), '--rs', str(rs), '--go', str(go), '--projects', str(spec),
           '--cache', str(root / f'cache-{spec.stem}'), '--out', str(root / out), '--no-referees', '--rs-runs', '1',
           *(['--snapshots', str(root / 'snapshots')] if '--snapshots' in help_text else [])]
    p = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **env})
    res = root / out / 'results.json'
    if not res.exists():
        return {'status': 'no results', 'stderr': p.stderr[-800:]}
    return next(iter(json.loads(res.read_text())['items'].values()))


def cache_entries(root, spec):
    d = root / f'cache-{spec.stem}'
    return sorted(p.name for p in d.rglob('*.json')) if d.exists() else []


def cli_checks(root):
    # (a) Another process writes the project while Go runs (A -> B): the reviewer's cache-probe-v2,
    # through the real tier. The fake Go does the write itself, through a hard link outside the
    # project (so the namespace's mount does not catch it), then prints what it reads.
    proj, spec = cli_project(root, 'poison')
    alias = root / 'alias.ts'
    os.link(proj / 'a.ts', alias)
    go = script(root / 'go-writer', 'import os\nalias = os.environ["H4_ALIAS"]\n'
                                    'if not os.path.exists(alias + ".done"):\n'
                                    '    open(alias, "w").write("B"); open(alias + ".done", "w").close()\n'
                                    'print(open("a.ts").read())\n')
    rs = script(root / 'rs-b', 'print("B")\n')
    env = {'H4_ALIAS': str(alias)}
    first = run_cli(root, spec, go, rs, 'poison-1', env)
    alias.write_text('A')  # the other process puts A back
    second = run_cli(root, spec, go, rs, 'poison-2', env)
    check('finding 1: a project edited while Go runs never caches the edit under the original content (probe cache-probe-v2)',
          second['status'] != 'MATCH' and first['status'] != 'MATCH', (first, second))

    # (b) The input itself (the overlay's lower dir) changes A -> B -> A while Go runs: the
    # reviewer's transient edit, and the stage-then-invalidate bypass.
    proj, spec = cli_project(root, 'transient')
    go = script(root / 'go-transient', 'import os, re\nmark = os.environ["H4_MARK"]\n'
                                       'lower = re.search(r"lowerdir=([^,]+)", next(l for l in open("/proc/self/mountinfo") '
                                       'if " overlay " in l and os.getcwd() in l.split()[4])).group(1)\n'
                                       'if not os.path.exists(mark):\n'
                                       '    p = os.path.join(lower, "a.ts"); open(p, "w").write("B"); seen = open("a.ts").read()\n'
                                       '    open(p, "w").write("A"); open(mark, "w").close()\n'
                                       'else:\n    seen = open("a.ts").read()\nprint(seen)\n')
    env = {'H4_MARK': str(root / 'transient.done')}
    first = run_cli(root, spec, go, rs, 'transient-1', env)
    staged_after_first = cache_entries(root, spec)
    second = run_cli(root, spec, go, rs, 'transient-2', env)
    check('finding 1: an input changed and put back while the tier runs is seen (probe cache-probe: transient edit)',
          first['status'] == 'ERROR', first)
    check('bypass: a cache entry made by a run that is then invalidated is never published', staged_after_first == [], staged_after_first)
    check('finding 1: the next run makes Go\'s result again, from the real input', second['status'] == 'DIFF', second)


# ---- lsp keys (finding 11) ----------------------------------------------------------------------

def lsp_key_checks(root):
    import parity_lsp as L
    proj = root / 'lsp-proj'
    (proj / 'src').mkdir(parents=True)
    (proj / 'src/a.ts').write_text('export const a = 1;\n')
    sess = {'name': 'ata-overlay', 'project': None, 'files': 12, 'scenario': 'ata-overlay'}
    if hasattr(L, 'session_material'):
        import parity_inputs as I
        info = json.loads(subprocess.run(['node', str(HERE / 'vendor/lspdiff/lspdiff.mjs'), 'info'], capture_output=True,
                                         text=True, env={**os.environ, 'ATA_IDLE_MS': '1500'}).stdout)
        other = json.loads(subprocess.run(['node', str(HERE / 'vendor/lspdiff/lspdiff.mjs'), 'info'], capture_output=True,
                                          text=True, env={**os.environ, 'ATA_IDLE_MS': '4000'}).stdout)
        k1 = I.json_hash(L.session_material(sess, proj, None, None, 'src', info))
        k2 = I.json_hash(L.session_material(sess, proj, None, None, 'src', other))
    else:
        import parity_inputs as I
        os.environ['ATA_IDLE_MS'] = '1500'
        k1 = L.session_key(sess, proj, I.Snapshot(), 'src')
        os.environ['ATA_IDLE_MS'] = '4000'
        k2 = L.session_key(sess, proj, I.Snapshot(), 'src')
        os.environ.pop('ATA_IDLE_MS')
    check('finding 11: the ATA settings are in the lsp cache key', k1 != k2, (k1, k2))


def variants_checks(root):
    """Accumulated Go evidence (root's decision after H4): only the same binary, inputs, sources and
    settings share Go answer variants."""
    import parity_inputs as I
    import parity_lsp as L
    sess = next(x for x in L.FULL if x['name'] == 'hono-f12-p6')
    info = {'settings': {'LSPDIFF_SETTLE_MS': 1000, 'LSPDIFF_REQUEST_TIMEOUT_MS': 300000}, 'scenarios': {}}
    snap = I.Snap(root / 'snap', '/p/hono', 'a' * 64, 'x')
    other = I.Snap(root / 'snap', '/p/hono', 'b' * 64, 'x')
    m = lambda s, runs: L.session_material(sess, '/p/hono', s, None, 'src', info, [f'g{runs}'])  # noqa: E731
    v = lambda mat, go: I.json_hash(L.variants_material(mat, go))  # noqa: E731
    check('go variants: the record of 4 and of 8 Go runs of the same material is one', v(m(snap, 4), 'go1') == v(m(snap, 8), 'go1'))
    check('go variants: negative control: another binary has another record', v(m(snap, 8), 'go1') != v(m(snap, 8), 'go2'))
    check('go variants: negative control: another input has another record', v(m(snap, 8), 'go1') != v(m(other, 8), 'go1'))
    cache = root / 'lsp'
    for tag, mat in (('same', m(snap, 4)), ('input', m(other, 4))):
        d = cache / f'hono-f12-p6-{tag:0<24}'
        d.mkdir(parents=True)
        (d / 'key.json').write_text(json.dumps(mat, sort_keys=True))
        (d / 'go.jsonl').write_text('{}\n')
    cur = m(snap, 8)
    got = [d.name for d in L.prior_dirs(cache, 'hono-f12-p6', 'hono-f12-p6-' + 'c' * 24, cur)]
    check('go variants: prior logs come from the same material at another run count, never another input',
          got == ['hono-f12-p6-' + 'same'.ljust(24, '0')], got)
    h3 = cache / ('hono-f12-p6-' + 'h3'.ljust(24, '0'))  # an H3 cache dir: no key.json, whatever its key says
    h3.mkdir()
    (h3 / 'go.jsonl').write_text('{}\n')
    got = [d.name for d in L.prior_dirs(cache, 'hono-f12-p6', 'hono-f12-p6-' + 'c' * 24, cur)]
    check('round 3: Go logs of a harness before H4 are never priors (no import by key alone)',
          h3.name not in got and not hasattr(L, 'legacy_prior'), got)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--only', default='')
    args = ap.parse_args()
    root = Path.home() / f'.cache/perf-rearch/parity/scratch/h4-selftest-{os.getpid()}'
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        for name, part in (('compare', compare_checks), ('fuzz', fuzz_checks), ('fuzz inputs', fuzz_input_checks),
                           ('cli', cli_checks), ('lsp keys', lsp_key_checks), ('go variants', variants_checks),
                           ('watch', watch_checks)):
            if args.only and args.only not in name:
                continue
            sub = root / name.replace(' ', '-')
            sub.mkdir()
            try:
                part(sub)
            except Exception as e:  # noqa: BLE001 - an older harness lacks an entry point: that part fails
                check(f'{name}: raised', False, repr(e)[:500])
    finally:
        if not args.keep:
            subprocess.run(['chmod', '-R', 'u+rwx', str(root)], check=False)
            shutil.rmtree(root, ignore_errors=True)
    print(f'H4 SELFTEST {"FAIL: " + "; ".join(FAILS) if FAILS else "OK"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
