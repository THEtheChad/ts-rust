#!/usr/bin/env python3
"""Lane B8's self-test of the api tier's callback mode (parity_api_session.py `callbacks`): the client
serves the scenario's files through Go's callbackFS callbacks, with nothing of them on disk.

usage: python3 -I scripts/perf-rearch/parity_b8_test.py [--unit]   (prints B8 SELFTEST OK, or each FAIL)

Unit checks (under a second): the answers of `Callbacks` for a served file, a missing file, a directory,
the root, a path outside the tree ("useOS"), a stat override, writeFile into the tree and what a later
read sees of it; the scenario format; the server arguments; and that a transcript without callbacks has
the same compared view as before (no new key), so the comparison of the other scenarios is unchanged.

End-to-end checks (about 10 s; real sessions with the Go oracle, nothing cached):
  served       a build of a project that exists only in the served tree: the outputs reach the client
               (clientFiles), nothing is written to disk, and the transcript records the callbacks that read
               the sources (readFile of each root file)
  empty-tree   the same build with an empty served tree: the server finds no tsconfig.json (the files came
               from the callbacks, not from anywhere else)
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_api as api  # noqa: E402
import parity_api_session as session  # noqa: E402

FAILED = []
ROOT = '/parity-api/b8-selftest'


def check(name, ok, info=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else ": " + str(info)[:300]}', flush=True)
    if not ok:
        FAILED.append(name)


def unit():
    cb = session.Callbacks({'enable': ['readFile'], 'files': {'@ROOT@/src/a.ts': 'A', '@ROOT@/tsconfig.json': '{}'},
                            'stat': {'@ROOT@/src/a.ts': '2030-01-01T00:00:00Z'}}, ROOT)
    check('readFile of a served file', cb.answer('readFile', f'{ROOT}/src/a.ts') == {'kind': 'value', 'value': 'A'})
    check('readFile of a missing file', cb.answer('readFile', f'{ROOT}/src/b.ts') == {'kind': 'missing'})
    check('fileExists', cb.answer('fileExists', f'{ROOT}/src/a.ts') == {'kind': 'value', 'value': True}
          and cb.answer('fileExists', f'{ROOT}/src') == {'kind': 'value', 'value': False})
    check('directoryExists of the root, a dir, a file and a missing dir',
          [cb.answer('directoryExists', p)['value'] for p in (ROOT, f'{ROOT}/src', f'{ROOT}/src/a.ts', f'{ROOT}/x')]
          == [True, True, False, False])
    check('getAccessibleEntries', cb.answer('getAccessibleEntries', ROOT)
          == {'kind': 'value', 'value': {'files': ['tsconfig.json'], 'directories': ['src']}})
    check('realpath under the root is the identity', cb.answer('realpath', f'{ROOT}/src') == {'kind': 'identity'})
    check('every callback outside the root is useOS',
          all(cb.answer(m, p) == {'kind': 'useOS'} for m in ('readFile', 'fileExists', 'directoryExists', 'stat')
              for p in ('/parity-api', 'bundled:///libs/lib.d.ts', '/usr/lib/x')))
    st = cb.answer('stat', f'{ROOT}/src/a.ts')
    check('stat of a served file: the override mtime, a regular file, its size',
          st == {'kind': 'value', 'value': {'mode': 0o100644, 'size': 1, 'mtime': '2030-01-01T00:00:00Z'}}, st)
    check('stat of a served dir: the fixed mtime', cb.answer('stat', f'{ROOT}/src')['value']
          == {'mode': 0o040755, 'size': 0, 'mtime': session.CALLBACK_MTIME})
    check('stat of a missing path', cb.answer('stat', f'{ROOT}/nope') == {'kind': 'missing'})
    check('writeFile into the tree', cb.answer('writeFile', {'path': f'{ROOT}/out/a.js', 'data': 'J'})
          == {'kind': 'value', 'value': None})
    check('a written file is read back and listed', cb.answer('readFile', f'{ROOT}/out/a.js')['value'] == 'J'
          and cb.answer('directoryExists', f'{ROOT}/out')['value'] and cb.written == {f'{ROOT}/out/a.js': 'J'})
    check('writeFile outside the tree is useOS', cb.answer('writeFile', {'path': '/x/y', 'data': ''}) == {'kind': 'useOS'})
    made = cb.take()
    check('the record: each callback once, sorted, and empty after take',
          made == sorted(set(made)) and f'readFile {ROOT}/src/a.ts' in made and cb.take() == [])
    try:
        cb.answer('readFile', 3)
        check('a callback whose path is not a string is malformed', False)
    except session.Malformed:
        check('a callback whose path is not a string is malformed', True)
    disk = session.Callbacks({'enable': ['stat'], 'stat': {'@ROOT@/out/a.d.ts': '2030-01-01T00:00:00Z'}}, ROOT)
    check('without a tree every read is useOS', disk.answer('readFile', f'{ROOT}/src/a.ts') == {'kind': 'useOS'})

    base = {'name': 'b8-unit', 'about': 'x'}
    for bad, why in [({'callbacks': {'enable': []}, 'disk': {'a': 'b'}}, 'empty enable'),
                     ({'callbacks': {'enable': ['readFile:bogus'], 'files': {}}}, 'a bad suffix'),
                     ({'callbacks': {'enable': ['nope'], 'files': {}}}, 'an unknown callback'),
                     ({'callbacks': {'enable': ['readFile'], 'files': {'/abs': 'x'}}}, 'a path outside @ROOT@'),
                     ({'callbacks': {'enable': ['readFile'], 'files': {}}, 'disk': {'a': 'b'}}, 'a tree and a disk'),
                     ({'callbacks': {'enable': ['stat']}}, 'no filesystem at all')]:
        try:
            session.validate({**base, **bad})
            check(f'validate refuses {why}', False)
        except session.ScenarioError:
            check(f'validate refuses {why}', True)
    ok = {**base, 'callbacks': {'enable': ['readFile', 'realpath:identity'], 'files': {}}, 'build': [{'create': {}}]}
    session.validate(ok)
    check('a build over a served tree needs no disk fixture', True)
    check('the server arguments end with the callbacks list',
          session.server_args(ok)[-2:] == ['--callbacks', 'readFile,realpath:identity'])
    check('no callbacks: no --callbacks argument', '--callbacks' not in session.server_args({**base, 'fs': {'kind': 'full'}}))

    plain = {'steps': [{'method': 'initialize', 'params': None, 'answer': '{}', 'fs': {}}], 'exit': 0, 'forced': False,
             'survivors': [], 'reaped': 0, 'failure': None, 'stderr': {'sha': 'x'}, 'fs': {}}
    check('a transcript without callbacks has the compared view it had before B8',
          api.view(plain) == {'steps': [{'method': 'initialize', 'params': None, 'answer': '{}'}], 'exit': 0, 'forced': False,
                              'survivors': [], 'reaped': 0, 'failure': None, 'stderr': 'x',
                              'storage': {'steps': [{}], 'end': {}, 'writes': {}}})
    with_cb = {**plain, 'clientFiles': {'/r/a': 'x'}, 'callbacksAfter': [],
               'steps': [{**plain['steps'][0], 'callbacks': ['readFile /r/a']}]}
    v = api.view(with_cb)
    check('a callback transcript compares its callbacks and the client files',
          v['steps'][0]['callbacks'] == ['readFile /r/a'] and v['clientFiles'] == {'/r/a': 'x'} and v['callbacksAfter'] == [])


TSCONFIG = '{"compilerOptions":{"noLib":true,"outDir":"out","rootDir":"src","module":"esnext","moduleResolution":"bundler"},"files":["src/a.ts","src/g.d.ts"]}'


def scenario(name, files):
    return {'name': name, 'about': 'B8 self-test',
            'callbacks': {'enable': ['readFile', 'fileExists', 'directoryExists', 'getAccessibleEntries', 'realpath', 'stat',
                                     'writeFile'], 'files': files},
            'build': [{'create': {'rootNames': ['@ROOT@/tsconfig.json']}}, {'build': '@ROOT@/tsconfig.json'}]}


def e2e():
    globals_text = json.loads(api.SCENARIOS.read_text())['texts']['@GLOBALS@']
    files = {'@ROOT@/tsconfig.json': TSCONFIG, '@ROOT@/src/a.ts': 'export const a: number = 1;\n', '@ROOT@/src/g.d.ts': globals_text}
    with tempfile.TemporaryDirectory(dir=session.SCRATCH.parent) as work:
        sc = scenario('b8-selftest-served', files)
        session.validate(sc)
        t = session.run_session(str(api.GO), sc, Path(work) / 'served')
        root = session.root_of(sc)
        build = [s for s in t['steps'] if s['method'] == 'build']
        made = build[0].get('callbacks', []) if build else []
        check('served: the session is clean', t['failure'] is None, t.get('why'))
        check('served: the build reads each source through readFile',
              all(f'readFile {root}/{p}' in made for p in ('tsconfig.json', 'src/a.ts', 'src/g.d.ts')), made)
        check('served: the outputs reach the client', t.get('clientFiles', {}).get(f'{root}/out/a.js', '').startswith('export const a'),
              t.get('clientFiles'))
        check('served: nothing is written to disk', not t['fs'].get('disk') and not t.get('writes'), t['fs'])
        check('served: the root is not on the disk the server sees', not Path(root).exists())
        e = session.run_session(str(api.GO), scenario('b8-selftest-empty', {}), Path(work) / 'empty')
        answer = next((s.get('answer', '') for s in e['steps'] if s['method'] == 'build'), '')
        made = next((s.get('callbacks', []) for s in e['steps'] if s['method'] == 'build'), [])
        check('empty-tree: the server looks for tsconfig.json through the client and does not find it',
              'tsconfig.json' in answer and not e.get('clientFiles') and any('tsconfig.json' in c for c in made), answer[:300])


def main():
    unit()
    if '--unit' not in sys.argv:
        e2e()
    print('B8 SELFTEST FAIL' if FAILED else 'B8 SELFTEST OK')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
