#!/usr/bin/env python3
"""The frozen typings store of the LSP watch scenarios (lane H5).

ATA (automatic type acquisition) in the watch scenarios used to run the user's npm, which fetched
`types-registry@latest` and the `@types/*` packages from the network into the shared typings cache
~/.cache/typescript/7.1. Neither was part of a Go cache key, so a cached Go answer could describe
another registry than the one tsc-rs ran against (watch-ts-pattern-f12/0018: 1,150 completion items
in the cache, 1,147 for Go and tsc-rs once the registry changed). Now every server's ATA runs the
stand-in npm (vendor/lspdiff/ata-npm/fake-npm.mjs, which documents the store's layout), offline, from
this store, into a private typings cache that is empty at the start of every server run.

The store is built once from the registry and then never changes: ~/.cache/perf-rearch/ata-typings/
<sha[:24]>/, read-only. `sha` is parity_inputs.tree_hash of the store under the fixed label STORE_LABEL
(so it names the content, not the path). The repo holds only the manifest (scripts/perf-rearch/
ata-typings.json: sha, package versions, the sha256 of every file), not the tarballs. parity_lsp.py
snapshots the store, requires its hash to be the manifest's (else the sessions that use it are
ERROR), and puts the hash in every affected session's key. A new store (a rebuild, another package
list) is a new hash, so no Go answer or Go-variant record of another store is ever read.

What npm downloads is untrusted: every build works in a new empty dir under ata-typings/, with its
own npm cache and an empty user config, installs with --ignore-scripts, extracts the registry
tarball with tarfile's `data` filter, and refuses symlinks, links and special files in the result.
Run it with `python3 -I`.

usage: ata_typings.py build [--manifest FILE] @types/<name>...   (network: the user's npm)
       ata_typings.py check [--manifest FILE]                    (the store is the manifest's)
       ata_typings.py path  [--manifest FILE]                    (prints the store dir)

`build` takes the full list of @types packages the scenarios acquire (the manifest's `requested`
plus any a gate run reported missing: a scenario whose ATA asks the store for a package it lacks
fails as a harness error, naming it). A requested package that npm has not (E404) is recorded as
absent (closure null): the stand-in then fails that call as npm would.
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
import parity_inputs as inputs  # noqa: E402

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'ata-typings.json'
STORES = Path.home() / '.cache/perf-rearch/ata-typings'
STORE_LABEL = 'ata-typings-store'
VERSION = 1


def content_sha(store):
    return inputs.tree_hash(store, label=STORE_LABEL)


def read_manifest(path=MANIFEST):
    m = json.loads(Path(path).read_text())
    if m.get('version') != VERSION or not isinstance(m.get('sha'), str) or len(m['sha']) != 64:
        raise ValueError(f'{path}: not a version-{VERSION} typings manifest')
    return m


def store_dir(manifest):
    return STORES / manifest['sha'][:24]


def check(manifest):
    """None when the store is the manifest's, else why not."""
    d = store_dir(manifest)
    if not d.is_dir():
        return f'no typings store {d} (build it: ata_typings.py build)'
    got = content_sha(d)
    return None if got == manifest['sha'] else f'the typings store {d} has hash {got[:16]}, the manifest {manifest["sha"][:16]}'


def _npm(args, cwd, dl):
    env = {**os.environ, 'npm_config_cache': str(dl / 'npm-cache'), 'npm_config_userconfig': str(dl / 'npmrc'),
           'npm_config_update_notifier': 'false', 'npm_config_fund': 'false', 'npm_config_audit': 'false'}
    return subprocess.run(['npm', *args], cwd=cwd, env=env, capture_output=True, text=True)


def _install(names, dl, tag):
    d = dl / f'install-{tag}'
    d.mkdir()
    (d / 'package.json').write_text('{ "private": true }')
    r = _npm(['install', '--ignore-scripts', '--save-dev', *[f'{n}@latest' for n in names]], d, dl)
    return d, r


def _resolve(lock, at, dep):
    """The lock path npm's layout resolves `dep` to from the package at lock path `at`."""
    base = at
    while True:
        cand = f'{base}/node_modules/{dep}' if base else f'node_modules/{dep}'
        if cand in lock:
            return cand
        if not base:
            return None
        cut = base.rfind('/node_modules/')
        base = base[:cut] if cut >= 0 else ''


def closure(lock, name):
    """Every lock path an install of `name` lays out: it and its dependencies (peer ones too, as npm 7+
    installs them), resolved as node does."""
    out, todo = [], [f'node_modules/{name}']
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.append(p)
        e = lock[p]
        for dep in sorted({**e.get('dependencies', {}), **{k: v for k, v in e.get('peerDependencies', {}).items()
                                                            if not e.get('peerDependenciesMeta', {}).get(k, {}).get('optional')}}):
            r = _resolve(lock, p, dep)
            if r is None:
                raise SystemExit(f'{p}: dependency {dep} is not in the lock')
            todo.append(r)
    return sorted(out)


def _plain(tree):
    """Refuses anything in the downloaded tree that is not a plain dir or file (links, devices)."""
    for d, dirs, files in os.walk(tree):
        for n in dirs + files:
            st = os.lstat(os.path.join(d, n))
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)) or (stat.S_ISREG(st.st_mode) and st.st_nlink > 1):
                raise SystemExit(f'{os.path.join(d, n)}: not a plain file or dir')


def build(names, manifest_path):
    STORES.mkdir(parents=True, exist_ok=True)
    dl = STORES / f'dl-{time.strftime("%Y%m%d-%H%M%S")}'
    dl.mkdir()
    (dl / 'npmrc').write_text('')
    npm_version = _npm(['--version'], dl, dl).stdout.strip()
    (dl / 'pack').mkdir()
    r = _npm(['pack', '--ignore-scripts', '--pack-destination', str(dl / 'pack'), 'types-registry@latest'], dl / 'pack', dl)
    if r.returncode:
        raise SystemExit(f'npm pack types-registry: {r.stderr[-500:]}')
    tgz = next((dl / 'pack').glob('types-registry-*.tgz'))
    with tarfile.open(tgz) as t:
        t.extractall(dl / 'registry-x', filter='data')
    absent = []
    inst, r = _install(names, dl, 'all')
    if r.returncode:  # find the packages npm has not, then install the rest
        for n in names:
            _, one = _install([n], dl, n.replace('/', '__'))
            if one.returncode:
                if 'E404' not in one.stderr:
                    raise SystemExit(f'npm install {n}: {one.stderr[-500:]}')
                absent.append(n)
        inst, r = _install([n for n in names if n not in absent], dl, 'present')
        if r.returncode:
            raise SystemExit(f'npm install: {r.stderr[-500:]}')
    lock = json.loads((inst / 'package-lock.json').read_text())
    pkgs = lock['packages']
    tmp = STORES / f'.tmp-{os.getpid()}'
    inputs.remove_tree(tmp)
    tmp.mkdir()
    top = [d for d in (dl / 'registry-x').iterdir()]
    if len(top) != 1 or not (top[0] / 'package.json').is_file():
        raise SystemExit(f'{tgz}: not one package dir')
    shutil.copytree(top[0], tmp / 'registry')
    shutil.copytree(inst / 'node_modules', tmp / 'node_modules', ignore=shutil.ignore_patterns('.bin', '.package-lock.json'))
    _plain(tmp)
    (tmp / 'package-lock.json').write_text(json.dumps({'lockfileVersion': lock['lockfileVersion'], 'requires': True,
                                                       'packages': {k: v for k, v in sorted(pkgs.items()) if k}}, indent=2) + '\n')
    clos = {n: (None if n in absent else closure(pkgs, n)) for n in sorted(names)}
    (tmp / 'closure.json').write_text(json.dumps(clos, indent=2) + '\n')
    sha = content_sha(tmp)
    final = STORES / sha[:24]
    if final.exists():
        inputs.remove_tree(tmp)
    else:
        subprocess.run(['chmod', '-R', 'a-w', str(tmp)], check=True)
        os.replace(tmp, final)
    files = {}
    for d, _, fs in os.walk(final):
        for f in fs:
            p = Path(d) / f
            files[str(p.relative_to(final))] = inputs._file_sha(p)
    registry = json.loads((final / 'registry/package.json').read_text())
    manifest = {'version': VERSION, 'sha': sha, 'label': STORE_LABEL, 'built': time.strftime('%FT%TZ', time.gmtime()),
                'npm': npm_version, 'registry': registry['version'], 'requested': sorted(names), 'absent': absent,
                'packages': {k: v['version'] for k, v in sorted(pkgs.items()) if k},
                'files': dict(sorted(files.items()))}
    Path(manifest_path).write_text(json.dumps(manifest, indent=1) + '\n')
    print(f'store {final}: sha {sha[:16]}, types-registry {registry["version"]}, {len(manifest["packages"])} packages, '
          f'{len(files)} files{", absent " + ", ".join(absent) if absent else ""}; download dir {dl} (untrusted, kept)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('build', 'check', 'path'))
    ap.add_argument('names', nargs='*')
    ap.add_argument('--manifest', default=str(MANIFEST))
    a = ap.parse_intermixed_args()
    if a.cmd == 'build':
        bad = [n for n in a.names if not n.startswith('@types/') or '@' in n[1:]]
        if not a.names or bad:
            sys.exit(f'build wants @types/<name> packages without a version: {bad or "none given"}')
        build(sorted(set(a.names)), a.manifest)
        return
    m = read_manifest(a.manifest)
    if a.cmd == 'path':
        print(store_dir(m))
        return
    why = check(m)
    print(why or f'OK {store_dir(m)} sha {m["sha"][:16]}')
    sys.exit(1 if why else 0)


if __name__ == '__main__':
    main()
