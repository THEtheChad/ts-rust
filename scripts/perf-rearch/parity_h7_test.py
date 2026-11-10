#!/usr/bin/env python3
"""Lane H7's negative controls for the api tier (parity_api.py, parity_api_session.py).

usage: parity_h7_test.py [--unit]      prints H7 SELFTEST OK, or each FAIL; exit 1 on a failure

Unit checks (seconds): the key holds every effective input (each change below is another key), the
scenario format refuses what is not hermetic, the judge's order, and api grandfather entries.
End-to-end checks (about a minute; real sessions in the sandbox, through `parity_api.py` and
`parity_compare.py compare`): fake tsc-rs servers are Python proxies in front of the Go oracle
(written to SCRATCH at run time):
  pass          forwards everything: MATCH (the proxy itself is transparent)
  wrong-path    writes emit outputs to /0ut/ instead of /out/: DIFF, and PARITY FAIL (LOST and NEW)
  crash         a Rust panic line and exit 101 at the first emit: DIFF rsFailure crash
  late-exit     answers everything, then exits 3: DIFF rsFailure crash
  malformed     a frame with a bad header: DIFF rsFailure crash (malformed)
  hang          never answers emit: DIFF rsFailure timeout (REQUEST_TIMEOUT lowered to 3 s)
  nondet        a different output name on every run: DIFF rs-nondet (full); as Go, GO-BROKEN
  home-write    writes a file into its home: DIFF (homeWrites)
  read-typings  reads the real ~/.cache/typescript: ERROR, and the tier publishes no Go entry
Cache controls on a scenario of its own (a unique fixture, so Go runs cold): a second run reads Go from
the cache; another case flag, one fixture byte more and a poisoned cache entry each make Go run again; a
fixture changed after its snapshot is made is ERROR.
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
GO = str(api.GO)
SCRATCH = session.STATE / 'scratch/h7-selftest'
FAILS = []


def check(name, ok, info=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else ": " + str(info)[:400]}', flush=True)
    if not ok:
        FAILS.append(name)


FAKE = r'''
import os, re, subprocess, sys, threading, time, random
mode, go, typings = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]
if mode == 'read-typings':
    os.listdir(typings)
    open(os.path.join(typings, '7.1', 'package.json')).read()
if mode == 'home-write':
    open(os.path.join(os.environ['HOME'], '.cache', 'fake-was-here'), 'w').write('x')
p = subprocess.Popen([go, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def pump():
    for chunk in iter(lambda: sys.stdin.buffer.read1(65536), b''):
        p.stdin.write(chunk); p.stdin.flush()
    p.stdin.close()
threading.Thread(target=pump, daemon=True).start()
out = sys.stdout.buffer
tag = str(random.random())
while True:
    head = p.stdout.readline()
    if not head:
        break
    n = int(head.split(b':')[1]); p.stdout.readline()
    body = p.stdout.read(n)
    emit = b'outputFiles' in body or b'emittedFiles' in body
    if emit and mode == 'wrong-path':
        body = body.replace(b'/out/', b'/0ut/')
    if emit and mode == 'nondet':
        body = body.replace(b'/out/', ('/out-' + tag + '/').encode())
    if emit and mode == 'crash':
        sys.stderr.write("thread 'main' panicked at src/fake.rs:1:1:\nfake\n"); sys.stderr.flush(); os._exit(101)
    if emit and mode == 'hang':
        time.sleep(3600)
    if emit and mode == 'malformed':
        out.write(b'Content-Lenght: 2\r\n\r\n{}'); out.flush(); continue
    out.write(b'Content-Length: %d\r\n\r\n' % len(body) + body); out.flush()
code = p.wait()
sys.exit(3 if mode == 'late-exit' else code)
'''


def fake(mode):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    f = SCRATCH / 'fake_api.py'
    f.write_text(FAKE)
    return f'/usr/bin/python3 {f} {mode} {GO} {Path.home() / ".cache/typescript"}'


def tier(rs, only, tier_='full', go=GO, scenarios=None, env=None):
    """Runs the api tier: (results.json, stdout)."""
    out = Path(tempfile.mkdtemp(prefix='run-', dir=SCRATCH))
    cmd = [sys.executable, '-I', str(HERE / 'parity_api.py'), '--rs', rs, '--go', go, '--out', str(out), '--tier', tier_,
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
    }
    for what, sc in variants.items():
        check(f'key: {what} is another key', inputs.json_hash(api.material(sc, None, {}, 'g' * 64)) != k0)
    check('key: the fixture hash', inputs.json_hash(api.material(base, 'f' * 24, {}, 'g' * 64)) != k0)
    check('key: the ancestors\' state', inputs.json_hash(api.material(base, None, {'/package.json': 'x'}, 'g' * 64)) != k0)
    check('key: the Go binary', inputs.json_hash(api.material(base, None, {}, 'h' * 64)) != k0)
    saved = (api.session_sha, session.PROTOCOL, session.SANDBOX_HOME)
    api.session_sha = lambda: 'another-source'
    check('key: the session driver\'s source (harness sources)', inputs.json_hash(api.material(base, None, {}, 'g' * 64)) != k0)
    api.session_sha = saved[0]
    session.PROTOCOL = 'another protocol'
    check('key: the API protocol', inputs.json_hash(api.material(base, None, {}, 'g' * 64)) != k0)
    session.PROTOCOL = saved[1]
    session.SANDBOX_HOME = Path('/elsewhere')
    check('key: the private home path', inputs.json_hash(api.material(base, None, {}, 'g' * 64)) != k0)
    session.SANDBOX_HOME = saved[2]
    check('key: unchanged inputs give the same key', inputs.json_hash(api.material(dict(base), None, {}, 'g' * 64)) == k0)

    refused = {
        'a layer filesystem (reads the host)': {**base, 'fs': {'kind': 'layer', 'files': {}}},
        'a host symlink': {**base, 'fs': {**base['fs'], 'symlinks': {'@ROOT@/l': {'target': '/etc', 'host': True}}}},
        'no filesystem': {k: v for k, v in base.items() if k != 'fs'},
        'build steps without a disk fixture': {**base, 'build': [{'create': {'rootNames': []}}]},
        'an unknown key': {**base, 'extra': 1},
        'an absolute fixture path': {k: v for k, v in base.items() if k != 'fs'} | {'disk': {'/etc/passwd': 'x'}},
        'an unknown request': {**base, 'requests': ['getTypeAtPosition']},
    }
    for what, sc in refused.items():
        try:
            session.validate(sc)
            check(f'scenario: {what} is refused', False, 'accepted')
        except session.ScenarioError:
            check(f'scenario: {what} is refused', True)

    t = lambda **kw: {'steps': [{'method': 'initialize', 'params': None, 'result': kw.get('r', 1)}], 'exit': kw.get('exit', 0),  # noqa: E731
                      'forced': False, 'failure': kw.get('failure'), 'why': kw.get('why'),
                      'stderr': {'sha': 's', 'site': 's', 'crash': None}, 'written': {}, 'writtenText': {}, 'homeWrites': 0, 'secs': 0}
    j = lambda go, rs, confirm=None: api.judge(go, rs, confirm or (lambda: t()))[:3]  # noqa: E731
    check('judge: equal is MATCH', j([t(), t()], [t(), t()])[0] == 'MATCH')
    check('judge: a tsc-rs crash is a DIFF with rsFailure crash, even where Go is broken',
          j([t(failure='crash', why='x'), t()], [t(failure='crash', why='x'), t()]) [::2] == ('DIFF', 'crash'))
    check('judge: malformed counts as crash', j([t(), t()], [t(failure='malformed', why='x'), t()])[2] == 'crash')
    check('judge: a tsc-rs timeout is rsFailure timeout', j([t(), t()], [t(failure='timeout', why='x'), t()])[2] == 'timeout')
    check('judge: two tsc-rs runs that differ are rs-nondet', 'rs-nondet' in (j([t(), t()], [t(), t(r=2)])[1] or ''))
    check('judge: Go runs that differ, tsc-rs stable: GO-BROKEN', j([t(), t(r=2)], [t(r=3), t(r=3)])[0] == 'GO-BROKEN')
    check('judge: a Go crash, tsc-rs clean: GO-BROKEN', j([t(failure='crash', why='p'), t()], [t(r=3), t(r=3)])[0] == 'GO-BROKEN')
    check('judge: quick GO-BROKEN needs the confirming run', j([t(), t(r=2)], [t(r=3)], lambda: t(r=4))[0] == 'DIFF')
    check('judge: quick GO-BROKEN with an equal confirming run', j([t(), t(r=2)], [t(r=3)], lambda: t(r=3))[0] == 'GO-BROKEN')
    check('judge: a written file that differs is a DIFF',
          j([t(), t()], [{**t(), 'written': {'a': 'x'}}, {**t(), 'written': {'a': 'x'}}])[0] == 'DIFF')
    check('judge: a home write is a DIFF', j([t(), t()], [{**t(), 'homeWrites': 1}, {**t(), 'homeWrites': 1}])[0] == 'DIFF')
    check('judge: stderr that differs is a DIFF',
          j([t(), t()], [{**t(), 'stderr': {'sha': 'z', 'site': 'z', 'crash': None}}] * 2)[0] == 'DIFF')

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


# ---- end to end ------------------------------------------------------------------------------

def e2e():
    good = '^case-mixed-dirs-sensitive$'
    r, log = tier(fake('pass'), good)
    check('e2e pass: a transparent proxy MATCHes', r['items'].get('api/case-mixed-dirs-sensitive', {}).get('status') == 'MATCH', log)

    r, log = tier(fake('wrong-path'), good)
    row = r['items'].get('api/case-mixed-dirs-sensitive', {})
    check('e2e wrong-path: DIFF', row.get('status') == 'DIFF' and '/0ut/' in row.get('detail', ''), row)
    code, out = compare({'api/case-mixed-dirs-sensitive': {'status': 'MATCH'}}, r)
    check('e2e wrong-path: LOST against a base where it MATCHed: PARITY FAIL', code == 1 and 'LOST      api/case-mixed-dirs-sensitive' in out, out)
    code, out = compare({}, r)
    check('e2e wrong-path: NEW against a base without it: PARITY FAIL', code == 1 and 'NEW DIFF   api/case-mixed-dirs-sensitive' in out, out)

    for mode, want in (('crash', 'crash'), ('late-exit', 'crash'), ('malformed', 'crash')):
        r, log = tier(fake(mode), good)
        row = r['items'].get('api/case-mixed-dirs-sensitive', {})
        check(f'e2e {mode}: DIFF rsFailure {want}', row.get('status') == 'DIFF' and row.get('rsFailure') == want, row or log)
    code, out = compare({'api/case-mixed-dirs-sensitive': {'status': 'MATCH'}}, r)
    check('e2e malformed: PARITY FAIL', code == 1, out)

    r, log = tier(fake('hang'), good, env={'PARITY_API_REQUEST_TIMEOUT': '3'})
    row = r['items'].get('api/case-mixed-dirs-sensitive', {})
    check('e2e hang: DIFF rsFailure timeout', row.get('status') == 'DIFF' and row.get('rsFailure') == 'timeout', row or log)

    r, log = tier(fake('nondet'), good)
    row = r['items'].get('api/case-mixed-dirs-sensitive', {})
    check('e2e nondet: DIFF rs-nondet', row.get('status') == 'DIFF' and 'rs-nondet' in row.get('detail', ''), row)

    r, log = tier(GO, good, go=fake('nondet'))
    row = r['items'].get('api/case-mixed-dirs-sensitive', {})
    check('e2e nondet as Go: GO-BROKEN (Go runs differ, tsc-rs stable)', row.get('status') == 'GO-BROKEN', row or log)
    r, log = tier(GO, good, tier_='quick', go=fake('nondet'))
    row = r['items'].get('api/case-mixed-dirs-sensitive', {})
    check('e2e nondet as Go, quick: GO-BROKEN after the confirming run', row.get('status') == 'GO-BROKEN', row or log)

    r, log = tier(fake('home-write'), good)
    row = r['items'].get('api/case-mixed-dirs-sensitive', {})
    check('e2e home-write: DIFF on homeWrites', row.get('status') == 'DIFF' and 'homeWrites' in row.get('detail', ''), row)

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

    item = 'api/h7-selftest-build'
    r1, log1 = tier(GO, '.', scenarios=with_scenario(sc))
    info1 = r1['scenarios'].get('h7-selftest-build', {})
    check('e2e cache: the first run fills Go (cold) and MATCHes (Go as tsc-rs)',
          info1.get('goCached') is False and r1['items'].get(item, {}).get('status') == 'MATCH', (r1['items'], log1))
    r2, log2 = tier(GO, '.', scenarios=with_scenario(sc))
    info2 = r2['scenarios'].get('h7-selftest-build', {})
    check('e2e cache: the second run reads Go from the cache, same key', info2.get('goCached') is True and info2.get('key') == info1.get('key'), info2)
    r3, _ = tier(GO, '.', scenarios=with_scenario({**sc, 'caseSensitive': False}))
    info3 = r3['scenarios'].get('h7-selftest-build', {})
    check('e2e cache: another case flag is another key, and Go runs again', info3.get('goCached') is False and info3.get('key') != info1.get('key'), info3)
    sc4 = {**sc, 'disk': {**sc['disk'], 'a/y.ts': 'export const x1 = 12;\n'}}
    r4, _ = tier(GO, '.', scenarios=with_scenario(sc4))
    info4 = r4['scenarios'].get('h7-selftest-build', {})
    check('e2e cache: one fixture byte more is another key, and Go runs again', info4.get('goCached') is False and info4.get('key') != info1.get('key'), info4)
    cfile = session.STATE / 'go-cache' / api.command_identity(GO)[:16] / 'api' / f'h7-selftest-build-{info1.get("key")}.json'
    entry = json.loads(cfile.read_text())
    entry['material']['scenario']['caseSensitive'] = 'poisoned'
    cfile.write_text(json.dumps(entry))
    r5, _ = tier(GO, '.', scenarios=with_scenario(sc))
    check('e2e cache: an entry whose material is not the key\'s is not read', r5['scenarios'].get('h7-selftest-build', {}).get('goCached') is False,
          r5['scenarios'])
    tree, _h = api.fixture_snapshot(sc)
    f = tree / 'a/y.ts'
    os.chmod(tree / 'a', 0o755)
    os.chmod(f, 0o644)
    f.write_text('export const x1 = 99;\n')
    os.chmod(f, 0o444)
    os.utime(f, (api.FIXED_MTIME, api.FIXED_MTIME))
    os.chmod(tree / 'a', 0o555)
    r6, log6 = tier(GO, '.', scenarios=with_scenario(sc))
    check('e2e cache: a fixture snapshot that is not its scenario\'s content is ERROR', r6['items'].get(item, {}).get('status') == 'ERROR', (r6['items'], log6))
    inputs.remove_tree(tree.parent)

    tag2 = f'{tag}-typings'
    sc7 = {**sc, 'disk': {**sc['disk'], 'A/x.ts': f'export const x0 = "{tag2}";\n'}}
    r7, log7 = tier(fake('read-typings'), '.', scenarios=with_scenario(sc7))
    row = r7['items'].get(item, {})
    check('e2e read-typings: ERROR (a process touched an unkeyed path)', row.get('status') == 'ERROR' and 'unkeyed path' in row.get('detail', ''),
          row or log7)
    key7 = r7['scenarios'].get('h7-selftest-build', {}).get('key')
    published = (cfile.parent / f'h7-selftest-build-{key7}.json').exists()
    check('e2e read-typings: the tier published no Go entry (staged entries discarded)', key7 and not published, (key7, log7))
    code, out = compare({}, r7)
    check('e2e read-typings: PARITY FAIL', code == 1 and 'ERROR' in out, out)
    sfile.unlink()


def main():
    unit()
    if '--unit' not in sys.argv:
        e2e()
    print('H7 SELFTEST OK' if not FAILS else f'H7 SELFTEST FAIL ({len(FAILS)}): {", ".join(FAILS)}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
