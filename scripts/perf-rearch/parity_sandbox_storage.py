"""A sandboxed server's writable storage: made, recorded and listed by the sandbox's init
(parity_sandbox_init.py), the same code for the api tier and the lsp tier (lane H8 round 2).

The storage is one tmpfs per session that the init mounts in its own user namespace. It holds the private
home (`home/`, with its skeleton) and, per overlay k, the overlay's upper and work dirs (`ovl<k>/upper`,
`ovl<k>/work`). The server reaches it only through the home bind and the overlays (the init detaches the
tmpfs itself). Two views of it, both the init's, never the server's:

  listing(root)   the state at one moment (walk): every entry of the home and of each upper dir, the root
                  of each as ".", none skipped, by type, mode and content hash; a dir that cannot be listed
                  or an entry that cannot be read is said so, not skipped. The init has every capability in
                  its namespace, which owns the tmpfs, so a mode the server sets hides nothing from it.
  Recorder        every write while the server runs: one fanotify mark on the whole tmpfs (a filesystem
                  mark, which a user namespace may set on a filesystem it mounted), set before the server
                  starts. Every create, delete, write, attribute change and rename anywhere in the storage
                  is an event, in a dir made a moment before too (no watch per dir, so no race of a watch
                  against a new dir), and the record is order-free: for every path ever touched, the set of
                  what happened to it. A file written and removed inside one request is in it.

Paths are relative to the storage: `home/<rel>` and `disk<k>/<rel>` (the upper dir of overlay k, so a
write into the server's inputs); the overlay's own work dir is left out (overlayfs's copy-up names there
come from a counter). A dir's events name it by its file handle; the recorder maps handles to paths as
dirs are made (name_to_handle_at; open_by_handle_at is refused in a user namespace), and a dir already gone
when its event is read is `?/<name>`: still a write, never dropped.
"""
import ctypes
import hashlib
import os
import select
import stat
import struct
import threading

HOME_DIRS = ['.cache', '.config', '.local/share', '.local/state', 'tmp']
HOME_SKELETON = sorted({'/'.join(d.split('/')[:k + 1]) for d in HOME_DIRS for k in range(len(d.split('/')))})
SKELETON_MODE = 0o755
TEXT_MAX = 1 << 18


def make_home(home, mtime):
    """The private home's skeleton: HOME_DIRS, every dir of it a real dir of mode SKELETON_MODE."""
    os.makedirs(home, exist_ok=True)
    for d in HOME_SKELETON:
        os.makedirs(os.path.join(home, d), exist_ok=True)
    for d in ['', *HOME_SKELETON]:
        os.chmod(os.path.join(home, d), SKELETON_MODE)
        os.utime(os.path.join(home, d), (mtime, mtime))


def _describe_root(st):
    if stat.S_ISDIR(st.st_mode):
        return f'dir {stat.S_IMODE(st.st_mode):o}'
    return f'not a dir: {stat.filemode(st.st_mode)}'


def walk(top, texts=None):
    """{relative path: what it is} of a dir: every entry, none skipped, as "<sha256 16> <mode>" (a file),
    "dir <mode>", "link <target>", "deleted" (an overlay whiteout: a deleted input) or "special <mode>".
    The root itself is entry "." (by lstat: a root the server made unreadable, or replaced, shows), every
    dir that cannot be listed is "unlistable <why>", an entry that cannot be stat'ed "unstatable <why>", a
    file that cannot be read "unreadable <why>". A root that does not exist gives no entries. texts: a dict
    that gets the text of each file up to TEXT_MAX bytes (for a diff report only)."""
    out = {}
    try:
        root_st = os.lstat(top)
    except FileNotFoundError:
        return out
    out['.'] = _describe_root(root_st)
    if not stat.S_ISDIR(root_st.st_mode):
        return out

    def unlistable(e):
        key = os.path.relpath(e.filename, top) if e.filename else '?'
        out[key] = f'{out[key]}; unlistable {e.strerror}' if key in out else f'unlistable {e.strerror}'
    for d, dirs, files in os.walk(top, onerror=unlistable):
        for x in dirs + files:
            p = os.path.join(d, x)
            rel = os.path.relpath(p, top)
            try:
                st = os.lstat(p)
            except OSError as e:
                out[rel] = f'unstatable {e.strerror}'
                continue
            mode = f'{stat.S_IMODE(st.st_mode):o}'
            if stat.S_ISCHR(st.st_mode):
                out[rel] = 'deleted'
            elif stat.S_ISLNK(st.st_mode):
                out[rel] = 'link ' + os.readlink(p)
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = f'dir {mode}'
            elif not stat.S_ISREG(st.st_mode):
                out[rel] = f'special {st.st_mode:o}'
            else:
                try:
                    with open(p, 'rb') as f:
                        data = f.read()
                except OSError as e:
                    out[rel] = f'unreadable {e.strerror}'
                    continue
                out[rel] = f'{hashlib.sha256(data).hexdigest()[:16]} {mode}'
                if texts is not None and len(data) <= TEXT_MAX:
                    texts[rel] = data.decode('utf-8', 'replace')
    return dict(sorted(out.items()))


def skeleton_errors(home):
    """How the home's skeleton is not what make_home made: a skeleton path that is gone, not a real dir,
    or of another mode, and the home itself. Its contents are in the listing, compared with Go's."""
    out = []
    for rel in ['.', *HOME_SKELETON]:
        try:
            st = os.lstat(os.path.join(home, rel))
        except OSError:
            out.append(f'{rel}: gone')
            continue
        if not stat.S_ISDIR(st.st_mode):
            out.append(f'{rel}: not a dir ({stat.filemode(st.st_mode)})')
        elif stat.S_IMODE(st.st_mode) != SKELETON_MODE:
            out.append(f'{rel}: mode {stat.S_IMODE(st.st_mode):o}, not {SKELETON_MODE:o}')
    return out


def listing(root, overlays, texts=None):
    """The storage's state: {'home': walk, 'disk': [walk of each upper dir], 'skeleton': deviations}."""
    return {'home': walk(os.path.join(root, 'home')), 'skeleton': skeleton_errors(os.path.join(root, 'home')),
            'disk': [walk(os.path.join(root, f'ovl{k}', 'upper'), texts) for k in range(overlays)]}


# ---- the write record (fanotify) ---------------------------------------------------------------

FAN_CLOEXEC, FAN_NONBLOCK, FAN_REPORT_DFID_NAME = 0x1, 0x2, 0x400 | 0x800
FAN_MARK_ADD, FAN_MARK_FILESYSTEM = 0x1, 0x100
FAN_MODIFY, FAN_ATTRIB, FAN_MOVED_FROM, FAN_MOVED_TO, FAN_CREATE, FAN_DELETE = 0x2, 0x4, 0x40, 0x80, 0x100, 0x200
FAN_Q_OVERFLOW, FAN_ONDIR = 0x4000, 0x40000000
OPS = {FAN_CREATE: 'create', FAN_MODIFY: 'write', FAN_ATTRIB: 'attrib', FAN_DELETE: 'delete', FAN_MOVED_FROM: 'moved-from',
       FAN_MOVED_TO: 'moved-to'}
INFO_DFID_NAME = 2
META = struct.Struct('<IBBHQii')


class FileHandle(ctypes.Structure):
    _fields_ = [('handle_bytes', ctypes.c_uint), ('handle_type', ctypes.c_int), ('f_handle', ctypes.c_ubyte * 128)]


class Recorder:
    """Every write to the storage at `root` (a path the init can reach: the tmpfs, or /proc/self/fd/<n> of
    it) from mark() on: start() runs a reader thread, finish() drains the queue and returns
    {'writes': {path: [ops]}, 'overflow': bool}."""

    def __init__(self, root, overlays):
        self.root, self.overlays = root, overlays
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.fanotify_mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int, ctypes.c_char_p]
        self.libc.name_to_handle_at.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        self.fd = self.libc.fanotify_init(FAN_CLOEXEC | FAN_NONBLOCK | FAN_REPORT_DFID_NAME, os.O_RDONLY)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), f'fanotify_init: {os.strerror(ctypes.get_errno())}')
        mask = FAN_CREATE | FAN_DELETE | FAN_MODIFY | FAN_ATTRIB | FAN_MOVED_FROM | FAN_MOVED_TO | FAN_ONDIR
        if self.libc.fanotify_mark(self.fd, FAN_MARK_ADD | FAN_MARK_FILESYSTEM, mask, -100, root.encode()) != 0:
            raise OSError(ctypes.get_errno(), f'fanotify_mark {root}: {os.strerror(ctypes.get_errno())}')
        self.dirs = {}  # file handle -> path relative to root ('' the root)
        for d, subdirs, _ in os.walk(root):
            self._learn(os.path.relpath(d, root) if d != root else '')
        self.writes, self.overflow, self.stop, self.thread = {}, False, threading.Event(), None
        self.lock = threading.Lock()

    def _handle(self, rel):
        fh = FileHandle(128, 0)
        mount_id = ctypes.c_int()
        path = os.path.join(self.root, rel) if rel else self.root
        if self.libc.name_to_handle_at(-100, path.encode(), ctypes.byref(fh), ctypes.byref(mount_id), 0) != 0:
            return None
        return (fh.handle_type, bytes(fh.f_handle[:fh.handle_bytes]))

    def _learn(self, rel):
        h = self._handle(rel)
        if h is None:
            return
        old = self.dirs.get(h)
        if old is not None and old != rel:  # a dir moved: so did everything under it
            for k, v in list(self.dirs.items()):
                if v.startswith(old + '/'):
                    self.dirs[k] = rel + v[len(old):]
        self.dirs[h] = rel

    def _name(self, rel):
        """The record's name of a storage path, or None for the overlays' own work dirs."""
        parts = rel.split('/') if rel else []
        if parts and parts[0].startswith('ovl') and parts[0][3:].isdigit():
            if len(parts) == 1 or parts[1] != 'upper':
                return None
            return '/'.join([f'disk{parts[0][3:]}', *parts[2:]])
        return rel or '.'

    def _parse(self, buf):
        off = 0
        while off + META.size <= len(buf):
            ev_len, _, _, meta_len, mask, _, _ = META.unpack_from(buf, off)
            if mask & FAN_Q_OVERFLOW:
                self.overflow = True
            info = off + meta_len
            while info < off + ev_len:
                kind, _, ilen = struct.unpack_from('<BBH', buf, info)
                if kind == INFO_DFID_NAME:
                    nbytes, htype = struct.unpack_from('<Ii', buf, info + 12)
                    handle = (htype, bytes(buf[info + 20: info + 20 + nbytes]))
                    name = bytes(buf[info + 20 + nbytes: info + ilen]).split(b'\0')[0].decode('utf-8', 'surrogateescape')
                    self._event(mask, handle, name)
                info += ilen
            off += ev_len

    def _event(self, mask, handle, name):
        parent = self.dirs.get(handle)
        if parent is None:
            rel = f'?/{name}'
        else:
            rel = parent if name == '.' else (f'{parent}/{name}' if parent else name)
            if mask & FAN_ONDIR and mask & (FAN_CREATE | FAN_MOVED_TO) and name != '.':
                self._learn(rel)
        key = rel if rel.startswith('?/') else self._name(rel)
        if key is None:
            return
        ops = {v for k, v in OPS.items() if mask & k}
        if ops:
            self.writes.setdefault(key, set()).update(ops)

    def _read(self):
        try:
            while True:
                buf = os.read(self.fd, 1 << 20)
                if not buf:
                    return
                with self.lock:
                    self._parse(buf)
        except BlockingIOError:
            return

    def _loop(self):
        while not self.stop.is_set():
            select.select([self.fd], [], [], 0.1)
            self._read()

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def finish(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        self._read()
        os.close(self.fd)
        return {'writes': {k: sorted(v) for k, v in sorted(self.writes.items())}, 'overflow': self.overflow}
