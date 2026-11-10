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
  5. every fd but 0, 1, 2 and the report closed; the server forked and exec'd at cwd /. An exec failure
     comes back on a close-on-exec pipe and is a setup failure (exit 126), not a server crash;
  6. this script's own 0, 1 and 2 replaced by /dev/null, so the session's pipes are held by the server
     and its descendants only; every process that ends is reaped; when the server ends, every other
     process still in the namespace is a survivor: listed in the report, then killed (SIGKILL to all),
     and the exit is the server's (128 + signal for a signal).
Report: {"setup": why} or {"status": the server's wait status, "survivors": [cmdline, ...]}.
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
    readonly(spec['home'], False, ro=False)
    if spec['lower']:
        readonly(spec['target'], False, ro=False)
    for fd in [*fds.values(), *files.values()]:
        os.close(fd)


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
    r, w = os.pipe()  # close-on-exec: an exec failure comes back here
    pid = os.fork()
    if pid == 0:
        os.close(r)
        os.chdir('/')
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
    survivors = []
    for d in os.listdir('/proc'):
        if d.isdigit() and int(d) != 1:
            try:
                if open(f'/proc/{d}/stat').read().rsplit(')', 1)[1].split()[0] == 'Z':
                    continue  # ended, not yet reaped: no survivor
                survivors.append(open(f'/proc/{d}/cmdline', 'rb').read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()[:200])
            except OSError:
                pass
    report.write(json.dumps({'status': status, 'survivors': sorted(survivors)}))
    report.flush()
    os.kill(-1, signal.SIGKILL)
    while True:
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            break
    code = os.waitstatus_to_exitcode(status)
    os._exit(code if code >= 0 else 128 - code)


main()
