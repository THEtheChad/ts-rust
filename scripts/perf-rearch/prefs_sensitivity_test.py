#!/usr/bin/env python3
"""Self-test of prefs_sensitivity.py (lane B6): its judge on made-up server logs. Prints one line per
check and ends `SENSITIVITY SELFTEST OK` (exit 0) or `SENSITIVITY SELFTEST FAIL` (exit 1)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
import prefs_sensitivity as ps  # noqa: E402

ROOT = '/tmp/b6-root'
failed = []


def check(name, ok, info=''):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else f': {info}'}")
    if not ok:
        failed.append(name)


def log(steps):
    """A server log: the initialize answer, then one probe answer per (path, value, what, result)."""
    rows = [{'step': 'initialize', 'method': 'initialize', 'params': {'rootUri': f'file://{ROOT}'}, 'result': {}}]
    for path, value, what, result in steps:
        rows.append({'step': f'probe {path}={value} {what}', 'method': 'textDocument/completion', 'result': result})
        rows.append({'step': f'probe {path}={value} configured server messages', 'method': 'server/messages', 'result': [value]})
    return rows


def completion(*labels):
    return {'isIncomplete': False, 'items': [{'label': lab, 'data': {'fileName': f'{ROOT}/src/a.ts'}} for lab in labels]}


P = [('p', ['"a"', '"b"'], [])]

report, ok = ps.judge(log([('p', '"a"', 'completion', completion('x', 'y')), ('p', '"b"', 'completion', completion('y', 'x'))]), P)
check('completion items in another order are the same answer (result.items is sorted)', not ok and report[0]['status'] == 'SAME', report)

report, ok = ps.judge(log([('p', '"a"', 'completion', completion('x', 'y')), ('p', '"b"', 'completion', completion('x', 'z'))]), P)
check('other completion items are another answer', ok and report[0]['status'] == 'OK', report)

report, ok = ps.judge(log([('p', '"a"', 'completion', completion('x')), ('p', '"b"', 'completion', completion('x'))]), [('p', ['"a"', '"b"'], [['"a"', '"b"']])])
check('values declared alike must answer alike', ok and report[0]['status'] == 'OK', report)

report, ok = ps.judge(log([('p', '"a"', 'completion', completion('x')), ('p', '"b"', 'completion', completion('y'))]), [('p', ['"a"', '"b"'], [['"a"', '"b"']])])
check('values declared alike that answer differently fail', not ok and report[0]['status'] == 'SAME', report)

report, ok = ps.judge(log([('p', '"a"', 'completion', completion('x'))]), P)
check('a value with no answer is MISSING', not ok and report[0]['status'] == 'MISSING', report)

report, ok = ps.judge(log([('p', '"a"', 'completion', completion('x')), ('p', '"b"', 'completion', completion('x'))]), P)
check('checkpoint items do not count as answers (only the completions, which are alike)', not ok and report[0]['status'] == 'SAME', report)

report, ok = ps.judge(log([('p', '"a"', 'completion', {'items': [{'label': 'x', 'path': f'{ROOT}/one'}]}),
                           ('p', '"b"', 'completion', {'items': [{'label': 'x', 'path': '/elsewhere/one'}]})]), P)
check('the root is masked, other paths are not', ok, report)

print('SENSITIVITY SELFTEST OK' if not failed else 'SENSITIVITY SELFTEST FAIL')
sys.exit(1 if failed else 0)
