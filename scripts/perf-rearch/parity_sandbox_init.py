"""PID 1 of a server's sandbox, shared by the api tier (parity_api_session.py; lane H7) and the lsp tier
(vendor/lspdiff/sandbox.mjs; lane H8). The caller runs `/usr/bin/python3 -I parity_sandbox_init.py
launch SPEC`, with the report fd open and the server's stdio and environment; that execs UNSHARE with this
script again as its command, so it is root of a new user, mount, network and PID namespace, with a new
/proc. Its spawner's own namespaces stay as they are. Killing the launched process (unshare) kills this
script (--kill-child), and with PID 1 every process of the namespace.

SPEC (JSON):
  report  an fd (a file the caller reads after the end): the only way out for this script's findings
  control optional [r, w]: two pipe fds; each line "list" read from r is answered on w with one JSON line,
          the storage's listing at that moment (the api tier lists it after every request)
  home    where the server sees its private home (the storage's home/, with its skeleton), writable
  plumbing optional: where the server sees the storage's plumbing/ dir, writable (the lsp tier's stand-in
          npm writes its log there: on the storage, so in the write record, and read by the harness through
          /proc/<this script>/root)
  mounts  in order, each one of
            {"bind": src, "at": dst, "rw": false}  src (a host file or dir) seen read-only at dst (lane H8
                    round 3: a writable location outside the recorded storage is refused)
            {"overlay": lower, "at": dst}  an overlay of the read-only lower at dst, its upper and work dirs
                    in the storage (ovl<k>/upper, ovl<k>/work for the k-th overlay): the server sees the
                    upper dir only through the overlay
  expose  host files and dirs the server runs or reads that a hidden dir would cover (the server binary,
          node, a store): each bound read-only at its own path; one outside every hidden dir is visible
          already and left as it is
  cwd     the server's working dir (default /)
  mtime   the time of every dir and mount point this script makes
  cmd     the server: [binary, args...]
The policy is this script's, the same for every tier: HIDDEN (the user's files, /tmp, /run and the
other host state no session may read or write) is covered by empty tmpfs dirs, so nothing there (the
real ~/.cache/typescript, ~/.npm, ~/.config, other lanes' trees, the tripwire dirs of before) exists for
the server, not even for stat; the whole tree is read-only but the `rw` binds and the overlays, which
the caller reads; the server has no privilege (drop()), no fd but 0, 1 and 2, no network.

Order (any failure is a setup failure: reported, exit 125, never a server result):
  1. every mount private; an O_PATH fd on every source (binds, lowers, bases, exposed paths), and every
     symlink in a hidden dir on the way to the cwd, an exposed path or a dst (lane H8: a project seen
     through a symlink, umami), taken before anything is hidden;
  2. an empty tmpfs on each hidden dir; the mount points made in it (every dst, every exposed path, a
     staging dir under /run) and those symlinks, with their targets, every made dir, file and link at
     the fixed mtime, dirs mode 755;
  3. the mounts in order: a bind from its fd; an overlay from its lower and base bound at the staging
     dir, which is detached again (the overlay holds its own clones of its layers), so the server cannot
     reach an upper or work dir but through the overlay; then the exposed paths, bound read-only;
  4. the whole tree read-only (mount_setattr AT_RECURSIVE), then the home, the `rw` binds and the overlays
     writable again: the only writable places are the session's own storage (parity_sandbox_storage.py:
     one tmpfs this script mounts, makes the home skeleton in, marks with fanotify for the write record and
     detaches, keeping an fd on it) and the `rw` binds; /proc is writable again
     too, for step 5's id maps; without capabilities the server can change nothing there that is not its
     own;
  5. every fd but 0, 1, 2 and the report closed; an fd on /proc kept (close-on-exec) for step 6; the
     server forked; it drops every privilege (drop(): a nested user namespace that owns none of this
     script's namespaces and may make no other, an empty bounding, ambient, inheritable, permitted and
     effective set, uid and gid 1000, no_new_privs, all checked), enters cwd and is exec'd. A drop, cwd
     or exec failure comes back on a close-on-exec pipe and is a setup failure (exit 126), not a server
     crash;
  6. this script's own 0, 1 and 2 replaced by /dev/null, so the session's pipes are held by the server
     and its descendants only; every process that ends is reaped; when the server ends, the ends already
     queued are reaped, every process still in the namespace is a survivor, listed through the /proc fd
     of step 5 (the server cannot mount over it), then all are killed (SIGKILL to all, which PID 1 of a
     PID namespace may do and none of them can stop) and reaped and counted (`reaped`: whatever /proc
     showed, a process this script reaps after the server ended outlived it), and the exit is the
     server's (128 + signal for a signal).
     Before the fork, every mount the server could write to is checked (/proc/self/mountinfo): it must be
     on the storage's tmpfs (the home, the plumbing dir), one of the overlays, or /proc (its own entries
     only, for drop()); any other writable mount is a setup failure (lane H8 round 3, finding 2: every
     writable place is recorded, or read-only);
  7. after the survivors are killed (nothing writes any more): the write record drained, the storage listed.
Report: {"setup": why} or {"status": the server's wait status, "survivors": [cmdline, ...], "reaped": n,
"storage": its listing (with "texts": the upper dirs' files, for a diff report), "writes": {path: [ops]},
"overflow": whether the record lost events (a failure of the session)}.
"""
import ctypes
import json
import os
import signal
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python3 -I leaves the script dir out
import parity_sandbox_storage as storage  # noqa: E402

# The host dirs every sandbox covers with an empty tmpfs (parity_api.py and parity_lsp.py key only the
# ancestor dirs outside them: the ones the server sees).
UNSHARE = ['/usr/bin/unshare', '--user', '--map-root-user', '--mount', '--net', '--pid', '--fork', '--kill-child',
           '--mount-proc', '--']
HIDDEN = ['/home', '/tmp', '/var/tmp', '/run', '/mnt', '/media', '/srv', '/opt', '/dev/shm']
STAGE = '/run/parity-sandbox-stage'
MS_NOSUID, MS_NODEV, MS_BIND, MS_REC, MS_PRIVATE = 2, 4, 4096, 16384, 1 << 18
MNT_DETACH, AT_RECURSIVE, MOUNT_ATTR_RDONLY, SYS_MOUNT_SETATTR = 2, 0x8000, 1, 442
CLONE_NEWUSER = 0x10000000
PR_CAPBSET_DROP, PR_SET_NO_NEW_PRIVS, PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL = 24, 38, 47, 4
SERVER_ID = 1000  # the server's uid and gid in its own user namespace (0 there would be root)


def hidden(path):
    return any(path == h or path.startswith(h + '/') for h in HIDDEN)


def links_along(path):
    """[(link, target)] of every symlink in a hidden dir among the path's prefixes, as the host has them."""
    parts = path.strip('/').split('/')
    prefixes = ['/' + '/'.join(parts[:k]) for k in range(1, len(parts) + 1)]
    return [(p, os.readlink(p)) for p in prefixes if hidden(p) and os.path.islink(p)]


class MountAttr(ctypes.Structure):
    _fields_ = [('attr_set', ctypes.c_uint64), ('attr_clr', ctypes.c_uint64), ('propagation', ctypes.c_uint64),
                ('userns_fd', ctypes.c_uint64)]


class CapHeader(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]


class Init:
    def __init__(self, spec):
        self.spec = spec
        self.report = os.fdopen(spec['report'], 'w')
        self.overlays = sum(1 for m in spec.get('mounts', []) if 'overlay' in m)
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
        self.libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]

    def done(self, obj, code):
        self.report.write(json.dumps(obj))
        self.report.flush()
        os._exit(code)

    def check(self, ok, what):
        if not ok:
            self.done({'setup': f'{what}: {os.strerror(ctypes.get_errno())}'}, 125)

    def mount(self, src, dst, fs, flags, data=None):
        enc = lambda s: s.encode() if s is not None else None  # noqa: E731
        self.check(self.libc.mount(enc(src), enc(dst), enc(fs), flags, enc(data)) == 0, f'mount {src} on {dst}')

    def readonly(self, path, recursive, ro=True):
        a = MountAttr(MOUNT_ATTR_RDONLY if ro else 0, 0 if ro else MOUNT_ATTR_RDONLY, 0, 0)
        self.check(self.libc.syscall(SYS_MOUNT_SETATTR, -100, path.encode(), AT_RECURSIVE if recursive else 0,
                                     ctypes.byref(a), ctypes.sizeof(a)) == 0, f'mount_setattr {path}')

    def setup(self):
        spec = self.spec
        mounts = spec.get('mounts', [])
        expose = [p for p in spec.get('expose', []) if hidden(p)]
        self.mount(None, '/', None, MS_REC | MS_PRIVATE)
        fd = lambda p: os.open(p, os.O_PATH)  # noqa: E731
        for m in mounts:
            if not (set(m) == {'overlay', 'at'} or (set(m) == {'bind', 'at', 'rw'} and m['rw'] is False)) or not hidden(m['at']):
                # a mount point outside the hidden dirs would be made on the host's own filesystem
                self.done({'setup': f'not a bind or an overlay at a path in a hidden dir: {m}'}, 125)
        if not hidden(spec['home']):
            self.done({'setup': f'the home is not in a hidden dir: {spec["home"]}'}, 125)
        src = [fd(m['bind'] if 'bind' in m else m['overlay']) for m in mounts]
        exposed = {p: fd(p) for p in expose}
        ats = [spec['home'], *([spec['plumbing']] if spec.get('plumbing') else []), *[m['at'] for m in mounts]]
        links = dict(x for p in [spec.get('cwd') or '/', *spec.get('expose', []), *ats] for x in links_along(p))
        for h in HIDDEN:
            if os.path.isdir(h) and not os.path.islink(h):
                self.mount('tmpfs', h, 'tmpfs', MS_NOSUID | MS_NODEV, 'mode=755')
        made = set()

        def makedirs(d):
            parts = d.strip('/').split('/')
            for k in range(1, len(parts) + 1):
                p = '/' + '/'.join(parts[:k])
                if not os.path.isdir(p):
                    os.mkdir(p, 0o755)
                    made.add(p)

        def point(path, like_fd):
            """A mount point at path for the file or dir like_fd is."""
            if os.path.isdir(f'/proc/self/fd/{like_fd}'):
                makedirs(path)
            else:
                makedirs(os.path.dirname(path))
                if not os.path.exists(path):
                    open(path, 'w').close()
                    made.add(path)
        makedirs(f'{STAGE}/lower')
        makedirs(f'{STAGE}/storage')
        makedirs(spec['home'])
        if spec.get('plumbing'):
            if not hidden(spec['plumbing']):
                self.done({'setup': f'the plumbing dir is not in a hidden dir: {spec["plumbing"]}'}, 125)
            makedirs(spec['plumbing'])
        for m, s in zip(mounts, src):
            point(m['at'], s)
        for p, f in exposed.items():
            point(p, f)
        for p, target in sorted(links.items()):
            if not os.path.lexists(p):
                makedirs(os.path.dirname(p))
                os.symlink(target, p)
                made.add(p)
        for p in sorted(made, key=len, reverse=True) + [h for h in HIDDEN if os.path.isdir(h)]:
            os.utime(p, (spec['mtime'], spec['mtime']), follow_symlinks=False)
        st = f'{STAGE}/storage'
        self.mount('tmpfs', st, 'tmpfs', MS_NOSUID | MS_NODEV, 'mode=755')
        storage.make_home(f'{st}/home', spec['mtime'])
        for k in range(self.overlays):
            for d in ('upper', 'work'):
                os.makedirs(f'{st}/ovl{k}/{d}', 0o755)
        self.mount(f'{st}/home', spec['home'], None, MS_BIND)
        if spec.get('plumbing'):
            os.makedirs(f'{st}/plumbing', 0o755)
            self.mount(f'{st}/plumbing', spec['plumbing'], None, MS_BIND)
        k = 0
        for m, s in zip(mounts, src):
            if 'bind' in m:
                self.mount(f'/proc/self/fd/{s}', m['at'], None, MS_BIND)
                continue
            self.mount(f'/proc/self/fd/{s}', f'{STAGE}/lower', None, MS_BIND)
            self.mount('overlay', m['at'], 'overlay', 0,
                       f'lowerdir={STAGE}/lower,upperdir={st}/ovl{k}/upper,workdir={st}/ovl{k}/work')
            self.check(self.libc.umount2(f'{STAGE}/lower'.encode(), MNT_DETACH) == 0, f'umount {STAGE}/lower')
            k += 1
        for p, f in exposed.items():
            self.mount(f'/proc/self/fd/{f}', p, None, MS_BIND)
        # The storage: an fd for this script (listing), the write record from here on, then detached: the
        # server reaches it only through the home and the overlays.
        self.storage_fd = os.open(st, os.O_RDONLY | os.O_DIRECTORY)
        self.root = f'/proc/self/fd/{self.storage_fd}'
        self.recorder = storage.Recorder(self.root, self.overlays)
        self.check(self.libc.umount2(st.encode(), MNT_DETACH) == 0, f'umount {st}')
        self.readonly('/', True)
        self.readonly('/proc', False, ro=False)  # the server's own uid_map, setgroups and user limit (drop(), step 5)
        self.readonly(spec['home'], False, ro=False)
        if spec.get('plumbing'):
            self.readonly(spec['plumbing'], False, ro=False)
        for m in mounts:
            if 'overlay' in m:
                self.readonly(m['at'], False, ro=False)
        self.check_writable()
        for f in [*exposed.values(), *src]:
            os.close(f)

    def check_writable(self):
        """Step 6's check: every writable mount is on the storage (its tmpfs's device), one of the overlays
        (an overlayfs at an overlay's `at`), or /proc. Anything else is a setup failure."""
        dev = os.stat(f'/proc/self/fd/{self.storage_fd}').st_dev
        storage_dev = f'{os.major(dev)}:{os.minor(dev)}'
        overlays = {m['at'] for m in self.spec.get('mounts', []) if 'overlay' in m}
        bad = []
        with open('/proc/self/mountinfo') as f:
            for line in f:
                fields = line.split()
                sep = fields.index('-')
                majmin, point, opts, fstype = fields[2], fields[4], fields[5].split(','), fields[sep + 1]
                if 'rw' not in opts:
                    continue
                point = point.replace('\\040', ' ')
                if majmin == storage_dev or (fstype == 'overlay' and point in overlays) or (fstype == 'proc' and point == '/proc'):
                    continue
                bad.append(f'{point} ({fstype})')
        if bad:
            self.done({'setup': f'writable mounts outside the recorded storage: {bad[:5]}'}, 125)

    def drop(self):
        """In the forked server, before exec: give up every privilege over the sandbox, or say why not.
        The mount, PID and network namespaces belong to this script's user namespace, in which the server
        would be root. The server enters a user namespace of its own, nested in it, which owns none of them,
        and there:
          - maps uid and gid SERVER_ID to this script's 0 (one id each, setgroups denied): the host sees the
            same user as before, so the server owns its home and overlay as before;
          - sets that namespace's user.max_user_namespaces to 0: a user namespace of its own would give it a
            full capability set again (the kernel resets the bounding set there), and with it a mount
            namespace it could change;
          - drops every capability from the bounding set, clears the ambient set, and empties the effective,
            permitted and inheritable sets (capset), then becomes SERVER_ID (setresgid, setresuid: no root
            uid left to regain them through exec) and sets no_new_privs;
          - checks /proc/self/status: CapInh, CapPrm, CapEff, CapBnd and CapAmb all 0, NoNewPrivs 1, the
            uid and gid SERVER_ID. Anything else is a setup failure.
        So mount, umount, mount_setattr, pivot_root, unshare (of a mount or user namespace), setns into this
        script's namespaces, ptrace of this script and access to its /proc/1/fd all fail for the server."""
        libc = self.libc

        def write(path, text):
            with open(path, 'w') as f:
                f.write(text)
        try:
            if libc.unshare(CLONE_NEWUSER) != 0:
                return f'unshare(CLONE_NEWUSER): {os.strerror(ctypes.get_errno())}'
            write('/proc/self/setgroups', 'deny')
            write('/proc/self/uid_map', f'{SERVER_ID} 0 1\n')
            write('/proc/self/gid_map', f'{SERVER_ID} 0 1\n')
            write('/proc/sys/user/max_user_namespaces', '0')
            last = int(open('/proc/sys/kernel/cap_last_cap').read())
            for cap in range(last + 1):
                if libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0:
                    return f'PR_CAPBSET_DROP {cap}: {os.strerror(ctypes.get_errno())}'
            if libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
                return f'PR_CAP_AMBIENT_CLEAR_ALL: {os.strerror(ctypes.get_errno())}'
            os.setresgid(SERVER_ID, SERVER_ID, SERVER_ID)
            os.setresuid(SERVER_ID, SERVER_ID, SERVER_ID)
            data = (CapData * 2)()
            if libc.capset(ctypes.byref(CapHeader(0x20080522, 0)), data) != 0:  # _LINUX_CAPABILITY_VERSION_3
                return f'capset: {os.strerror(ctypes.get_errno())}'
            if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                return f'PR_SET_NO_NEW_PRIVS: {os.strerror(ctypes.get_errno())}'
        except OSError as e:
            return f'dropping privileges: {e}'
        status = dict(line.split(':', 1) for line in open('/proc/self/status') if ':' in line)
        bad = [k for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb') if int(status.get(k, '1'), 16) != 0]
        if bad or status.get('NoNewPrivs', '').strip() != '1':
            return f'privileges left after the drop: {bad or "no_new_privs unset"}'
        if status['Uid'].split() != [str(SERVER_ID)] * 4 or status['Gid'].split() != [str(SERVER_ID)] * 4:
            return f'ids after the drop: Uid {status["Uid"].split()}, Gid {status["Gid"].split()}'
        return None

    @staticmethod
    def living(proc):
        """[cmdline] of every process of the namespace but this one that has not ended, read through an fd
        on /proc opened before the server started (the server cannot mount anything, so it cannot hide one)."""
        out = []
        for d in os.listdir(proc):
            if d.isdigit() and int(d) != 1:
                try:
                    fd = os.open(f'{d}/stat', os.O_RDONLY, dir_fd=proc)
                    state = os.read(fd, 4096).decode().rsplit(')', 1)[1].split()[0]
                    os.close(fd)
                    if state == 'Z':
                        continue  # ended, not yet reaped: no survivor
                    fd = os.open(f'{d}/cmdline', os.O_RDONLY, dir_fd=proc)
                    out.append(os.read(fd, 4096).replace(b'\0', b' ').decode('utf-8', 'replace').strip()[:200] or f'pid {d}')
                    os.close(fd)
                except (OSError, IndexError):
                    pass
        return sorted(out)

    def start(self):
        """Steps 5 and 6 up to the server's start: (its pid, the /proc fd)."""
        control = self.spec.get('control') or []
        keep = {0, 1, 2, self.report.fileno(), self.storage_fd, self.recorder.fd, *control}
        for fd in [int(x) for x in os.listdir('/proc/self/fd')]:
            if fd not in keep:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for fd in [self.report.fileno(), *control]:
            os.set_inheritable(fd, False)
        proc = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY)  # close-on-exec: the survivor scan's own handle
        r, w = os.pipe()  # close-on-exec: a privilege-drop, cwd or exec failure comes back here
        cmd = self.spec['cmd']
        pid = os.fork()
        if pid == 0:
            os.close(r)
            why = self.drop()
            if not why:
                try:
                    os.chdir(self.spec.get('cwd') or '/')
                    os.execv(cmd[0], cmd)
                except OSError as e:
                    why = f'{"exec " + cmd[0] if e.filename == cmd[0] else "cwd " + str(e.filename)}: {e.strerror}'
            else:
                why = f'the server keeps privileges: {why}'
            os.write(w, why.encode())
            os._exit(127)
        os.close(w)
        null = os.open('/dev/null', os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        os.close(null)
        failed = os.read(r, 4096).decode()
        os.close(r)
        if failed:
            os.waitpid(pid, 0)
            self.done({'setup': failed}, 126)
        return pid, proc

    def serve(self, r, w):
        """The control pipe: each "list" line is answered with the storage's listing, one JSON line."""
        with os.fdopen(r, 'r') as rf, os.fdopen(w, 'w') as wf:
            for line in rf:
                if line.strip() == 'list':
                    wf.write(json.dumps(storage.listing(self.root, self.overlays)) + '\n')
                    wf.flush()

    def run(self):
        self.setup()
        pid, proc = self.start()
        # Threads only after the fork: the server's child never inherits one mid-call.
        self.recorder.start()
        if self.spec.get('control'):
            threading.Thread(target=self.serve, args=tuple(self.spec['control']), daemon=True).start()
        while True:
            got, status = os.waitpid(-1, 0)
            if got == pid:
                break
        while True:  # the ends already queued: processes that ended before the server did
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        survivors = self.living(proc)
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:  # nothing else is left
            pass
        reaped = 0  # every process left in the namespace ends here, whatever /proc showed
        while True:
            try:
                os.waitpid(-1, 0)
                reaped += 1
            except ChildProcessError:
                break
        record = self.recorder.finish()
        texts = {}
        final = storage.listing(self.root, self.overlays, texts)
        self.report.write(json.dumps({'status': status, 'survivors': survivors, 'reaped': reaped, 'storage': {**final, 'texts': texts},
                                      'writes': record['writes'], 'overflow': record['overflow']}))
        self.report.flush()
        code = os.waitstatus_to_exitcode(status)
        os._exit(code if code >= 0 else 128 - code)


def main():
    if sys.argv[1:2] == ['launch']:  # the callers' entry: this script again, as PID 1 of new namespaces
        os.execv(UNSHARE[0], [*UNSHARE, sys.executable, '-I', os.path.abspath(__file__), 'run', sys.argv[2]])
    init = Init(json.loads(sys.argv[2]))
    try:
        init.run()
    except BaseException as e:  # noqa: BLE001 (a failure of this script is a setup failure, with its reason)
        import traceback
        init.done({'setup': f'parity_sandbox_init.py failed: {type(e).__name__}: {e}; {traceback.format_exc()[-600:]}'}, 125)


if __name__ == '__main__':
    main()
