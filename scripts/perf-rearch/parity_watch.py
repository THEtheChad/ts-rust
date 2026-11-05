"""inotify watches for the parity gate's inputs (lane H4): a change to an input while a tier runs is
seen even when it is undone before the tier ends.

`Watch(roots)` adds a watch on every directory under each root (symlinks not followed) for every
event that changes an entry: a write or truncation (IN_MODIFY), a metadata change (IN_ATTRIB: chmod, chown, touch, a hard link), a create, delete or rename in the
directory, and the directory's own delete or move. Reads and opens for reading are not watched,
and an overlayfs mount never writes its lower dir, so a compiler that reads an input makes no
event. A thread reads the events; `dirty()` is {root: the first event seen under it}. An event
queue overflow marks every root. A directory created after the watch started is watched too.

inotify watches inodes, so a write through any path (a bind mount, an overlay upper copied up from
it is not one) is seen. Watches cost about 1 KB of kernel memory each; /proc/sys/fs/inotify/
max_user_watches (524,288 on MS-A2) bounds them. `available()` says whether inotify works here; the
callers fall back to checking the inputs' identity after each process when it does not.
"""
import ctypes
import ctypes.util
import errno
import os
import select
import struct
import threading

IN_MODIFY = 0x002
IN_ATTRIB = 0x004
IN_CLOSE_WRITE = 0x008
IN_MOVED_FROM = 0x040
IN_MOVED_TO = 0x080
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_DELETE_SELF = 0x400
IN_MOVE_SELF = 0x800
IN_Q_OVERFLOW = 0x4000
IN_IGNORED = 0x8000
IN_ISDIR = 0x40000000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
# IN_CLOSE_WRITE is not watched: it says that a file opened for writing was closed, not that it
# changed (a write is IN_MODIFY, a truncation too). And it comes late and from elsewhere: a thread
# that forks while another thread has a file open for writing gives the child a copy of that fd,
# which the child closes at exec, after the writer closed its own; in a 12-thread fuzz tier that made
# a close-write event on a just-written input in about 1 case in 1,000.
CHANGES = (IN_MODIFY | IN_ATTRIB | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
           | IN_DELETE_SELF | IN_MOVE_SELF)
NAMES = {IN_MODIFY: 'modify', IN_ATTRIB: 'attrib', IN_CLOSE_WRITE: 'close-write', IN_MOVED_FROM: 'moved-from',
         IN_MOVED_TO: 'moved-to', IN_CREATE: 'create', IN_DELETE: 'delete', IN_DELETE_SELF: 'delete-self',
         IN_MOVE_SELF: 'move-self', IN_Q_OVERFLOW: 'queue overflow'}
_EVENT = struct.Struct('iIII')

_libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
_libc.inotify_init1.argtypes = [ctypes.c_int]
_libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]


class WatchError(OSError):
    pass


def available():
    fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
    if fd < 0:
        return False
    os.close(fd)
    return True


def _describe(mask):
    return ','.join(n for bit, n in NAMES.items() if mask & bit) or hex(mask)


class Watch:
    """Watches every directory under `roots` (a {root: label} dict or a list) until close()."""

    def __init__(self, roots, mask=CHANGES, keep=None):
        """keep(path): when given, only events on paths it accepts count, and all of them are listed in
        `events` (a process's events on its input files)."""
        self.mask = mask
        self.keep = keep
        self.events = []
        self.fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise WatchError(ctypes.get_errno(), 'inotify_init1 failed')
        self.wd = {}  # wd -> (root, directory)
        self.first = {}  # root -> the first event under it
        self.lock = threading.Lock()
        self.parse_lock = threading.Lock()  # the thread and drain() both read the queue
        self.stop_r, self.stop_w = os.pipe()
        self.roots = [str(r) for r in roots]
        for root in self.roots:
            self._add_tree(root, root)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _add(self, root, path):
        wd = _libc.inotify_add_watch(self.fd, os.fsencode(path), self.mask | IN_ONLYDIR | IN_DONT_FOLLOW)
        if wd < 0:
            err = ctypes.get_errno()
            if err in (errno.ENOENT, errno.ENOTDIR):  # gone or not a directory: the parent's event says so
                return
            raise WatchError(err, f'inotify_add_watch {path}: {os.strerror(err)}')
        self.wd[wd] = (root, path)

    def _add_tree(self, root, top):
        self._add(root, top)
        stack = [top]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        self._add(root, e.path)
                        stack.append(e.path)

    def _mark(self, root, why, path=None):
        if self.keep is not None and path is not None and not self.keep(path):
            return
        with self.lock:
            self.first.setdefault(root, why)
            if self.keep is not None:
                self.events.append(why)

    def _loop(self):
        while True:
            ready, _, _ = select.select([self.fd, self.stop_r], [], [])
            if self.stop_r in ready and self.fd not in ready:
                return
            with self.parse_lock:
                try:
                    data = os.read(self.fd, 1 << 16)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                self._parse(data)
            if self.stop_r in ready:
                return

    def _parse(self, data):
        off = 0
        while off + _EVENT.size <= len(data):
            wd, mask, _cookie, size = _EVENT.unpack_from(data, off)
            name = data[off + _EVENT.size:off + _EVENT.size + size].rstrip(b'\0').decode('utf-8', 'surrogateescape')
            off += _EVENT.size + size
            if mask & IN_Q_OVERFLOW:
                for root in self.roots:
                    self._mark(root, 'inotify queue overflow (events lost)')
                continue
            if mask & IN_IGNORED and not mask & CHANGES:
                continue
            root, d = self.wd.get(wd, (None, None))
            if root is None:
                continue
            path = os.path.join(d, name) if name else d
            self._mark(root, f'{_describe(mask & (CHANGES | IN_Q_OVERFLOW))} {path}', path)
            if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO):
                try:
                    self._add_tree(root, path)
                except WatchError as e:
                    self._mark(root, str(e))

    def drain(self):
        """Reads every event queued so far (the thread does too; this makes the check synchronous)."""
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return
            with self.parse_lock:
                try:
                    data = os.read(self.fd, 1 << 16)
                except (BlockingIOError, OSError):
                    return
                self._parse(data)

    def dirty(self):
        self.drain()
        with self.lock:
            return dict(self.first)

    def all_events(self):
        self.drain()
        with self.lock:
            return list(self.events)

    def close(self):
        os.write(self.stop_w, b'x')
        self.thread.join(5)
        for fd in (self.fd, self.stop_r, self.stop_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
