#!/usr/bin/env python3
"""Lane B9's self-test of the api tier's raw request form (parity_api_session.py `calls`).

usage: python3 -I scripts/perf-rearch/parity_b9_test.py [--unit]   (prints B9 SELFTEST OK, or each FAIL)

Unit checks (under a second): the scenario format (what validate refuses), the placeholders
(@SNAPSHOT@, @PROJECT@, @ANSWER:<k>:<path>@, the large integers sent exactly), and that a scenario without
calls asks the server exactly what it asked before (the request list of every scenario in the file, made
by a recording session, is the one the old script makes), so the compared view of the other scenarios is
unchanged.

End-to-end checks (a few seconds; real sessions with the Go oracle, nothing cached): batch-page-cap-int64
sends each cap as written and Go answers one page at 2^32; positions-index-int64 resolves the module
symbol's handle from an earlier answer into the later calls.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_api as api  # noqa: E402
import parity_api_session as session  # noqa: E402

FAILED = []


def check(name, ok, info=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else ": " + str(info)[:300]}', flush=True)
    if not ok:
        FAILED.append(name)


class Recorder(session.Session):
    """A session that records what it would send; every answer is a fixed createSnapshot answer."""

    ANSWER = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {
        'snapshot': 7, 'projects': [{'id': 'p1'}, {'id': 'p2'}], 'data': 'QUJD', 'deep': {'list': [5, {'x': 'y'}]}}}).encode()

    def __init__(self, sc):  # noqa: D107 (no process: only the script's requests)
        self.sc, self.root, self.failure, self.sent = sc, session.root_of(sc), None, []

    def ask(self, method, params):
        self.sent.append((method, params))
        return self.ANSWER

    def interleave(self, orchestrators, arg):
        self.sent.append(('interleave', arg))


def old_script(sc):
    """The requests the script made before B9 (no calls), from the same recorder answers."""
    root = session.root_of(sc)
    sent = [('initialize', None)]
    params = {}
    if sc.get('fs'):
        params['fileSystem'] = session.expand(sc['fs'], root)
    for k in ('createPrograms', 'openProjects'):
        if sc.get(k) is not None:
            params[k] = session.expand(sc[k], root)
    sid, projects = None, []
    per = lambda sid, projects, reqs: [(m, {'snapshot': sid, 'project': p}) for p in projects for m in reqs or session.PER_PROJECT]
    if params:
        sent.append(('createSnapshot', params))
        sid, projects = 7, ['p1', 'p2']
        sent += per(sid, projects, sc.get('requests'))
    for u in sc.get('updates') or []:
        sent.append(('updateSnapshot', {'snapshot': sid, 'changes': session.expand(u['changes'], root)}))
        sent += per(7, projects, u.get('requests'))
        if u.get('release'):
            sent.append(('release', {'snapshot': sid}))
    orchestrator = None
    for step in sc.get('build') or []:
        (kind, arg), = step.items()
        if kind == 'create':
            sent.append((session.BUILD_STEPS[kind], session.expand(arg, root)))
            orchestrator = None  # the recorder's answer has no buildOrchestratorID
        elif kind == 'interleave':
            sent.append(('interleave', session.expand(arg, root)))
        elif kind == 'dispose':
            sent.append((session.BUILD_STEPS[kind], {'buildOrchestratorID': orchestrator}))
        else:
            sent.append((session.BUILD_STEPS[kind], {'buildOrchestratorID': orchestrator, **({'project': session.expand(arg, root)} if arg else {})}))
    for u in sc.get('afterBuild') or []:
        sent.append(('updateSnapshot', {'snapshot': 7, 'changes': session.expand(u['changes'], root)}))
        sent += per(7, projects, u.get('requests'))
        if u.get('release'):
            sent.append(('release', {'snapshot': 7}))
    return sent


def unit():
    base = {'name': 'b9-unit', 'about': 'x', 'fs': {'kind': 'full', 'files': {}}}
    for bad, why in [({'calls': {}}, 'calls that are not a list'),
                     ({'calls': [{'method': 'ping'}]}, 'a call without params'),
                     ({'calls': [{'method': 'ping', 'params': None, 'x': 1}]}, 'an extra key'),
                     ({'calls': [{'method': 'createSnapshot', 'params': {}}]}, 'createSnapshot as a call'),
                     ({'calls': [{'method': 'build', 'params': {}}]}, 'a build-orchestrator method'),
                     ({'calls': [{'method': 'ping', 'params': '@NOPE@'}]}, 'an unknown placeholder'),
                     ({'calls': [{'method': 'ping', 'params': '@ANSWER:0:result@'}]}, 'an answer of itself'),
                     ({'calls': [{'method': 'ping', 'params': None}, {'method': 'ping', 'params': '@ANSWER:2:result@'}]},
                      'an answer of a later call')]:
        try:
            session.validate({**base, **bad})
            check(f'validate refuses {why}', False)
        except session.ScenarioError:
            check(f'validate refuses {why}', True)
    sc = {**base, 'createPrograms': [], 'requests': ['getSourceFileNames'], 'calls': [
        {'method': 'ping', 'params': None},
        {'method': 'x', 'params': {'s': '@SNAPSHOT@', 'p': '@PROJECT@', 'd': '@ANSWER:0:result.data@',
                                   'deep': ['@ANSWER:0:result.deep.list.1.x@', '@ANSWER:0:result.missing@', '@ROOT@/a.ts'],
                                   'big': 9223372036854775808, 'neg': -4294967296}}]}
    session.validate(sc)
    r = Recorder(sc)
    r.script()
    calls = r.sent[-2:]
    root = session.root_of(sc)
    check('the placeholders resolve to the snapshot, the first project and earlier answers',
          calls[1] == ('x', {'s': 7, 'p': 'p1', 'd': 'QUJD', 'deep': ['y', None, f'{root}/a.ts'],
                             'big': 9223372036854775808, 'neg': -4294967296}), calls[1])
    check('calls come after the createSnapshot requests and before the updates',
          [m for m, _ in r.sent] == ['initialize', 'createSnapshot', 'getSourceFileNames', 'getSourceFileNames', 'ping', 'x'],
          [m for m, _ in r.sent])
    check('a large integer is sent as written', json.dumps(calls[1][1]['big']) == '9223372036854775808')
    same = []
    for s in api.load_scenarios():
        if 'calls' in s:
            continue
        r = Recorder(s)
        r.script()
        if r.sent != old_script(s):
            same.append(s['name'])
    check('every scenario without calls asks what the old script asked', not same, same)
    with_calls = next(s for s in api.load_scenarios() if 'calls' in s)
    r = Recorder(with_calls)
    r.script()
    check('control: a scenario with calls does not ask what the old script asked', r.sent != old_script(with_calls))


def e2e():
    by_name = {s['name']: s for s in api.load_scenarios()}
    with tempfile.TemporaryDirectory(dir=session.SCRATCH.parent) as work:
        t = session.run_session(str(api.GO), by_name['batch-page-cap-int64'], Path(work) / 'batch')
        batch = [s for s in t['steps'] if s['method'] == 'batchRequests']
        check('batch: the session is clean', t['failure'] is None, t.get('why'))
        check('batch: the caps are sent as written',
              [s['params']['maxResponseBytesPerPage'] for s in batch] == [4294967296, 9007199254740991, 9223372036854775808, 0, 1])
        check('batch: Go answers one page at 2^32', '"responses":[{"method":"ping","result":"pong"},{"method":"ping","result":"pong"}]}'
              in batch[0].get('answer', ''), batch[0].get('answer'))
        t = session.run_session(str(api.GO), by_name['positions-index-int64'], Path(work) / 'positions')
        refs = [s for s in t['steps'] if s['method'] == 'getReferencedSymbolsForNode']
        symbol = json.loads(t['steps'][[s['method'] for s in t['steps']].index('getSymbolAtPosition')]['answer'])
        check('positions: the node handle comes from the getSymbolAtPosition answer',
              refs and all(s['params']['node'] == symbol['result']['valueDeclaration'] for s in refs), refs[:1])
        check('positions: Go answers [] at 2^32', refs and '"result":[]' in refs[2].get('answer', ''), refs[2:3])


def main():
    unit()
    if '--unit' not in sys.argv:
        e2e()
    print('B9 SELFTEST FAIL' if FAILED else 'B9 SELFTEST OK')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
