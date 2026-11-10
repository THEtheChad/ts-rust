# Lane H7's fake tsc-rs (parity_h7_test.py): Astra's probe.py of her round 2 recheck of the api tier
# (~/.cache/perf-rearch/scratch/review-h7r2-astra/probe.py), verbatim but for the `privs` mode (round 3), with the
# root-mode and root-hide modes of her final recheck (review-h7r3-astra/extra-probe2.py), verbatim.
# usage (by the self-test): python3 parity_h7_fake.py MODE GO TYPINGS <server args>; OUTSIDE_LIST is
# replaced by the self-test before it runs.

import os, re, subprocess, sys, threading, time, random
mode, go, typings = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]
if mode == 'fds':
    fds = sorted(int(x) for x in os.listdir('/proc/self/fd'))
    if [fd for fd in fds if fd > 3]:  # 3: the listing's own fd
        sys.stderr.write('panic: inherited fds %r\n' % fds); sys.exit(2)
if mode == 'read-typings':
    os.listdir(typings)
    open(os.path.join(typings, '7.1', 'package.json')).read()
if mode == 'home-write':
    open(os.path.join(os.environ['HOME'], '.cache', 'fake-was-here'), 'w').write('x')
if mode == 'outside-write':
    for target in OUTSIDE_LIST:
        try:
            open(target, 'w').write('escaped')
        except OSError:
            pass
if mode == 'stat-bait':
    assert os.stat(os.path.join(typings, '7.1', 'package.json')).st_size > 0
    fd = os.open(os.path.join(typings, '7.1', 'package.json'), os.O_PATH)
    os.fstat(fd)
    os.close(fd)

import ctypes, json, signal
if mode == 'shm-remount':
    class Attr(ctypes.Structure):
        _fields_=[('a',ctypes.c_uint64),('b',ctypes.c_uint64),('c',ctypes.c_uint64),('d',ctypes.c_uint64)]
    libc=ctypes.CDLL(None,use_errno=True)
    attr=Attr(0,1,0,0)
    rc=libc.syscall(442,-100,b'/dev/shm',0,ctypes.byref(attr),ctypes.sizeof(attr))
    assert rc == 0, ('remount failed',ctypes.get_errno())
    open('/dev/shm/h7r2-review-private-marker','w').write('unexpected write')
    assert open('/dev/shm/h7r2-review-private-marker').read() == 'unexpected write'
if mode == 'home-skeleton':
    os.rmdir(os.environ['HOME']+'/tmp')
    open(os.environ['HOME']+'/tmp','w').write('replaced directory with file')
if mode == 'report-access':
    spec=json.loads(open('/proc/1/cmdline','rb').read().split(b'\0')[-2])
    fd=os.open('/proc/1/fd/'+str(spec['report']),os.O_WRONLY)
    os.write(fd,b'{"status":0,"survivors":[]}')
    os.close(fd)


if mode == 'privs':  # lane H7 round 3: every way to change the sandbox must fail, and no capability is left
    libc = ctypes.CDLL(None, use_errno=True)
    st = dict(l.split(':', 1) for l in open('/proc/self/status') if ':' in l)
    left = [k for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb') if int(st[k], 16)] + (['uid 0'] if os.getuid() == 0 else [])
    class Attr(ctypes.Structure):
        _fields_ = [('a', ctypes.c_uint64), ('b', ctypes.c_uint64), ('c', ctypes.c_uint64), ('d', ctypes.c_uint64)]
    clr = Attr(0, 1, 0, 0)
    def setns_mnt():
        try:
            fd = os.open('/proc/1/ns/mnt', os.O_RDONLY)
        except OSError:
            return -1
        return libc.setns(fd, 0)
    tries = {'mount tmpfs on /proc': lambda: libc.mount(b'tmpfs', b'/proc', b'tmpfs', 0, b'mode=755'),
             'mount --bind / /mnt': lambda: libc.mount(b'/', b'/mnt', None, 4096, None),
             'umount /proc': lambda: libc.umount2(b'/proc', 2),
             'mount_setattr rw /dev/shm': lambda: libc.syscall(442, -100, b'/dev/shm', 0, ctypes.byref(clr), ctypes.sizeof(clr)),
             'mount_setattr rw / (recursive)': lambda: libc.syscall(442, -100, b'/', 0x8000, ctypes.byref(clr), ctypes.sizeof(clr)),
             'pivot_root': lambda: libc.syscall(155, b'/tmp', b'/tmp'),
             'unshare(CLONE_NEWNS)': lambda: libc.unshare(0x20000),
             'unshare(CLONE_NEWUSER)': lambda: libc.unshare(0x10000000),
             "setns to PID 1's mount namespace": setns_mnt}
    for name, f in tries.items():
        if f() == 0:
            left.append(name)
    if left:
        sys.stderr.write('panic: the server can still: %r\n' % left); sys.exit(2)

p = subprocess.Popen([go, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def pump():
    for chunk in iter(lambda: sys.stdin.buffer.read1(65536), b''):
        p.stdin.write(chunk); p.stdin.flush()
    p.stdin.close()
threading.Thread(target=pump, daemon=True).start()
out = sys.stdout.buffer
tag = str(random.random())
while True:
    head = p.stdout.readline()
    if not head:
        break
    n = int(head.split(b':')[1]); p.stdout.readline()
    body = p.stdout.read(n)
    if mode == 'bad-rpc':
        assert b'"jsonrpc":"2.0"' in body
    if mode == 'corrupt-build' and b'"ProjectsBuilt":1' in body:
        cwd=args[args.index('--cwd')+1]
        target=os.path.join(cwd,'out','a.js')
        if os.path.exists(target):
            open(target,'w').write('CORRUPTED BUILD OUTPUT')
    if mode == 'bool-number':
        body = re.sub(rb'("emitSkipped"\s*:\s*)false', rb'\g<1>0', body)
    if mode == 'bad-rpc':
        body = body.replace(b'"jsonrpc":"2.0"', b'"jsonrpc":"9.9"')

    if mode == 'large-int':
        body=body.replace(b'"caseSensitivity":1',b'"caseSensitivity":9007199254740993')
    if mode == 'minus-zero':
        body=re.sub(rb'(:)1([,}])',rb'\g<1>-0\2',body)
    if mode == 'unicode-escape':
        body=body.replace(b'"jsonrpc"',b'"jsonrp\\u0063"')
    if mode == 'bom':
        body=b'\xef\xbb\xbf'+body
    if mode == 'whitespace':
        body+=b' \t\r\n'
    if mode == 'overflow':
        body=body.replace(b'"emitSkipped":false',b'"emitSkipped":1e999')

    emit = b'outputFiles' in body or b'emittedFiles' in body
    if emit and mode == 'wrong-path':
        body = body.replace(b'/out/', b'/0ut/')
    if emit and mode == 'nondet':
        body = body.replace(b'/out/', ('/out-' + tag + '/').encode())
    if emit and mode == 'crash':
        sys.stderr.write("thread 'main' panicked at src/fake.rs:1:1:\nfake\n"); sys.stderr.flush(); os._exit(101)
    if emit and mode == 'hang':
        time.sleep(3600)
    if emit and mode == 'malformed':
        out.write(b'Content-Lenght: 2\r\n\r\n{}'); out.flush(); continue
    out.write(b'Content-Length: %d\r\n\r\n' % len(body) + body); out.flush()
code = p.wait()

if mode in ('root-mode', 'root-hide'):
    cwd=args[args.index('--cwd')+1]
    if mode == 'root-hide':
        open(cwd+'/unexpected.js','w').write('unexpected output')
    os.chmod(cwd,0 if mode == 'root-hide' else 0o700)
    assert (os.stat(cwd).st_mode & 0o777) == (0 if mode == 'root-hide' else 0o700)
if mode == 'trailing-deep':
    body=b'['*2000+b'0'+b']'*2000
    out.write(b'Content-Length: %d\r\n\r\n' % len(body)+body); out.flush()
if mode == 'trailing-length':
    out.write(b'Content-Length: '+b'9'*5000+b'\r\n\r\n'); out.flush()


if mode == 'hide-proc-survivor':
    libc=ctypes.CDLL(None,use_errno=True)
    assert libc.mount(b'tmpfs',b'/proc',b'tmpfs',0,b'mode=755')==0, ctypes.get_errno()
if mode in ('detached','hide-proc-survivor'):
    if os.fork() == 0:
        os.setsid()
        if os.fork() != 0: os._exit(0)
        n=os.open('/dev/null',os.O_RDWR)
        for f in (0,1,2): os.dup2(n,f)
        os.close(n)
        time.sleep(60)
        os._exit(0)
    time.sleep(0.1)

if mode == 'child-stdout':
    child = os.fork()
    if child == 0:
        time.sleep(120)
        os._exit(0)
sys.exit(3 if mode == 'late-exit' else code)
