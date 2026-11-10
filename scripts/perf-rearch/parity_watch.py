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
callers fall back to checking the inputs' identity after each process when it does not, or when a
Watch cannot be built (WatchError: the instance or watch limit; it leaves no fd or watch behind).
Results are final only after finish() (dirty() and all_events() call it): it stops the reader thread,
joins it, and drains the queue, so an event the reader had read but not parsed yet is never lost.
"""
import ctypes
import ctypes.util
import errno
import os
import select
import struct
import threading

IN_ACCESS = 0x001
IN_MODIFY = 0x002
IN_ATTRIB = 0x004
IN_CLOSE_WRITE = 0x008
IN_CLOSE_NOWRITE = 0x010
IN_OPEN = 0x020
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
# Every access, reads too: the tripwires of parity_lsp.py (lane H5), dirs no process of a session may
# read. A read or a directory listing opens the file or dir after the watch started, which makes IN_OPEN.
# IN_CLOSE_NOWRITE is not watched (lane H7): a close alone is the late close of a file description opened
# before the watch, such as the watch's own listing of the dirs (os.scandir), whose fd a child that another
# thread forked at that moment holds until subprocess's close_fds closes it, after _discard(). That made
# lsp/hono-f12-p6 ERROR in a full gate ("touched ~/.config (close-nowrite)"; 15 to 52 of 400 watches
# under 4 forking threads in parity_h7_test.py, 0 without it). Dropping it loses nothing only because of
# the lsp tier's lifecycle: each session's watch is built before lspdiff starts, every server is a
# descendant started after it, and Popen gives them no fd but 0, 1 and 2, so none of them holds a
# description of a bait opened before the watch.
# What no inotify mask sees (Astra, H7 review): stat, statx, access and a lookup (no event at all), and
# open(O_PATH) with fstat on it. The lsp tier's tripwires therefore catch reads and listings of the
# guarded dirs, not metadata reads; the api tier has no tripwires: its sandbox has no such dirs at all
# (parity_api_init.py).
ACCESS = CHANGES | IN_ACCESS | IN_OPEN
NAMES = {IN_ACCESS: 'access', IN_OPEN: 'open', IN_CLOSE_NOWRITE: 'close-nowrite', IN_MODIFY: 'modify', IN_ATTRIB: 'attrib', IN_CLOSE_WRITE: 'close-write', IN_MOVED_FROM: 'moved-from',
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

    def __init__(self, roots, mask=CHANGES, keep=None, recursive=True):
        """keep(path): when given, only events on paths it accepts count, and all of them are listed in
        `events` (a process's events on its input files). recursive=False watches each root dir alone
        (its entries, not the dirs under it). A WatchError (no inotify instance, the watch
        limit) leaves nothing behind: the instance, its watches and the stop pipe are closed."""
        self.mask = mask
        self.keep = keep
        self.recursive = recursive
        self.events = []
        self.wd = {}  # wd -> (root, directory)
        self.first = {}  # root -> the first event under it
        self.lock = threading.Lock()
        self.parse_lock = threading.Lock()  # the reader and drain() both read the queue
        self.thread = None
        self.fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise WatchError(ctypes.get_errno(), 'inotify_init1 failed')
        self.stop_r = self.stop_w = None
        try:
            self.stop_r, self.stop_w = os.pipe()
            self.roots = [str(r) for r in roots]
            for root in self.roots:
                self._add_tree(root, root)
            if mask & (IN_ACCESS | IN_OPEN | IN_CLOSE_NOWRITE):
                self._discard()  # the watch's own listing of the dirs opened them
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        except BaseException:
            self._close_fds()  # closing the instance drops every watch it added
            raise

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
        if not self.recursive:
            return
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

    def _discard(self):
        """Drops the queued events (before the reader starts)."""
        while select.select([self.fd], [], [], 0)[0]:
            try:
                os.read(self.fd, 1 << 16)
            except (BlockingIOError, OSError):
                return

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
                return  # finish() drains what is left
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
            if mask & IN_IGNORED and not mask & self.mask:
                continue
            root, d = self.wd.get(wd, (None, None))
            if root is None:
                continue
            path = os.path.join(d, name) if name else d
            self._mark(root, f'{_describe(mask & (self.mask | IN_Q_OVERFLOW))} {path}', path)
            if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO) and self.recursive:
                try:
                    self._add_tree(root, path)
                except WatchError as e:
                    self._mark(root, str(e))

    def drain(self):
        """Reads every event queued so far, under the parse lock (so an event the reader already read is
        parsed first). Final results come from finish(), which stops the reader first."""
        while True:
            with self.parse_lock:
                r, _, _ = select.select([self.fd], [], [], 0)
                if not r:
                    return
                try:
                    data = os.read(self.fd, 1 << 16)
                except (BlockingIOError, OSError):
                    return
                self._parse(data)

    def finish(self):
        """Stops the reader, waits for it (it parses whatever it read), then reads the rest of the queue:
        after this every event that reached the instance is in `first` and `events`. Idempotent."""
        if self.thread is not None:
            os.write(self.stop_w, b'x')
            self.thread.join()
            self.thread = None
        self.drain()

    def dirty(self):
        """{root: first event}, final (finish()); the watch stays open for close()."""
        self.finish()
        with self.lock:
            return dict(self.first)

    def all_events(self):
        self.finish()
        with self.lock:
            return list(self.events)

    def _close_fds(self):
        for name in ('fd', 'stop_r', 'stop_w'):
            fd = getattr(self, name, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, None)

    def close(self):
        if self.fd is not None:
            self.finish()
        self._close_fds()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
