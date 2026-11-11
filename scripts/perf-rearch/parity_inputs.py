"""Content identities, immutable snapshots and staged caches of the parity gate's inputs.

`tree_hash(root)` is the sha256 of a project tree as tsc and the language servers can see it: every
directory entry under the root's realpath except `.git` (directories, symlinks with their target
text, regular files with the sha256 of their bytes), sorted by path, plus the root's name (its
realpath, or `label`). A symlink is not followed; one whose target is outside the tree adds that
target's own tree hash, so an input outside the root is covered too. Any content edit, new file,
removed file, renamed file or new install changes it.

Hashing every byte of the projects (about 900,000 files, 15 GB) takes minutes, so the per-file
hashes are cached in ~/.cache/perf-rearch/parity/input-hash/<sha of the root>.pkl, keyed by the
file's (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns). Every write to a file changes its
ctime, and ctime cannot be set from user space (touch -r sets only mtime and atime), so a cached
hash is reused only for the unchanged file. A later run only stats the tree.

`json_hash(value)` is the sha256 of a JSON value (fuzz and corpus cases, options, parameters).

Snapshots (lane H4). A tier never runs a compiler or a language server on a shared project dir.
`Inputs.of(root)` gives a `Snap`: a read-only copy of the tree (rsync, `.git` left out, files 0444,
dirs 0555) in ~/.cache/perf-rearch/parity/snapshots/<sha of the root>/<content hash>/tree, made
once per content and reused while the source has that content. Its hash is taken from the copy
itself (with the source's realpath as the label), so a source edit during the copy names another
snapshot, never a wrong one. Every symlink in it must be relative and stay inside the tree.
The CLI tier mounts the snapshot as the overlay's lower dir at the project's own path, and the LSP
tier binds it there (parity_lsp.py), so paths in the output are the project's and another process
that writes into the project changes nothing a run sees.

While the tier runs, `Inputs.watch()` holds an inotify watch on every directory of every snapshot
(parity_watch.py), so a write, chmod, create, delete or rename in a snapshot is seen even when it
is undone at once. `verify()` at the tier's end reports, per root, the first such event, and a
snapshot whose stat identity (every entry's inode, size, mode, mtime and ctime: any write changes
the ctime) or content hash is no longer the one it had when the tier started. A root it names makes
its items ERROR, and the tier discards its staged cache entries. Without inotify, `check(root)`
after each process does the identity check instead.

Fixtures. `fixture_tree(files, base, ...)` writes a synthetic project (a JSON map of relative path to
text, or to {"symlink": relative target}) once into a content-addressed dir, base/<name-><hash>-m<dir
mode><file mode>/tree, with fixed modes and a fixed mtime, and checks it against the map on every call
(`check_fixture`). The api tier's disk fixtures (parity_api.py) and the CLI tier's synthetic projects
(parity_cli.py, parity-fixtures.json) are made this way.

`Stage` holds the cache entries a tier run makes until that run has verified clean: the tier writes
them to ~/.cache/perf-rearch/parity/stage/<run>/ and publishes them (a rename into the cache) only
at its end, when `verify()` found nothing; otherwise it discards them. Every entry carries the key
material it was made from (the inputs' content identities and the settings), and a read that finds
other material treats the entry as absent.
"""
import hashlib
import json
import os
import pickle
import shutil
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import parity_watch

STATE = Path.home() / '.cache/perf-rearch/parity'
CACHE = STATE / 'input-hash'
SNAPSHOTS = STATE / 'snapshots'
STAGE = STATE / 'stage'
SNAPSHOT_VERSION = 1  # bump when the form of a snapshot changes
VERSION = 1  # bump when the hashed form changes
_LOCKS = {}
_LOCKS_LOCK = threading.Lock()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()  # \uXXXX escapes: lossless for the surrogate-escaped bytes of fuzz cases


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


def tree_hash(root, jobs=8, _seen=None, label=None):
    """The content hash of the tree at root (see the top comment): 64 hex digits. `label` names the
    tree in the hash instead of its realpath (a snapshot is hashed as the project it copies)."""
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
    h = hashlib.sha256(f'tree v{VERSION}\0{label or real}\0'.encode())
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


def stat_identity(root):
    """The sha256 of every entry's (path, kind, inode, size, mode, mtime, ctime, link target) under
    root, `.git` included: any write, chmod, create, delete or rename in the tree changes it, even
    one that restored the bytes (a write sets the ctime, which user space cannot set back)."""
    h = hashlib.sha256()
    n = 0
    stack = ['']
    entries = []
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(root, rel) if rel else root) as it:
            for e in it:
                p = f'{rel}/{e.name}' if rel else e.name
                st = e.stat(follow_symlinks=False)
                link = os.readlink(e.path) if stat.S_ISLNK(st.st_mode) else ''
                entries.append((p, st.st_ino, st.st_size, st.st_mode, st.st_mtime_ns, st.st_ctime_ns, link))
                if stat.S_ISDIR(st.st_mode):
                    stack.append(p)
    st = os.lstat(root)
    entries.append(('.', st.st_ino, st.st_size, st.st_mode, st.st_mtime_ns, st.st_ctime_ns, ''))
    for e in sorted(entries):
        h.update(repr(e).encode('utf-8', 'surrogateescape'))
        n += 1
    return f'{h.hexdigest()}:{n}'


class SnapshotError(Exception):
    pass


class Snap:
    """A read-only snapshot of a project tree: `path` (the copy), `label` (the project's realpath, where
    a run sees it), `hash` (tree_hash of the copy under the label) and `identity` (stat_identity)."""

    def __init__(self, path, label, hash_, identity):
        self.path, self.label, self.hash, self.identity = Path(path), label, hash_, identity

    def as_json(self):
        return {'path': str(self.path), 'label': self.label, 'hash': self.hash, 'identity': self.identity}


def _make_writable(d):
    subprocess.run(['chmod', '-R', 'u+w', str(d)], check=False, stderr=subprocess.DEVNULL)


def remove_tree(d):
    """Removes a snapshot (read-only) or scratch tree."""
    if os.path.lexists(d):
        _make_writable(d)
        shutil.rmtree(d, ignore_errors=True)


def _check_links(tree):
    """Every symlink of a snapshot is relative and resolves inside it (else the snapshot would read the
    live tree through it)."""
    top = os.path.realpath(tree)
    for d, dirs, files in os.walk(tree):
        for name in dirs + files:
            p = os.path.join(d, name)
            if os.path.islink(p):
                t = os.readlink(p)
                real = os.path.realpath(p)
                if os.path.isabs(t) or not (real == top or real.startswith(top + '/')):
                    raise SnapshotError(f'symlink {os.path.relpath(p, tree)} -> {t} leaves the tree')


def fixture_tree(files, base, dir_mode, file_mode, mtime, name=''):
    """(tree, content hash) of a synthetic project: `files` ({relative path: text or {"symlink":
    relative target}}) written once into base/<name-><hash>-m<modes>/tree with dir_mode, file_mode and
    the mtime `mtime` on every entry, and checked against `files` every time (SnapshotError when the
    tree is not exactly that)."""
    h = json_hash(files)[:24]
    tree = Path(base) / f'{name + "-" if name else ""}{h}-m{dir_mode:o}{file_mode:o}' / 'tree'
    if not tree.exists():
        tmp = tree.parent / f'tmp-{os.getpid()}-{time.monotonic_ns()}'
        tmp.mkdir(parents=True)
        for rel, v in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(v, dict):
                os.symlink(v['symlink'], p)
            else:
                p.write_text(v)
        for top, dirs, fs in os.walk(tmp, topdown=False):
            for x in fs + dirs:
                q = os.path.join(top, x)
                if not os.path.islink(q):
                    os.chmod(q, dir_mode if os.path.isdir(q) else file_mode)
                os.utime(q, (mtime, mtime), follow_symlinks=False)
        os.chmod(tmp, dir_mode)
        os.utime(tmp, (mtime, mtime))
        try:
            tmp.rename(tree)
        except OSError:  # another tier made it at the same time
            remove_tree(tmp)
    check_fixture(tree, files, dir_mode, file_mode, mtime)
    return tree, h


def check_fixture(tree, files, dir_mode, file_mode, mtime):
    """Raises SnapshotError unless tree holds exactly files (bytes, link targets, names, the modes, the
    mtime)."""
    seen = {}
    for top, dirs, fs in os.walk(tree):
        for x in dirs + fs:
            p = os.path.join(top, x)
            st = os.lstat(p)
            link = stat.S_ISLNK(st.st_mode)
            want = None if link else dir_mode if stat.S_ISDIR(st.st_mode) else file_mode
            if (want is not None and stat.S_IMODE(st.st_mode) != want) or int(st.st_mtime) != mtime:
                raise SnapshotError(f'{p}: another mode or mtime than the fixture\'s')
            if link:
                seen[os.path.relpath(p, tree)] = {'symlink': os.readlink(p)}
            elif not stat.S_ISDIR(st.st_mode):
                seen[os.path.relpath(p, tree)] = Path(p).read_bytes() if stat.S_ISREG(st.st_mode) else None
    want = {k: v if isinstance(v, dict) else v.encode() for k, v in files.items()}
    if seen != want:
        raise SnapshotError(f'{tree} is not its fixture: {sorted(set(seen) ^ set(want))[:5] or "bytes differ"}')


def snapshot(root):
    """The snapshot of root's current content (made when there is none): a Snap. Raises SnapshotError."""
    real = os.path.realpath(root)
    if not os.path.isdir(real):
        raise SnapshotError(f'{root}: not a directory')
    src = tree_hash(real)
    base = SNAPSHOTS / hashlib.sha256(real.encode()).hexdigest()[:16]
    for attempt in range(2):
        found = _existing(base, real, src)
        if found:
            return found
        snap = _copy(base, real)
        if snap.hash == src or attempt:
            return snap
        src = tree_hash(real)  # the source changed during the copy: a snapshot of its new content
    raise SnapshotError(f'{real} keeps changing')  # not reached


def _existing(base, real, src):
    meta_file = base / src[:24] / 'meta.json'
    if not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text())
        snap = Snap(base / src[:24] / 'tree', meta['label'], meta['hash'], meta['identity'])
    except (OSError, ValueError, KeyError):
        return None
    if meta.get('version') != SNAPSHOT_VERSION or snap.label != real or snap.hash != src:
        return None
    if stat_identity(snap.path) != snap.identity:  # changed since it was made: never reused
        remove_tree(base / src[:24])
        return None
    return snap


def _copy(base, real):
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / f'.tmp-{os.getpid()}-{threading.get_ident()}'
    remove_tree(tmp)
    (tmp / 'tree').mkdir(parents=True)
    r = subprocess.run(['rsync', '-a', '--exclude=/.git', f'{real}/', f'{tmp}/tree/'], capture_output=True, text=True)
    if r.returncode:
        remove_tree(tmp)
        raise SnapshotError(f'rsync {real}: exit {r.returncode}: {r.stderr[-300:]}')
    try:
        _check_links(tmp / 'tree')
    except SnapshotError:
        remove_tree(tmp)
        raise
    subprocess.run(['chmod', '-R', 'a-w', str(tmp / 'tree')], check=True)
    h = tree_hash(tmp / 'tree', label=real)
    ident = stat_identity(tmp / 'tree')
    (tmp / 'meta.json').write_text(json.dumps({'version': SNAPSHOT_VERSION, 'label': real, 'hash': h, 'identity': ident,
                                                'made': time.strftime('%FT%TZ', time.gmtime())}, indent=1))
    final = base / h[:24]
    remove_tree(final)
    os.replace(tmp, final)
    for old in base.iterdir():  # one snapshot per project: the older ones go
        if old != final:
            remove_tree(old)
    return Snap(final / 'tree', real, h, ident)


class Inputs:
    """The input snapshots of one tier run: `of(root)` snapshots a root once (the key material of every
    item that reads it is the Snap's hash), `watch()` starts the inotify watch over all of them,
    `check(root)` re-checks one root's identity (after a process, when inotify is not available) and
    `verify()` returns {root: why} for the roots whose snapshot changed while the tier ran."""

    def __init__(self):
        self.snaps = {}
        self.errors = {}
        self.lock = threading.Lock()
        self.watcher = None
        self.watch_error = None
        self.changed = {}

    def of(self, root):
        root = str(root)
        with _lock(f'inputs:{id(self)}:{root}'):
            if root in self.errors:
                raise SnapshotError(self.errors[root])
            if root not in self.snaps:
                try:
                    self.snaps[root] = snapshot(root)
                except SnapshotError as e:
                    self.errors[root] = str(e)
                    raise
            return self.snaps[root]

    @property
    def hashes(self):
        return {r: s.hash for r, s in self.snaps.items()}

    def watch(self):
        """Watches every snapshot made so far (call it after the snapshots, before any process)."""
        if not parity_watch.available():
            self.watch_error = 'inotify is not available: identity checked after each process'
            return
        try:
            self.watcher = parity_watch.Watch({str(s.path): r for r, s in self.snaps.items()})
        except OSError as e:
            self.watch_error = f'inotify watch failed ({e}): identity checked after each process'

    def check(self, root):
        """After a process, without inotify: the root's snapshot must still have its identity."""
        if self.watcher is not None:
            return None
        s = self.snaps.get(str(root))
        if s is None:
            return None
        if stat_identity(s.path) != s.identity:
            with self.lock:
                self.changed.setdefault(str(root), f'the snapshot of {root} ({s.path}) changed while a process ran')
            return self.changed[str(root)]
        return None

    def verify(self):
        changed = dict(self.changed)
        if self.watcher is not None:
            by_path = {str(s.path): r for r, s in self.snaps.items()}
            for path, why in self.watcher.dirty().items():
                changed.setdefault(by_path[path], f'the snapshot of {by_path[path]} changed while the tier ran ({why})')
            self.watcher.close()
            self.watcher = None
        for root, s in self.snaps.items():
            if root in changed:
                continue
            if not s.path.is_dir():
                changed[root] = f'the snapshot of {root} is gone'
            elif stat_identity(s.path) != s.identity:
                changed[root] = f'the snapshot of {root} ({s.path}) changed while the tier ran (stat identity)'
            elif tree_hash(s.path, label=s.label) != s.hash:
                changed[root] = f'the snapshot of {root} changed while the tier ran (content)'
        return changed


class Stage:
    """Cache entries of one tier run, published only when the run verified clean (see the top comment).
    `load(final, material)` reads an entry (this run's staged one first), `save(final, material,
    value)` stages one; entries are JSON ({"material", "value"}) or pickles ((material, value))."""

    def __init__(self, name):
        self.dir = STAGE / f'{name}-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}'
        self.lock = threading.Lock()
        self.count = 0

    def staged(self, final):
        final = Path(final)
        try:
            rel = final.relative_to(STATE)
        except ValueError:  # a cache outside the state dir (the self-tests): staged beside it
            rel = Path('elsewhere') / str(final).lstrip('/')
        return self.dir / rel

    def _read(self, final):
        p = self.staged(final)
        if p.exists():
            return p
        return Path(final) if Path(final).exists() else None

    def load(self, final, material):
        p = self._read(final)
        if p is None:
            return None
        try:
            if p.suffix == '.json':
                d = json.loads(p.read_text())
                got, value = d.get('material'), d.get('value')
            else:
                got, value = pickle.loads(p.read_bytes())
        except Exception:  # a torn or old entry: absent
            return None
        return value if json_hash(got) == json_hash(material) else None

    def save(self, final, material, value):
        p = self.staged(final)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f'.tmp{os.getpid()}.{threading.get_ident()}')
        if p.suffix == '.json':
            tmp.write_text(json.dumps({'material': material, 'value': value}))
        else:
            tmp.write_bytes(pickle.dumps((material, value)))
        tmp.replace(p)
        with self.lock:
            self.count += 1

    def stage_dir(self, final):
        """A staged directory for a tool that writes a whole cache dir (lspdiff's Go and JS logs)."""
        return self.staged(final)

    def publish(self):
        """Moves every staged entry into its cache path; returns how many."""
        n = 0
        if not self.dir.exists():
            return 0
        for top, _dirs, files in os.walk(self.dir):
            for f in files:
                src = Path(top) / f
                rel = src.relative_to(self.dir)
                final = Path('/') / Path(*rel.parts[1:]) if rel.parts[0] == 'elsewhere' else STATE / rel
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, final)
                n += 1
        shutil.rmtree(self.dir, ignore_errors=True)
        return n

    def discard(self):
        n = sum(len(f) for _, _, f in os.walk(self.dir)) if self.dir.exists() else 0
        shutil.rmtree(self.dir, ignore_errors=True)
        return n
