#!/usr/bin/env python3
"""Lane B7's self-test of the api tier's one normalisation: parity_api.sort_changed_projects puts the
members of result.changes.changedProjects of an answer in key order and changes nothing else.

usage: python3 -I scripts/perf-rearch/parity_b7_test.py      (under a second; prints B7 SELFTEST OK)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_api as api  # noqa: E402

FAILED = []


def check(name, ok):
    print(f'{"ok  " if ok else "FAIL"} {name}')
    if not ok:
        FAILED.append(name)


def msg(changes, rest=',"operation":{},"projects":[{"id":"/r/q"},{"id":"/r/p"}],"snapshot":3'):
    return '{"jsonrpc":"2.0","id":27,"result":{"changes":' + changes + rest + '}}'


B = '"/r/b/tsconfig.json":{"changedFiles":["/r/b/src/z.ts","/r/b/src/a.ts"],"x":{"b":1,"a":2}}'
P = '"/r/b/tsconfig.preserve.json":{"changedFiles":["/r/b/src/index.ts"]}'
A = '"/r/a/tsconfig.json":{"changedFiles":["/r/a/src/a, b.ts"]}'


def main():
    sort = api.sort_changed_projects
    raw = msg('{"changedProjects":{' + P + ',' + B + '}}')
    want = msg('{"changedProjects":{' + B + ',' + P + '}}')
    check('two projects: the members in key order, every byte of each member as it was', sort(raw) == want)
    check('the sorted message is the same JSON value', json.loads(sort(raw)) == json.loads(raw))
    check('already in order: unchanged', sort(want) == want)
    check('idempotent', sort(sort(raw)) == sort(raw))
    three = msg('{"changedProjects":{' + P + ',' + A + ',' + B + '}}')
    check('three projects (a value holding ", "): key order',
          sort(three) == msg('{"changedProjects":{' + A + ',' + B + ',' + P + '}}'))
    check('arrays and objects inside a member keep their order',
          '["/r/b/src/z.ts","/r/b/src/a.ts"],"x":{"b":1,"a":2}' in sort(raw))
    check('the result\'s other members and arrays keep their order (projects q before p)',
          sort(raw).endswith(',"operation":{},"projects":[{"id":"/r/q"},{"id":"/r/p"}],"snapshot":3}}'))
    other = msg('{"removedProjects":["/r/z","/r/a"],"changedProjects":{' + P + ',' + B + '},"zz":{"b":1,"a":2}}')
    check('the other members of changes keep their order and bytes',
          sort(other) == msg('{"removedProjects":["/r/z","/r/a"],"changedProjects":{' + B + ',' + P + '},"zz":{"b":1,"a":2}}'))
    one = msg('{"changedProjects":{' + P + '}}')
    check('one project: unchanged', sort(one) == one)
    elsewhere = '{"jsonrpc":"2.0","id":3,"result":{"changedProjects":{' + P + ',' + B + '}}}'
    check('a changedProjects not under result.changes: unchanged', sort(elsewhere) == elsewhere)
    nested = msg('{"x":{"changedProjects":{' + P + ',' + B + '}}}')
    check('a changedProjects deeper in changes: unchanged', sort(nested) == nested)
    in_string = '{"jsonrpc":"2.0","id":3,"result":{"text":"\\"changedProjects\\":{\\"b\\":1,\\"a\\":2}"}}'
    check('the name inside a string: unchanged', sort(in_string) == in_string)
    spaced = msg('{"changedProjects":{' + P + ', ' + B + '}}')
    check('not compact (a space between members): unchanged', sort(spaced) == spaced)
    broken = raw[:-5]
    check('malformed: returned as it was', sort(broken) == broken)
    error = '{"jsonrpc":"2.0","id":4,"error":{"code":-32602,"message":"changedProjects"}}'
    check('an error answer: unchanged', sort(error) == error)

    def transcript(answer):
        return {'steps': [{'method': 'updateSnapshot', 'params': {'snapshot': 2}, 'answer': answer}], 'exit': 0,
                'forced': False, 'survivors': [], 'reaped': 0, 'failure': None, 'stderr': {'sha': 'x'},
                'fs': {}, 'writes': {}}

    check('answers(): the two byte forms of one map compare equal',
          api.same(transcript(raw), transcript(want)))
    changed_value = msg('{"changedProjects":{' + P.replace('index.ts', 'other.ts') + ',' + B + '}}')
    check('answers(): a changed value still differs', not api.same(transcript(raw), transcript(changed_value)))
    reordered_files = msg('{"changedProjects":{' + P + ',' + B.replace('"/r/b/src/z.ts","/r/b/src/a.ts"', '"/r/b/src/a.ts","/r/b/src/z.ts"') + '}}')
    check('answers(): the order of changedFiles still counts', not api.same(transcript(raw), transcript(reordered_files)))
    reordered_projects = raw.replace('[{"id":"/r/q"},{"id":"/r/p"}]', '[{"id":"/r/p"},{"id":"/r/q"}]')
    check('answers(): the order of projects still counts', not api.same(transcript(raw), transcript(reordered_projects)))
    check('answers(): the raw bytes of another key order still count (operation before changes)',
          not api.same(transcript(raw), transcript('{"jsonrpc":"2.0","id":27,"result":{"operation":{},"changes":{"changedProjects":{'
                                                   + B + ',' + P + '}},"projects":[{"id":"/r/q"},{"id":"/r/p"}],"snapshot":3}}')))
    print('B7 SELFTEST FAIL: ' + ', '.join(FAILED) if FAILED else 'B7 SELFTEST OK')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
