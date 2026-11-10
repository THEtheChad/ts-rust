"""PID 1 of an api session's sandbox (lane H7). parity_api_session.py runs it as
`unshare --user --map-root-user --mount --net --pid --fork --kill-child --mount-proc -- python3 -I
parity_api_init.py SPEC`, so it is root of a new user, mount, network and PID namespace, with a new /proc.

SPEC (JSON): report (an fd: the only way out for this script's findings), hide (dirs that are covered by
an empty tmpfs: the user's files, /tmp, /run and the other host state no session may read or write), work
(the session's host scratch dir: home/, and for a disk fixture upper/ and ovl/), home (where the private
home is seen: work/home bound there), lower and target (a disk fixture's read-only snapshot and the
scenario root its overlay is mounted at; or null), files (the command's files under a hidden dir, each
bound read-only at its own path), mtime (the time of every dir this script makes), cmd (the server).

Order (any failure is a setup failure: reported, exit 125, never a server result):
  1. every mount private; an O_PATH fd on work, lower and each file, taken before anything is hidden;
  2. an empty tmpfs on each hidden dir; the mount points made in it (the home, the fixture root, the
     files, a staging dir under /run), every made dir at the fixed mtime, mode 755;
  3. work and lower bound at the staging dir; work/home bound at the home; the overlay (lower, upper
     work/upper, workdir work/ovl) at the fixture root; the files bound at their paths; the staging
     binds detached again (the overlay holds its own clones of its layers), so the server cannot reach
     the upper or overlay work dir but through the overlay;
  4. the whole tree read-only (mount_setattr AT_RECURSIVE), then the home and the overlay writable again:
     the only writable places are the session's own storage, which the caller reads after every request;
     /proc is writable again too, for step 5's id maps; without capabilities the server can change
     nothing there that is not its own;
  5. every fd but 0, 1, 2 and the report closed; an fd on /proc kept (close-on-exec) for step 6; the
     server forked; it drops every privilege (drop(): a nested user namespace that owns none of this
     script's namespaces and may make no other, an empty bounding, ambient, inheritable, permitted and
     effective set, uid and gid 1000, no_new_privs, all checked) and is exec'd at cwd /. A drop or exec
     failure comes back on a close-on-exec pipe and is a setup failure (exit 126), not a server crash;
  6. this script's own 0, 1 and 2 replaced by /dev/null, so the session's pipes are held by the server
     and its descendants only; every process that ends is reaped; when the server ends, the ends already
     queued are reaped, every process still in the namespace is a survivor, listed through the /proc fd
     of step 5 (the server cannot mount over it), then all are killed (SIGKILL to all, which PID 1 of a
     PID namespace may do and none of them can stop) and reaped and counted (`reaped`: whatever /proc
     showed, a process this script reaps after the server ended outlived it), and the exit is the
     server's (128 + signal for a signal).
Report: {"setup": why} or {"status": the server's wait status, "survivors": [cmdline, ...], "reaped": n}.
"""
import ctypes
import json
import os
import signal
import sys

spec = json.loads(sys.argv[1])
report = os.fdopen(spec['report'], 'w')
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
MS_NOSUID, MS_NODEV, MS_BIND, MS_REC, MS_PRIVATE = 2, 4, 4096, 16384, 1 << 18
MNT_DETACH, AT_RECURSIVE, MOUNT_ATTR_RDONLY, SYS_MOUNT_SETATTR = 2, 0x8000, 1, 442
STAGE = '/run/parity-api-stage'


class MountAttr(ctypes.Structure):
    _fields_ = [('attr_set', ctypes.c_uint64), ('attr_clr', ctypes.c_uint64), ('propagation', ctypes.c_uint64),
                ('userns_fd', ctypes.c_uint64)]


def done(obj, code):
    report.write(json.dumps(obj))
    report.flush()
    os._exit(code)


def check(ok, what):
    if not ok:
        done({'setup': f'{what}: {os.strerror(ctypes.get_errno())}'}, 125)


def mount(src, dst, fs, flags, data=None):
    enc = lambda s: s.encode() if s is not None else None  # noqa: E731
    check(libc.mount(enc(src), enc(dst), enc(fs), flags, enc(data)) == 0, f'mount {src} on {dst}')


def readonly(path, recursive, ro=True):
    a = MountAttr(MOUNT_ATTR_RDONLY if ro else 0, 0 if ro else MOUNT_ATTR_RDONLY, 0, 0)
    check(libc.syscall(SYS_MOUNT_SETATTR, -100, path.encode(), AT_RECURSIVE if recursive else 0, ctypes.byref(a),
                       ctypes.sizeof(a)) == 0, f'mount_setattr {path}')


def setup():
    mount(None, '/', None, MS_REC | MS_PRIVATE)
    fds = {'work': os.open(spec['work'], os.O_PATH | os.O_DIRECTORY)}
    if spec['lower']:
        fds['lower'] = os.open(spec['lower'], os.O_PATH | os.O_DIRECTORY)
    files = {f: os.open(f, os.O_PATH) for f in spec['files']}
    for h in spec['hide']:
        if os.path.isdir(h) and not os.path.islink(h):
            mount('tmpfs', h, 'tmpfs', MS_NOSUID | MS_NODEV, 'mode=755')
    made = set()

    def makedirs(d):
        parts = d.strip('/').split('/')
        for k in range(1, len(parts) + 1):
            p = '/' + '/'.join(parts[:k])
            if not os.path.isdir(p):
                os.mkdir(p, 0o755)
                made.add(p)
    for d in [spec['home'], f'{STAGE}/work', f'{STAGE}/lower', *([spec['target']] if spec['lower'] else [])]:
        makedirs(d)
    for f in files:
        makedirs(os.path.dirname(f))
        if not os.path.exists(f):
            open(f, 'w').close()
            made.add(f)
    for p in sorted(made, key=len, reverse=True) + [h for h in spec['hide'] if os.path.isdir(h)]:
        os.utime(p, (spec['mtime'], spec['mtime']), follow_symlinks=False)
    mount(f'/proc/self/fd/{fds["work"]}', f'{STAGE}/work', None, MS_BIND)
    mount(f'{STAGE}/work/home', spec['home'], None, MS_BIND)
    if spec['lower']:
        mount(f'/proc/self/fd/{fds["lower"]}', f'{STAGE}/lower', None, MS_BIND)
        mount('overlay', spec['target'], 'overlay', 0,
              f'lowerdir={STAGE}/lower,upperdir={STAGE}/work/upper,workdir={STAGE}/work/ovl')
    for f, fd in files.items():
        mount(f'/proc/self/fd/{fd}', f, None, MS_BIND)
    for d in (f'{STAGE}/lower', f'{STAGE}/work'):
        if os.path.ismount(d):
            check(libc.umount2(d.encode(), MNT_DETACH) == 0, f'umount {d}')
    readonly('/', True)
    readonly('/proc', False, ro=False)  # the server's own uid_map, setgroups and user limit (drop(), step 5)
    readonly(spec['home'], False, ro=False)
    if spec['lower']:
        readonly(spec['target'], False, ro=False)
    for fd in [*fds.values(), *files.values()]:
        os.close(fd)


CLONE_NEWUSER = 0x10000000
PR_CAPBSET_DROP, PR_SET_NO_NEW_PRIVS, PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL = 24, 38, 47, 4
SERVER_ID = 1000  # the server's uid and gid in its own user namespace (0 there would be root)


class CapHeader(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]


def drop():
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


def living(proc):
    """[cmdline] of every process of the namespace but this one that has not ended, read through an fd on
    /proc opened before the server started (the server cannot mount anything, so it cannot hide one)."""
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


def main():
    setup()
    keep = {0, 1, 2, report.fileno()}
    for fd in [int(x) for x in os.listdir('/proc/self/fd')]:
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass
    os.set_inheritable(report.fileno(), False)
    proc = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY)  # close-on-exec: the survivor scan's own handle
    r, w = os.pipe()  # close-on-exec: a privilege-drop or exec failure comes back here
    pid = os.fork()
    if pid == 0:
        os.close(r)
        os.chdir('/')
        why = drop()
        if why:
            os.write(w, f'the server keeps privileges: {why}'.encode())
            os._exit(127)
        try:
            os.execv(spec['cmd'][0], spec['cmd'])
        except OSError as e:
            os.write(w, f'exec {spec["cmd"][0]}: {e.strerror}'.encode())
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
        done({'setup': failed}, 126)
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
    survivors = living(proc)
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
    report.write(json.dumps({'status': status, 'survivors': survivors, 'reaped': reaped}))
    report.flush()
    code = os.waitstatus_to_exitcode(status)
    os._exit(code if code >= 0 else 128 - code)



try:
    main()
except BaseException as e:  # noqa: BLE001 (a failure of this script is a setup failure, with its reason)
    import traceback
    done({'setup': f'parity_api_init.py failed: {type(e).__name__}: {e}; {traceback.format_exc()[-600:]}'}, 125)
