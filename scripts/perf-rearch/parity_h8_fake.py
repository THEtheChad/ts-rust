# Lane H8's fake tsc-rs for the lsp tier (parity_h8_test.py): an LSP proxy in front of the Go LSP oracle,
# in the style of parity_h7_fake.py (Astra's api probe). Its modes are Astra's lsp probes of her H7 round 2
# recheck (~/.cache/perf-rearch/scratch/review-h7r2-astra/lsp/: init.py, overflow.py, home.py; unexec is
# the test's own non-executable Go), her api probes of rounds 2 and 3 (detached, shm-remount,
# hide-proc-survivor, home-skeleton, root-mode/root-hide on the storage root: here the private home), and
# one per gap of the lsp tier that parity.md listed ("The lsp tier: the same gaps").
# usage: python3 parity_h8_fake.py MODE GO TYPINGS [server args]; OUTSIDE_LIST is replaced by the self-test.
import json
import os
import re
import subprocess
import sys
import threading
import time

mode, go, typings = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]
HOME = os.environ['HOME']

# ---- before the server: what a server can see and do in its sandbox ----
if mode == 'fds':
    fds = sorted(int(x) for x in os.listdir('/proc/self/fd'))
    if [fd for fd in fds if fd > 3]:  # 3: the listing's own fd
        sys.stderr.write('panic: inherited fds %r\n' % fds); sys.exit(2)
if mode == 'home-write':  # Astra's home.py
    open(HOME + '/tmp/unexpected', 'w').write('not a Go write')
if mode == 'home-skeleton':
    os.rmdir(HOME + '/tmp')
    open(HOME + '/tmp', 'w').write('replaced directory with file')
if mode == 'outside-write':
    for target in OUTSIDE_LIST:
        try:
            open(target, 'w').write('escaped')
        except OSError:
            pass
if mode == 'stat-bait':  # the guarded typings cache: stat and O_PATH, which no inotify watch sees
    assert os.stat(os.path.join(typings, '7.1', 'package.json')).st_size > 0
    fd = os.open(os.path.join(typings, '7.1', 'package.json'), os.O_PATH)
    os.fstat(fd)
    os.close(fd)

import ctypes  # noqa: E402
if mode == 'shm-remount':
    class Attr(ctypes.Structure):
        _fields_ = [('a', ctypes.c_uint64), ('b', ctypes.c_uint64), ('c', ctypes.c_uint64), ('d', ctypes.c_uint64)]
    libc = ctypes.CDLL(None, use_errno=True)
    attr = Attr(0, 1, 0, 0)
    rc = libc.syscall(442, -100, b'/dev/shm', 0, ctypes.byref(attr), ctypes.sizeof(attr))
    assert rc == 0, ('remount failed', ctypes.get_errno())
    open('/dev/shm/h8-review-private-marker', 'w').write('unexpected write')
if mode == 'privs':  # every way to change the sandbox must fail, and no capability is left
    libc = ctypes.CDLL(None, use_errno=True)
    st = dict(line.split(':', 1) for line in open('/proc/self/status') if ':' in line)
    left = [k for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb') if int(st[k], 16)] + (['uid 0'] if os.getuid() == 0 else [])
    class Attr(ctypes.Structure):  # noqa: E306
        _fields_ = [('a', ctypes.c_uint64), ('b', ctypes.c_uint64), ('c', ctypes.c_uint64), ('d', ctypes.c_uint64)]
    clr = Attr(0, 1, 0, 0)
    tries = {'mount tmpfs on /proc': lambda: libc.mount(b'tmpfs', b'/proc', b'tmpfs', 0, b'mode=755'),
             'umount /proc': lambda: libc.umount2(b'/proc', 2),
             'mount_setattr rw /': lambda: libc.syscall(442, -100, b'/', 0x8000, ctypes.byref(clr), ctypes.sizeof(clr)),
             'unshare(CLONE_NEWNS)': lambda: libc.unshare(0x20000),
             'unshare(CLONE_NEWUSER)': lambda: libc.unshare(0x10000000)}
    left += [name for name, f in tries.items() if f() == 0]
    if left:
        sys.stderr.write('panic: the server can still: %r\n' % left); sys.exit(2)

# ---- the proxy ----
p = subprocess.Popen([go, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
methods = {}  # request id -> method (the client's requests)


def frames(f):
    while True:
        head = f.readline()
        if not head:
            return
        n = int(head.split(b':')[1])
        f.readline()
        yield f.read(n)


def pump():
    for body in frames(sys.stdin.buffer):
        msg = json.loads(body)
        if 'id' in msg and 'method' in msg:
            methods[msg['id']] = msg['method']
        p.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
        p.stdin.flush()
    p.stdin.close()


threading.Thread(target=pump, daemon=True).start()
out = sys.stdout.buffer
answers = 0
for body in frames(p.stdout):
    msg = json.loads(body)
    method = methods.get(msg.get('id')) if 'method' not in msg else None
    if method:
        answers += 1
    if method == 'initialize' and mode == 'init-null':  # Astra's init.py
        body = re.sub(rb'"result":\{.*\}\}$', b'"result":null}', body)
    if method and method not in ('initialize', 'shutdown') and b'"result":null' in body:
        if mode == 'overflow':  # Astra's overflow.py: 1e999 for null
            body = body.replace(b'"result":null', b'"result":1e999')
    if method and method != 'initialize':
        if mode == 'minus-zero':
            body = re.sub(rb'"line":0([,}])', rb'"line":-0\1', body)
        if mode == 'float':
            body = re.sub(rb'"line":1([,}])', rb'"line":1.0\1', body)
        if mode == 'exp':
            body = re.sub(rb'"line":1([,}])', rb'"line":1e0\1', body)
        if mode == 'dup-key':
            body = body.replace(b'{"jsonrpc":"2.0",', b'{"jsonrpc":"2.0","jsonrpc":"2.0",', 1)
        if mode == 'bad-rpc':
            body = body.replace(b'"jsonrpc":"2.0"', b'"jsonrpc":"9.9"')
        if mode == 'extra-key':
            body = body.replace(b'{"jsonrpc":"2.0",', b'{"jsonrpc":"2.0","extra":1,', 1)
        if mode == 'stray-answer' and answers == 5:  # an answer to a request nobody sent
            stray = b'{"jsonrpc":"2.0","id":999999,"result":null}'
            out.write(b'Content-Length: %d\r\n\r\n' % len(stray) + stray)
        if mode == 'home-transient' and answers == 5:
            open(HOME + '/tmp/transient', 'w').write('written and removed between two answers')
        if mode == 'home-transient' and answers == 6:
            os.remove(HOME + '/tmp/transient')
    out.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
    out.flush()
code = p.wait()

# ---- after the server: what it leaves behind ----
if mode in ('home-root-hide', 'home-root-mode'):  # Astra's root-hide and root-mode, on the private home
    if mode == 'home-root-hide':
        open(HOME + '/unexpected.js', 'w').write('unexpected output')
    os.chmod(HOME, 0 if mode == 'home-root-hide' else 0o700)
if mode == 'hide-proc-survivor':
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.mount(b'tmpfs', b'/proc', b'tmpfs', 0, b'mode=755') == 0, ctypes.get_errno()
if mode in ('detached', 'hide-proc-survivor'):
    if os.fork() == 0:
        os.setsid()
        if os.fork() != 0:
            os._exit(0)
        n = os.open('/dev/null', os.O_RDWR)
        for f in (0, 1, 2):
            os.dup2(n, f)
        os.close(n)
        time.sleep(60)
        os._exit(0)
    time.sleep(0.1)
if mode == 'child-stderr':  # a child that holds only stderr open after the server exits
    if os.fork() == 0:
        n = os.open('/dev/null', os.O_RDWR)
        os.dup2(n, 0)
        os.dup2(n, 1)
        os.close(n)
        time.sleep(20)
        os._exit(0)
sys.exit(code)
