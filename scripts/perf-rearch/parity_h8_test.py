#!/usr/bin/env python3
"""Lane H8's negative controls for the lsp tier (parity_lsp.py, vendor/lspdiff, parity_sandbox_init.py).

usage: parity_h8_test.py [--unit]                  prints H8 SELFTEST OK, or each FAIL; exit 1 on a failure
       parity_h8_test.py --e2e-only --harness DIR   the end-to-end controls against another harness (a `git
                                                    archive` of an older commit), to show which fail there

Unit checks (seconds, node): the strict JSON reader (json.mjs: duplicate keys, numbers keep their text),
the envelope check (client.mjs envelopeError), the home listing (sandbox.mjs listHome), the init's refusal
of a mount point outside the hidden dirs, and the tier's key (the init and json.mjs are session sources;
the ancestors it keys are the ones a server sees).

End-to-end checks (about 15 minutes; real sessions through `parity_lsp.py --tier quick --no-js` on
ts-pattern-f12-p6, whose Go answers are cached): the fake tsc-rs is parity_h8_fake.py, an LSP proxy in front
of the Go oracle, one mode per gap of the lsp tier (parity.md, "The lsp tier: the same gaps") and per api
control of rounds 2 and 3. Each must be good with `pass`, `fds` and `privs`, and fail (a DIFF, an ERROR, or no
escape) with every other mode:
  privileges    privs (no capability; mount, umount, mount_setattr, unshare of a mount or user namespace
                all fail): good here, a DIFF where the server is root in its namespace
  survivors     detached (a double-forked setsid child), hide-proc-survivor (tmpfs over /proc first),
                child-stderr (a child holds stderr open: its EOF is enforced): session end DIFF
  host writes   outside-write (the review dir, /etc, /var/tmp, /tmp): no file appears;
                shm-remount (root in its namespace before): the remount fails, DIFF
  metadata      stat-bait (stat and O_PATH of the real ~/.cache/typescript): it does not exist, DIFF
  initialize    init-null (Astra's init.py: a successful initialize answered null): initialize DIFF
  numbers       overflow (Astra's overflow.py: 1e999 for null), minus-zero, float (1.0), exp (1e0): DIFF
  envelopes     dup-key, bad-rpc ("9.9"), extra-key, stray-answer (an answer to no request): DIFF (protocol)
  home          home-write (Astra's home.py), home-transient (written and removed between two answers),
                home-skeleton (Astra's: tmp replaced by a file), home-root-hide, home-root-mode (her
                storage-root chmod probes, on the home): session home DIFF
  exec failure  unexec-go (Astra's: a Go that cannot be exec'd): ERROR, and nothing cached
It takes the gate's lock (the tier's paths are fixed) and uses the real Go cache.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = Path(sys.argv[sys.argv.index('--harness') + 1]) if '--harness' in sys.argv else HERE
sys.path.insert(0, str(HERE))
import parity_lsp  # noqa: E402

GO = str(Path.home() / '.local/bin/tsgo-lsp-oracle-3e9236361fa9')
SCRATCH = Path.home() / '.cache/perf-rearch/parity/scratch/h8-selftest'
REVIEW = Path.home() / '.cache/perf-rearch/scratch/review-h7r2-astra/lsp'
SESSION = 'ts-pattern-f12-p6'
OUTSIDE = [str(SCRATCH / 'outside-sandbox'), '/etc/parity-h8-escape', '/var/tmp/parity-h8-escape', '/tmp/parity-h8-escape']
LSPDIFF = HERE / 'vendor/lspdiff'
FAILS = []


def check(name, ok, info=''):
    print(f'{"ok  " if ok else "FAIL"} {name}{"" if ok else ": " + str(info)[:600]}', flush=True)
    if not ok:
        FAILS.append(name)


def node(code):
    r = subprocess.run(['node', '--input-type=module', '-e', code], capture_output=True, text=True, cwd=LSPDIFF)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])
    return json.loads(r.stdout)


# ---- unit ------------------------------------------------------------------------------------

def unit():
    got = node('''
const { parseStrict } = await import("./json.mjs");
const t = (s) => { try { return JSON.stringify(parseStrict(s).value); } catch (e) { return "error: " + e.message; } };
console.log(JSON.stringify({
  overflow: t('{"r":1e999}'), minus: t('{"r":-0}'), float: t('{"r":1.0}'), exp: t('{"r":1e0}'), big: t('{"r":9007199254740993}'),
  plain: t('{"r":[1,2.5,-3,null,true,"x"]}'), dup: t('{"a":1,"a":1}'), deepDup: t('[{"b":{"c":1,"c":1}}]'),
  keyInString: t('{"a":"{\\\\"a\\\\":1}","b":{"a":1}}'),
  escapedDup: t('{"a":1,"\\\\u0061":2}'), exactFlags: [parseStrict('{"r":[1,2.5,"1.0"]}').exact, parseStrict('{"r":1.0}').exact], bom: t('\\ufeff{}'), nan: t('{"r":NaN}'), trailing: t('{} x'),
}));''')
    check('json: 1e999 keeps its text (Astra: it parsed to Infinity and was written as null)', got['overflow'] == '{"r":1e999}', got)
    check('json: -0, 1.0, 1e0 and 2^53+1 keep their text', [got[k] for k in ('minus', 'float', 'exp', 'big')] ==
          ['{"r":-0}', '{"r":1.0}', '{"r":1e0}', '{"r":9007199254740993}'], got)
    check('json: canonical numbers and other types are unchanged', got['plain'] == '{"r":[1,2.5,-3,null,true,"x"]}', got)
    check('json: a duplicate key is refused, nested too', got['dup'].startswith('error') and got['deepDup'].startswith('error'), got)
    check('json: a key-like text in a string is no key', not got['keyInString'].startswith('error'), got)
    check('json: a duplicate spelled with an escape is a duplicate (keys compared decoded)', got['escapedDup'].startswith('error'), got)
    check('json: plain JSON.parse only where every number is canonical (a fraction is, 1.0 is not; a number in a string is no number)',
          got['exactFlags'] == [True, False], got)
    check('json: a BOM, NaN and trailing bytes are refused', all(got[k].startswith('error') for k in ('bom', 'nan', 'trailing')), got)
    env = node('''
const { envelopeError } = await import("./client.mjs");
const { parseKeep } = await import("./json.mjs");
const pending = new Map([[7, {}]]);
const e = (s) => envelopeError(parseKeep(s), pending);
console.log(JSON.stringify({
  ok: e('{"jsonrpc":"2.0","id":7,"result":null}'), err: e('{"jsonrpc":"2.0","id":7,"error":{"code":-32601,"message":"x"}}'),
  req: e('{"jsonrpc":"2.0","id":"a","method":"client/registerCapability","params":{}}'), note: e('{"jsonrpc":"2.0","method":"window/logMessage","params":{}}'),
  version: e('{"jsonrpc":"9.9","id":7,"result":null}'), both: e('{"jsonrpc":"2.0","id":7,"result":null,"error":{"code":1,"message":"x"}}'),
  extra: e('{"jsonrpc":"2.0","id":7,"result":null,"x":1}'), unknown: e('{"jsonrpc":"2.0","id":8,"result":null}'),
  floatId: e('{"jsonrpc":"2.0","id":7.0,"result":null}'), badError: e('{"jsonrpc":"2.0","id":7,"error":{"code":"1","message":"x"}}'),
  noResult: e('{"jsonrpc":"2.0","id":7}'), badParams: e('{"jsonrpc":"2.0","method":"m","params":3}'), array: e('[]'),
}));''')
    check('envelope: answers, errors, server requests and notifications pass', [env[k] for k in ('ok', 'err', 'req', 'note')] == [None] * 4, env)
    bad = ('version', 'both', 'extra', 'unknown', 'floatId', 'badError', 'noResult', 'badParams', 'array')
    check('envelope: every malformed envelope is refused (version, result and error, an extra key, no pending id, '
          'id 7.0, a bad error object, no result, params 3, not an object)', all(env[k] for k in bad), env)
    # The one storage lister (parity_sandbox_storage.py walk; the api tier's _walk until round 2).
    import parity_sandbox_storage as storage
    d = Path(tempfile.mkdtemp(prefix='home-', dir=SCRATCH))
    (d / 'tmp').mkdir()
    (d / 'tmp' / 'f').write_text('x')
    (d / '__proto__').write_text('y')
    os.symlink('nowhere', d / 'l')
    os.chmod(d, 0o750)
    home = storage.walk(str(d))
    check('storage listing: the dir itself with its mode, each entry with type, mode and content hash, links by target',
          home.get('.') == 'dir 750' and home.get('tmp') == 'dir 775' and home.get('l') == 'link nowhere'
          and home.get('tmp/f', '').endswith(' 664') and len(home['tmp/f'].split()[0]) == 16, home)
    check('storage listing: finding 2: an entry named __proto__ is listed', home.get('__proto__', '').endswith(' 664'), home)
    (d / 'tmp').chmod(0)
    hidden_dir = storage.walk(str(d))
    (d / 'tmp').chmod(0o755)
    check('storage listing: finding 1: a dir that cannot be listed is said so, never skipped (as an unprivileged lister)',
          'unlistable' in hidden_dir.get('tmp', ''), hidden_dir)
    shutil.rmtree(d)
    # The write record: a fanotify filesystem mark on a tmpfs of the namespace's own (as the init sets it).
    rec = subprocess.run(['unshare', '-rm', sys.executable, '-I', '-c', f"""
import json, os, sys, ctypes, time
sys.path.insert(0, {str(HERE)!r})
import parity_sandbox_storage as storage
t = {str(SCRATCH / 'rec')!r}; os.makedirs(t, exist_ok=True)
assert ctypes.CDLL(None).mount(b'tmpfs', t.encode(), b'tmpfs', 0, None) == 0
storage.make_home(t + '/home', 0); os.makedirs(t + '/ovl0/upper'); os.makedirs(t + '/ovl0/work')
r = storage.Recorder(t, 1); r.start()
os.makedirs(t + '/home/new/deep'); time.sleep(0.3)  # the reader names the new dirs (the race is round 3's test)
open(t + '/home/new/deep/f', 'w').write('x'); os.unlink(t + '/home/new/deep/f')
# a dir made and moved at once, as overlayfs does for a copy-up: its events are named where it lands
os.mkdir(t + '/home/tmpdir'); os.chmod(t + '/home/tmpdir', 0o700); os.rename(t + '/home/tmpdir', t + '/home/landed')
open(t + '/home/__proto__', 'w').write('y'); open(t + '/ovl0/work/tmp', 'w').write('z'); open(t + '/ovl0/upper/in', 'w').write('w')
os.rename(t + '/home/new', t + '/home/moved'); open(t + '/home/moved/deep/g', 'w').write('g')
time.sleep(0.3); print(json.dumps(r.finish()))
"""], capture_output=True, text=True)
    try:
        got = json.loads(rec.stdout)['writes']
    except ValueError:
        got = {'error': rec.stderr[-400:]}
    check('write record: finding 3: a file made and removed in a dir made a moment before is recorded (by its dir\'s last path)',
          'create' in got.get('home/moved/deep/f', []) and 'delete' in got.get('home/moved/deep/f', []), got)
    check('write record: round 3: a dir made, changed and moved at once has its change at the path it landed at, never unresolved',
          'attrib' in got.get('home/landed', []) and not any(k.startswith('?') for k in got), got)
    check('write record: __proto__, a write into an input (disk0), a moved dir\'s new path; the overlay work dir left out',
          'home/__proto__' in got and 'disk0/in' in got and 'home/moved/deep/g' in got and not any('work' in k for k in got), got)
    # The stand-in npm, as a user without privileges, from a read-only store (dirs 555, as the frozen store's
    # snapshot is): it installs with npm's modes, and a failure is logged as `error` (a harness error).
    t = Path(tempfile.mkdtemp(prefix='npm-', dir=SCRATCH))
    store = t / 'store'
    (store / 'registry').mkdir(parents=True)
    (store / 'registry/package.json').write_text('{"version":"1.0.0"}')
    (store / 'node_modules/@types/x').mkdir(parents=True)
    (store / 'node_modules/@types/x/index.d.ts').write_text('export {};\n')
    (store / 'closure.json').write_text('{"@types/x":["node_modules/@types/x"]}')
    (store / 'package-lock.json').write_text('{"packages":{"node_modules/@types/x":{"version":"2.0.0"}}}')
    subprocess.run(['chmod', '-R', 'a-w', str(store)], check=True)
    loc = t / 'cache/typescript/7.1'
    loc.mkdir(parents=True)
    import hashlib
    files = {str(f.relative_to(store)): hashlib.sha256(f.read_bytes()).hexdigest() for f in store.rglob('*') if f.is_file()}
    (t / 'log').mkdir()
    (t / 'config.json').write_text(json.dumps({'store': str(store), 'files': files, 'log': '/run/lspdiff-ata/log/npm.log', 'delayMs': 0,
                                                'cache': str(t / 'cache')}))
    bind = ('mount -t tmpfs tmpfs /run && mkdir -p /run/lspdiff-ata/log && touch /run/lspdiff-ata/config.json /run/lspdiff-ata/node '
            '/run/lspdiff-ata/fake-npm.mjs && mount --bind "$1" /run/lspdiff-ata/config.json && mount --bind "$2" /run/lspdiff-ata/node && '
            'mount --bind "$3" /run/lspdiff-ata/log && mount --bind "$4" /run/lspdiff-ata/fake-npm.mjs && cd "$5" && shift 5 && exec "$@"')

    def npm(*a):  # the stand-in as the sandbox runs it: its config, node, script and log dir at /run/lspdiff-ata
        return subprocess.run(['unshare', '-rm', 'sh', '-c', bind, 'sh', str(t / 'config.json'), os.path.realpath(shutil.which('node')),
                               str(t / 'log'), str(LSPDIFF / 'ata-npm/fake-npm.mjs'), str(loc), str(LSPDIFF / 'ata-npm/npm'), *a],
                              env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True).returncode
    rcs = [npm('install', a, '--save-dev') for a in ('types-registry@latest', '@types/x@latest')]
    modes = {p: oct(os.stat(loc / p).st_mode & 0o777) for p in ('node_modules/@types/x', 'node_modules/@types/x/index.d.ts')}
    check('stand-in npm: installs from a read-only store with npm\'s modes (dirs 755, files 644)',
          rcs == [0, 0] and modes == {'node_modules/@types/x': '0o755', 'node_modules/@types/x/index.d.ts': '0o644'}, (rcs, modes))
    bad = {**files, 'node_modules/@types/x/index.d.ts': '0' * 64}  # a store file that fails its sha256: a logged failure
    (t / 'config.json').write_text(json.dumps({'store': str(store), 'files': bad, 'log': '/run/lspdiff-ata/log/npm.log', 'delayMs': 0,
                                                'cache': str(t / 'cache')}))
    npm('install', '@types/x@latest', '--save-dev')
    last = json.loads((t / 'log/npm.log').read_text().splitlines()[-1])
    check('stand-in npm: a failure of its own is a done line with `error` (scenario.mjs: a harness error, not a hang)',
          last.get('done') and last.get('code') == 1 and 'error' in last, last)
    subprocess.run(['chmod', '-R', 'u+rwx', str(t)])
    shutil.rmtree(t)
    # Round 3 (Astra's recheck), finding 1: events in dirs gone before they are read (the record's reader
    # not started yet, so every dir is gone when it reads) get distinct identities, never ?/f twice.
    rec3 = subprocess.run(['unshare', '-rm', sys.executable, '-I', '-c', f"""
import json, os, sys, ctypes
sys.path.insert(0, {str(HERE)!r})
import parity_sandbox_storage as storage
t = {str(SCRATCH / 'rec3')!r}; os.makedirs(t, exist_ok=True)
assert ctypes.CDLL(None).mount(b'tmpfs', t.encode(), b'tmpfs', 0, None) == 0
storage.make_home(t + '/home', 0)
r = storage.Recorder(t, 0)
for d in ('A', 'B'):
    os.mkdir(t + '/home/' + d); open(t + '/home/' + d + '/f', 'w').write('x'); os.unlink(t + '/home/' + d + '/f'); os.rmdir(t + '/home/' + d)
print(json.dumps(r.finish()))
"""], capture_output=True, text=True)
    try:
        unresolved = [k for k in json.loads(rec3.stdout)['writes'] if k.startswith('?')]
    except ValueError:
        unresolved = [rec3.stderr[-300:]]
    check('round 3 finding 1: two files in dirs gone before their events are read are two distinct unresolved paths, never ?/f',
          len(unresolved) == 2 and len(set(unresolved)) == 2 and all(k.endswith('/f') and not k.startswith('?/') for k in unresolved), unresolved)
    # finding 6: xattrs (name and value hash) and hard-link groups in the listing
    d6 = Path(tempfile.mkdtemp(prefix='xl-', dir=SCRATCH))
    (d6 / 'a').write_text('same')
    os.link(d6 / 'a', d6 / 'b')
    (d6 / 'c').write_text('same')
    os.setxattr(d6 / 'c', 'user.probe', b'one')
    l6 = storage.walk(str(d6))
    check('round 3 finding 6: two names of one inode are one link group, a separate file is not',
          l6['a'].endswith('; links 2 group 0') and l6['b'].endswith('; links 2 group 0') and 'links' not in l6['c'], l6)
    check('round 3 finding 6: an xattr is listed by name and value hash', '; xattr user.probe=' in l6['c'], l6)
    os.setxattr(d6 / 'c', 'user.probe', b'two')
    check('round 3 finding 6: another xattr value is another listing', storage.walk(str(d6))['c'] != l6['c'])
    shutil.rmtree(d6)
    import parity_sandbox_init as init
    r = subprocess.run(['/usr/bin/python3', '-I', str(HERE / 'parity_sandbox_init.py'), 'launch',
                        json.dumps({'report': 1, 'mounts': [{'bind': '/etc', 'at': '/etc/x', 'rw': True}], 'expose': [], 'mtime': 0,
                                    'cmd': ['/bin/true']})], capture_output=True, text=True)
    check('init: a mount point outside the hidden dirs (it would be made on the host) is a setup failure',
          r.returncode == 125 and 'hidden dir' in r.stdout and not os.path.exists('/etc/x'), (r.returncode, r.stdout, r.stderr))
    check('init: the hidden dirs cover the user\'s files and the host state dirs', {'/home', '/tmp', '/run', '/dev/shm'} <= set(init.HIDDEN))
    rw = subprocess.run(['/usr/bin/python3', '-I', str(HERE / 'parity_sandbox_init.py'), 'launch',
                         json.dumps({'report': 1, 'home': str(SCRATCH / 'h3home'), 'mounts': [{'bind': str(SCRATCH), 'at': str(SCRATCH / 'rwbind'), 'rw': True}],
                                     'expose': [], 'mtime': 0, 'cmd': ['/bin/true']})], capture_output=True, text=True)
    check('round 3 finding 2: a writable bind of a host dir (outside the recorded storage) is a setup failure',
          rw.returncode == 125 and 'setup' in rw.stdout, (rw.returncode, rw.stdout[-300:], rw.stderr[-300:]))
    check('key: the sandbox init and json.mjs are session sources (a change resets every Go fill)',
          {'../../parity_sandbox_init.py', 'json.mjs'} <= set(parity_lsp.SESSION_SOURCES))
    check('key: the ancestors keyed are the ones a server sees (none in a hidden dir)',
          parity_lsp.ancestor_dirs(str(Path.home() / 'x/y')) == ['/'] and parity_lsp.ancestor_dirs('/usr/lib/x') == ['/', '/usr', '/usr/lib'],
          parity_lsp.ancestor_dirs(str(Path.home() / 'x/y')))


# ---- end to end ------------------------------------------------------------------------------

NEW = (HARNESS / 'parity_sandbox_init.py').exists()


def fake(mode):
    """The fake tsc-rs command: for this harness the proxy's words (lspdiff binds each file it names into
    the sandbox); for a harness before H8 (one path, everything of the host visible) a wrapper script."""
    f = SCRATCH / 'fake_lsp.py'
    f.write_text((HERE / 'parity_h8_fake.py').read_text().replace('OUTSIDE_LIST', repr(OUTSIDE)))
    words = f'/usr/bin/python3 {f} {mode} {GO} {Path.home() / ".cache/typescript"}'
    if NEW:
        return words
    w = SCRATCH / f'fake-{mode}.sh'
    w.write_text(f'#!/bin/sh\nexec {words} "$@"\n')
    w.chmod(0o755)
    return str(w)


def tier(rs, go=GO, session=SESSION):
    out = Path(tempfile.mkdtemp(prefix='run-', dir=SCRATCH))
    r = subprocess.run([sys.executable, '-I', str(HARNESS / 'parity_lsp.py'), '--rs', rs, '--go', go, '--out', str(out), '--tier', 'quick',
                        '--only', f'^{session}$', '--no-js'], capture_output=True, text=True)
    try:
        return json.loads((out / 'results.json').read_text())['items'], r.stdout + r.stderr
    except OSError:
        return {}, r.stdout + r.stderr


def bad(items):
    return {k: v for k, v in items.items() if v['status'] not in ('MATCH', 'GO-BROKEN')}


def short(items, log=''):
    b = bad(items)
    return f'{len(items)} items, {len(b)} not good: {json.dumps(dict(list(b.items())[:2]))[:400]} {log[-300:]}'


def item(step, status='DIFF', **more):
    """A test that some item of a session is `step` with that status (and the fields in `more`)."""
    return lambda i: any(k.endswith(' ' + step) and v['status'] == status and all(v.get(f) == x for f, x in more.items()) for k, v in i.items())


EXPECT = {  # mode -> (what a good harness shows, a test on the items)
    'detached': ('session end DIFF (a survivor)', item('session end')),
    'hide-proc-survivor': ('a DIFF (it cannot mount over /proc)', lambda i: bool(bad(i))),
    'child-stderr': ('session end DIFF (stderr held open)', item('session end')),
    'shm-remount': ('a DIFF (the remount fails)', lambda i: bool(bad(i))),
    'stat-bait': ('a DIFF (the guarded dir does not exist)', lambda i: bool(bad(i))),
    'init-null': ('initialize DIFF', item('initialize')),
    'overflow': ('a DIFF (1e999 is not null)', lambda i: bool(bad(i))),
    'minus-zero': ('a DIFF (-0 is not 0)', lambda i: bool(bad(i))),
    'float': ('a DIFF (1.0 is not 1)', lambda i: bool(bad(i))),
    'exp': ('a DIFF (1e0 is not 1)', lambda i: bool(bad(i))),
    'dup-key': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'bad-rpc': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'extra-key': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'stray-answer': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'home-write': ('session storage DIFF', item('session storage')),
    'home-transient': ('session writes DIFF (written and removed between two answers)', item('session writes')),
    'home-skeleton': ('session storage DIFF', item('session storage')),
    'home-root-hide': ('session storage DIFF', item('session storage')),
    'home-root-mode': ('session storage DIFF', item('session storage')),
    # round 2: Astra's H8 review, one per finding
    'input-root-hide': ('finding 1: session end DIFF rsFailure input, and session storage DIFF', lambda i: item('session end', rsFailure='input')(i) and item('session storage')(i)),
    'input-root-mode': ('finding 1: session end DIFF rsFailure input', item('session end', rsFailure='input')),
    'home-proto': ('finding 2: session storage DIFF ($HOME/__proto__)', item('session storage')),
    'within-request': ('finding 3: session writes DIFF (written and removed inside one request)', item('session writes')),
    'late-diag': ('finding 4: session messages DIFF (a notification while shutdown is answered)', item('session messages')),
    'late-request': ('finding 4: session messages DIFF (a request while shutdown is answered)', item('session messages')),
    'request': ('finding 4: session messages DIFF (a request in a standard session)', item('session messages')),
    'shutdown-result': ('finding 5: session end DIFF (shutdown answered 12345)', item('session end')),
}


def lspdiff(tag, go, rs, extra=(), scenario=None):
    """The harness's lspdiff alone (sandboxed, as parity_lsp.py runs it) on a one-file project, or on a fixture
    scenario: its items."""
    proj = SCRATCH / 'project'
    proj.mkdir(parents=True, exist_ok=True)
    (proj / 'a.ts').write_text('export const foo = 1;\nfoo;\n')
    root = SCRATCH / f'fixture-{tag}' if scenario else proj
    out = SCRATCH / f'lspdiff-{tag}'
    shutil.rmtree(out, ignore_errors=True)
    (SCRATCH / 'home').mkdir(exist_ok=True)
    env = {**os.environ, 'LSPDIFF_SANDBOX': json.dumps({'scratch': str(SCRATCH / 'sandbox'), 'home': str(SCRATCH / 'home'),
                                                         'mounts': [] if scenario else [{'lower': str(proj), 'target': str(proj)}]})}
    r = subprocess.run(['node', str(HARNESS / 'vendor/lspdiff/lspdiff.mjs'), 'diff', '--project', str(root), '--file', 'a.ts', '--positions', '1',
                        '--go', go, '--rs', rs, '--out', str(out), '--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1', *extra,
                        *(['--scenario', scenario] if scenario else [])],
                       env=env, capture_output=True, text=True, timeout=600)
    files = list(out.glob('*/items.json'))
    return {f'{x["i"]:04d} {x["step"]}': x for x in json.loads(files[0].read_text())} if files else {}, r.stdout + r.stderr


def finding1():
    """Astra's finding 1 as she ran it: lspdiff alone, as an ordinary user, on her one-file project; the server
    writes into its project after Go exits, then makes the project's root unreadable (or of another mode)."""
    fake('pass')
    for mode in ('input-root-hide', 'input-root-mode'):
        items, log = lspdiff(f'f1-{mode}', GO, f'/usr/bin/python3 {SCRATCH / "fake_lsp.py"} {mode} {GO} -')
        check(f'finding 1 ({mode}, lspdiff as an ordinary user): session end DIFF rsFailure input, session storage DIFF',
              item('session end', rsFailure='input')(items) and item('session storage')(items), short(items, log))


def round3():
    """Astra's round 2 recheck (review-h8r2-astra), one control per finding root took (1, 2, 3, 4, 6), as she
    ran them: lspdiff alone, her fakes as parity_h8_fake.py modes. Each must not be MATCH; the ATA control
    session must be."""
    fake('pass')
    sh = lambda m: f'/usr/bin/python3 {SCRATCH / "fake_lsp.py"} {m} - -'  # noqa: E731
    cases = [('finding 1: files in two dirs made and removed at once never share an unresolved path', 'path-go', 'path-rs', None, 'session writes'),
             ('finding 2: a write into the stand-in npm\'s log dir, removed at once, is recorded', 'go', 'ata-log-transient', 'ata-overlay', 'session writes'),
             ('finding 3: NODE_OPTIONS of the server does not reach the stand-in npm', 'go', 'npm-env', 'ata-overlay', None),
             ('finding 4: a diagnostic "bad watcher 999." is not "bad watcher 1."', 'mask-go', 'mask-rs', None, 'session messages'),
             ('finding 6: an xattr of another value', 'xattr-good', 'xattr-bad', None, 'session storage'),
             ('finding 6: two names of one inode where Go has two files', 'links-good', 'links-bad', None, 'session storage')]
    for name, go, rs, scenario, step in cases:
        items, log = lspdiff(f'r3-{rs}', sh(go), sh(rs), scenario=scenario)
        ok = any(k.endswith(' ' + step) and v['status'] == 'DIFF' for k, v in items.items()) if step else bool(bad(items))
        check(f'round 3 {name}: {step + " DIFF" if step else "a DIFF"}', items and ok, short(items, log))
    items, log = lspdiff('r3-control', sh('go'), sh('go'), scenario='ata-overlay')
    check('round 3 control: the ATA scenario with the same fake on both sides: every item MATCH', items and not bad(items), short(items, log))


def finding7():
    """Astra's finding 7: a Go that varies on one path (P, in about half of its runs) must not exempt a
    path no Go run gives (Q), now or through a recorded variant later; the final storage is judged against
    this fill's Go runs only."""
    sh = lambda m: f'/usr/bin/python3 {SCRATCH / "fake_lsp.py"} {m} - -'  # noqa: E731
    fake('pass')  # writes fake_lsp.py
    record = SCRATCH / 'f7-variants.json'
    record.unlink(missing_ok=True)
    flags = ['--go-runs', '8', '--go-variants-material', 'h8-finding7', '--go-variants-out', str(record)]
    for attempt in range(4):  # until Go's 8 runs differ on P (each writes it with probability 1/2)
        items, log = lspdiff('f7-extra', sh('vary'), sh('vary-extra'), flags)
        try:
            varied = any(len(v) > 1 for v in json.loads(record.read_text())['variants'].values())
        except (OSError, ValueError, KeyError):
            varied = False
        if varied:
            break
        record.unlink(missing_ok=True)
    detail = lambda i, s: next((v.get('detail', '') for k, v in i.items() if k.endswith(' ' + s)), '')  # noqa: E731
    check('finding 7: Go varies on P, tsc-rs also writes Q: session storage and session writes are DIFF, on Q only',
          item('session storage')(items) and item('session writes')(items) and 'tmp/Q' in detail(items, 'session writes')
          and 'tmp/P' not in detail(items, 'session writes'), short(items, log))
    only, log2 = lspdiff('f7-only', sh('vary'), sh('vary-only'), ['--go-runs', '8'])
    check('finding 7: control: tsc-rs writes only P, where Go varies: GO-BROKEN, P alone exempt',
          item('session writes', 'GO-BROKEN')(only) and 'tmp/P' in detail(only, 'session writes'), short(only, log2))
    later, log3 = lspdiff('f7-prior', sh('none'), sh('vary-extra'), ['--go-runs', '1', '--go-variants-material', 'h8-finding7', '--go-variants', str(record)])
    check('finding 7: a later fill with the record: Q is still a DIFF (a recorded variant of P exempts P alone)',
          item('session writes')(later) and 'tmp/Q' in detail(later, 'session writes') and 'tmp/P' not in detail(later, 'session writes'), short(later, log3))
    check('finding 7: and the storage is judged against this fill\'s Go runs only: P is a DIFF there', item('session storage')(later)
          and 'tmp/P' in detail(later, 'session storage'), short(later, log3))


def e2e():
    lock = open(Path.home() / '.cache/perf-rearch/parity/.lock', 'w')  # the gate's lock: the tier's paths are fixed
    print('e2e: waiting for the gate lock', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    for mode in ('pass', 'fds', 'privs'):  # privs: the proxy fails if any way to change the sandbox works
        items, log = tier(fake(mode))
        check(f'e2e control {mode}: every item good', items and not bad(items), short(items, log))
    for f in OUTSIDE:
        Path(f).unlink(missing_ok=True)
    items, log = tier(fake('outside-write'))
    escaped = [f for f in OUTSIDE if os.path.exists(f)]
    check('e2e host writes: outside-write: no file outside the session appears', items and not escaped, f'{escaped} {short(items, log)}')
    for f in [*escaped, '/dev/shm/h8-review-private-marker']:
        try:
            os.unlink(f)
        except OSError:
            pass
    only = sys.argv[sys.argv.index('--modes') + 1].split(',') if '--modes' in sys.argv else None
    for mode, (want, ok) in EXPECT.items():
        if only and mode not in only:
            continue
        items, log = tier(fake(mode))
        check(f'e2e {mode}: {want}', items and ok(items), short(items, log))
    Path('/dev/shm/h8-review-private-marker').unlink(missing_ok=True)
    if not only or 'ata-unlogged' in only:  # finding 6, on a scenario with ATA on
        items, log = tier(fake('ata-unlogged'), session='ata-overlay')
        check('e2e finding 6: an npm call of the server\'s own, its log pointed nowhere: logged anyway, and session writes DIFF',
              items and item('session writes')(items), short(items, log))
    if not only or 'finding1' in only:
        finding1()
    if not only or 'finding7' in only:
        finding7()
    if not only or 'round3' in only:
        round3()
    # Astra's non-executable Go (review-h7r2-astra/lsp/unexec), made unique to this harness so its cache
    # dir is new: ERROR, and nothing of it cached.
    unexec = SCRATCH / f'unexec-go-{"new" if NEW else "old"}'
    unexec.write_text((REVIEW / 'unexec').read_text() + f'# {HARNESS}\n')
    unexec.chmod(0o644)
    cache = Path.home() / '.cache/perf-rearch/parity/go-cache' / parity_lsp.sha256_file(unexec)[:16]
    shutil.rmtree(cache, ignore_errors=True)
    items, log = tier(fake('pass'), go=str(unexec))
    cached = sorted(str(p.relative_to(cache)) for p in cache.rglob('go*.jsonl')) if cache.exists() else []
    check('e2e exec failure: a Go that cannot be exec\'d is ERROR, not GO-BROKEN, and nothing is cached',
          items and all(v['status'] == 'ERROR' for v in items.values()) and not cached, f'cached {cached[:3]}; {short(items, log)}')
    shutil.rmtree(cache, ignore_errors=True)


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if '--e2e-only' not in sys.argv:
        unit()
    if '--unit' not in sys.argv:
        e2e()
    print(f'H8 SELFTEST {"FAIL: " + "; ".join(FAILS) if FAILS else "OK"}')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
