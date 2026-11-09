#!/usr/bin/env python3
"""Negative controls of lane H6: the site form of tsc-rs's stderr in the grandfather pins
(parity_grandfather.stderr_site; docs/perf-rearch/parity.md, "Grandfather pins without Rust line
numbers").

usage: python3 -I scripts/perf-rearch/parity_h6_test.py [--keep]

Three parts:
  form     stderr_site on the texts crates/ts_goport/src/gopanic.rs prints (a panic, with and without
           the signal line, a repanicked panic, `fatal error: newosproc`) and on texts it must leave
           alone (the site line not last, no blank line before it, no panic line, not a .rs path);
  pin      the real fuzz tier (parity_fuzz.run_case) on fake Go and tsc-rs binaries: a case is pinned
           from its row by regen's own entry builder, then tsc-rs changes and the case runs again;
           parity_grandfather.match decides. The same crash at another line of the same file still
           matches; another file, another panic line, an extra stderr line (before the block or
           after the site line) or another exit code does not;
  compare  the same rows through parity_compare with the entry as the accepted grandfather file:
           PARITY OK for the moved line, PARITY FAIL for another file; a version 2 file (the raw
           stderr hash) is INVALID, and regen refuses a row without stderrSite.
Each check prints `ok` or `FAIL`; the last line is `H6 SELFTEST OK` or `H6 SELFTEST FAIL: ...`.
About 10 seconds.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'vendor/difffuzz'))
import parity_compare as C  # noqa: E402
import parity_grandfather as G  # noqa: E402
import parity_h4_test as H4  # noqa: E402
import parity_referees_test as T  # noqa: E402
from parity_modes import ArtifactError  # noqa: E402

FAILS = []


def check(name, ok, got=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else f": got {str(got)[:1500]!r}"}')
    if not ok:
        FAILS.append(name)


SITE = '\tcrates/ts_goport/src/execute/tsc/diagnostics.rs:{}\n'
REPEAT = 'panic: strings: negative Repeat count\n\n' + SITE
NIL = ('panic: runtime error: invalid memory address or nil pointer dereference\n'
       '[signal SIGSEGV: segmentation violation code=0x1 addr=0x8 pc=0x0]\n\n'
       '\tcrates/ts_goport/src/frontend/tsoptions/tsconfig_p2.rs:{}\n')
REPANIC = ('panic: runtime error: invalid memory address or nil pointer dereference [recovered, repanicked]\n'
           '[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x0]\n\n\tcrates/ts_goport/src/ast/node.rs:{}\n')
NEWOSPROC = ('runtime: failed to create new OS thread (have 7 already; errno=11)\n'
             'runtime: may need to increase max user processes (ulimit -u)\nfatal error: newosproc\n\n'
             '\tcrates/ts_goport/src/gopanic.rs:{}\n')
GO_TRACE = 'panic: strings: negative Repeat count\n\ngoroutine 1 [running]:\nstrings.Repeat(...)\n\t/go/src/strings/strings.go:640 +0x1d\n'


def form_checks():
    b = lambda s: s.encode()
    for name, text in (('a panic', REPEAT), ('a panic with the signal line', NIL), ('a repanicked panic', REPANIC),
                       ('fatal error: newosproc', NEWOSPROC)):
        got = G.stderr_site(b(text.format(1383)))
        check(f'form: {name}: only the site line number is replaced', got == b(text.format('<line>')), got)
        check(f'form: {name}: another line number gives the same form',
              G.stderr_site_hash(b(text.format(1383))) == G.stderr_site_hash(b(text.format(7))))
    prefix = 'src/a.ts(1,1): error TS2322: x.\n'
    check('form: output before the panic block stays and the number is still replaced',
          G.stderr_site(b(prefix + REPEAT.format(9))) == b(prefix + REPEAT.format('<line>')))
    untouched = {
        'the site line not last': REPEAT.format(9) + 'more\n',
        'no blank line before the site line': 'panic: x\n' + SITE.format(9),
        'no panic or fatal error line': 'error: x\n\n' + SITE.format(9),
        'a blank line between the panic line and the block': 'panic: x\n\nother\n\n' + SITE.format(9),
        'not a .rs path': 'panic: x\n\n\tmain.go:9\n',
        'no newline after the site line': REPEAT.format(9)[:-1],
        'a Go goroutine trace': GO_TRACE,
        'a Rust panic hook line (tsgo: panic at <file>:<line>)': 'tsgo: panic at crates/ts_goport/src/a.rs:9: boom\n',
        'a site line inside other output': 'x\n\tcrates/ts_goport/src/a.rs:9\ny\n',
    }
    for name, text in untouched.items():
        check(f'form: unchanged: {name}', G.stderr_site(b(text)) == b(text), G.stderr_site(b(text)))


# ---- the fuzz tier on fake binaries ---------------------------------------------------------

CASE = {'name': 'h6-site', 'files': {'a.ts': 'export {};', 'scenario': 'h6-site'}, 'options': {'noEmit': True}, 'args': []}


def run(root, bins, rs_err, code=2):
    """The case's row with tsc-rs printing rs_err and exiting with code (Go: a crash with its trace)."""
    T.SPEC['h6-site'] = {'go': {'*': {'code': 2, 'err': GO_TRACE}}, 'rs': {'*': {'code': code, 'err': rs_err}}}
    (bins['go'].parent / 'spec.json').write_text(json.dumps(T.SPEC))
    return H4.run_case(root, H4.fuzz_args(bins, root, timeout=10), CASE)


def pin_checks(root):
    root.mkdir()
    bins, _ = T.write_fakes(root)
    pinned = run(root, bins, REPEAT.format(1383))
    check('pin: the evidence row is a crash DIFF with stderrSite on every attempt',
          pinned['status'] == 'DIFF' and pinned.get('rsFailure') == 'crash' and all('stderrSite' in a for a in pinned['attempts']),
          pinned)
    entry = G._entry('fuzz/1:1', pinned, None, 'selftest', 'selftest')
    G.validate({'about': 'x', 'version': G.VERSION, 'items': {'fuzz/1:1': entry}}, 'selftest')
    check('pin: Go\'s evidence holds no stderr hash', set(entry['go']) == set(G.GO) and 'stderr' not in entry['go'], entry['go'])
    rows = {'same': run(root, bins, REPEAT.format(1383)), 'moved': run(root, bins, REPEAT.format(1384))}
    check('pin: control: the same output matches its pin', G.match(entry, rows['same'], 'full') is None,
          G.match(entry, rows['same'], 'full'))
    check('pin: the same crash at another line of the same file matches its pin',
          G.match(entry, rows['moved'], 'full') is None, G.match(entry, rows['moved'], 'full'))
    check('pin: (the raw stderr hash of the moved line differs: version 2 failed here)',
          rows['moved']['attempts'][0]['stderr'] != pinned['attempts'][0]['stderr'])
    bad = {
        'another file': (REPEAT.replace('diagnostics.rs', 'program.rs').format(1383), 2),
        'another panic line': (REPEAT.replace('negative Repeat count', 'Repeat output length overflow').format(1383), 2),
        'an extra stderr line before the block': ('warning: x\n' + REPEAT.format(1383), 2),
        'an extra stderr line after the site line': (REPEAT.format(1383) + 'goroutine 1 [running]:\n', 2),
        'the signal line added': (REPEAT.replace('count\n', 'count\n[signal SIGSEGV: segmentation violation code=0x1 addr=0x8 pc=0x0]\n')
                                  .format(1383), 2),
        'another exit code': (REPEAT.format(1383), 1),
    }
    for name, (err, code) in bad.items():
        rows[name] = row = run(root, bins, err, code)
        why = G.match(entry, row, 'full')
        check(f'pin: {name} does not match the pin', why is not None and why.startswith('attempt '), (why, row))
    return entry, pinned, rows


def compare_checks(root, entry, pinned, rows):
    root.mkdir()
    k = 'fuzz/1:1'
    base = {k: {'status': 'GO-FAIL'}}
    grand = {'about': 'selftest', 'version': G.VERSION, 'items': {k: entry}}
    rc, out = H4.run_compare(root, 'moved', base, {k: rows['moved']}, raw_grand=grand)
    check('compare: the moved line is GRANDFATHERED, PARITY OK', rc == 0 and f'GRANDFATHERED {k}' in out, out)
    rc, out = H4.run_compare(root, 'other-file', base, {k: rows['another file']}, raw_grand=grand)
    check('compare: another file is KNOWN-BAD not as pinned, PARITY FAIL', rc == 1 and 'KNOWN-BAD' in out, out)
    old_attempt = {('stderr' if kk == 'stderrSite' else kk): v for kk, v in entry['attempt'].items()}
    old = {'about': 'selftest', 'version': 2, 'items': {k: {**entry, 'attempt': old_attempt}}}
    rc, out = H4.run_compare(root, 'v2', base, {k: rows['same']}, raw_grand=old)
    check('compare: a version 2 file (the raw stderr hash) is INVALID', rc == 1 and 'GRANDFATHER INVALID' in out, out)
    stripped = {**pinned, 'attempts': [{kk: v for kk, v in a.items() if kk != 'stderrSite'} for a in pinned['attempts']]}
    try:
        G._entry(k, stripped, None, 'selftest', 'selftest')
        check('regen: a row without stderrSite (an older harness) is refused', False, 'pinned')
    except ArtifactError as e:
        check('regen: a row without stderrSite (an older harness) is refused', 'stderrSite' in str(e), e)
    check('match: a row without stderrSite does not match', G.match(entry, stripped, 'full') is not None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()
    root = Path.home() / f'.cache/perf-rearch/parity/scratch/h6-selftest-{os.getpid()}'
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        form_checks()
        with contextlib.redirect_stderr(io.StringIO()):
            entry, pinned, rows = pin_checks(root / 'pin')
        compare_checks(root / 'compare', entry, pinned, rows)
    except Exception as e:  # noqa: BLE001 - an older harness lacks an entry point: that part fails
        check('raised', False, repr(e)[:800])
    finally:
        if not args.keep:
            subprocess.run(['chmod', '-R', 'u+rwx', str(root)], check=False)
            shutil.rmtree(root, ignore_errors=True)
    print(f'H6 SELFTEST {"FAIL: " + "; ".join(FAILS) if FAILS else "OK"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
