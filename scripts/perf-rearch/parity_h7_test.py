#!/usr/bin/env python3
"""Lane H7's negative controls for the api tier (parity_api.py, parity_api_session.py, parity_sandbox_init.py).

usage: parity_h7_test.py [--unit]                 prints H7 SELFTEST OK, or each FAIL; exit 1 on a failure
       parity_h7_test.py --e2e-only --harness DIR  the end-to-end controls against another harness's
                                                   parity_api.py (a `git archive` of an older commit), to
                                                   show which of them fail there

Unit checks (seconds): the key holds every effective input, the scenario format refuses what is not
hermetic, the judge's order and its type-sensitive comparison, Go's pinned evidence covers its stderr,
regen --only refuses to relabel an old file, and api grandfather entries. The tripwire checks (about
10 s; parity_watch.ACCESS, which the lsp tier uses): no false access from the watch's own listing under
4 threads that fork, and a read and a listing after the watch started are seen.

End-to-end checks (about a minute; real sessions through `parity_api.py` and `parity_compare.py compare`).
The fake tsc-rs servers are Astra's review proxy (~/.cache/perf-rearch/scratch/review-h7-astra/
proxy-extra.py; FAKE below, with its two log writes outside the session removed, plus `fds`) in front of
the Go oracle. Each finding of Astra's H7 review has its control:
  1 child-stdout   a child holds stdout and stderr after the proxy exits 0: DIFF rsFailure crash (survivor)
  2 outside-write  writes to the host outside the session (the review dir, /etc, /var/tmp): no file appears
  3 bool-number    emitSkipped false -> 0: DIFF;  bad-rpc  "jsonrpc": "9.9": DIFF rsFailure crash (malformed)
  4 unexecutable Go (Astra's go-launch-failure): ERROR, nothing cached
  5 corrupt-build  out/a.js corrupted after build, then removed by cleanBuild: DIFF
  6 stat-bait      stat and O_PATH of the real ~/.cache/typescript: the path does not exist in the sandbox
                   (the proxy fails: DIFF); fds: the server has no fd but 0, 1 and 2
  7 regen --only of Astra's version 3 file: refused (unit)
  8 a changed Go stderr changes Go's pinned evidence (unit)
  9 dyn-config-include-dirs-insensitive exists, is in the quick manifest and is pinned for B2 (unit)
and the round 1 controls: pass, wrong-path (LOST and NEW: PARITY FAIL), crash, late-exit, malformed, hang,
nondet (rs-nondet; as Go, GO-BROKEN in full and quick), home-write, read-typings, and the cache controls on
a scenario of its own (a second run reads the cache; another case flag, one fixture byte more and a
poisoned entry make Go run again; a fixture that is not its scenario's content is ERROR).
Run it when no gate runs: it uses the tier's fixed paths and the real Go cache.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_api as api  # noqa: E402
import parity_api_session as session  # noqa: E402
import parity_grandfather as grandfather  # noqa: E402
import parity_inputs as inputs  # noqa: E402

HERE = Path(__file__).resolve().parent
HARNESS = Path(sys.argv[sys.argv.index('--harness') + 1]) if '--harness' in sys.argv else HERE
GO = str(api.GO)
SCRATCH = session.STATE / 'scratch/h7-selftest'
ASTRA = Path.home() / '.cache/perf-rearch/scratch/review-h7-astra'
FAILS = []


def check(name, ok, info=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else ": " + str(info)[:400]}', flush=True)
    if not ok:
        FAILS.append(name)


FAKE = (HERE / 'parity_h7_fake.py').read_text()  # Astra's round 2 probe, plus `privs`
OUTSIDE = [str(ASTRA / 'outside-sandbox-r2'), '/etc/parity-h7-escape', '/var/tmp/parity-h7-escape', '/tmp/parity-h7-escape']


def fake(mode):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    f = SCRATCH / 'fake_api.py'
    f.write_text(FAKE.replace('OUTSIDE_LIST', repr(OUTSIDE)))
    return f'/usr/bin/python3 {f} {mode} {GO} {Path.home() / ".cache/typescript"}'


def tier(rs, only, tier_='full', go=GO, scenarios=None, env=None):
    """Runs the api tier of HARNESS: (results.json, stdout)."""
    out = Path(tempfile.mkdtemp(prefix='run-', dir=SCRATCH))
    cmd = [sys.executable, '-I', str(HARNESS / 'parity_api.py'), '--rs', rs, '--go', go, '--out', str(out), '--tier', tier_,
           '--only', only, *(['--scenarios', str(scenarios)] if scenarios else [])]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    try:
        return json.loads((out / 'results.json').read_text()), r.stdout + r.stderr
    except OSError:
        return {'items': {}, 'scenarios': {}}, r.stdout + r.stderr


def compare(base_items, cur, mode='full'):
    """parity_compare compare of a run (tier results) against a base of base_items, with the accepted
    artifacts of this checkout (--accepted-dir). Returns (exit code, output)."""
    d = Path(tempfile.mkdtemp(prefix='cmp-', dir=SCRATCH))
    for name, items in (('base', base_items), ('cur', cur['items'])):
        (d / name).mkdir()
        (d / name / 'results.json').write_text(json.dumps({'meta': {'mode': mode, 'tiers': ['api']},
                                                           'tiers': {'api': {'summary': {}, 'items': items}}}))
    acc = d / 'accepted'
    acc.mkdir()
    shutil.copy(HERE / 'parity-quick-manifest.json', acc)
    shutil.copy(HERE / 'parity-grandfathered.json', acc)
    r = subprocess.run([sys.executable, '-I', str(HERE / 'parity_compare.py'), 'compare', str(d / 'base'), str(d / 'cur'),
                        '--accepted-dir', str(acc)], capture_output=True, text=True)
    return r.returncode, r.stdout


# ---- unit ------------------------------------------------------------------------------------

def unit():
    base = {'name': 'u', 'about': 'x', 'caseSensitive': False,
            'fs': {'kind': 'full', 'files': {'@ROOT@/a.ts': 'export const a = 1;'}},
            'createPrograms': [{'rootFiles': ['@ROOT@/a.ts'], 'compilerOptions': {'outDir': '@ROOT@/out'}}]}
    m0 = api.material(base, None, {}, 'g' * 64)
    k0 = inputs.json_hash(m0)
    variants = {
        'the case flag': {**base, 'caseSensitive': True},
        'no case flag (the OS default)': {k: v for k, v in base.items() if k != 'caseSensitive'},
        'a file\'s content': {**base, 'fs': {'kind': 'full', 'files': {'@ROOT@/a.ts': 'export const a = 2;'}}},
        'the filesystem shape (a listing)': {**base, 'fs': {**base['fs'], 'directories': {'@ROOT@': {'files': ['a.ts'], 'directories': []}}}},
        'an option': {**base, 'createPrograms': [{'rootFiles': ['@ROOT@/a.ts'], 'compilerOptions': {'outDir': '@ROOT@/OUT'}}]},
        'the requests': {**base, 'requests': ['emit']},
        'the updates': {**base, 'updates': [{'changes': {}}]},
    }
    for what, sc in variants.items():
        check(f'key: {what} is another key', inputs.json_hash(api.material(sc, None, {}, 'g' * 64)) != k0)
    check('key: the fixture hash', inputs.json_hash(api.material(base, 'f' * 24, {}, 'g' * 64)) != k0)
    check('key: the ancestors\' state', inputs.json_hash(api.material(base, None, {'/package.json': 'x'}, 'g' * 64)) != k0)
    check('key: the Go binary', inputs.json_hash(api.material(base, None, {}, 'h' * 64)) != k0)
    saved = (api.session_sha, session.PROTOCOL, session.SANDBOX_HOME)
    api.session_sha = lambda: 'another-source'
    check('key: the session driver\'s and init\'s source (harness sources)', inputs.json_hash(api.material(base, None, {}, 'g' * 64)) != k0)
    api.session_sha = saved[0]
    check('key: both sandbox sources are hashed', [f.name for f in api.SOURCES] == ['parity_api_session.py', 'parity_sandbox_init.py'])
    session.PROTOCOL = 'another protocol'
    check('key: the API protocol', inputs.json_hash(api.material(base, None, {}, 'g' * 64)) != k0)
    session.PROTOCOL = saved[1]
    session.SANDBOX_HOME = Path('/elsewhere')
    check('key: the private home path', inputs.json_hash(api.material(base, None, {}, 'g' * 64)) != k0)
    session.SANDBOX_HOME = saved[2]
    check('key: unchanged inputs give the same key', inputs.json_hash(api.material(dict(base), None, {}, 'g' * 64)) == k0)

    refused = {
        'a layer filesystem without a disk fixture (it would read the host)': {**base, 'fs': {'kind': 'layer', 'files': {}}},
        'a host symlink': {**base, 'fs': {**base['fs'], 'symlinks': {'@ROOT@/l': {'target': '/etc', 'host': True}}}},
        'no filesystem': {k: v for k, v in base.items() if k != 'fs'},
        'build steps without a disk fixture': {**base, 'build': [{'create': {'rootNames': []}}]},
        'an unknown key': {**base, 'extra': 1},
        'an absolute fixture path': {k: v for k, v in base.items() if k != 'fs'} | {'disk': {'/etc/passwd': 'x'}},
        'a fixture symlink that leaves the fixture': {k: v for k, v in base.items() if k != 'fs'} | {'disk': {'a/l': {'symlink': '../../x'}}},
        'an unknown request': {**base, 'requests': ['getTypeAtPosition']},
    }
    for what, sc in refused.items():
        try:
            session.validate(sc)
            check(f'scenario: {what} is refused', False, 'accepted')
        except session.ScenarioError:
            check(f'scenario: {what} is refused', True)

    def t(**kw):
        return {'steps': [{'method': 'initialize', 'params': None, 'answer': kw.get('a', '{"jsonrpc":"2.0","id":1,"result":1}'),
                           'fs': {'disk': kw.get('disk', {}), 'home': {}}}],
                'exit': kw.get('exit', 0), 'forced': False, 'survivors': [], 'failure': kw.get('failure'), 'why': kw.get('why'),
                'stderr': {'sha': kw.get('err', 's'), 'site': 's', 'crash': None}, 'fs': {'disk': kw.get('end', {}), 'home': kw.get('home', {})},
                'writtenText': {}, 'secs': 0}
    j = lambda go, rs, confirm=None: api.judge(go, rs, confirm or (lambda: t()))[:3]  # noqa: E731
    r2 = '{"jsonrpc":"2.0","id":1,"result":2}'
    r3 = '{"jsonrpc":"2.0","id":1,"result":3}'
    check('judge: equal is MATCH', j([t(), t()], [t(), t()])[0] == 'MATCH')
    check('judge: a tsc-rs crash is a DIFF with rsFailure crash, even where Go is broken',
          j([t(failure='crash', why='x'), t()], [t(failure='crash', why='x'), t()])[::2] == ('DIFF', 'crash'))
    check('judge: malformed counts as crash', j([t(), t()], [t(failure='malformed', why='x'), t()])[2] == 'crash')
    check('judge: a tsc-rs timeout is rsFailure timeout', j([t(), t()], [t(failure='timeout', why='x'), t()])[2] == 'timeout')
    check('judge: two tsc-rs runs that differ are rs-nondet', 'rs-nondet' in (j([t(), t()], [t(), t(a=r2)])[1] or ''))
    check('judge: Go runs that differ, tsc-rs stable: GO-BROKEN', j([t(), t(a=r2)], [t(a=r3), t(a=r3)])[0] == 'GO-BROKEN')
    check('judge: a Go crash, tsc-rs clean: GO-BROKEN', j([t(failure='crash', why='p'), t()], [t(a=r3), t(a=r3)])[0] == 'GO-BROKEN')
    check('judge: quick GO-BROKEN needs the confirming run', j([t(), t(a=r2)], [t(a=r3)], lambda: t(a=r2))[0] == 'DIFF')
    check('judge: quick GO-BROKEN with an equal confirming run', j([t(), t(a=r2)], [t(a=r3)], lambda: t(a=r3))[0] == 'GO-BROKEN')
    check('judge: a written file that differs is a DIFF', j([t(), t()], [t(end={'a': 'x'}), t(end={'a': 'x'})])[0] == 'DIFF')
    check('judge: a file written and gone again between requests is a DIFF (finding 5)',
          j([t(), t()], [t(disk={'out/a.js': 'c0'}), t(disk={'out/a.js': 'c0'})])[0] == 'DIFF')
    check('judge: a home write is a DIFF', j([t(), t()], [t(home={'x': 'y'}), t(home={'x': 'y'})])[0] == 'DIFF')
    check('judge: stderr that differs is a DIFF', j([t(), t()], [t(err='z'), t(err='z')])[0] == 'DIFF')
    f0 = '{"jsonrpc":"2.0","id":1,"result":{"emitSkipped":false}}'
    for what, other in (('false against 0 (finding 3)', '{"jsonrpc":"2.0","id":1,"result":{"emitSkipped":0}}'),
                        ('1 against 1.0', '{"jsonrpc":"2.0","id":1,"result":{"emitSkipped":1.0}}'),
                        ('other bytes of the same value', '{"jsonrpc":"2.0", "id":1,"result":{"emitSkipped":false}}')):
        check(f'judge: type-sensitive: {what} is a DIFF', j([t(a=f0), t(a=f0)], [t(a=other), t(a=other)])[0] == 'DIFF')
    for what, msg in (('jsonrpc 9.9 (finding 3)', {'jsonrpc': '9.9', 'id': 1, 'result': 1}),
                      ('an extra envelope key', {'jsonrpc': '2.0', 'id': 1, 'result': 1, 'x': 1}),
                      ('id true for 1', {'jsonrpc': '2.0', 'id': True, 'result': 1}),
                      ('id 1.0 for 1', {'jsonrpc': '2.0', 'id': 1.0, 'result': 1}),
                      ('result and error', {'jsonrpc': '2.0', 'id': 1, 'result': 1, 'error': {'code': 1, 'message': 'm'}}),
                      ('an error code false', {'jsonrpc': '2.0', 'id': 1, 'error': {'code': False, 'message': 'm'}}),
                      ('a server request', {'jsonrpc': '2.0', 'id': 1, 'method': 'x'})):
        check(f'envelope: {what} is malformed', session.envelope_error(msg, 1) is not None)
    check('envelope: a strict answer passes', session.envelope_error({'jsonrpc': '2.0', 'id': 1, 'result': None}, 1) is None)
    for what, body in (('a duplicate key', b'{"a":1,"a":2}'), ('NaN', b'{"a":NaN}')):
        try:
            session.parse(body)
            check(f'frame: {what} is malformed', False)
        except session.Malformed:
            check(f'frame: {what} is malformed', True)

    # round 3 finding 3: the home skeleton is checked against its exact structure
    h = Path(tempfile.mkdtemp(prefix='skel-', dir=SCRATCH)) / 'home'
    session.make_home(h)
    check('round 3 finding 3: a fresh skeleton has no deviation', session.skeleton_errors(h) == [], session.skeleton_errors(h))
    (h / 'tmp').rmdir()
    (h / 'tmp').write_text('x')
    check('round 3 finding 3: tmp replaced by a file is a deviation', any(e.startswith('tmp:') for e in session.skeleton_errors(h)))
    (h / 'tmp').unlink()
    (h / 'tmp').mkdir(mode=0o700)
    os.chmod(h / 'tmp', 0o700)
    check('round 3 finding 3: tmp of another mode is a deviation', any('mode 700' in e for e in session.skeleton_errors(h)))
    os.chmod(h / '.cache', 0o755)
    (h / '.cache').rmdir()
    check('round 3 finding 3: a skeleton dir gone is a deviation', any(e == '.cache: gone' for e in session.skeleton_errors(h)))
    inputs.remove_tree(h.parent)
    w = Path(tempfile.mkdtemp(prefix='skel-', dir=SCRATCH))
    session.make_home(w / 'home')
    listing = session.storage(w)[0]
    check('round 3 finding 3: the skeleton is in the storage listing, with its modes, not skipped',
          listing['home'].get('tmp') == 'dir 755' and listing['skeleton'] == [], listing)
    inputs.remove_tree(w)

    # finding 8: Go's stderr is in its pinned evidence
    check('pin: a changed Go stderr changes Go\'s evidence (finding 8)', api.go_evidence(t(), None) != api.go_evidence(t(err='other'), None))
    check('pin: a changed Go storage changes Go\'s evidence', api.go_evidence(t(), None) != api.go_evidence(t(end={'a': 'b'}), None))

    # finding 7: regen --only never relabels an old file
    v3 = ASTRA / 'grandfather-v3.json'
    runs = session.STATE / 'runs'
    full, quick = runs / 'h7-gf-evidence-ed6edf820-full', runs / 'h7-gf-evidence-ed6edf820-quick'
    if v3.exists() and full.exists():
        out = SCRATCH / 'regen-v3.json'
        r = subprocess.run([sys.executable, '-I', str(HERE / 'parity_grandfather.py'), 'regen', '--full', str(full), '--quick', str(quick),
                            '--only', '^api/', '--from', str(v3), '--out', str(out)], capture_output=True, text=True)
        check('regen --only: a version 3 file with kept entries is refused, nothing written (finding 7)',
              r.returncode == 1 and 'version 3' in r.stdout and not out.exists(), r.stdout + r.stderr)
    else:
        check('regen --only: Astra\'s version 3 file and the evidence runs exist', False, (v3, full))

    # finding 9: the directory-collision scenario exists, quick runs it, and B2's pin tracks it
    name9 = 'dyn-config-include-dirs-insensitive'
    try:
        sc9 = next((x for x in api.load_scenarios() if x['name'] == name9), None)
        files9 = sorted((sc9 or {}).get('fs', {}).get('files', {}))
        pins = grandfather.load((HERE / 'parity-grandfathered.json').read_text(), 'checkout')
        ok9 = (sc9 is not None and sc9.get('caseSensitive') is False and any(f.endswith('/src/A/x.ts') for f in files9)
               and any(f.endswith('/src/a/y.ts') for f in files9) and name9 in api.modes.working_manifest().api
               and 'B2' in pins.get(f'api/{name9}', {}).get('reason', ''))
        check('finding 9: dynamic src/A/x.ts and src/a/y.ts on an insensitive host: a scenario, in quick, pinned for B2', ok9, files9)
    except Exception as e:  # noqa: BLE001 (an older harness lacks what this reads)
        check('finding 9: dynamic src/A/x.ts and src/a/y.ts on an insensitive host: a scenario, in quick, pinned for B2', False, e)

    try:
        grandfather.validate({'about': 'x', 'version': grandfather.VERSION, 'items': {'api/x-y': {
            'status': 'DIFF', 'rsFailure': None, 'reason': 'r', 'input': '0' * 16,
            'go': {'status': 'ok', 'outcome': 'exit 0', 'panic': None, 'stdout': '0' * 16, 'written': '0' * 16},
            'attempt': {'outcome': 'exit 0', 'code': 0, 'stdout': '1' * 16, 'stderrSite': '2' * 16, 'written': '3' * 16, 'inputs': 'intact'},
            'attempts': {'full': ['run1', 'run2']}, 'evidence': 'e'}}}, 't')
        check('grandfather: an api item is a valid entry', True)
    except Exception as e:  # noqa: BLE001
        check('grandfather: an api item is a valid entry', False, e)
    try:
        grandfather.validate({'about': 'x', 'version': grandfather.VERSION, 'items': {'lsp/x/0001 hover': {}}}, 't')
        check('grandfather: an lsp item is still refused', False)
    except Exception:  # noqa: BLE001
        check('grandfather: an lsp item is still refused', True)


def tripwire():
    """The access mask (parity_watch.ACCESS) against the inherited-fd race, and that real reads still trip."""
    import threading
    import parity_watch as w
    base = Path(tempfile.mkdtemp(prefix='tripwire-', dir=SCRATCH))

    def trips(mask, n=400):
        stop = []

        def forker():
            while not stop:
                subprocess.run(['/bin/true'])
        threads = [threading.Thread(target=forker) for _ in range(4)]
        for t in threads:
            t.start()
        hit = 0
        try:
            for i in range(n):
                d = base / f'{mask:x}-{i}'
                (d / 'sub').mkdir(parents=True)
                with w.Watch([str(d)], mask=mask) as g:
                    time.sleep(0.005)
                    hit += bool(g.dirty())
        finally:
            stop.append(1)
            for t in threads:
                t.join()
        return hit
    old = trips(w.ACCESS | w.IN_CLOSE_NOWRITE)
    new = trips(w.ACCESS)
    print(f'     (the old mask with IN_CLOSE_NOWRITE: {old} false trips of 400)')
    check('tripwire: the watch\'s own listing, inherited by children other threads fork, is no access (0 of 400)', new == 0, new)
    d = base / 'real'
    (d / 'sub').mkdir(parents=True)
    (d / 'f').write_text('x')
    for what, act in (('a read', lambda: (d / 'f').read_text()), ('a listing', lambda: os.listdir(d)),
                      ('a listing of a subdir', lambda: os.listdir(d / 'sub'))):
        with w.Watch([str(d)], mask=w.ACCESS) as g:
            act()
            check(f'tripwire: {what} after the watch started is seen', bool(g.dirty()))
    inputs.remove_tree(base)


# ---- end to end ------------------------------------------------------------------------------

def row_of(r, name):
    return r['items'].get(f'api/{name}', {})


def e2e_findings():
    """Astra's probes, one control per finding (see the top comment)."""
    good, item = '^case-mixed-dirs-sensitive$', 'case-mixed-dirs-sensitive'
    start = time.monotonic()
    r, log = tier(fake('child-stdout'), good)
    row = row_of(r, item)
    check('finding 1: a child that outlives the server holding its output: DIFF rsFailure crash, not MATCH',
          row.get('status') == 'DIFF' and row.get('rsFailure') == 'crash', row or log)
    check('finding 1: and the tier does not wait for it (under 15 s)', time.monotonic() - start < 15, time.monotonic() - start)
    for f in OUTSIDE:
        if os.path.exists(f):
            os.unlink(f)
    r, log = tier(fake('outside-write'), good)
    escaped = [f for f in OUTSIDE if os.path.exists(f)]
    check('finding 2: a write outside the session reaches no host file', not escaped, (escaped, row_of(r, item), log[-300:]))
    for f in escaped:
        os.unlink(f)
    r, log = tier(fake('bool-number'), good)
    check('finding 3: emitSkipped false -> 0 is a DIFF', row_of(r, item).get('status') == 'DIFF', row_of(r, item) or log)
    r, log = tier(fake('bad-rpc'), good)
    row = row_of(r, item)
    check('finding 3: "jsonrpc": "9.9" is a DIFF (malformed: rsFailure crash)', row.get('status') == 'DIFF' and row.get('rsFailure') == 'crash',
          row or log)
    unexec = SCRATCH / 'unexecutable-go'
    unexec.write_text('#!/bin/sh\nexit 0\n')
    os.chmod(unexec, 0o644)
    tag = f'{os.getpid()}-{time.time_ns()}'
    sc = {'name': 'h7-selftest-launch', 'about': f'self-test {tag}', 'fs': {'kind': 'full', 'files': {'@ROOT@/a.ts': f'export const a = "{tag}";'}},
          'createPrograms': [{'rootFiles': ['@ROOT@/a.ts'], 'compilerOptions': {'noLib': True}}]}
    sfile = SCRATCH / f'scenarios-launch-{tag}.json'
    sfile.write_text(json.dumps({'about': 'h7 self-test', 'version': 1, 'texts': {}, 'scenarios': [sc]}))
    r, log = tier(GO, '.', go=str(unexec), scenarios=sfile)
    row = row_of(r, 'h7-selftest-launch')
    key = r['scenarios'].get('h7-selftest-launch', {}).get('key')
    cached = key and (session.STATE / 'go-cache' / api.command_identity(str(unexec))[:16] / 'api' / f'h7-selftest-launch-{key}.json').exists()
    check('finding 4: a Go that cannot be exec\'d is ERROR, not GO-BROKEN', row.get('status') == 'ERROR', row or log)
    check('finding 4: and nothing is cached', key and not cached, (key, cached))
    sfile.unlink()
    r, log = tier(fake('corrupt-build'), '^build-clean$')
    check('finding 5: a build output corrupted, then removed by cleanBuild: DIFF', row_of(r, 'build-clean').get('status') == 'DIFF',
          row_of(r, 'build-clean') or log)
    r, log = tier(fake('stat-bait'), good)
    check('finding 6: stat and O_PATH of the real ~/.cache/typescript find nothing (the proxy fails: DIFF)',
          row_of(r, item).get('status') == 'DIFF', row_of(r, item) or log)
    r, log = tier(fake('fds'), good)
    check('finding 6: the server has no fd but 0, 1 and 2', row_of(r, item).get('status') == 'MATCH', row_of(r, item) or log)


def e2e_round3():
    """Astra's round 2 recheck (of 5c6b0c8d5): her cases, one control per finding."""
    good, item = '^case-mixed-dirs-sensitive$', 'case-mixed-dirs-sensitive'

    def status(mode):
        r, log = tier(fake(mode), good)
        return row_of(r, item), log
    for mode in ('trailing-length', 'trailing-deep'):
        row, log = status(mode)
        check(f'round 3 finding 1: {mode} (a header the reader cannot take, after the last answer) is a DIFF rsFailure crash',
              row.get('status') == 'DIFF' and row.get('rsFailure') == 'crash', row or log[-400:])
    for mode, what in (('shm-remount', 'clears read-only on /dev/shm and writes there'),
                       ('hide-proc-survivor', 'mounts a tmpfs over /proc and leaves a detached child'),
                       ('report-access', "writes a forged report into PID 1's report pipe")):
        row, log = status(mode)
        check(f'round 3 finding 2: a server that {what} is not MATCH (it cannot: {row.get("status")})',
              row.get('status') in ('DIFF', 'ERROR') and row.get('status') != 'MATCH', row or log[-400:])
    row, log = status('detached')
    check('round 3 finding 2: a detached grandchild (setsid) is a survivor: DIFF rsFailure crash',
          row.get('status') == 'DIFF' and row.get('rsFailure') == 'crash' and 'survived' in row.get('detail', ''), row or log[-400:])
    row, log = status('privs')
    check('round 3 finding 2: no capability is left, and mount, umount, mount_setattr, pivot_root, unshare(CLONE_NEWNS), '
          'unshare(CLONE_NEWUSER) and setns all fail for the server (MATCH)', row.get('status') == 'MATCH', row or log[-400:])
    row, log = status('home-skeleton')
    check('round 3 finding 3: $HOME/tmp replaced by a file is a DIFF', row.get('status') == 'DIFF', row or log[-400:])
    # the final recheck (of a08c934d6): storage the tier cannot read, or whose root changed, is no MATCH
    sc = {'name': 'review-noemit', 'about': 'read-only API over a disk fixture (Astra, final H7 recheck)',
          'disk': {'tsconfig.json': '{"compilerOptions":{"noLib":true,"noEmit":true},"files":["a.ts"]}', 'a.ts': 'export const a = 1;\n'},
          'openProjects': ['@ROOT@/tsconfig.json'], 'requests': ['getSourceFileNames']}
    sfile = SCRATCH / f'scenarios-noemit-{os.getpid()}.json'
    sfile.write_text(json.dumps({'about': 'review', 'version': 1, 'texts': {}, 'scenarios': [sc]}))
    r, log = tier(fake('root-hide'), '.', scenarios=sfile)
    row = row_of(r, 'review-noemit')
    check('final recheck: a write, then chmod 000 of the storage root (review-noemit): DIFF',
          row.get('status') == 'DIFF', row or log[-400:])
    sfile.unlink()
    for tier_ in ('full', 'quick'):
        r, log = tier(fake('root-mode'), '^build-composite$', tier_=tier_)
        row = row_of(r, 'build-composite')
        check(f'final recheck: chmod 0700 of the storage root on build-composite ({tier_}): DIFF',
              row.get('status') == 'DIFF', row or log[-400:])
    for mode in ('large-int', 'minus-zero', 'unicode-escape', 'bom', 'whitespace', 'overflow'):
        row, log = status(mode)
        check(f'round 2 recheck: {mode} still fails closed (not MATCH)', row.get('status') not in ('MATCH', None), row or log[-400:])


def e2e():
    good = '^case-mixed-dirs-sensitive$'
    item = 'case-mixed-dirs-sensitive'
    r, log = tier(fake('pass'), good)
    check('e2e pass: a transparent proxy MATCHes', row_of(r, item).get('status') == 'MATCH', log)

    r, log = tier(fake('wrong-path'), good)
    row = row_of(r, item)
    check('e2e wrong-path: DIFF', row.get('status') == 'DIFF' and '/0ut/' in row.get('detail', ''), row)
    code, out = compare({f'api/{item}': {'status': 'MATCH'}}, r)
    check('e2e wrong-path: LOST against a base where it MATCHed: PARITY FAIL', code == 1 and f'LOST      api/{item}' in out, out)
    code, out = compare({}, r)
    check('e2e wrong-path: NEW against a base without it: PARITY FAIL', code == 1 and f'NEW DIFF   api/{item}' in out, out)

    for mode, want in (('crash', 'crash'), ('late-exit', 'crash'), ('malformed', 'crash')):
        r, log = tier(fake(mode), good)
        row = row_of(r, item)
        check(f'e2e {mode}: DIFF rsFailure {want}', row.get('status') == 'DIFF' and row.get('rsFailure') == want, row or log)
    code, out = compare({f'api/{item}': {'status': 'MATCH'}}, r)
    check('e2e malformed: PARITY FAIL', code == 1, out)

    r, log = tier(fake('hang'), good, env={'PARITY_API_REQUEST_TIMEOUT': '3'})
    row = row_of(r, item)
    check('e2e hang: DIFF rsFailure timeout', row.get('status') == 'DIFF' and row.get('rsFailure') == 'timeout', row or log)

    r, log = tier(fake('nondet'), good)
    row = row_of(r, item)
    check('e2e nondet: DIFF rs-nondet', row.get('status') == 'DIFF' and 'rs-nondet' in row.get('detail', ''), row)
    r, log = tier(GO, good, go=fake('nondet'))
    check('e2e nondet as Go: GO-BROKEN (Go runs differ, tsc-rs stable)', row_of(r, item).get('status') == 'GO-BROKEN', row_of(r, item) or log)
    r, log = tier(GO, good, tier_='quick', go=fake('nondet'))
    check('e2e nondet as Go, quick: GO-BROKEN after the confirming run', row_of(r, item).get('status') == 'GO-BROKEN', row_of(r, item) or log)

    r, log = tier(fake('home-write'), good)
    row = row_of(r, item)
    check('e2e home-write: DIFF on the home', row.get('status') == 'DIFF' and 'home' in row.get('detail', ''), row)
    r, log = tier(fake('read-typings'), good)
    check('e2e read-typings: the real typings cache is not there (the proxy fails: DIFF)', row_of(r, item).get('status') == 'DIFF',
          row_of(r, item) or log)

    # Cache controls on a scenario of its own: a unique fixture, so its Go key is new.
    tag = f'{os.getpid()}-{time.time_ns()}'
    gl = 'interface Array<T> {}\ninterface Boolean {}\ninterface Function {}\ninterface CallableFunction {}\ninterface NewableFunction {}\ninterface IArguments {}\ninterface Number {}\ninterface Object {}\ninterface RegExp {}\ninterface String {}\n'
    sc = {'name': 'h7-selftest-build', 'about': f'self-test {tag}', 'caseSensitive': True,
          'disk': {'tsconfig.json': '{"compilerOptions": {"composite": true, "outDir": "out", "noLib": true}, "files": ["A/x.ts", "a/y.ts", "g.d.ts"]}',
                   'A/x.ts': f'export const x0 = "{tag}";\n', 'a/y.ts': 'export const x1 = 1;\n', 'g.d.ts': gl},
          'openProjects': ['@ROOT@/tsconfig.json'], 'requests': ['getSourceFileNames', 'emitToString'],
          'build': [{'create': {'rootNames': ['@ROOT@/tsconfig.json']}}, {'build': '@ROOT@/tsconfig.json'}]}
    sfile = SCRATCH / f'scenarios-{tag}.json'

    def with_scenario(s):
        sfile.write_text(json.dumps({'about': 'h7 self-test', 'version': 1, 'texts': {}, 'scenarios': [s]}))
        return sfile

    name = 'h7-selftest-build'
    r1, log1 = tier(GO, '.', scenarios=with_scenario(sc))
    info1 = r1['scenarios'].get(name, {})
    check('e2e cache: the first run fills Go (cold) and MATCHes (Go as tsc-rs)',
          info1.get('goCached') is False and row_of(r1, name).get('status') == 'MATCH', (r1['items'], log1))
    r2, _ = tier(GO, '.', scenarios=with_scenario(sc))
    info2 = r2['scenarios'].get(name, {})
    check('e2e cache: the second run reads Go from the cache, same key', info2.get('goCached') is True and info2.get('key') == info1.get('key'), info2)
    r3, _ = tier(GO, '.', scenarios=with_scenario({**sc, 'caseSensitive': False}))
    info3 = r3['scenarios'].get(name, {})
    check('e2e cache: another case flag is another key, and Go runs again', info3.get('goCached') is False and info3.get('key') != info1.get('key'), info3)
    sc4 = {**sc, 'disk': {**sc['disk'], 'a/y.ts': 'export const x1 = 12;\n'}}
    r4, _ = tier(GO, '.', scenarios=with_scenario(sc4))
    info4 = r4['scenarios'].get(name, {})
    check('e2e cache: one fixture byte more is another key, and Go runs again', info4.get('goCached') is False and info4.get('key') != info1.get('key'), info4)
    cfile = session.STATE / 'go-cache' / api.command_identity(GO)[:16] / 'api' / f'{name}-{info1.get("key")}.json'
    entry = json.loads(cfile.read_text())
    entry['material']['scenario']['caseSensitive'] = 'poisoned'
    cfile.write_text(json.dumps(entry))
    r5, _ = tier(GO, '.', scenarios=with_scenario(sc))
    check('e2e cache: an entry whose material is not the key\'s is not read', r5['scenarios'].get(name, {}).get('goCached') is False, r5['scenarios'])
    tree, _h = api.fixture_snapshot(sc)
    f = tree / 'a/y.ts'
    f.write_text('export const x1 = 99;\n')
    os.utime(f, (api.FIXED_MTIME, api.FIXED_MTIME))
    r6, log6 = tier(GO, '.', scenarios=with_scenario(sc))
    check('e2e cache: a fixture snapshot that is not its scenario\'s content is ERROR', row_of(r6, name).get('status') == 'ERROR', (r6['items'], log6))
    inputs.remove_tree(tree.parent)
    sfile.unlink()


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if '--e2e-only' in sys.argv:
        print(f'harness: {HARNESS}')
        e2e_round3()
        e2e_findings()
        e2e()
    else:
        unit()
        tripwire()
        if '--unit' not in sys.argv:
            e2e_round3()
            e2e_findings()
            e2e()
    print('H7 SELFTEST OK' if not FAILS else f'H7 SELFTEST FAIL ({len(FAILS)}): {", ".join(FAILS)}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
