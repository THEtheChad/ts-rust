#!/usr/bin/env python3
"""LSP tier of the parity gate: vendor/lspdiff sessions on tsc-rs against cached Go LSP sessions.

usage: parity_lsp.py --rs BIN --out DIR [--go BIN] [--tier quick|full] [--only REGEX] [--jobs 4]
                     [--js TSSERVER | --no-js] [--js-all]

Standard sessions (lspdiff's session.mjs; project, files, positions): 12 files and 6 positions per
file (lspdiff's defaults are 6 and 4) on umami and ts-pattern, and in full also on hono. Watch
scenarios (scenario.mjs; the client advertises dynamic didChangeWatchedFiles registration and
refresh support, and registrations, pushed diagnostics and every other server message are compared
at checkpoints): fsevents, fsevents-narrow and pkgjson-overlay (the watch-small fixture, written
fresh into ~/.cache/perf-rearch/parity/scratch/lsp-<session> before each server run), ata-overlay
(the ata-small fixture with ATA on against the fixture's typings store), watch-ts-pattern-f12 and unsaved-ts-pattern; full adds watch-umami-f12, watch-narrow-umami-f12 (the
workspace folder is umami/src), watch-hono-f12 and second-project-query. Up to --jobs sessions run
side by side. quick's
sessions are all full sessions, so a quick run compares with a full base.

Go's four sessions per item set (go.jsonl to go4.jsonl with their session results, GO_RUNS) are
kept in ~/.cache/perf-rearch/parity/go-cache/<go sha>/lsp/<session>-<key>/ and reused, so a run starts
only tsc-rs. The key (session_material) is the sha of what the session reads and how it runs: the
session's definition (project, files, positions, scenario), the project's path and the content of
its snapshot (parity_inputs.Inputs), for scenarios with ATA on the frozen typings store also the
store's content hash (ata_typings.py), the lspdiff sources that script the session
(SESSION_SOURCES: the stand-in npm and the server environment of sandbox.mjs among them), every
lspdiff setting (vendor/lspdiff/settings.mjs, read with `lspdiff.mjs info`: the settle and quiet
waits, the request and shutdown timeouts, the ATA idle window, npm delay and deadline), the node
that runs lspdiff, the stand-in npm and JS (node_identity), the servers' private home path
(SANDBOX_HOME) and GO_RUNS. The cache dir's key.json holds that material, and a dir whose key.json is
other material is not read. New Go and JS logs go to a staging dir (lspdiff --go-stage/--js-stage)
and are published only when the tier verified its inputs clean; lspdiff's settings.json must equal
the keyed settings. Standard sessions run with ATA off (vendor/lspdiff client.mjs initializeParams).

Inputs (lanes H4, H5 and H8): every project and the frozen typings store are read-only snapshots, made
before the first session and watched with inotify while the tier runs. lspdiff runs in a user, mount
and network namespace (no network) with each snapshot bound read-only at its own path (so the file
texts it sends are the snapshot's), and every server process (Go, tsc-rs, JS) in the sandbox the api
tier uses (parity_sandbox_init.py, under vendor/lspdiff/sandbox.mjs, LSPDIFF_SANDBOX; lane H8): PID 1
of its own user, mount, network and PID namespace, the host's state dirs covered by empty tmpfs dirs
(so the real dirs a server could read outside its keyed inputs, the shared typings cache
~/.cache/typescript, npm's cache and config, ~/.config, do not exist for it, not even for stat), the
whole tree read-only but a fresh overlay of the project and a private home, new for every process with
only its skeleton, at SANDBOX_HOME; no privilege, no fd but 0, 1 and 2, nothing of this environment.
A server that writes into its project fails its session ("session end": DIFF with rsFailure input for
tsc-rs), one that leaves a process behind or holds its output open fails it too, and what it writes
into its home is compared with Go's (item "session home"). ATA's typings cache is in the private home,
so every run starts it empty and fills it from the store. A server that cannot be started (a sandbox or
exec failure) makes the session ERROR, and nothing of it is cached. A change to a snapshot while the
tier runs makes its sessions ERROR and discards the staged logs. SANDBOX_HOME must still be empty when
the tier ends. In full, tsc-rs runs three times: twice as is and once with
GOPORT_PARSE_THREADS=1. An item is judged in this order (docs/perf-rearch/parity.md, "JS tsserver as
the LSP referee"):
1. tsc-rs determinism: a tsc-rs run that crashed or timed out, or an answer on which a later tsc-rs
   run differs from the first, is a DIFF (detail "tsc-rs: ...", rs-nondet or rs-schedule), even
   when one run matches Go.
2. GO-BROKEN: a Go run crashed or timed out before the answer (vendor/lspdiff client.mjs: no answer
   in LSPDIFF_REQUEST_TIMEOUT_MS, default 300 s), or Go's own runs differ on it (Go's references
   order and scenario registrations race across its goroutines). The last case is the old
   GO-UNSTABLE status, which the lsp tier no longer reports. A Go crash is cached, a timeout is not.
3. JS-AGREES: a DIFF of a kind JS referees (referee.mjs REFEREED_KINDS), on which JS tsserver 6.0.3
   (tsserver.mjs; --js, default ~/.cache/perf-rearch/js-tsc) sides with tsc-rs on every point where
   tsc-rs differs from Go. JS replays the session only when the session has such a DIFF; its log is
   cached in ~/.cache/perf-rearch/parity/js-cache/<tsserver identity>/lsp/<session>-<key>/ (the same
   key, with tsserver.mjs among the sources; parity_referees.js_identity: the whole typescript
   6.0.3 package).
   With one tsc-rs run (quick), an answer that would be GO-BROKEN or JS-AGREES needs one more tsc-rs
   run (rs-confirm) that gives it again; otherwise it is a DIFF rs-nondet.
4. Otherwise DIFF (the item's js.reason says why JS did not decide, when it ran).
--js-all runs JS on every session: it records JS's view of every item that is not MATCH (also
GO-BROKEN ones, for the report) and the calibration (per kind: on how many answers where tsc-rs
equals Go JS's normalized answer equals Go's too) in results.json "jsCalibration".
Items: lsp/<session>/<index> <step> (index: the answer's place in the session; the initialize answer
and the home come after the last answer, lane H8). Status MATCH,
GO-BROKEN, JS-AGREES, DIFF; a session that fails is one ERROR item. Output:
DIR/results.json and DIR/<session>/ (lspdiff's files: mismatches.json, summary.json, rs.jsonl,
js.json, js.jsonl when JS ran).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
import ata_typings  # noqa: E402
import parity_inputs as inputs  # noqa: E402
import parity_modes as modes  # noqa: E402
import parity_referees as referees  # noqa: E402
import parity_sandbox_init as sandbox_init  # noqa: E402
import parity_watch  # noqa: E402

HERE = Path(__file__).resolve().parent
HOME = Path.home()
PROJECTS = {
    'umami': ['~/.cache/perf-rearch/projects/umami', '~/.cache/lspdiff/projects/umami'],
    'ts-pattern': ['~/.cache/perf-rearch/parity-projects/repos/gvergnaud__ts-pattern'],
    'hono': ['~/.cache/perf-rearch/parity-projects/repos/honojs__hono'],
    'query': ['~/.cache/perf-rearch/projects/query', '~/.cache/perf-rearch/parity-projects/repos/TanStack__query'],
}
FIXTURE = None  # a scenario on a fixture: its project dir is the scratch dir


def std(project):
    return {'name': f'{project}-f12-p6', 'project': project, 'files': 12, 'positions': 6}


def scen(name, scenario, project=FIXTURE, files=12):
    return {'name': name, 'project': project, 'files': files, 'scenario': scenario}


QUICK = [std('umami'), std('ts-pattern'),
         scen('fsevents', 'fsevents'), scen('fsevents-narrow', 'fsevents-narrow'), scen('pkgjson-overlay', 'pkgjson-overlay'),
         scen('watch-ts-pattern-f12', 'watch', 'ts-pattern'), scen('unsaved-ts-pattern', 'unsaved', 'ts-pattern'),
         scen('ata-overlay', 'ata-overlay')]
FULL = QUICK[:2] + [std('hono')] + QUICK[2:] + [
    scen('watch-umami-f12', 'watch', 'umami'), scen('watch-narrow-umami-f12', 'watch-narrow', 'umami'),
    scen('watch-hono-f12', 'watch', 'hono'),
    scen('second-project-query', 'second-project', 'query')]


def sessions(tier):
    """The sessions of a tier: every one for full; for quick, the quick manifest's (it may name only
    sessions that full runs too, so a quick run compares with a full base)."""
    if tier == 'full':
        return FULL
    names = modes.working_manifest().lsp
    unknown = names - {s['name'] for s in FULL}
    if unknown:
        sys.exit(f'parity_lsp: the quick manifest names sessions that do not exist: {sorted(unknown)}')
    return [s for s in FULL if s['name'] in names]
# The tsc-rs runs per session: lspdiff options. full: two plain runs (rs-nondet) and one with a
# single parse worker (rs-schedule).
# Go sessions per item set. Go's own answers vary between runs on some items (hono `references`
# order: ls/crossproject.go handleCrossProject searches projects in goroutines; scenario watch
# registrations), so every session runs Go four times, and eight on multi-project workspaces (hono,
# query), where the cross-project search races, and an answer any two runs disagree on is
# GO-BROKEN. Part of the cache key, so caches filled with fewer runs are not reused (their answers
# still count: accumulated Go evidence, below).
GO_RUNS = 4
MULTI_PROJECT = {'hono': 8, 'query': 8}


def go_runs(sess):
    return MULTI_PROJECT.get(sess['project'], GO_RUNS)


# Accumulated Go evidence (root's decision after H4): any answer the pinned Go binary has given for the
# same inputs and settings is a Go answer. variants_material is a session's key material without the
# run count and the log format version, with the Go binary's full sha; the record of every Go answer
# variant for it (lspdiff --go-variants) is go-cache/<go sha>/lsp-variants/<session>-<sha>.json,
# staged and published like the logs, and only ever grows. Prior Go logs of the same material: the
# other cache dirs of the session whose key.json is this material at another run count (lspdiff uses
# each only with a clean session result beside it). Nothing of another binary, input, source or
# setting is merged, and nothing a harness before H4 made (round 3: its runs did not verify their
# inputs, so a key match alone is no evidence). Version 2: per-variant provenance; the version-1
# records, which held the base's imported H3 answers, are not read.
VARIANTS_VERSION = 2


def variants_material(material, go_sha):
    return {**{k: v for k, v in material.items() if k not in ('extra', 'version')}, 'go': go_sha, 'variants': VARIANTS_VERSION}


def refill_dirs(refills, material):
    """The extra Go fills of a key (--go-refill), each with this exact material in its key.json."""
    out = []
    for d in sorted(refills.glob('*')) if refills.is_dir() else []:
        try:
            if json.loads((d / 'key.json').read_text()) == json.loads(json.dumps(material)):
                out.append(d)
        except (OSError, ValueError):
            continue
    return out


def same_but_runs(a, b):
    strip = lambda m: {k: v for k, v in m.items() if k != 'extra'}  # noqa: E731
    return strip(a) == strip(b)


def prior_dirs(cache_root, name, key, material):
    """The other Go cache dirs of this session whose key.json is this material at another run count."""
    out = []
    for d in sorted(cache_root.glob(f'{name}-*')):
        if d.name == key or d.name.count('-') != key.count('-'):
            continue
        try:
            other = json.loads((d / 'key.json').read_text())
        except (OSError, ValueError):
            continue
        if same_but_runs(other, json.loads(json.dumps(material))):
            out.append(d)
    return out





RS_RUNS = {'quick': [], 'full': ['--rs-runs', '2', '--rs-env', 'GOPORT_PARSE_THREADS=1']}
JS_TSSERVER = HOME / '.cache/perf-rearch/js-tsc/node_modules/typescript/lib/tsserver.js'
# The lspdiff sources that script a session, so Go's (and JS's) answers depend on them; the diff and
# referee code (lspdiff.mjs, referee.mjs) and the bench scripts do not change a cached answer.
# The sandbox's init (shared with the api tier) is one of them.
SESSION_SOURCES = ('client.mjs', 'fixtures.mjs', 'json.mjs', 'project.mjs', 'scenario.mjs', 'session.mjs', 'sandbox.mjs', 'settings.mjs',
                   'ata-npm/npm', 'ata-npm/fake-npm.mjs', '../../parity_sandbox_init.py')
# Bump when the form of the cached logs changes (2: lane H4, session results, snapshots; 3: lane H5, the
# hermetic servers: private home, frozen typings store, no network; 4: lane H8, the sealed sandbox, the
# initialize answer in the log and the home in the session result).
CACHE_VERSION = 4


def scenario_sources_sha(extra=()):
    h = hashlib.sha256()
    for name in sorted(SESSION_SOURCES + tuple(extra)):
        f = HERE / 'vendor/lspdiff' / name
        h.update(name.encode() + b'\0' + f.read_bytes())
    return h.hexdigest()[:12]


LSPDIFF = HERE / 'vendor/lspdiff/lspdiff.mjs'
# The servers' private home (sandbox.mjs): where every server process sees its own new home, so the
# typings location (os.UserCacheDir()/typescript/7.1, in some answers) has the same path in every run.
SANDBOX_HOME = HOME / '.cache/perf-rearch/parity/lsp-home'


# lspdiff in a user, mount and network namespace with each snapshot bound read-only at its path: $1
# snapshot, $2 path, then the next pair, then the command.
def outer(binds):
    script = [f'mount --bind "${{{2 * k + 1}}}" "${{{2 * k + 2}}}" && mount -o remount,bind,ro "${{{2 * k + 2}}}"' for k in range(len(binds))]
    return ['unshare', '-rmn', '--', 'sh', '-c', ' && '.join([*script, f'shift {2 * len(binds)}', 'exec "$@"']), 'outer',
            *[x for b in binds for x in b]]


# The names a server looks up in every ancestor dir of its project and of its typings location (module
# resolution and the default typeRoots walk up to /, the config search, Yarn PnP): not in any snapshot.
# Their state (absent, or their content hash) is in the key of every session (ancestors_state), and the
# ancestor dirs are watched for them while the tier runs (any create, delete or change: ERROR).
ANCESTOR_NAMES = ('node_modules', 'package.json', 'tsconfig.json', 'jsconfig.json', '.pnp.cjs', '.pnp.js', 'bower_components')


def ancestor_dirs(*paths):
    """The dirs above paths that a server sees from the host: the sandbox's hidden dirs are empty tmpfs dirs
    there (parity_sandbox_init.py HIDDEN), so the dirs in them are not looked up on the host."""
    out = {'/'}
    for p in paths:
        p = Path(os.path.realpath(p))
        out.update(str(a) for a in p.parents if not sandbox_init.hidden(str(a)))
    return sorted(out)


def ancestors_state(dirs):
    """{dir/name: content hash} of every ANCESTOR_NAMES entry in dirs (an absent one is not listed)."""
    out = {}
    for d in dirs:
        for n in ANCESTOR_NAMES:
            p = os.path.join(d, n)
            if os.path.isdir(p) and not os.path.islink(p):
                out[p] = inputs.tree_hash(p)
            elif os.path.lexists(p):
                out[p] = inputs._file_sha(p) if os.path.isfile(p) and not os.path.islink(p) else f'link {os.readlink(p)}'
    return out


def node_identity():
    """The node on PATH that runs lspdiff, the stand-in npm and JS tsserver: realpath, version, sha256."""
    node = shutil.which('node')
    v = subprocess.run([node, '--version'], capture_output=True, text=True, check=True)
    return {'path': os.path.realpath(node), 'version': v.stdout.strip(), 'sha': sha256_file(os.path.realpath(node))[:16]}


def lspdiff_info():
    """lspdiff's effective settings and scenarios (`lspdiff.mjs info`), as the sessions will see them."""
    r = subprocess.run(['node', str(LSPDIFF), 'info'], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def uses_frozen_typings(sess, info):
    sc = info['scenarios'].get(sess.get('scenario') or '', {})
    return bool(sess.get('scenario')) and sc.get('ata') and sc.get('typings') == 'frozen'


def session_material(sess, root, project_snap, typings, sources_sha, info, node, extra=(), ancestors=None):
    """The key material of a session's Go (or JS) logs (see the top comment). A fixture session's root
    is its scratch dir, rewritten from fixtures.mjs (in the sources) before every server run, so it has
    no snapshot. typings: the frozen store's content hash, for the sessions whose ATA installs from it."""
    material = {'version': CACHE_VERSION, 'session': sess, 'root': str(root), 'sources': sources_sha,
                'settings': info['settings'], 'node': node, 'home': str(SANDBOX_HOME), 'ancestors': ancestors or {},
                'extra': list(extra)}
    if project_snap is not None:
        material.update(real=project_snap.label, tree=project_snap.hash)
    if typings is not None:
        material.update(typings=typings)
    return material


def key_dir(cache, material, stage):
    """The cache dir for lspdiff to read, when its key.json holds this material (else None), and the
    staging dir it writes to (with key.json)."""
    staged = stage.stage_dir(cache)
    staged.mkdir(parents=True, exist_ok=True)
    (staged / 'key.json').write_text(json.dumps(material, sort_keys=True))
    try:
        ok = json.loads((cache / 'key.json').read_text()) == json.loads(json.dumps(material))
    except (OSError, ValueError):
        ok = False
    return (cache if ok else None), staged


def _snapshot_error(snaps, root):
    try:
        snaps.of(root)
    except inputs.SnapshotError as e:
        return str(e)
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rs', required=True)
    ap.add_argument('--go', default=str(HOME / '.local/bin/tsgo-lsp-oracle-3e9236361fa9'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--tier', default='full', choices=('quick', 'full'))
    ap.add_argument('--only', default='')
    ap.add_argument('--jobs', type=int, default=4, help='sessions run side by side (default 4)')
    ap.add_argument('--js', default=str(JS_TSSERVER), help='JS tsserver.js, the JS-AGREES referee')
    ap.add_argument('--no-js', action='store_true', help='no JS referee (statuses MATCH, GO-BROKEN, DIFF)')
    ap.add_argument('--js-all', action='store_true', help='run JS on every session: calibration and the report')
    ap.add_argument('--ata-manifest', default=str(ata_typings.MANIFEST),
                    help='the frozen typings store, by its manifest (default the committed ata-typings.json)')
    ap.add_argument('--go-refill', help='sessions (regex) whose Go runs run again into one more fill '
                    '(go-cache/<go sha>/lsp-refills/<key>/<run>/; its clean runs join the variant record)')
    args = ap.parse_args()
    use_js = not args.no_js and Path(args.js).exists()
    js_error = None
    if use_js:
        try:
            js_sha = referees.js_identity(args.js)
        except (OSError, ValueError) as e:  # no JS 6.0.3: no JS referee
            use_js, js_error = False, str(e)
            print(f'lsp: JS referee unavailable: {js_error}', flush=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    go_sha_full = sha256_file(args.go)
    go_sha = go_sha_full[:16]
    items, sessions_info = {}, {}
    t0 = time.monotonic()
    src_sha = scenario_sources_sha()
    js_src_sha = scenario_sources_sha(('tsserver.mjs',))
    js_sha = js_sha if use_js else None
    info = lspdiff_info()
    node = node_identity()
    SANDBOX_HOME.mkdir(parents=True, exist_ok=True)
    if any(SANDBOX_HOME.iterdir()):
        sys.exit(f'parity_lsp: {SANDBOX_HOME} is not empty: every server binds its own home over it')
    stage = inputs.Stage('lsp')
    snaps = inputs.Inputs()
    chosen = [sess for sess in sessions(args.tier) if re.search(args.only, sess['name'])]
    roots = {}
    for sess in chosen:
        if sess['project'] is FIXTURE:
            roots[sess['name']] = HOME / f'.cache/perf-rearch/parity/scratch/lsp-{sess["name"]}'
        else:
            roots[sess['name']] = next((Path(os.path.expanduser(d)) for d in PROJECTS[sess['project']]
                                        if Path(os.path.expanduser(d)).is_dir()), None)
    need = {str(r) for name, r in roots.items() if r is not None and next(s for s in chosen if s['name'] == name)['project'] is not FIXTURE}
    store, store_error = None, None
    if any(uses_frozen_typings(sess, info) for sess in chosen):
        try:
            manifest = ata_typings.read_manifest(args.ata_manifest)
            store = ata_typings.store_dir(manifest)
            need.add(str(store))
        except (OSError, ValueError) as e:
            store_error = f'no typings manifest: {e}'
    snap_start = time.monotonic()
    with ThreadPoolExecutor(4) as pool:  # every snapshot before the first session, then the watch
        errs = dict(zip(sorted(need), pool.map(lambda r: _snapshot_error(snaps, r), sorted(need))))
    snaps.watch()
    up_dirs = ancestor_dirs(SANDBOX_HOME / '.cache/typescript/7.1', *[r for r in roots.values() if r is not None])
    up_watch = parity_watch.Watch(up_dirs, keep=lambda p: os.path.basename(p) in ANCESTOR_NAMES, recursive=False)
    up_state = ancestors_state(up_dirs)
    snap_secs = round(time.monotonic() - snap_start, 1)
    print(f'lsp: {len(snaps.snaps)} snapshots in {snap_secs}s; {snaps.watch_error or "watched with inotify"}', flush=True)
    if store is not None and str(store) in snaps.snaps:  # the snapshot must be the manifest's content
        got = ata_typings.content_sha(snaps.snaps[str(store)].path)
        if got != manifest['sha']:
            store_error = f'the typings store {store} has hash {got[:16]}, its manifest {manifest["sha"][:16]}'
    elif store is not None:
        store_error = errs.get(str(store)) or f'no snapshot of {store}'
    if store is not None:
        print(f'lsp: typings store {manifest["sha"][:16]} (types-registry {manifest["registry"]}): {store_error or "verified"}', flush=True)

    def run_one(sess):
        """One session: (its items, its summary, the line to print)."""
        name, project, scenario = sess['name'], sess['project'], sess.get('scenario')
        root = roots[name]
        if root is None:
            return {f'lsp/{name}': {'status': 'SKIP', 'detail': 'project not prepared'}}, None, f'lsp {name}: SKIP'
        project_snap = typings = None
        binds, mounts = [], []
        frozen = uses_frozen_typings(sess, info)
        if frozen and store_error:
            return {f'lsp/{name}': {'status': 'ERROR', 'detail': store_error}}, None, f'lsp {name}: ERROR {store_error}'
        try:
            if project is not FIXTURE:
                project_snap = snaps.of(root)
                binds.append((str(project_snap.path), project_snap.label))
                mounts.append({'lower': str(project_snap.path), 'target': project_snap.label})
            if frozen:
                store_snap = snaps.of(store)
                binds.append((str(store_snap.path), store_snap.label))
                typings = manifest['sha']
        except inputs.SnapshotError as e:
            return {f'lsp/{name}': {'status': 'ERROR', 'detail': f'no snapshot: {e}'}}, None, f'lsp {name}: ERROR {e}'
        runs_go = go_runs(sess)
        material = session_material(sess, root, project_snap, typings, src_sha, info, node, [f'g{runs_go}'], up_state)
        js_material = session_material(sess, root, project_snap, typings, js_src_sha, info, node, (), up_state)
        key = f'{name}-{inputs.json_hash(material)[:24]}'
        js_key = f'{name}-{inputs.json_hash(js_material)[:24]}'
        go_dir = HOME / f'.cache/perf-rearch/parity/go-cache/{go_sha}'
        go_cache, go_stage = key_dir(go_dir / 'lsp' / key, material, stage)
        refills = go_dir / 'lsp-refills' / key
        go_final = go_dir / 'lsp' / key
        if args.go_refill and re.search(args.go_refill, name):  # one more Go fill, kept beside the others
            go_final = refills / stage.dir.name
            go_cache, go_stage = None, key_dir(go_final, material, stage)[1]
        vmat = variants_material(material, go_sha_full)
        vfile = go_dir / 'lsp-variants' / f'{name}-{inputs.json_hash(vmat)[:24]}.json'
        priors = [f for d in [*prior_dirs(go_dir / 'lsp', name, key, material), *refill_dirs(refills, material)]
                  for f in sorted(d.glob('go*.jsonl'))]
        sout = out / name
        sandbox = {'scratch': str(HOME / f'.cache/perf-rearch/parity/scratch/lsp-sandbox/{name}'), 'home': str(SANDBOX_HOME),
                   'mounts': mounts}
        # A heap above Node's default: umami's 7 logs are 340 MB each, and JS adds one.
        cmd = [*outer(binds), 'node', '--max-old-space-size=12288', str(LSPDIFF), 'diff', '--project', str(root),
               '--files', str(sess['files']), '--go', args.go, '--rs', args.rs, '--out', str(sout),
               '--go-stage', str(go_stage), *(['--go-cache', str(go_cache)] if go_cache else []), *RS_RUNS[args.tier]]
        cmd += ['--go-runs', str(runs_go), '--go-variants', str(stage.staged(vfile) if stage.staged(vfile).exists() else vfile),
                '--go-variants-out', str(stage.staged(vfile)), '--go-variants-material', json.dumps(vmat, sort_keys=True)]
        for f in priors:
            cmd += ['--go-prior', str(f)]
        cmd += ['--fill-id', f'{stage.dir.name}/{name}', '--go-final', str(go_final)]
        if use_js:
            js_cache, js_stage = key_dir(HOME / f'.cache/perf-rearch/parity/js-cache/{js_sha}/lsp/{js_key}', js_material, stage)
            cmd += ['--js', args.js, '--js-stage', str(js_stage), *(['--js-cache', str(js_cache)] if js_cache else [])]
            cmd += ['--js-all'] if args.js_all else []
        cmd += ['--scenario', scenario] if scenario else ['--positions', str(sess['positions'])]
        cmd += ['--ata-store', str(store)] if frozen else []
        start = time.monotonic()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env={**os.environ, 'LSPDIFF_SANDBOX': json.dumps(sandbox)})
        secs = round(time.monotonic() - start, 1)
        (out / f'{name}.log').write_text(r.stdout + r.stderr)
        produced = sout / f'{root.name}-diff{"-" + scenario if scenario else ""}' / 'items.json'
        if r.returncode or not produced.exists():
            return ({f'lsp/{name}': {'status': 'ERROR', 'detail': f'lspdiff exit {r.returncode}: {r.stderr[-300:]}'}},
                    {'secs': secs, 'error': True, 'root': str(root)}, f'lsp {name}: ERROR (exit {r.returncode}) {secs}s')
        used = json.loads((produced.parent / 'settings.json').read_text())
        if used != info['settings']:
            return ({f'lsp/{name}': {'status': 'ERROR', 'detail': f'lspdiff ran with other settings than its key: {used}'}},
                    {'secs': secs, 'error': True, 'root': str(root)}, f'lsp {name}: ERROR (settings)')
        rows = json.loads(produced.read_text())
        got = {}
        for row in rows:
            item = {'status': row['status'], 'method': row['method']}
            if row.get('detail'):
                item['detail'] = row['detail']
            if row.get('rsFailure'):
                item['rsFailure'] = row['rsFailure']
            if row.get('js'):
                item['referees'] = {'js': row['js']}
            got[f'lsp/{name}/{row["i"]:04d} {row["step"]}'] = item
        c = Counter(v['status'] for v in got.values())
        js_file = produced.parent / 'js.json'
        js_info = json.loads(js_file.read_text()) if js_file.exists() else {}
        runs = dict(re.findall(r'^(go\d*|rs[\w-]*)(?: \(.*\))?: \d+ answers in ([\d.]+) s', r.stdout, re.M))
        runs.update(re.findall(r'^(js|rs-confirm): \d+ answers in ([\d.]+) s', r.stdout, re.M))
        sinfo = {'secs': secs, 'answers': len(rows), 'counts': dict(c), 'root': str(root), 'key': key, 'goRuns': runs_go,
                 'variants': vfile.name, 'priors': [str(f) for f in priors],
                 'typings': typings, 'runSecs': {k: float(v) for k, v in runs.items()}}
        if js_info.get('ran'):
            sinfo['js'] = {'calibration': js_info.get('calibration', {}), 'decided': js_info.get('decided', 0),
                           'cached': bool(re.search(r'^js: \d+ answers from ', r.stdout, re.M))}
        return got, sinfo, f'lsp {name}: {dict(c)} {secs}s'

    # Sessions run side by side (each server run is its own process; fixture sessions have their own
    # scratch dirs). Most of a scenario's time is the settle wait at its checkpoints.
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, sess): sess['name'] for sess in chosen}
        for f in as_completed(futures):
            got, sinfo, line = f.result()
            items.update(got)
            if sinfo is not None:
                sessions_info[futures[f]] = sinfo
            print(line, flush=True)
    verify_start = time.monotonic()
    changed = snaps.verify()
    up_events = up_watch.all_events()
    up_watch.close()
    if up_events or ancestors_state(up_dirs) != up_state:  # a lookup of every session saw another state than its key
        why = f'an ancestor dir changed while the tier ran: {(up_events or ["state"])[0]}'
        changed['ancestors'] = why
        for k in [k for k in items if k.startswith('lsp/')]:
            items[k] = {'status': 'ERROR', 'detail': why, 'was': items[k]}
    verify_secs = round(time.monotonic() - verify_start, 1)
    for sess in chosen:
        sinfo = sessions_info.get(sess['name'], {})
        why = next((w for r, w in changed.items() if r == sinfo.get('root') or (sinfo.get('typings') and r == str(store))), None)
        if why:
            for k in [k for k in items if k.startswith(f'lsp/{sess["name"]}/')]:
                items[k] = {'status': 'ERROR', 'detail': why, 'was': items[k]}
            print(f'lsp {sess["name"]}: ERROR {why}', flush=True)
    for root, err in errs.items():
        if err:
            print(f'lsp: no snapshot of {root}: {err}', flush=True)
    if any(SANDBOX_HOME.iterdir()):  # a server wrote there without its own home over it
        changed[str(SANDBOX_HOME)] = f'{SANDBOX_HOME} is not empty after the tier: {sorted(p.name for p in SANDBOX_HOME.iterdir())[:5]}'
        for k in [k for k in items if k.startswith('lsp/')]:
            items[k] = {'status': 'ERROR', 'detail': changed[str(SANDBOX_HOME)], 'was': items[k]}
    if changed:
        print(f'lsp: inputs changed ({len(changed)} roots): {stage.discard()} staged files discarded', flush=True)
    else:
        print(f'lsp: {stage.publish()} cache files published', flush=True)
    counts = Counter(v['status'] for v in items.values())
    summary = {'tier': args.tier, 'rs': args.rs, 'goSha': go_sha, 'counts': dict(counts), 'sessions': sessions_info,
               'inputs': snaps.hashes, 'node': node, 'ancestors': up_state, 'typingsStore': (manifest['sha'] if store is not None else None), 'snapshots': {r: x.as_json() for r, x in snaps.snaps.items()},
               'watch': snaps.watch_error or 'inotify', 'changed': changed, 'settings': info['settings'],
               'secs': {'snapshots': snap_secs, 'verify': verify_secs},
               **({'jsError': js_error} if js_error else {}), 'wallSecs': round(time.monotonic() - t0, 1)}
    if use_js:
        summary['jsSha'] = js_sha
        calib = {}
        for sinfo in sessions_info.values():
            for method, c in sinfo.get('js', {}).get('calibration', {}).items():
                acc = calib.setdefault(method, {'goEqual': 0, 'jsEqual': 0, 'noJs': 0})
                for k in acc:
                    acc[k] += c.get(k, 0)
        if calib:
            summary['jsCalibration'] = calib
            for method, c in sorted(calib.items()):
                print(f'js calibration {method}: {c["jsEqual"]}/{c["goEqual"]} '
                      f'({100 * c["jsEqual"] / max(c["goEqual"], 1):.1f}%){" no JS answer " + str(c["noJs"]) if c["noJs"] else ""}')
    for k, v in sorted(items.items()):
        if v['status'] == 'JS-AGREES' or (args.js_all and v.get('referees', {}).get('js') and v['status'] != 'MATCH'):
            js = v['referees']['js']
            if 'equalsRs' in js:
                said = (f'normalized, JS equals tsc-rs: {js["equalsRs"]}; JS equals Go runs: {",".join(js["equalsGoRuns"]) or "none"}; '
                        f'tsc-rs equals Go runs: {",".join(js["rsEqualsGoRuns"]) or "none"}')
            else:
                said = 'agrees' if js.get('agrees') else js.get('reason', '')
            print(f'js {v["status"]:<11} {k}: {said}'[:400])
    summary['items'] = dict(sorted(items.items()))
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'lsp: {dict(counts)} in {summary["wallSecs"]}s')


if __name__ == '__main__':
    main()
