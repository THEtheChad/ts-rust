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
const t = (s) => { try { return JSON.stringify(parseStrict(s)); } catch (e) { return "error: " + e.message; } };
console.log(JSON.stringify({
  overflow: t('{"r":1e999}'), minus: t('{"r":-0}'), float: t('{"r":1.0}'), exp: t('{"r":1e0}'), big: t('{"r":9007199254740993}'),
  plain: t('{"r":[1,2.5,-3,null,true,"x"]}'), dup: t('{"a":1,"a":1}'), deepDup: t('[{"b":{"c":1,"c":1}}]'),
  keyInString: t('{"a":"{\\\\"a\\\\":1}","b":{"a":1}}'), bom: t('\\ufeff{}'), nan: t('{"r":NaN}'), trailing: t('{} x'),
}));''')
    check('json: 1e999 keeps its text (Astra: it parsed to Infinity and was written as null)', got['overflow'] == '{"r":1e999}', got)
    check('json: -0, 1.0, 1e0 and 2^53+1 keep their text', [got[k] for k in ('minus', 'float', 'exp', 'big')] ==
          ['{"r":-0}', '{"r":1.0}', '{"r":1e0}', '{"r":9007199254740993}'], got)
    check('json: canonical numbers and other types are unchanged', got['plain'] == '{"r":[1,2.5,-3,null,true,"x"]}', got)
    check('json: a duplicate key is refused, nested too', got['dup'].startswith('error') and got['deepDup'].startswith('error'), got)
    check('json: a key-like text in a string is no key', not got['keyInString'].startswith('error'), got)
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
    d = Path(tempfile.mkdtemp(prefix='home-', dir=SCRATCH))
    (d / 'tmp').mkdir()
    (d / 'tmp' / 'f').write_text('x')
    os.symlink('nowhere', d / 'l')
    os.chmod(d, 0o750)
    home = node(f'const {{ listHome }} = await import("./sandbox.mjs"); console.log(JSON.stringify(listHome({json.dumps(str(d))})));')
    check('home listing: the dir itself with its mode, each entry with type, mode and content hash, links by target',
          home.get('.') == 'dir 750' and home.get('tmp') == 'dir 775' and home.get('l') == 'link nowhere'
          and home.get('tmp/f', '').endswith(' 664') and len(home['tmp/f'].split()[0]) == 16, home)
    shutil.rmtree(d)
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
    npm_env = {'PATH': '/usr/bin:/bin', 'XDG_CACHE_HOME': str(t / 'cache'), 'ATA_STORE': str(store), 'ATA_NPM_LOG': str(t / 'npm.log'),
               'ATA_NPM_DELAY_MS': '0', 'ATA_NODE': shutil.which('node')}
    rcs = [subprocess.run([str(LSPDIFF / 'ata-npm/npm'), 'install', a, '--save-dev'], cwd=loc, env=npm_env).returncode
           for a in ('types-registry@latest', '@types/x@latest')]
    modes = {p: oct(os.stat(loc / p).st_mode & 0o777) for p in ('node_modules/@types/x', 'node_modules/@types/x/index.d.ts')}
    check('stand-in npm: installs from a read-only store with npm\'s modes (dirs 755, files 644)',
          rcs == [0, 0] and modes == {'node_modules/@types/x': '0o755', 'node_modules/@types/x/index.d.ts': '0o644'}, (rcs, modes))
    (store / 'node_modules/@types/x').chmod(0)  # a store it cannot read: a logged failure
    subprocess.run([str(LSPDIFF / 'ata-npm/npm'), 'install', '@types/x@latest', '--save-dev'], cwd=loc, env=npm_env)
    last = json.loads((t / 'npm.log').read_text().splitlines()[-1])
    check('stand-in npm: a failure of its own is a done line with `error` (scenario.mjs: a harness error, not a hang)',
          last.get('done') and last.get('code') == 1 and 'error' in last, last)
    subprocess.run(['chmod', '-R', 'u+rwx', str(t)])
    shutil.rmtree(t)
    import parity_sandbox_init as init
    r = subprocess.run(['/usr/bin/python3', '-I', str(HERE / 'parity_sandbox_init.py'), 'launch',
                        json.dumps({'report': 1, 'mounts': [{'bind': '/etc', 'at': '/etc/x', 'rw': True}], 'expose': [], 'mtime': 0,
                                    'cmd': ['/bin/true']})], capture_output=True, text=True)
    check('init: a mount point outside the hidden dirs (it would be made on the host) is a setup failure',
          r.returncode == 125 and 'hidden dir' in r.stdout and not os.path.exists('/etc/x'), (r.returncode, r.stdout, r.stderr))
    check('init: the hidden dirs cover the user\'s files and the host state dirs', {'/home', '/tmp', '/run', '/dev/shm'} <= set(init.HIDDEN))
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


def tier(rs, go=GO):
    out = Path(tempfile.mkdtemp(prefix='run-', dir=SCRATCH))
    r = subprocess.run([sys.executable, '-I', str(HARNESS / 'parity_lsp.py'), '--rs', rs, '--go', go, '--out', str(out), '--tier', 'quick',
                        '--only', f'^{SESSION}$', '--no-js'], capture_output=True, text=True)
    try:
        return json.loads((out / 'results.json').read_text())['items'], r.stdout + r.stderr
    except OSError:
        return {}, r.stdout + r.stderr


def bad(items):
    return {k: v for k, v in items.items() if v['status'] not in ('MATCH', 'GO-BROKEN')}


def short(items, log=''):
    b = bad(items)
    return f'{len(items)} items, {len(b)} not good: {json.dumps(dict(list(b.items())[:2]))[:400]} {log[-300:]}'


EXPECT = {  # mode -> (what a good harness shows, a test on the items)
    'detached': ('session end DIFF (a survivor)', lambda i: any(k.endswith('session end') and v['status'] == 'DIFF' for k, v in i.items())),
    'hide-proc-survivor': ('a DIFF (it cannot mount over /proc)', lambda i: bool(bad(i))),
    'child-stderr': ('session end DIFF (stderr held open)', lambda i: any(k.endswith('session end') and v['status'] == 'DIFF' for k, v in i.items())),
    'shm-remount': ('a DIFF (the remount fails)', lambda i: bool(bad(i))),
    'stat-bait': ('a DIFF (the guarded dir does not exist)', lambda i: bool(bad(i))),
    'init-null': ('initialize DIFF', lambda i: any(k.endswith(' initialize') and v['status'] == 'DIFF' for k, v in i.items())),
    'overflow': ('a DIFF (1e999 is not null)', lambda i: bool(bad(i))),
    'minus-zero': ('a DIFF (-0 is not 0)', lambda i: bool(bad(i))),
    'float': ('a DIFF (1.0 is not 1)', lambda i: bool(bad(i))),
    'exp': ('a DIFF (1e0 is not 1)', lambda i: bool(bad(i))),
    'dup-key': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'bad-rpc': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'extra-key': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'stray-answer': ('a protocol DIFF', lambda i: any(v.get('rsFailure') == 'crash' for v in bad(i).values())),
    'home-write': ('session home DIFF', lambda i: any(k.endswith('session home') and v['status'] == 'DIFF' for k, v in i.items())),
    'home-transient': ('session home DIFF', lambda i: any(k.endswith('session home') and v['status'] == 'DIFF' for k, v in i.items())),
    'home-skeleton': ('session home DIFF', lambda i: any(k.endswith('session home') and v['status'] == 'DIFF' for k, v in i.items())),
    'home-root-hide': ('session home DIFF', lambda i: any(k.endswith('session home') and v['status'] == 'DIFF' for k, v in i.items())),
    'home-root-mode': ('session home DIFF', lambda i: any(k.endswith('session home') and v['status'] == 'DIFF' for k, v in i.items())),
}


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
