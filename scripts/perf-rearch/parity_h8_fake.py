# Lane H8's fake tsc-rs for the lsp tier (parity_h8_test.py): an LSP proxy in front of the Go LSP oracle,
# in the style of parity_h7_fake.py (Astra's api probe). Its modes are Astra's lsp probes of her H7 round 2
# recheck (~/.cache/perf-rearch/scratch/review-h7r2-astra/lsp/: init.py, overflow.py, home.py; unexec is
# the test's own non-executable Go), her api probes of rounds 2 and 3 (detached, shm-remount,
# hide-proc-survivor, home-skeleton, root-mode/root-hide on the storage root: here the private home), and
# one per gap of the lsp tier that parity.md listed ("The lsp tier: the same gaps"). Round 2: her probes of
# the H8 review (~/.cache/perf-rearch/scratch/review-h8-astra/: proxy-root.py's home-proto,
# within-request, late-diag, shutdown-result, input-root-hide and input-root-mode; fake-extra.py's request,
# late-request and ata-unlogged), and the synthetic case of her finding 7 (vary, vary-extra, none).
# usage: python3 parity_h8_fake.py MODE GO TYPINGS [server args]; OUTSIDE_LIST is replaced by the self-test.
# GO `-`: no Go behind it; the fake answers itself (initialize with no capabilities, every other request
# with null), as Astra's fake-extra.py does.
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
if mode == 'home-proto':  # Astra's H8 review: a name a {} dictionary loses
    open(HOME + '/__proto__', 'w').write('persistent unexpected output')
# Her finding 7, synthetic: `vary` writes P in about half of its runs (as Go varies on one path), `vary-extra`
# always writes P and also Q (a path no Go run writes), `none` writes nothing.
if mode == 'vary' and time.time_ns() // 1000 % 2:
    open(HOME + '/tmp/P', 'w').write('legitimate, timing-dependent')
if mode == 'vary-extra':
    open(HOME + '/tmp/P', 'w').write('legitimate, timing-dependent')
    open(HOME + '/tmp/Q', 'w').write('unrelated damage')
if mode == 'vary-only':
    open(HOME + '/tmp/P', 'w').write('legitimate, timing-dependent')
# Astra's round 2 recheck (~/.cache/perf-rearch/scratch/review-h8r2-astra/: fake-more.py, fake-probes.py,
# fake-env.py), as she wrote them: Go's side is the `-go` mode, tsc-rs's the other.
if mode.startswith('links-'):  # finding 6: two names of one inode where Go writes two files
    a, b = HOME + '/tmp/a', HOME + '/tmp/b'
    open(a, 'w').write('same')
    if mode == 'links-bad':
        os.link(a, b)
    open(b, 'w').write('same')
if mode.startswith('xattr-'):  # finding 6: an extended attribute of another value
    os.setxattr(HOME + '/tmp', 'user.probe', b'good' if mode == 'xattr-good' else b'bad')
if mode.startswith('path-'):  # finding 1: a file made and removed in A (Go) or in B (tsc-rs), both dirs removed at once
    for sub in ['A', 'B']:
        d = HOME + '/tmp/' + sub
        os.mkdir(d)
        if (sub == 'A' and mode == 'path-go') or (sub == 'B' and mode == 'path-rs'):
            open(d + '/f', 'w').write('data')
            os.unlink(d + '/f')
        os.rmdir(d)
if mode.startswith('mmap-'):  # out of scope (documented): a change through a writable mapping, put back
    import mmap
    open(HOME + '/tmp/cache', 'wb').write(b'original')
    if mode == 'mmap-bad':
        with open(HOME + '/tmp/cache', 'r+b') as f:
            m = mmap.mmap(f.fileno(), 0)
            m[:] = b'corrupt!'
            m.flush()
            m[:] = b'original'
            m.flush()
            m.close()
if mode == 'ata-log-transient':  # finding 2: a write into the stand-in npm's log dir, removed at once
    open('/run/lspdiff-ata/log/extra', 'w').write('unexpected write')
    os.unlink('/run/lspdiff-ata/log/extra')
if mode == 'npm-env':  # finding 3: node of the stand-in npm told to run other code first
    subprocess.run(['npm', 'install', 'not-in-the-store@latest'], env={**os.environ, 'NODE_OPTIONS': '--import=data:text/javascript,process.exit(0)'},
                   check=True, stdout=subprocess.DEVNULL)
if mode == 'ata-unlogged':  # Astra's fake-extra.py: its own npm call, with the log pointed nowhere
    loc = os.environ['HOME'] + '/.cache/extra-install'
    os.makedirs(loc)
    subprocess.run(['npm', 'install', '--ignore-scripts', 'types-registry@latest'], cwd=loc, stdout=subprocess.DEVNULL,
                   env={**os.environ, 'ATA_NPM_LOG': '/dev/null', 'ATA_NPM_DELAY_MS': '0'})
    import shutil
    shutil.rmtree(loc, ignore_errors=True)
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
# GO `-`: the server the proxy talks to is this minimal one (Astra's fake-extra.py without its modes).
SELF_CODE = """
import json, sys
f, o = sys.stdin.buffer, sys.stdout.buffer
while True:
    h = f.readline()
    if not h:
        break
    n = int(h.split(b':')[1]); f.readline(); m = json.loads(f.read(n))
    if m.get('method') == 'exit':
        break
    if 'id' not in m or 'method' not in m:
        continue
    b = json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'capabilities': {}} if m['method'] == 'initialize' else None},
                   separators=(',', ':')).encode()
    o.write(b'Content-Length: %d\\r\\n\\r\\n' % len(b) + b); o.flush()
"""
p = subprocess.Popen([go, *args] if go != '-' else ['/usr/bin/python3', '-c', SELF_CODE], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
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
    # Astra's H8 review (proxy-root.py, fake-extra.py)
    if method and mode == 'within-request':  # written and removed before the answer goes out
        open(HOME + '/tmp/ephemeral', 'w').write('not a Go write')
        os.unlink(HOME + '/tmp/ephemeral')
    if method == 'shutdown' and mode == 'shutdown-result':
        body = body.replace(b'"result":null', b'"result":12345')
    extra = None
    if method == 'shutdown' and mode.startswith('mask-'):  # finding 4: a diagnostic text that looks like a watcher id
        extra = json.dumps({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics', 'params': {
            'uri': 'file:///a.ts', 'diagnostics': [{'message': 'bad watcher ' + ('1' if mode == 'mask-go' else '999') + '. result'}]}}).encode()
        out.write(b'Content-Length: %d\r\n\r\n' % len(extra) + extra)
    if method == 'shutdown' and mode == 'late-diag':
        extra = b'{"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":"file:///bogus.ts","diagnostics":[{"message":"late bogus error"}]}}'
    if (method == 'shutdown' and mode == 'late-request') or (method == 'initialize' and mode == 'request'):
        extra = b'{"jsonrpc":"2.0","id":"server-1","method":"workspace/applyEdit","params":{"edit":{"changes":{}}}}'
    if extra:
        out.write(b'Content-Length: %d\r\n\r\n' % len(extra) + extra)
    out.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
    out.flush()
code = p.wait()
if mode == 'input-root-hide':  # Astra's: output into the project, then its root unreadable
    open(os.getcwd() + '/unexpected.ts', 'w').write('unexpected compiler output')
    os.chmod(os.getcwd(), 0)
if mode == 'input-root-mode':
    os.chmod(os.getcwd(), 0o700)

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
