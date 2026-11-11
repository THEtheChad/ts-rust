#!/usr/bin/env python3
"""Self-test of lane T1: `parity_grandfather.py regen --drop-recovered` (docs/perf-rearch/parity.md,
"Grandfather entries that recover").

usage: python3 -I scripts/perf-rearch/parity_t1_test.py [--keep]

The evidence rows come from the real fuzz tier (parity_fuzz.run_case) on fake Go and tsc-rs binaries,
as in parity_h6_test.py: a case where both crash (a pinned DIFF) and the same case where tsc-rs answers
and Go still crashes (GO-BROKEN, recovered). A synthetic MATCH row stands for an output DIFF that
recovered. regen then runs on run dirs made of those rows:
  plain      without the option, regen refuses a recovered item (not a DIFF) and writes nothing; on
             a run with no recovered item it writes the same file as with the option;
  drop       recovered (GO-BROKEN, MATCH): dropped and printed with its status and old reason; still
             bad: kept and pinned again; good but with a tsc-rs failure: not dropped;
  not run    an item the --full run lacks is never dropped (regen refuses it, writes nothing);
  only       an entry outside --only stays byte for byte even when its item recovered;
  quick      a recovered item that the --quick run has as not good is an error.
Each check prints `ok` or `FAIL`; the last line is `T1 SELFTEST OK` or `T1 SELFTEST FAIL: ...`.
Under a second.
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
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'vendor/difffuzz'))
import parity_grandfather as G  # noqa: E402
import parity_h4_test as H4  # noqa: E402
import parity_h6_test as H6  # noqa: E402
import parity_referees_test as T  # noqa: E402
from parity_modes import ArtifactError  # noqa: E402

FAILS = []
# Items outside the quick manifest (fuzz cases 0 to 1499 of seed 1), so regen needs no --quick run,
# except QUICK_ITEM, which is inside it.
BAD, RECOVERED, MATCHED, CRASHY, ABSENT, QUICK_ITEM = (f'fuzz/1:{n}' for n in (2001, 2002, 2003, 2004, 2005, 7))


def check(name, ok, got=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else f": got {str(got)[:1500]!r}"}')
    if not ok:
        FAILS.append(name)


def rows(root):
    """(a crash DIFF row, a GO-BROKEN row of the same case) from the fuzz tier on fake binaries."""
    root.mkdir()
    bins, _ = T.write_fakes(root)
    crash = H6.run(root, bins, H6.REPEAT.format(1383))
    T.SPEC['h6-site'] = {'go': {'*': {'code': 2, 'err': H6.GO_TRACE}}, 'rs': {'*': {'code': 0, 'out': ''}}}
    (bins['go'].parent / 'spec.json').write_text(json.dumps(T.SPEC))
    broken = H4.run_case(root, H4.fuzz_args(bins, root, timeout=10), H6.CASE)
    return crash, broken


def write_run(d, mode, items):
    d.mkdir(parents=True)
    meta = {'mode': mode, 'rs': '/selftest/tsgo', 'rsSha': '0' * 64}
    (d / 'results.json').write_text(json.dumps({'meta': meta, 'tiers': {'fuzz': {'items': items}}}))
    return d


def write_grand(path, entries):
    path.write_text(json.dumps({'about': 'selftest', 'version': G.VERSION, 'items': entries}, indent=1) + '\n')
    return path


def regen(src, full, out, quick=None, only=None, drop=False):
    """(exit: 0 or the ArtifactError text, stdout) of regen."""
    args = SimpleNamespace(src=str(src), full=str(full), quick=quick and str(quick), old=None, add=None, out=str(out),
                           only=only, drop_recovered=drop)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            G.regen(args)
        return 0, buf.getvalue()
    except ArtifactError as e:
        return str(e), buf.getvalue()


def selftests(root):
    crash, broken = rows(root / 'rows')
    check('rows: the fake case crashes on both sides (a pinned DIFF)', crash['status'] == 'DIFF' and crash['rsFailure'] == 'crash',
          crash)
    check('rows: tsc-rs answering where Go crashes is GO-BROKEN (good)', broken['status'] == 'GO-BROKEN', broken)
    entry = G._entry(BAD, crash, None, 'still bad', 'selftest')
    reasons = {BAD: 'still bad', RECOVERED: 'went GO-BROKEN', MATCHED: 'went MATCH', CRASHY: 'good but tsc-rs crashed',
               ABSENT: 'not run'}
    grand = write_grand(root / 'from.json', {k: {**entry, 'reason': r} for k, r in reasons.items()})
    matched = {'status': 'MATCH', 'detail': ''}
    run = {BAD: crash, RECOVERED: broken, MATCHED: matched, CRASHY: {'status': 'MATCH', 'rsFailure': 'crash'}}
    full = write_run(root / 'full', 'full', run)
    full_bad = write_run(root / 'full-bad', 'full', {k: crash for k in reasons})

    # plain: unchanged
    out = root / 'plain.json'
    rc, text = regen(grand, full, out)
    check('plain: a recovered item is refused (not a DIFF), as before', rc != 0 and 'not a DIFF' in rc, (rc, text))
    check('plain: nothing is written when regen refuses', not out.exists())
    rc1, _ = regen(grand, full_bad, root / 'plain-bad.json')
    rc2, text2 = regen(grand, full_bad, root / 'drop-bad.json', drop=True)
    check('plain: with no recovered item, the option writes the same file and drops nothing',
          rc1 == rc2 == 0 and (root / 'plain-bad.json').read_bytes() == (root / 'drop-bad.json').read_bytes()
          and 'dropped' not in text2.replace('0 recovered dropped', ''), (rc1, rc2, text2))

    # drop
    no_crashy = {k: v for k, v in reasons.items() if k not in (CRASHY, ABSENT)}
    src = write_grand(root / 'from-drop.json', {k: {**entry, 'reason': r} for k, r in no_crashy.items()})
    out = root / 'drop.json'
    rc, text = regen(src, full, out, drop=True)
    got = json.loads(out.read_text())['items'] if out.exists() else {}
    check('drop: regen succeeds', rc == 0, (rc, text))
    check('drop: the GO-BROKEN and the MATCH item are dropped', RECOVERED not in got and MATCHED not in got, sorted(got))
    check('drop: the still-bad item is kept and pinned again', got.get(BAD, {}).get('attempt') == entry['attempt']
          and got[BAD]['reason'] == 'still bad', got.get(BAD))
    check('drop: each dropped entry is printed with its status, run and old reason',
          f"dropped {RECOVERED}: GO-BROKEN in full" in text and "'went GO-BROKEN'" in text
          and f'dropped {MATCHED}: MATCH in full' in text and "'went MATCH'" in text, text)
    check('drop: the summary counts the dropped entries', '2 recovered dropped' in text, text)
    G.validate(json.loads(out.read_text()), 'drop')
    rc, text = regen(grand, full, root / 'crashy.json', only=f'^{CRASHY}$', drop=True)
    check('drop: a good status with a tsc-rs failure is not dropped (regen refuses it)',
          rc != 0 and CRASHY in rc and 'dropped' not in text, (rc, text))

    # not run
    with_absent = write_grand(root / 'from-absent.json', {k: {**entry, 'reason': reasons[k]} for k in (BAD, RECOVERED, ABSENT)})
    out = root / 'absent.json'
    rc, text = regen(with_absent, full, out, drop=True)
    check('not run: an item the full run lacks is not dropped (regen refuses it)',
          rc != 0 and ABSENT in rc and f'dropped {ABSENT}' not in text, (rc, text))
    check('not run: nothing is written', not out.exists())

    # only
    out = root / 'only.json'
    rc, text = regen(src, full, out, only=f'^{BAD}$', drop=True)
    got = json.loads(out.read_text())['items'] if out.exists() else {}
    before = json.loads(src.read_text())['items']
    check('only: a recovered entry outside --only stays byte for byte',
          rc == 0 and got.get(RECOVERED) == before[RECOVERED] and got.get(MATCHED) == before[MATCHED]
          and 'dropped' not in text.replace('0 recovered dropped', ''), (rc, text))
    out = root / 'only2.json'
    rc, text = regen(src, full, out, only=f'^({BAD}|{RECOVERED})$', drop=True)
    got = json.loads(out.read_text())['items'] if out.exists() else {}
    check('only: a recovered entry inside --only is dropped', rc == 0 and RECOVERED not in got and MATCHED in got,
          (rc, text, sorted(got)))

    # quick
    qsrc = write_grand(root / 'from-quick.json', {QUICK_ITEM: {**entry, 'reason': 'in the quick manifest'}})
    qfull = write_run(root / 'qfull', 'full', {QUICK_ITEM: broken})
    rc, text = regen(qsrc, qfull, root / 'quick-bad.json', quick=write_run(root / 'qquick-bad', 'quick', {QUICK_ITEM: crash}),
                     drop=True)
    check('quick: recovered in full but not good in quick is an error', rc != 0 and 'quick' in rc, (rc, text))
    rc, text = regen(qsrc, qfull, root / 'quick-ok.json', quick=write_run(root / 'qquick', 'quick', {QUICK_ITEM: broken}),
                     drop=True)
    got = json.loads((root / 'quick-ok.json').read_text())['items'] if rc == 0 else None
    check('quick: recovered in both runs is dropped', got == {} and f'dropped {QUICK_ITEM}' in text, (rc, text))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()
    root = Path.home() / f'.cache/perf-rearch/parity/scratch/t1-selftest-{os.getpid()}'
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            selftests(root)
    except Exception as e:  # noqa: BLE001 - a failure in setup fails the test, with its text
        check('raised', False, repr(e)[:800])
    finally:
        if not args.keep:
            subprocess.run(['chmod', '-R', 'u+rwx', str(root)], check=False)
            shutil.rmtree(root, ignore_errors=True)
    print(f'T1 SELFTEST {"FAIL: " + "; ".join(FAILS) if FAILS else "OK"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
