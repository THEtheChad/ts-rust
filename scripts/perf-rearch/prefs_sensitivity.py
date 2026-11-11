#!/usr/bin/env python3
"""Lane B6: does each probe of the lsp prefs session bite? For every preference the session probes
(vendor/lspdiff/prefs.mjs PROBES), the answers its values got in one server log, compared value by
value: a probe bites when every value got other answers than every other value of it, except the groups
of values the probe declares alike (Go defines them to behave the same), which must answer alike.

usage: python3 -I scripts/perf-rearch/prefs_sensitivity.py <server log .jsonl> [--json]

The log is one server run of the session: an lspdiff log (go.jsonl in a Go cache dir or an lspdiff out
dir). A step of a probe is named `probe <path>=<value> <what>`; its checkpoint items (watch, diagnostics
and server messages) are not answers to the probe's requests and are left out. Answers are compared as
lspdiff compares them: the project root masked, completion items sorted. Prints one line per
preference (values, distinct answers, OK or SAME with the values that answered alike) and ends
`SENSITIVITY OK` (exit 0) or `SENSITIVITY FAIL` (exit 1).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEP = re.compile(r'^probe (\S+?)=(\S+) (.*)$')
CHECKPOINT_METHODS = {'watch/events', 'watch/delta', 'watch/active', 'publishDiagnostics', 'server/messages'}


def probes():
    """[(path, [values as JSON text], [alike groups as JSON text])] from prefs.mjs, in its order."""
    code = ("import(process.argv[1]).then((m) => console.log(JSON.stringify(m.PROBES.map(([p, v, , , alike = []]) => "
            "[p, v.map((x) => JSON.stringify(x)), alike.map((g) => g.map((x) => JSON.stringify(x)))]))))")
    r = subprocess.run(['node', '-e', code, str(HERE / 'vendor/lspdiff/prefs.mjs')], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def normalize(answer, root):
    text = json.dumps(answer, sort_keys=True).replace(root, '<root>') if root else json.dumps(answer, sort_keys=True)
    value = json.loads(text)
    if isinstance(value, dict) and isinstance(value.get('items'), list):
        value['items'] = sorted(value['items'], key=lambda i: json.dumps(i, sort_keys=True))
    return json.dumps(value, sort_keys=True)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()]
    init = next((r for r in rows if r.get('method') == 'initialize'), {})
    root = ((init.get('params') or {}).get('rootUri') or '').removeprefix('file://')
    answers = {}  # (path, value) -> [(what, normalized answer)]
    for r in rows:
        m = STEP.match(r.get('step', ''))
        if not m or r.get('method') in CHECKPOINT_METHODS or m.group(3).endswith('configured'):
            continue
        answers.setdefault((m.group(1), m.group(2)), []).append((m.group(3), normalize({'result': r.get('result'), 'error': r.get('error')}, root)))
    report, ok = [], True
    for path, values, alike in probes():
        got = {v: tuple(answers.get((path, v), ())) for v in values}
        missing = [v for v, a in got.items() if not a]
        groups = {}
        for v, a in got.items():
            groups.setdefault(a, []).append(v)
        same = sorted(sorted(g) for g in groups.values() if len(g) > 1)
        status = 'MISSING' if missing else ('OK' if same == sorted(sorted(g) for g in alike) else 'SAME')
        ok = ok and status == 'OK'
        report.append({'path': path, 'values': values, 'distinct': len(groups), 'status': status,
                       'same': same, 'declaredAlike': alike, 'missing': missing, 'requests': sorted({w for a in got.values() for w, _ in a})})
    if '--json' in sys.argv:
        print(json.dumps(report, indent=1))
    else:
        for e in report:
            extra = f" alike: {'; '.join(' = '.join(g) for g in e['same'])}" if e['same'] else ''
            extra += ' (declared alike)' if e['same'] and e['status'] == 'OK' else ''
            extra += f" missing: {', '.join(e['missing'])}" if e['missing'] else ''
            print(f"{e['status']:7} {e['path']}: {len(e['values'])} values, {e['distinct']} distinct answers"
                  f" ({', '.join(e['requests'])}){extra}")
    print('SENSITIVITY OK' if ok else 'SENSITIVITY FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
