#!/usr/bin/env python3
"""Self-test of the parity gate's referees (parity_referees.py) and of their use by the CLI, fuzz
and corpus tiers and by parity_compare.py.

usage: python3 -I scripts/perf-rearch/parity_referees_test.py [--keep]

Fake Go, tsc-rs and JS compilers (written to a scratch dir) print what a scenario spec says, per
`--checkers N` for Go, and can alternate between outputs (nondeterminism), crash, sleep or write
files. Every classification path runs: through the units, through parity_cli.py as a subprocess
(on its real overlay, with its Go and JS caches in the scratch dir) and through parity_fuzz.py's
run_case (corpus mode, synthetic cases). The CLI tier runs twice to check that Go and JS results
are cached. Prints one line per check and `SELFTEST OK` or `SELFTEST FAIL` (exit 1). About 2 minutes
at a load of 30 (most of it overlay mounts and Python starts).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parity_fuzz  # noqa: E402
import parity_referees as R  # noqa: E402
from oracle import Result  # noqa: E402

FAILS = []


def check(name, ok, got=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else f": got {got!r}"}')
    if not ok:
        FAILS.append(name)


# ---- units ----------------------------------------------------------------------------------

E = '\x1b['
GO_PRETTY = (f'{E}96msrc/a.ts{E}0m:{E}93m3{E}0m:{E}93m4{E}0m - {E}91merror{E}0m{E}90m TS2322: {E}0mType \'number\' is not assignable.\n'
             '  Chain line.\n\n'
             f'{E}7m3{E}0m f({{b: 1}});\n{E}7m {E}0m {E}91m   ~{E}0m\n\n'
             f'  {E}96msrc/a.ts{E}0m:{E}93m2{E}0m:{E}93m16{E}0m - The expected type comes from here.\n'
             f'    {E}7m2{E}0m function f(a: {{b: string}}) {{}}\n    {E}7m {E}0m {E}96m      ~{E}0m\n\n'
             f'{E}96msrc/b.ts{E}0m:{E}93m1{E}0m:{E}93m1{E}0m - {E}91merror{E}0m{E}90m TS2344: {E}0mGo only.\n\n'
             f'{E}7m1{E}0m x\n{E}7m {E}0m {E}91m~{E}0m\n\n\n'
             'Found 2 errors in 2 files.\n\nErrors  Files\n     1  src/a.ts\x1b[90m:3\x1b[0m\n     1  src/b.ts\x1b[90m:1\x1b[0m\n\n')
JS_PRETTY = (f'{E}96msrc/a.ts{E}0m:{E}93m3{E}0m:{E}93m4{E}0m - {E}91merror{E}0m{E}90m TS2322: {E}0mType \'number\' is not assignable.\n'
             '  Chain line.\n\n'
             f'{E}7m3{E}0m f({{b: 1}});\n{E}7m {E}0m {E}91m   ~{E}0m\n\n'
             f'  {E}96msrc/a.ts{E}0m:{E}93m2{E}0m:{E}93m16{E}0m\n'
             f'    {E}7m2{E}0m function f(a: {{b: string}}) {{}}\n    {E}7m {E}0m {E}96m      ~{E}0m\n'
             '    The expected type comes from here.\n\n\n'
             'Found 1 error in src/a.ts\x1b[90m:3\x1b[0m\n\n')
RS_PRETTY = JS_PRETTY.replace('Found 1 error in', 'Found 1 error in')  # tsc-rs: Go's text minus TS2344, JS layout not needed
RS_PRETTY_GO_LAYOUT = GO_PRETTY.split(f'{E}96msrc/b.ts')[0] + '\nFound 1 error in src/a.ts\x1b[90m:3\x1b[0m\n\n'


def units():
    d, rest, summ = R.parse_output(GO_PRETTY.encode())
    check('parse pretty: 2 diagnostics, frames dropped', len(d) == 2 and d[0][5] == "Type 'number' is not assignable.\n  Chain line.", d)
    check('parse pretty: Go related layout', d[0][6] == (('src/a.ts', 2, 16, 'The expected type comes from here.'),), d[0][6])
    check('parse pretty: summary with file table', summ == (2, {'src/a.ts': 1, 'src/b.ts': 1}) and rest == [], (summ, rest))
    j, rest, summ = R.parse_output(JS_PRETTY.encode())
    check('parse pretty: JS 6 related layout equals Go\'s', j[0] == d[0], j[0])
    check('parse pretty: single-file summary', summ == (1, {}) and rest == [], (summ, rest))
    plain = (b"src/a.ts(1,7): error TS2322: Type 'string' is not assignable.\n  The types of 'p' differ.\n"
             b"tsconfig.json(1,49): error TS5023: Unknown option.\nerror TS5058: The specified path does not exist.\n"
             b"/lib/lib.d.ts\n  Default library\n")
    p, rest, summ = R.parse_output(plain)
    check('parse plain: chain, config and global diagnostics',
          [x[:5] for x in p] == [('src/a.ts', 1, 7, 'error', 2322), ('tsconfig.json', 1, 49, 'error', 5023), ('', 0, 0, 'error', 5058)]
          and p[0][5].endswith("  The types of 'p' differ."), p)
    check('parse plain: other lines are the rest', rest == ['/lib/lib.d.ts', '  Default library'] and summ is None, rest)

    run = lambda out, code=0, err=b'', tree=None: Result(code, out.encode() if isinstance(out, str) else out, err, tree or {})
    D1, D2, D3 = 'src/a.ts(1,1): error TS2344: Go only.\n', 'src/a.ts(2,1): error TS2322: Common.\n', 'src/a.ts(3,1): error TS1005: New.\n'
    OTHER = 'src/z.ts(9,9): error TS7031: JS only, elsewhere.\n'
    cases = [
        ('js: go-only absent from JS, rs-only in JS', True, (D1 + D2, 2), (D2 + D3, 2), (D2 + D3 + OTHER, 2)),
        ('js: go-only also in JS', False, (D1 + D2, 2), (D2, 2), (D1 + D2, 2)),
        ('js: go-only, JS has another text at its place', False, (D1, 2), ('', 0), (D1.replace('Go only', 'Other'), 2)),
        ('js: text at one place replaced, JS has the new text', True, (D1, 2), (D1.replace('Go only', 'Better'), 2),
         (D1.replace('Go only', 'Better'), 2)),
        ('js: rs-only missing from JS', False, (D2, 2), (D2 + D3, 2), (D2, 2)),
        ('js: rs-only in JS with another text', False, (D2, 2), (D2 + D3, 2), (D2 + D3.replace('New', 'Nem'), 2)),
        ('js: exit differs, JS agrees', True, (D1, 2), ('', 0), ('', 0)),
        ('js: exit differs, JS does not', False, (D1, 2), ('', 0), ('', 1)),
        ('js: order of common diagnostics differs', False, (D2 + D3 + D1, 2), (D3 + D2, 2), (D3 + D2, 2)),
        ('js: non-diagnostic line differs from Go', False, (D1 + 'listed\n', 2), ('other\n', 0), ('', 0)),
        ('js: only blank lines differ (no point)', False, ('a\n', 0), ('a\n\n', 0), ('a\n\n', 0)),
        ('js: pretty, Go-layout tsc-rs vs JS-layout JS', True, (GO_PRETTY, 2), (RS_PRETTY_GO_LAYOUT, 2), (JS_PRETTY, 2)),
        ('js: pretty summary that does not count tsc-rs\'s errors', False, (GO_PRETTY, 2),
         (RS_PRETTY_GO_LAYOUT.replace('Found 1 error in src/a.ts', 'Found 2 errors in the same file, starting at: src/a.ts'), 2), (JS_PRETTY, 2)),
    ]
    for name, want, g, r, j in cases:
        ok, detail, _ = R.js_delta([run(*g)], [run(*r)], [run(*j)])
        check(f'{name} -> {"JS-AGREES" if want else "no"}', ok == want, detail)
    ok, detail, info = R.js_delta([run(D1 + D2, 2)], [run(D2 + D3, 2)], [run(D2 + D3, 2)])
    check('js: detail names the points', ok and info['points'] == ['go-only TS2344 at src/a.ts(1,1)', 'rs-only TS1005 at src/a.ts(3,1)']
          and 'TS2344 x1' in detail, (detail, info))
    tree = lambda **kw: {k.replace('_', '/'): v for k, v in kw.items()}
    ok, _, info = R.js_delta([run('', 0, tree=tree(out_a='g', out_b='same'))], [run('', 0, tree=tree(out_a='r', out_b='same'))],
                             [run('', 0, tree=tree(out_a='r', out_b='js-other'))])
    check('js: a differing file equals JS\'s (the rest need not)', ok and info['points'] == ['file out/a'], info)
    ok, _, _ = R.js_delta([run('', 0, tree=tree(out_a='g'))], [run('', 0, tree={})], [run('', 0, tree=tree(out_a='g'))])
    check('js: a file tsc-rs does not write, JS writes -> no', not ok)
    ok, _, _ = R.js_delta([run('x', 0, err=b'e1')], [run('x', 0, err=b'e2')], [run('x', 0, err=b'e2')])
    check('js: stderr differs -> no (not refereed)', not ok)
    ok, detail, _ = R.js_delta([run(D1, 2)], [run('', 0)], [run('', 'timeout')])
    check('js: JS timed out -> no', not ok and 'failed' in detail, detail)
    ok, _, _ = R.js_delta([run(D1, 2), run('', 0)], [run('', 0), run('', 0)], [run('', 0), run('', 0)])
    check('js: two-run side (incr), point in run 1', ok)

    # Negative controls (lane H3, finding 4): every stdout line accounted for. Each pairs a change JS
    # approves (Go's TS2344 that JS lacks; tsc-rs drops it) with an unrelated corruption.
    go_summary_less = GO_PRETTY.split('\n\n\nFound')[0] + '\n\n'
    corrupt = [
        ('the summary deleted', RS_PRETTY_GO_LAYOUT.split('\n\n\nFound')[0] + '\n\n'),
        ('the summary count wrong', RS_PRETTY_GO_LAYOUT.replace('Found 1 error in src/a.ts', 'Found 3 errors in the same file, starting at: src/a.ts')),
        ('the summary line number wrong', RS_PRETTY_GO_LAYOUT.replace('src/a.ts\x1b[90m:3', 'src/a.ts\x1b[90m:9')),
        ('a code frame of a common diagnostic dropped', RS_PRETTY_GO_LAYOUT.replace(f'{E}7m3{E}0m f({{b: 1}});\n{E}7m {E}0m {E}91m   ~{E}0m\n', '')),
        ('a code frame of a common diagnostic changed', RS_PRETTY_GO_LAYOUT.replace('f({b: 1});', 'f({b: 2});')),
        ('a blank line added', RS_PRETTY_GO_LAYOUT.replace('Chain line.\n\n', 'Chain line.\n\n\n', 1)),
        ('an extra stdout line', RS_PRETTY_GO_LAYOUT + 'stray line\n'),
    ]
    for name, rs_out in corrupt:
        ok, detail, _ = R.js_delta([run(GO_PRETTY, 2)], [run(rs_out, 2)], [run(JS_PRETTY, 2)])
        check(f'negative control: finding 4: {name} beside an approved change -> no JS-AGREES', not ok, detail)
    ok, detail, _ = R.js_delta([run(GO_PRETTY, 2)], [run(RS_PRETTY_GO_LAYOUT, 2)], [run(JS_PRETTY, 2)])
    check('finding 4: the approved change alone is still JS-AGREES', ok, detail)
    # A table row's file wrong: Go 2 errors in 2 files; tsc-rs adds an error JS prints, its table must name it.
    two = GO_PRETTY.replace('Found 2 errors in 2 files.', 'Found 3 errors in 2 files.')
    extra = (f'{E}96msrc/b.ts{E}0m:{E}93m2{E}0m:{E}93m1{E}0m - {E}91merror{E}0m{E}90m TS1005: {E}0mNew.\n\n'
             f'{E}7m2{E}0m y\n{E}7m {E}0m {E}91m~{E}0m\n')
    rs3 = GO_PRETTY.replace('\n\n\nFound 2 errors in 2 files.', '\n\n' + extra + '\n\nFound 3 errors in 2 files.').replace('     1  src/b.ts', '     2  src/b.ts')
    js3 = JS_PRETTY.split('\n\n\nFound')[0] + '\n\n' + extra + '\n'
    ok, detail, _ = R.js_delta([run(GO_PRETTY, 2)], [run(rs3, 2)], [run(js3, 2)])
    check('finding 4: an extra diagnostic JS prints, with its block equal to JS\'s and Go\'s summary for it -> JS-AGREES', ok, detail)
    for name, bad in [('the table counting the wrong file', rs3.replace('     1  src/a.ts', '     2  src/a.ts').replace('     2  src/b.ts', '     1  src/b.ts')),
                      ('the extra diagnostic\'s code frame not JS\'s', rs3.replace(f'{E}7m2{E}0m y', f'{E}7m2{E}0m z'))]:
        ok, detail, _ = R.js_delta([run(GO_PRETTY, 2)], [run(bad, 2)], [run(js3, 2)])
        check(f'negative control: finding 4: {name} -> no JS-AGREES', not ok, detail)
    plain_go = D1 + D2
    for name, bad in [('a plain stdout line changed beside an approved change', D2 + 'garbage\n'),
                      ('a common diagnostic\'s chain changed beside an approved change', D2.replace('Common.', 'Common.\n  extra chain.'))]:
        ok, detail, _ = R.js_delta([run(plain_go, 2)], [run(bad, 2)], [run(D2, 2)])
        check(f'negative control: finding 4: {name} -> no JS-AGREES', not ok, detail)

    check('blocker: --checkers', R.blocker(['-p', 'x', '--Checkers', '2']) == 'sets --checkers')
    check('blocker: --singleThreaded', R.blocker(['--singleThreaded']) == 'sets --singleThreaded')
    check('blocker: -b, --watch, --showConfig', all(R.blocker([a]) for a in ('-b', '--build', '-w', '--watch', '--showConfig')))
    check('blocker: none', R.blocker(['-p', 'tsconfig.json', '--noEmit', '--pretty', 'false']) is None)
    per = {n: ('lo' if n < 3 else 'c4') for n in R.CHECKERS}
    check('partition: match', R.classify_partition(per, 'lo')[:2] == ('GO-PARTITION', 'matches Go --checkers 1,2 (2 Go outputs over N)'))
    check('partition: miss', R.classify_partition(per, 'zz')[0] == 'DIFF')
    check('partition: Go the same at every N', R.classify_partition({n: 'c4' for n in R.CHECKERS}, 'zz')[0] is None)
    per = {n: ('c4' if n != 1 else R.UNSTABLE) for n in R.CHECKERS}
    s, _, info = R.classify_partition(per, 'r1')
    check('partition: an unstable N is left out', s is None and info['unstable'] == [1], info)
    per = {n: ('lo' if n < 3 else R.FAILED if n == 16 else 'c4') for n in R.CHECKERS}
    s, _, info = R.classify_partition(per, 'lo')
    check('partition: a failed N is left out', s == 'GO-PARTITION' and info['failed'] == [16], info)

    crash = run('', 2, err=b'panic: boom\n\ngoroutine 1 [running]:\n')
    check('broken: Go crash reason names the panic', R.go_broken_reason([crash], None) == 'Go crash: panic: boom')
    check('broken: Go timeout reason', R.go_broken_reason([run('', 'timeout')], None) == 'Go timed out')
    check('broken: Go runs differ reason', R.go_broken_reason([run('')], 'stdout').startswith('Go runs differ'))
    check('broken: Go fine', R.go_broken_reason([run('x', 2)], None) is None)
    check('broken: tsc-rs stable -> GO-BROKEN', R.judge_broken('r', [run('x')])[0] == 'GO-BROKEN')
    rs_crash = run('', 70, err=b"thread 'main' panicked at src/x.rs:1:1:\n")
    check('broken: tsc-rs crashes -> DIFF', R.judge_broken('r', [rs_crash])[0] == 'DIFF')
    check('broken: tsc-rs times out -> DIFF', R.judge_broken('r', [run('', 'timeout')])[0] == 'DIFF')
    mimic = run('', 2, err=b'panic: strings: negative Repeat count\n\n\tcrates/x.rs\n')
    check('broken: tsc-rs reproduces a Go panic (no trace) -> DIFF',
          R.judge_broken('r', [mimic]) == ('DIFF', 'go-broken (r), tsc-rs crash: panic: strings: negative Repeat count'),
          R.judge_broken('r', [mimic]))


# ---- the fakes -------------------------------------------------------------------------------

DIAG_GO = 'src/a.ts(1,7): error TS2344: Go only.\nsrc/a.ts(2,1): error TS2322: Common.\n'
DIAG_RS = 'src/a.ts(2,1): error TS2322: Common.\nsrc/a.ts(3,1): error TS1005: New.\n'
PART = {'d': 'c4\n', '1': 'lo\n', '2': 'lo\n', '8': 'hi\n', '12': 'hi\n', '16': 'hi\n', '*': 'c4\n'}
GO_CRASH = {'out': '', 'err': 'panic: boom\n\ngoroutine 1 [running]:\n', 'code': 2}
# scenario: role -> {N or 'd' (no --checkers) or '*': entry}; entry: stdout text, or {out, err, code, alt, sleep, write}
SPEC = {
    'match': {'go': {'*': 'A\n'}, 'rs': {'*': 'A\n'}},
    'same': {'go': {'*': 'E1\n'}, 'rs': {'*': 'E2\n'}},
    'part': {'go': PART, 'rs': {'*': 'lo\n'}},
    'miss': {'go': PART, 'rs': {'*': 'zz\n'}},
    'nondet': {'go': PART, 'rs': {'*': {'alt': ['lo\n', 'hi\n']}}},
    'nondet1': {'go': PART, 'rs': {'*': {'alt': ['lo\n', 'hi\n']}}},
    'race': {'go': {'1': {'alt': ['r1\n', 'r2\n']}, '*': 'c4\n'}, 'rs': {'*': 'r1\n'}},
    'unstable': {'go': {'d': {'alt': ['x\n', 'y\n']}, '*': 'x\n'}, 'rs': {'*': 'x\n'}},
    'gocrash': {'go': {'*': GO_CRASH}, 'rs': {'*': 'fine\n'}},
    'bothcrash': {'go': {'*': GO_CRASH}, 'rs': {'*': {'out': '', 'err': "thread 'main' panicked at src/x.rs:1:1:\n", 'code': 70}}},
    'mimic': {'go': {'*': GO_CRASH}, 'rs': {'*': {'out': '', 'err': 'panic: boom\n', 'code': 2}}},
    'jsagree': {'go': {'*': {'out': DIAG_GO, 'code': 2}}, 'rs': {'*': {'out': DIAG_RS, 'code': 2}},
                'js': {'*': {'out': DIAG_RS + 'src/z.ts(9,9): error TS7031: Elsewhere.\n', 'code': 2}}},
    'jsexit': {'go': {'*': {'out': DIAG_GO, 'code': 2}}, 'rs': {'*': {'out': 'src/a.ts(2,1): error TS2322: Common.\n', 'code': 2}},
               'js': {'*': {'out': 'src/a.ts(2,1): error TS2322: Common.\n', 'code': 2}}},
    'jsgoonly': {'go': {'*': {'out': DIAG_GO, 'code': 2}}, 'rs': {'*': {'out': 'src/a.ts(2,1): error TS2322: Common.\n', 'code': 2}},
                 'js': {'*': {'out': DIAG_GO, 'code': 2}}},
    'jsrsonly': {'go': {'*': {'out': DIAG_GO, 'code': 2}}, 'rs': {'*': {'out': DIAG_RS, 'code': 2}},
                 'js': {'*': {'out': 'src/a.ts(2,1): error TS2322: Common.\n', 'code': 2}}},
    'jsbadexit': {'go': {'*': {'out': 'src/a.ts(1,7): error TS2344: Go only.\n', 'code': 2}}, 'rs': {'*': {'out': '', 'code': 0}},
                  'js': {'*': {'out': '', 'code': 1}}},
    'jsfile': {'go': {'*': {'out': 'x\n', 'write': {'out/a.js': 'go'}}}, 'rs': {'*': {'out': 'x\n', 'write': {'out/a.js': 'rs'}}},
               'js': {'*': {'out': 'x\n', 'write': {'out/a.js': 'rs'}}}},
    'jsbadfile': {'go': {'*': {'out': 'x\n', 'write': {'out/a.js': 'go'}}}, 'rs': {'*': {'out': 'x\n', 'write': {'out/a.js': 'rs'}}},
                  'js': {'*': {'out': 'x\n', 'write': {'out/a.js': 'js'}}}},
    # Go's partition and JS both side with tsc-rs: GO-PARTITION decides (step 3 before step 4)
    'partjs': {'go': {'d': {'out': DIAG_GO, 'code': 2}, '1': {'out': '', 'code': 0}, '*': {'out': DIAG_GO, 'code': 2}},
               'rs': {'*': {'out': '', 'code': 0}}, 'js': {'*': {'out': '', 'code': 0}}},
    'show': {'go': {'d': 'checkers 4\n', **{str(n): f'checkers {n}\n' for n in R.CHECKERS}}, 'rs': {'*': 'checkers 1\n'}},
    'partb': {'go': PART, 'rs': {'*': 'lo\n'}},
    'slow': {'go': {**PART, '16': {'out': 'hi\n', 'sleep': 6}}, 'rs': {'*': 'lo\n'}},
    'bothhang': {'go': {'*': {'out': 'x\n', 'sleep': 7}}, 'rs': {'*': {'out': 'x\n', 'sleep': 7}}},
    # finding 6 (the reviewer's case): Go's runs differ (GO-BROKEN), tsc-rs times out once, then answers
    'retry': {'go': {'d': {'alt': ['x\n', 'y\n']}, '*': 'x\n'}, 'rs': {'*': {'out': 'x\n', 'sleep_first': 5}}},
    # the same with Go fine: timeout, then the Go answer
    'retrym': {'go': {'*': 'x\n'}, 'rs': {'*': {'out': 'x\n', 'sleep_first': 5}}},
}
JS_DEFAULT = {'*': {'out': '', 'code': 0}}

FAKE_PY = r'''#!/usr/bin/env python3
import json, os, sys, time
ROLE, SPEC, STATE = {role!r}, {spec!r}, {state!r}
argv = sys.argv[1:]
n = argv[argv.index('--checkers') + 1] if '--checkers' in argv else 'd'
cfg = next((argv[i + 1] for i, a in enumerate(argv) if a in ('-p', '-b')), 'x.json')
scen = open('scenario').read().strip() if os.path.exists('scenario') else os.path.basename(cfg).rsplit('.', 1)[0]
role = json.load(open(SPEC))[scen].get(ROLE, {js_default!r})
e = role.get(n, role.get('*'))
e = {{'out': e}} if isinstance(e, str) else e
count = os.path.join(STATE, f'{{ROLE}}-{{scen}}-{{n}}.count')
k = int(open(count).read()) if os.path.exists(count) else 0
open(count, 'w').write(str(k + 1))
open(os.path.join(STATE, 'calls.log'), 'a').write(f'{{ROLE}} {{scen}} {{n}}\n')
if 'alt' in e:
    e = {{'out': e['alt'][k % len(e['alt'])]}}
time.sleep(e.get('sleep', 0) + (e.get('sleep_first', 0) if k == 0 else 0))
for f, text in e.get('write', {{}}).items():
    os.makedirs(os.path.dirname(f) or '.', exist_ok=True)
    open(f, 'w').write(text)
sys.stdout.write(e.get('out', ''))
sys.stderr.write(e.get('err', ''))
sys.exit(e.get('code', 0))
'''
# The CLI tier runs JS as `node <js>`: a node script that runs the Python fake in the JS role.
FAKE_JS = '''#!/usr/bin/env node
const r = require('child_process').spawnSync({py!r}, process.argv.slice(2), {{ stdio: 'inherit' }});
process.exit(r.status === null ? 1 : r.status);
'''


def write_fakes(root):
    state = root / 'state'
    state.mkdir()
    (root / 'spec.json').write_text(json.dumps(SPEC))
    bins = {}
    for role in ('go', 'rs', 'js-py'):
        f = root / f'fake-{role}'
        f.write_text(FAKE_PY.format(role=role.replace('-py', ''), spec=str(root / 'spec.json'), state=str(state),
                                    js_default=JS_DEFAULT))
        f.chmod(0o755)
        bins[role] = f
    write_js_package(root / 'fakejs', bins['js-py'])
    bins['js'] = root / 'fakejs/bin/tsc'
    return bins, state


def write_js_package(pkg, py, version='6.0.3', impl='// the compiler\n'):
    """A fake JS referee laid out as the typescript package: package.json, bin/tsc (runs the Python
    fake in the JS role), lib/tsc.js (the shim) and lib/_tsc.js (the "implementation")."""
    (pkg / 'bin').mkdir(parents=True, exist_ok=True)
    (pkg / 'lib').mkdir(exist_ok=True)
    (pkg / 'package.json').write_text(json.dumps({'name': 'typescript', 'version': version}))
    js = pkg / 'bin/tsc'
    js.write_text(FAKE_JS.format(py=str(py)))
    js.chmod(0o755)
    (pkg / 'lib/tsc.js').write_text("require('./_tsc.js')\n")
    (pkg / 'lib/_tsc.js').write_text(f'var version = "{version}";\n{impl}')


def calls(state):
    f = state / 'calls.log'
    return f.read_text().splitlines() if f.exists() else []


# ---- the CLI tier ---------------------------------------------------------------------------

CLI_EXPECT = {  # item -> (status, detail substring, check of the row or None)
    'match/noemit': ('MATCH', '', None),
    'same/noemit': ('DIFF', 'js: non-diagnostic stdout differs', lambda r: r['referees']['partition'] == {'outputs': 1}),
    'part/noemit': ('GO-PARTITION', 'matches Go --checkers 1,2', lambda r: r['referees']['partition']['groups'] == [[1, 2], [3, 4, 6], [8, 12, 16]]),
    'miss/noemit': ('DIFF', 'go-partition-miss', None),
    'nondet/noemit': ('DIFF', 'rs-nondet', None),
    'race/noemit': ('DIFF', '', lambda r: r['referees']['partition']['unstable'] == [1]),
    'unstable/noemit': ('GO-BROKEN', 'Go runs differ', None),
    'gocrash/noemit': ('GO-BROKEN', 'Go crash', None),
    'bothcrash/noemit': ('DIFF', 'go-broken', None),
    'mimic/noemit': ('DIFF', 'tsc-rs crash: panic: boom', None),
    'jsagree/noemit': ('JS-AGREES', '1 go-only, 1 rs-only', None),
    'jsexit/noemit': ('JS-AGREES', '1 go-only', None),
    'jsgoonly/noemit': ('DIFF', "JS also prints Go's TS2344", None),
    'jsrsonly/noemit': ('DIFF', "JS does not print tsc-rs's TS1005", None),
    'jsbadexit/noemit': ('DIFF', 'exit 2>0, JS exit 1', None),
    'jsfile/emit': ('JS-AGREES', '1 file', None),
    'jsbadfile/emit': ('DIFF', 'differs from Go and from JS', None),
    'partjs/noemit': ('GO-PARTITION', 'matches Go --checkers 1', lambda r: 'js' not in r['referees']),
    'show/showconfig': ('DIFF', '', lambda r: r['referees']['partition'] == {'skipped': '--showConfig prints the checker count'}),
    'partb/build': ('DIFF', '', lambda r: r['referees']['partition'] == {'skipped': 'build mode'}),
}


def cli_projects(root):
    proj = root / 'project'
    proj.mkdir()
    (proj / 'package.json').write_text('{}')
    configs = []
    for item in CLI_EXPECT:
        scen, variant = item.split('/')
        (proj / f'{scen}.json').write_text('{}')
        configs.append({'label': scen, 'config': f'{scen}.json', 'build': variant == 'build',
                        'skipVariants': [v for v in ('noemit', 'emit', 'decl', 'incr', 'listfiles', 'extdiag', 'pretty',
                                                     'showconfig', 'build') if v != variant]})
    (proj / 'nondet1.json').write_text('{}')
    configs.append({'label': 'nondet1', 'config': 'nondet1.json',
                    'skipVariants': ['emit', 'decl', 'incr', 'listfiles', 'extdiag', 'pretty', 'showconfig', 'build']})
    f = root / 'projects.json'
    f.write_text(json.dumps({'projects': [{'name': 'selftest', 'dirs': [str(proj)], 'configs': configs}]}))
    return f


def run_cli(root, bins, projects, out, *extra):
    cmd = [sys.executable, '-I', str(HERE / 'parity_cli.py'), '--rs', str(bins['rs']), '--go', str(bins['go']),
           '--js', str(bins['js']), '--projects', str(projects), '--cache', str(root / 'go-cache'),
           '--out', str(root / out), '--jobs', '4', *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, 'HOME': str(Path.home())})
    if p.returncode:
        print(p.stdout[-2000:], p.stderr[-2000:])
    return json.loads((root / out / 'results.json').read_text())['items']


def cli(root, bins, state):
    projects = cli_projects(root)
    js_cache = Path.home() / f'.cache/perf-rearch/parity/js-cache/{R.js_sha(bins["js"])}/cli'
    shutil.rmtree(js_cache, ignore_errors=True)
    caches = [js_cache]
    items = run_cli(root, bins, projects, 'cli1', '--rs-runs', '2', '--only', r'^cli/selftest/(?!nondet1/)')
    for item, (status, sub, extra) in CLI_EXPECT.items():
        row = items.get(f'cli/selftest/{item}', {})
        ok = row.get('status') == status and sub in row.get('detail', '') and (extra is None or extra(row))
        check(f'cli {item} -> {status}', ok, row)
    go_calls = [c for c in calls(state) if c.startswith('go part ')]
    check('cli: Go ran twice per N plus twice by default (18 runs) on part', len(go_calls) == 18, len(go_calls))
    before = len(calls(state))
    items = run_cli(root, bins, projects, 'cli2', '--rs-runs', '2', '--only', r'^cli/selftest/(part|jsagree)/')
    new = [c for c in calls(state)[before:] if not c.startswith('rs ')]
    check('cli: second run: Go and JS results come from the cache', new == [], new)
    check('cli: second run: same statuses', items['cli/selftest/part/noemit']['status'] == 'GO-PARTITION'
          and items['cli/selftest/jsagree/noemit']['status'] == 'JS-AGREES', items)
    items = run_cli(root, bins, projects, 'cli3', '--rs-runs', '1', '--only', r'^cli/selftest/nondet1/')
    row = items['cli/selftest/nondet1/noemit']
    check('cli --rs-runs 1: the extra tsc-rs run catches nondeterminism', row['status'] == 'DIFF' and 'rs-nondet' in row['detail'], row)
    items = run_cli(root, bins, projects, 'cli4', '--rs-runs', '2', '--referee-all', '--only', r'^cli/selftest/partjs/')
    row = items['cli/selftest/partjs/noemit']
    check('cli --referee-all: GO-PARTITION decides, the JS check is recorded too',
          row['status'] == 'GO-PARTITION' and row['referees']['js']['agrees'] is True, row)
    items = run_cli(root, bins, projects, 'cli5', '--rs-runs', '2', '--no-referees', '--only', r'^cli/selftest/(unstable|part)/')
    check('cli --no-referees: GO-UNSTABLE and DIFF as before', items['cli/selftest/unstable/noemit']['status'] == 'GO-UNSTABLE'
          and items['cli/selftest/part/noemit']['status'] == 'DIFF', items)
    # Negative controls (lane H3). Finding 7: a change of the JS implementation (_tsc.js, not the
    # tsc.js shim) must make JS run again, not reuse the cached verdict.
    write_js_package(root / 'fakejs', bins['js-py'], impl='// NEW COMPILER\n')
    caches.append(Path.home() / f'.cache/perf-rearch/parity/js-cache/{R.js_sha(bins["js"])}/cli')
    before = len(calls(state))
    run_cli(root, bins, projects, 'cli6', '--rs-runs', '2', '--only', r'^cli/selftest/jsagree/')
    js_calls = [c for c in calls(state)[before:] if c.startswith('js ')]
    check('negative control: finding 7: a changed JS implementation (_tsc.js) runs JS again', js_calls == ['js jsagree d'], js_calls)
    write_js_package(root / 'fakejs', bins['js-py'], version='6.0.2')
    items = run_cli(root, bins, projects, 'cli7', '--rs-runs', '2', '--only', r'^cli/selftest/jsagree/')
    row = items['cli/selftest/jsagree/noemit']
    check('negative control: finding 7: a JS referee that is not 6.0.3 decides nothing', row['status'] == 'DIFF', row)
    write_js_package(root / 'fakejs', bins['js-py'], impl='// NEW COMPILER\n')
    # Finding 8: a content edit in the project (no commit, no node_modules change) with a warm cache
    # must run Go again.
    before = len(calls(state))
    (projects.parent / 'project' / 'src.ts').write_text('let a: number = "wrong";\n')
    run_cli(root, bins, projects, 'cli8', '--rs-runs', '2', '--only', r'^cli/selftest/part/')
    go_calls = [c for c in calls(state)[before:] if c == 'go part d']
    check('negative control: finding 8: a project content edit with a warm cache runs Go again', len(go_calls) == 2, go_calls)
    for c in caches:
        shutil.rmtree(c, ignore_errors=True)


# ---- the fuzz and corpus tier ---------------------------------------------------------------

FUZZ_EXPECT = {  # scenario -> (status, detail substring, extra case args, rs runs)
    'match': ('MATCH', '', [], 2),
    'same': ('DIFF', 'js: non-diagnostic stdout differs', [], 2),
    'part': ('GO-PARTITION', 'matches Go --checkers 1,2', [], 2),
    'miss': ('DIFF', 'go-partition-miss', [], 2),
    'nondet': ('DIFF', '', [], 2),
    'nondet1': ('DIFF', 'rs-nondet', [], 1),
    'race': ('DIFF', '', [], 2),
    'unstable': ('GO-BROKEN', 'GO-UNSTABLE', [], 2),
    'gocrash': ('GO-BROKEN', 'GO-FAIL: Go crash', [], 2),
    'bothcrash': ('DIFF', 'go-broken', [], 2),
    'mimic': ('DIFF', 'tsc-rs crash: panic: boom', [], 2),
    'jsagree': ('JS-AGREES', '1 go-only, 1 rs-only', [], 2),
    'jsexit': ('JS-AGREES', '1 go-only', [], 2),
    'jsgoonly': ('DIFF', "JS also prints Go's TS2344", [], 2),
    'jsrsonly': ('DIFF', "JS does not print tsc-rs's TS1005", [], 2),
    'jsbadexit': ('DIFF', 'JS exit 1', [], 2),
    'jsfile': ('JS-AGREES', '1 file', [], 2),
    'jsbadfile': ('DIFF', 'differs from Go and from JS', [], 2),
    'partjs': ('GO-PARTITION', 'matches Go --checkers 1', [], 2),
    'show': ('DIFF', '', ['--showConfig'], 2),
    'slow': ('GO-PARTITION', 'matches Go --checkers 1,2', [], 1),
    'bothhang': ('DIFF', 'go-broken (GO-FAIL: Go timed out), tsc-rs timed out too', [], 2),
    'retry': ('DIFF', 'tsc-rs timeout in an attempt', [], 2),
    'retrym': ('DIFF', 'tsc-rs timeout in an attempt', [], 2),
}


def fuzz(root, bins, state):
    names = list(FUZZ_EXPECT)
    corpus = [{'name': f'selftest/{s}', 'files': {'a.ts': 'export {}\n', 'scenario': s}, 'options': {'noEmit': True},
               'args': FUZZ_EXPECT[s][2]} for s in names]
    parity_fuzz.WORK = root / 'fuzz-work'
    cache, js_cache, out = root / 'fuzz-cache', root / 'fuzz-js-cache', root / 'fuzz-out'
    cache.mkdir()
    for i, s in enumerate(names):
        status, sub, _, rs_runs = FUZZ_EXPECT[s]
        args = argparse.Namespace(corpus=True, go=str(bins['go']), rs=str(bins['rs']), js=str(bins['js']), timeout=3,
                                  rs_runs=rs_runs, referees=True, referee_all=False, js_cache=js_cache)
        _, row, _, _ = parity_fuzz.run_case(args, corpus, cache, out, i)
        extra = True
        if s == 'slow':
            extra = row.get('referees', {}).get('partition', {}).get('failed') == [16] \
                and not list(cache.glob(f'corpus-{i}-*.r2.p{parity_fuzz.PARTITION_VERSION}-c16.pkl')) \
                and list(cache.glob(f'corpus-{i}-*.r2.p{parity_fuzz.PARTITION_VERSION}-c8.pkl'))
        if s == 'show':
            extra = row['referees']['partition'] == {'skipped': '--showConfig prints the checker count'}
        if s == 'nondet':
            extra = row['sig'].startswith('rs-nondet')
        if s == 'bothhang':  # Go hung: one tsc-rs run of --timeout, no retry, no second run
            extra = [c for c in calls(state) if c.startswith('rs bothhang')] == ['rs bothhang d']
        if s == 'race':
            extra = row['referees']['partition']['unstable'] == [1]
        if s in ('retry', 'retrym'):  # every attempt kept, the case flagged for parity_compare
            extra = row.get('rsFailure') == 'timeout' and [a['outcome'] for a in row.get('attempts', [])][:2] == ['timeout', 'exit 0']
        detail = row.get('detail', '')
        check(f'corpus {s} -> {status}', row['status'] == status and sub in detail and extra, row)
    check('corpus: the summary names a DIFF without a signature (both timed out)',
          parity_fuzz.diff_kind({'status': 'DIFF', 'detail': 'go-broken (x), tsc-rs timed out too'}) == 'go-broken (x), tsc-rs timed out too')
    before = len(calls(state))
    args.rs_runs, args.timeout = 2, 3
    _, row, _, _ = parity_fuzz.run_case(args, corpus, cache, out, names.index('jsagree'))
    new = [c for c in calls(state)[before:] if not c.startswith('rs ')]
    check('corpus: second run: Go and JS results come from the cache', new == [] and row['status'] == 'JS-AGREES', (new, row))
    # Finding 8: a case whose content changed at the same index must not reuse Go's cached result.
    i = names.index('match')
    corpus[i] = {**corpus[i], 'files': {**corpus[i]['files'], 'a.ts': 'export const changed = 1;\n'}}
    before = len(calls(state))
    parity_fuzz.run_case(args, corpus, cache, out, i)
    go_calls = [c for c in calls(state)[before:] if c.startswith('go match')]
    check('negative control: finding 8: a changed fuzz/corpus case at the same index runs Go again', len(go_calls) == 2, go_calls)


# ---- parity_compare -------------------------------------------------------------------------

def compare(root):
    def write(d, mode, items):
        d.mkdir()
        (d / 'results.json').write_text(json.dumps({'meta': {'mode': mode}, 'tiers': {'cli': {'summary': {}, 'items': items}}}))
    base, cur, bad = root / 'cmp-base', root / 'cmp-cur', root / 'cmp-bad'
    write(base, 'full', {'a': {'status': 'MATCH'}, 'b': {'status': 'MATCH'}, 'c': {'status': 'MATCH'}, 'd': {'status': 'DIFF'},
                         'e': {'status': 'GO-PARTITION'}, 'f': {'status': 'MATCH'}})
    write(cur, 'full', {'a': {'status': 'GO-PARTITION', 'detail': 'matches Go --checkers 8'}, 'b': {'status': 'JS-AGREES', 'detail': 'js'},
                        'c': {'status': 'GO-BROKEN', 'detail': 'Go timed out'}, 'd': {'status': 'JS-AGREES', 'detail': 'js'},
                        'e': {'status': 'MATCH'}, 'f': {'status': 'MATCH'}, 'g': {'status': 'GO-BROKEN', 'detail': 'new'}})
    write(bad, 'full', {'a': {'status': 'MATCH'}, 'b': {'status': 'MATCH'}, 'c': {'status': 'MATCH'}, 'd': {'status': 'DIFF'},
                        'e': {'status': 'DIFF', 'detail': 'x'}, 'f': {'status': 'MATCH'}})
    p = subprocess.run([sys.executable, '-I', str(HERE / 'parity_compare.py'), 'compare', str(base), str(cur)], capture_output=True, text=True)
    lines = p.stdout.splitlines()
    check('compare: referee statuses are good (PARITY OK)', p.returncode == 0 and lines[-1] == 'PARITY OK', p.stdout)
    want = ['GO-BROKEN: Go itself is broken here (crash, timeout or runs that differ); tsc-rs is stable (2; 1 were MATCH in the base)',
            '  MATCH       -> GO-BROKEN    c: Go timed out',
            '  new         -> GO-BROKEN    g: new',
            'GO-PARTITION: now matches another Go partition (--checkers N) (1; 1 were MATCH in the base)',
            '  MATCH       -> GO-PARTITION a: matches Go --checkers 8',
            'JS-AGREES: JS tsc 6.0.3 sides with tsc-rs on every point where it differs from Go (2; 1 were MATCH in the base)',
            '  MATCH       -> JS-AGREES    b: js',
            '  DIFF        -> JS-AGREES    d: js']
    at = lines.index(want[0]) if want[0] in lines else -1
    check('compare: every item that left MATCH (and recovered and new ones) listed by status', lines[at:at + len(want)] == want, p.stdout)
    p = subprocess.run([sys.executable, '-I', str(HERE / 'parity_compare.py'), 'compare', str(base), str(bad)], capture_output=True, text=True)
    check('compare: GO-PARTITION in the base, DIFF now -> lost', p.returncode == 1 and 'LOST      e: DIFF x' in p.stdout, p.stdout)
    # Negative controls (lane H3): the reviewer's probes, with real item names too.
    cmp = lambda b, c: subprocess.run([sys.executable, '-I', str(HERE / 'parity_compare.py'), 'compare', str(b), str(c)],  # noqa: E731
                                      capture_output=True, text=True)
    nb, qe, qok, crash = root / 'cmp-newbad', root / 'cmp-quick-empty', root / 'cmp-quick', root / 'cmp-crash'
    write(nb, 'full', {'a': {'status': 'MATCH'}, 'b': {'status': 'MATCH'}, 'c': {'status': 'MATCH'}, 'd': {'status': 'DIFF'},
                       'e': {'status': 'GO-PARTITION'}, 'f': {'status': 'MATCH'}, 'lsp/crash': {'status': 'DIFF'}})
    p = cmp(base, nb)
    check('negative control: finding 9: a new item that is not good fails the gate', p.returncode == 1 and 'NEW DIFF   lsp/crash' in p.stdout, p.stdout)
    rb = root / 'cmp-real-base'
    write(rb, 'full', {'cli/query/core/noemit': {'status': 'MATCH'}, 'cli/query/core/decl': {'status': 'MATCH'},
                       'fuzz/1:3': {'status': 'MATCH'}, 'fuzz/1:20000': {'status': 'MATCH'}, 'corpus/x': {'status': 'MATCH'}})
    write(qe, 'quick', {})
    p = cmp(rb, qe)
    check('negative control: finding 10: an empty quick run against a full base fails (the quick items are missing)',
          p.returncode == 1 and 'NOT RUN   cli/query/core/noemit' in p.stdout and 'NOT RUN   fuzz/1:3' in p.stdout
          and 'NOT RUN   cli/query/core/decl' not in p.stdout and 'NOT RUN   corpus/x' not in p.stdout, p.stdout)
    write(qok, 'quick', {'cli/query/core/noemit': {'status': 'MATCH'}, 'fuzz/1:3': {'status': 'MATCH'}})
    p = cmp(rb, qok)
    check('compare: a complete quick run against a full base is PARITY OK (full-only items exempt)', p.returncode == 0, p.stdout)
    p = cmp(base, qe)  # the reviewer's probe: unknown item names are required too
    check('negative control: finding 10: a quick run lacking an item it cannot prove full-only fails', p.returncode == 1 and 'NOT RUN   a' in p.stdout, p.stdout)
    write(crash, 'full', {'a': {'status': 'MATCH'}, 'b': {'status': 'MATCH'}, 'c': {'status': 'MATCH'},
                          'd': {'status': 'DIFF', 'rsFailure': 'timeout', 'detail': 'tsc-rs timeout'},
                          'e': {'status': 'GO-PARTITION'}, 'f': {'status': 'MATCH'}})
    p = cmp(base, crash)
    check('negative control: finding 9: a tsc-rs timeout fails the gate even on a known diff', p.returncode == 1 and 'CRASH     d' in p.stdout, p.stdout)
    gf = root / 'grandfathered.json'
    gf.write_text(json.dumps({'items': {'d': {'status': 'DIFF', 'rsFailure': 'timeout', 'reason': 'Go times out too'}}}))
    sys.path.insert(0, str(HERE))
    import parity_compare
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = parity_compare.compare(str(base), str(crash), gf)
    check('compare: a grandfathered crash (listed with its status and reason) passes and is printed',
          rc == 0 and 'GRANDFATHERED d: DIFF (Go times out too)' in buf.getvalue(), buf.getvalue())


def lsp_keys(root):
    """Finding 8 on the lsp tier: the Go and JS cache keys of a session change with the content of its
    project and with the project dir it resolved to."""
    import parity_inputs
    import parity_lsp
    proj, other = root / 'lsp-proj', root / 'lsp-proj2'
    for d in (proj, other):
        (d / 'src').mkdir(parents=True)
        (d / 'src/a.ts').write_text('export const a = 1;\n')
    sess = parity_lsp.std('umami')
    key = lambda r: parity_lsp.session_key(sess, r, parity_inputs.Snapshot(), 'src')  # noqa: E731
    k1 = key(proj)
    (proj / 'src/a.ts').write_text('export const a = "two";\n')
    check('negative control: finding 8: an lsp session key changes with a content edit of its project', key(proj) != k1)
    check('negative control: finding 8: an lsp session key names the project dir it resolved to', key(other) != key(proj))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keep', action='store_true', help='keep the scratch dir')
    args = ap.parse_args()
    root = Path.home() / f'.cache/perf-rearch/parity/scratch/referees-selftest-{os.getpid()}'
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        # Each part on its own: an exception fails that part and the others still run (the negative
        # controls also run against older harness versions, which lack some of these modules).
        bins = state = None
        for name, part in [('units', units), ('fakes', lambda: None), ('cli', lambda: cli(root, bins, state)),
                           ('fuzz', lambda: fuzz(root, bins, state)), ('compare', lambda: compare(root)),
                           ('lsp keys', lambda: lsp_keys(root))]:
            try:
                if name == 'fakes':
                    bins, state = write_fakes(root)
                else:
                    part()
            except Exception as e:  # noqa: BLE001
                check(f'{name}: raised', False, repr(e)[:300])
    finally:
        if not args.keep:
            subprocess.run(['chmod', '-R', 'u+rwx', str(root)], check=False)
            shutil.rmtree(root, ignore_errors=True)
    print(f'SELFTEST {"FAIL: " + ", ".join(FAILS) if FAILS else "OK"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
