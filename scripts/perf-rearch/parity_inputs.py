"""Content identities of the parity gate's inputs, for the Go, Go-N and JS cache keys.

`tree_hash(root)` is the sha256 of a project tree as tsc and the language servers can see it: every
directory entry under the root's realpath except `.git` (directories, symlinks with their target
text, regular files with the sha256 of their bytes), sorted by path, plus the realpath itself. A
symlink is not followed; one whose target is outside the tree adds that target's own tree hash, so
an input outside the root is covered too. Any content edit, new file, removed file, renamed file or
new install changes it.

Hashing every byte of the projects (about 900,000 files, 15 GB) takes minutes, so the per-file
hashes are cached in ~/.cache/perf-rearch/parity/input-hash/<sha of the root>.pkl, keyed by the
file's (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns). Every write to a file changes its
ctime, and ctime cannot be set from user space (touch -r sets only mtime and atime), so a cached
hash is reused only for the unchanged file. A later run only stats the tree.

`json_hash(value)` is the sha256 of a JSON value (fuzz and corpus cases, options, parameters).

`Snapshot` holds the hashes a tier read at its start; `verify()` hashes again at the end, so an
input that changed while the tier ran makes its items ERROR instead of a result keyed by content it
did not see.
"""
import hashlib
import json
import os
import pickle
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE = Path.home() / '.cache/perf-rearch/parity/input-hash'
VERSION = 1  # bump when the hashed form changes
_LOCKS = {}
_LOCKS_LOCK = threading.Lock()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _walk(root):
    """[(relpath, kind, stat or link target)] of every entry under root (not following symlinks),
    `.git` left out, sorted by path."""
    out = []
    stack = ['']
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(root, rel) if rel else root) as it:
            for e in it:
                p = f'{rel}/{e.name}' if rel else e.name
                if p == '.git':
                    continue
                st = e.stat(follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    out.append((p, 'l', os.readlink(e.path)))
                elif stat.S_ISDIR(st.st_mode):
                    out.append((p, 'd', None))
                    stack.append(p)
                elif stat.S_ISREG(st.st_mode):
                    out.append((p, 'f', (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)))
                else:
                    out.append((p, 'o', st.st_mode))
    out.sort()
    return out


def _lock(key):
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(key, threading.Lock())


def tree_hash(root, jobs=8, _seen=None):
    """The content hash of the tree at root (see the top comment): 64 hex digits."""
    real = os.path.realpath(root)
    seen = set() if _seen is None else _seen
    if real in seen:  # a symlink cycle: the tree is already part of the hash
        return f'cycle:{real}'
    seen.add(real)
    cfile = CACHE / f'{hashlib.sha256(real.encode()).hexdigest()[:24]}.pkl'
    with _lock(real):
        try:
            old = pickle.loads(cfile.read_bytes()) if cfile.exists() else {}
        except Exception:  # a torn or old cache file: hash again
            old = {}
        if old.get('version') != VERSION:
            old = {}
        files = old.get('files', {})
        entries = _walk(real)
        todo = [(p, ident) for p, kind, ident in entries if kind == 'f' and files.get(p, (None,))[0] != ident]
        if todo:
            with ThreadPoolExecutor(jobs) as pool:
                for (p, ident), sha in zip(todo, pool.map(lambda t: _file_sha(os.path.join(real, t[0])), todo)):
                    files[p] = (ident, sha)
        live = {p for p, kind, _ in entries if kind == 'f'}
        files = {p: v for p, v in files.items() if p in live}
        if todo or len(files) != len(old.get('files', {})):
            CACHE.mkdir(parents=True, exist_ok=True)
            tmp = cfile.with_suffix(f'.tmp{os.getpid()}.{threading.get_ident()}')
            tmp.write_bytes(pickle.dumps({'version': VERSION, 'files': files}))
            tmp.replace(cfile)
    h = hashlib.sha256(f'tree v{VERSION}\0{real}\0'.encode())
    for p, kind, ident in entries:
        if kind == 'f':
            h.update(f'f\0{p}\0{files[p][1]}\n'.encode('utf-8', 'surrogateescape'))
        elif kind == 'l':
            h.update(f'l\0{p}\0{ident}\n'.encode('utf-8', 'surrogateescape'))
            target = os.path.realpath(os.path.join(real, p))
            if not (target == real or target.startswith(real + '/')) and os.path.exists(target):
                sub = tree_hash(target, jobs, seen) if os.path.isdir(target) else _file_sha(target)
                h.update(f'L\0{p}\0{sub}\n'.encode('utf-8', 'surrogateescape'))
        else:
            h.update(f'{kind}\0{p}\0{ident if kind == "o" else ""}\n'.encode('utf-8', 'surrogateescape'))
    return h.hexdigest()


class Snapshot:
    """The input hashes of one tier run: `of(root)` hashes a root once (the key material of every
    item that reads it), `verify()` hashes every root again and returns {root: why} for the roots
    that changed while the tier ran."""

    def __init__(self):
        self.hashes = {}
        self.lock = threading.Lock()

    def of(self, root):
        root = str(root)
        with _lock(f'snapshot:{id(self)}:{root}'):
            if root not in self.hashes:
                self.hashes[root] = tree_hash(root)
            return self.hashes[root]

    def verify(self):
        changed = {}
        for root, before in list(self.hashes.items()):
            now = tree_hash(root) if os.path.exists(root) else 'missing'
            if now != before:
                changed[root] = f'the input tree {root} changed while the tier ran ({before[:12]} -> {now[:12]})'
        return changed
