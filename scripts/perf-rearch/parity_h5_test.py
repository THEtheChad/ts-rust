#!/usr/bin/env python3
"""Negative controls of lane H5: hermetic ATA and every external input of the LSP tier keyed. The JS side
(the stand-in npm and its store, the server environment and sandbox) is the `h5` part of
vendor/lspdiff/selftest/h4-selftest.mjs.

usage: python3 -I scripts/perf-rearch/parity_h5_test.py [--e2e BIN] [--keep]

Unit checks (a second): the typings store's hash is in the key, a key of another store never reads
the old Go cache, its priors, refills or Go-variant record; the store's content hash moves with any
byte; an entry made in an ancestor dir of a project (a package.json, node_modules) is seen and changes
the key, and the ancestors keyed are the ones a server sees (lane H8: none in the sandbox's hidden dirs).
The tripwire on the real typings cache is gone with lane H8: in the sandbox that dir does not exist
(parity_h8_test.py stat-bait).
--e2e BIN (a tsc-rs binary; minutes, takes the gate lock): through the real lsp tier,
  - the real ~/.cache/typescript does not exist for a tsc-rs (lane H8; before, a read of it made the
    session ERROR), and one whose ATA writes there (XDG_CACHE_HOME pointing at the real ~/.cache) cannot:
    its session is not MATCH, and the real cache is unchanged;
  - a store that lacks a package the scenario's ATA asks for makes the session ERROR (not GO-BROKEN);
  - a store with one byte changed is another key: Go runs again (no cached log is reused) and its
    variant record is another file;
  - a manifest whose hash is not the store's makes the session ERROR.
The last line is `H5 SELFTEST OK` or `H5 SELFTEST FAIL: ...`.
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ata_typings as A  # noqa: E402
import parity_inputs as I  # noqa: E402
import parity_lsp as L  # noqa: E402
import parity_watch as W  # noqa: E402

FAILS = []
HOME = Path.home()


def check(name, ok, got=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else f": got {str(got)[:1500]!r}"}', flush=True)
    if not ok:
        FAILS.append(name)


def unit_checks(root):
    info = json.loads(subprocess.run(['node', str(L.LSPDIFF), 'info'], capture_output=True, text=True, check=True).stdout)
    sess = next(s for s in L.FULL if s['name'] == 'watch-ts-pattern-f12')
    snap = I.Snap(root / 'snap', '/p/ts-pattern', 'a' * 64, 'x')
    node = {'path': '/n', 'version': 'v1', 'sha': '0' * 16}
    m = lambda typings, runs=4, n=node: L.session_material(sess, '/p/ts-pattern', snap, typings, 'src', info, n, [f'g{runs}'])  # noqa: E731
    a, b = m('1' * 64), m('2' * 64)
    check('key: the frozen store hash is in every frozen session\'s key', I.json_hash(a) != I.json_hash(b))
    check('key: the node that runs lspdiff, the stand-in npm and JS is in the key', I.json_hash(a) != I.json_hash(m('1' * 64, n={**node, 'version': 'v2'})))
    check('key: the servers\' private home path is in the key', a.get('home') == str(L.SANDBOX_HOME))
    check('key: no part of the key names the user\'s npm or the shared typings cache',
          'npm' not in a and str(HOME / '.cache/typescript') not in json.dumps(a), a)
    check('frozen sessions: every watch scenario with ATA on but ata-overlay installs from the frozen store',
          {s['name'] for s in L.FULL if L.uses_frozen_typings(s, info)} ==
          {'fsevents', 'fsevents-narrow', 'watch-ts-pattern-f12', 'unsaved-ts-pattern', 'watch-umami-f12',
           'watch-narrow-umami-f12', 'watch-hono-f12', 'second-project-query'})
    # Old Go evidence of another store: its cache dir, its priors and refills, its variant record.
    cache = root / 'lsp'
    old = cache / ('watch-ts-pattern-f12-' + 'o' * 24)
    old.mkdir(parents=True)
    (old / 'key.json').write_text(json.dumps(a, sort_keys=True))
    (old / 'go.jsonl').write_text('{}\n')
    other_runs = cache / ('watch-ts-pattern-f12-' + 'p' * 24)
    other_runs.mkdir()
    (other_runs / 'key.json').write_text(json.dumps(m('1' * 64, runs=8), sort_keys=True))
    stage = I.Stage('h5-selftest')
    try:
        got, _ = L.key_dir(old, b, stage)
        check('another store: the old store\'s Go cache dir is not read', got is None, got)
        priors = L.prior_dirs(cache, 'watch-ts-pattern-f12', 'watch-ts-pattern-f12-' + 'n' * 24, b)
        check('another store: no prior Go log of the old store (any run count) is used', priors == [], priors)
        check('control: the same store at another run count is a prior',
              [d.name for d in L.prior_dirs(cache, 'watch-ts-pattern-f12', 'watch-ts-pattern-f12-' + 'n' * 24, a)] == [old.name, other_runs.name])
        refills = root / 'refills'
        (refills / 'r1').mkdir(parents=True)
        (refills / 'r1' / 'key.json').write_text(json.dumps(a, sort_keys=True))
        check('another store: no refill of the old store is used', L.refill_dirs(refills, b) == [])
    finally:
        shutil.rmtree(stage.dir, ignore_errors=True)
    va, vb = L.variants_material(a, 'go'), L.variants_material(b, 'go')
    check('variant record: another store hash is another record (file name and material), never merged',
          I.json_hash(va) != I.json_hash(vb) and va['typings'] != vb['typings'])
    # The store hash moves with any byte, and not with the store's path.
    s1 = root / 's1'
    (s1 / 'node_modules/@types/x').mkdir(parents=True)
    (s1 / 'node_modules/@types/x/index.d.ts').write_text('export {};\n')
    s2 = root / 's2'
    shutil.copytree(s1, s2)
    h1, h2 = A.content_sha(s1), A.content_sha(s2)
    (s2 / 'node_modules/@types/x/index.d.ts').write_text('export {};\n ')
    check('store hash: the same content at another path has the same hash', h1 == h2)
    check('store hash: one more byte changes it', A.content_sha(s2) != h1)
    # Ancestor dirs: what a server looks up above its project is keyed and watched.
    proj = root / 'up/a/proj'
    proj.mkdir(parents=True)
    check('ancestors: the dirs above a project in a hidden dir are not keyed (the sandbox shows them empty)', L.ancestor_dirs(proj) == ['/'],
          L.ancestor_dirs(proj))
    dirs = [str(d) for d in proj.parents]  # the watch and the state, on dirs of the test's own
    st0 = L.ancestors_state(dirs)
    with W.Watch(dirs, keep=lambda p: os.path.basename(p) in L.ANCESTOR_NAMES, recursive=False) as w:
        (root / 'up/a/unrelated.txt').write_text('x')
        quiet = list(w.all_events())
    with W.Watch(dirs, keep=lambda p: os.path.basename(p) in L.ANCESTOR_NAMES, recursive=False) as w:
        (root / 'up/package.json').write_text('{}')
        seen = w.all_events()
    st1 = L.ancestors_state(dirs)
    check('ancestors: a package.json made above the project is seen while the tier runs', any('up/package.json' in e for e in seen), seen)
    check('ancestors: control: another file there is not', quiet == [], quiet)
    check('ancestors: and the key changes with it', I.json_hash(m('1' * 64) | {'ancestors': st0}) != I.json_hash(m('1' * 64) | {'ancestors': st1})
          and str(root / 'up/package.json') in st1 and not st0, (st0, st1))
    real = L.ancestors_state(L.ancestor_dirs(L.SANDBOX_HOME / '.cache/typescript/7.1', *[os.path.expanduser(p[0]) for p in L.PROJECTS.values()]))
    check('ancestors: none of the real projects\' ancestor dirs holds such an entry now', real == {}, real)


def e2e_checks(root, rs):
    """Through parity_lsp.py on real sessions (under the gate lock: the fixture scratch paths are fixed)."""
    lock = open(HOME / '.cache/perf-rearch/parity/.lock', 'w')
    print('e2e: waiting for the gate lock', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    manifest = A.read_manifest()
    store = A.store_dir(manifest)

    def tier(name, rs_bin, only, man=None):
        out = root / name
        cmd = [sys.executable, '-I', str(HERE / 'parity_lsp.py'), '--rs', str(rs_bin), '--out', str(out), '--tier', 'quick',
               '--only', only, '--no-js', *(['--ata-manifest', str(man)] if man else [])]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        (root / f'{name}.out').write_text(r.stdout + r.stderr)
        res = json.loads((out / 'results.json').read_text()) if (out / 'results.json').exists() else {}
        return r, res

    def wrapper(name, body):
        # A command of words (lspdiff binds each file it names into the sandbox): sh, the script, tsc-rs.
        w = script(root / f'{name}.sh', f'#!/bin/sh\n{body}\nrs="$1"; shift; exec "$rs" "$@"\n')
        return f'/bin/sh {w} {rs}'

    # 1. a tsc-rs that reads the real typings cache. ata-overlay: fixture, ATA on, Go cached after its first fill.
    tier('warm', rs, '^ata-overlay$')
    real = HOME / '.cache/typescript'
    before = sorted(str(p) for p in real.rglob('*')) if real.exists() else []
    r, res = tier('reads-cache', wrapper('reads-cache', f'[ -e {real} ] && exit 9'), '^ata-overlay$')
    items = res.get('items', {})
    check('e2e: the real ~/.cache/typescript does not exist for tsc-rs (every item good)',
          items and all(v['status'] in ('MATCH', 'GO-BROKEN') for v in items.values()), r.stdout[-500:])
    r, res = tier('xdg-leak', wrapper('xdg-leak', f'export XDG_CACHE_HOME={HOME}/.cache'), '^ata-overlay$')
    items = res.get('items', {})
    after = sorted(str(p) for p in real.rglob('*')) if real.exists() else []
    check('e2e: a tsc-rs whose ATA writes into the real ~/.cache cannot: its session is not MATCH, the real cache is unchanged',
          items and any(v['status'] not in ('MATCH', 'GO-BROKEN') for v in items.values()) and before == after, r.stdout[-500:])
    # 2-4. stores: one that lacks @types/node, one with a byte changed, a manifest that is not its store's.
    lacking = make_store(root, store, 'lack', lambda d: drop_closure(d, '@types/node'))
    # A byte no earlier run of this test added (its pid), so no Go fill of this store exists yet.
    changed = make_store(root, store, 'byte', lambda d: append(d / 'node_modules/@types/node/index.d.ts', f'// {os.getpid()}\n'))
    bad = root / 'bad-manifest.json'
    bad.write_text(json.dumps({**manifest, 'sha': 'f' * 64}))
    try:
        r, res = tier('lacking', rs, '^watch-ts-pattern-f12$', lacking)
        item = res.get('items', {}).get('lsp/watch-ts-pattern-f12', {})
        check('e2e: a store that lacks a package ATA asks for makes the session ERROR (a harness error, not GO-BROKEN)',
              item.get('status') == 'ERROR' and 'lacks @types/node' in item.get('detail', ''), item or r.stdout[-500:])
        _, base = tier('frozen', rs, '^watch-ts-pattern-f12$')
        r, res = tier('changed', rs, '^watch-ts-pattern-f12$', changed)
        log = (root / 'changed' / 'watch-ts-pattern-f12.log').read_text() if (root / 'changed' / 'watch-ts-pattern-f12.log').exists() else ''
        s0, s1 = base.get('sessions', {}).get('watch-ts-pattern-f12', {}), res.get('sessions', {}).get('watch-ts-pattern-f12', {})
        check('e2e: a store with one byte changed is another key and another variant record',
              s0.get('key') and s1.get('key') and s0['key'] != s1['key'] and s0['variants'] != s1['variants'], (s0.get('key'), s1.get('key')))
        check('e2e: under the changed store Go runs again: no cached Go log is reused, no prior, no variant',
              'answers from' not in log and 'go: ' in log and s1.get('priors') == [] and '0 requests recorded' in log, log[:800])
        r, res = tier('bad-manifest', rs, '^watch-ts-pattern-f12$', bad)
        item = res.get('items', {}).get('lsp/watch-ts-pattern-f12', {})
        check('e2e: a manifest whose hash is not its store\'s makes the session ERROR', item.get('status') == 'ERROR', item or r.stdout[-500:])
    finally:
        for d in (lacking, changed):
            I.remove_tree(A.store_dir(A.read_manifest(d)))
        lock.close()


def script(path, text):
    path.write_text(text)
    path.chmod(0o755)
    return path


def append(f, text):
    f.chmod(0o644)
    with open(f, 'a') as fh:
        fh.write(text)


def drop_closure(d, name):
    f = d / 'closure.json'
    f.chmod(0o644)
    c = json.loads(f.read_text())
    del c[name]
    f.write_text(json.dumps(c, indent=2) + '\n')


def make_store(root, store, tag, edit):
    """A copy of the store with `edit` applied, in its own store dir, and a manifest that names it."""
    tmp = A.STORES / f'.h5-selftest-{tag}-{os.getpid()}'
    I.remove_tree(tmp)
    shutil.copytree(store, tmp)
    subprocess.run(['chmod', '-R', 'u+w', str(tmp)], check=True)
    edit(tmp)
    sha = A.content_sha(tmp)
    final = A.STORES / sha[:24]
    I.remove_tree(final)
    os.replace(tmp, final)
    man = root / f'{tag}-manifest.json'
    man.write_text(json.dumps({**A.read_manifest(), 'sha': sha}))
    return man


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--e2e', metavar='BIN')
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()
    root = HOME / f'.cache/perf-rearch/parity/scratch/h5-selftest-{os.getpid()}'
    I.remove_tree(root)
    root.mkdir(parents=True)
    try:
        for name, part in (('unit', lambda: unit_checks(root / 'unit')), ('e2e', lambda: e2e_checks(root / 'e2e', args.e2e))):
            if name == 'e2e' and not args.e2e:
                continue
            (root / name).mkdir()
            try:
                part()
            except Exception as e:  # noqa: BLE001
                check(f'{name}: raised', False, repr(e)[:800])
    finally:
        if not args.keep:
            I.remove_tree(root)
    print(f'H5 SELFTEST {"FAIL: " + "; ".join(FAILS) if FAILS else "OK"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
