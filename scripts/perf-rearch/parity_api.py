#!/usr/bin/env python3
"""API tier of the parity gate (lane H7): scripted `tsgo --api --async` sessions on tsc-rs against cached
Go sessions, every answer compared byte for byte.

usage: parity_api.py --rs BIN --out DIR [--go BIN] [--tier quick|full] [--only REGEX] [--jobs 8]
       parity_api.py probe BIN SCENARIO     one session in the sandbox; prints its comparable transcript
                                            (SCENARIO: a name in parity-api-scenarios.json, or a JSON file
                                            holding one scenario). Diff two probes to reproduce an item:
                                            diff <(parity_api.py probe GO x) <(parity_api.py probe RS x)
       parity_api.py list [quick|full]      the scenarios a tier runs
       parity_api.py key SCENARIO [--go BIN]   a scenario's key material (what its Go cache entry holds)

An API session is a client the CLI, LSP, fuzz and corpus tiers never are: it gives the server its own
filesystem (createSnapshot fileSystem) and its own case setting (--useCaseSensitiveFileNames), so
output paths, common directories and the overwrite checks run on another host than the OS's. Lane B3
found two output differences there (docs/perf-rearch/audits/b3-prepin.md, D1 and D2).

Scenarios (parity-api-scenarios.json; parity_api_session.py has the format): the createSnapshot
filesystem is always kind "full" (canonical and total: the server reads no disk for it), or a disk
fixture mounted as an overlay for the requests that use the host filesystem (the build orchestrator).
Each session: initialize, createSnapshot, per project getSourceFileNames, getConfigFileNames, every
diagnostics method, emitToString and emit (with a full filesystem emit returns the contents and writes
nothing), and the scenario's build steps. Compared: every request as sent and its raw answer (result,
or error code and message), the exit code, whether the server exited by itself, the sha256 of stderr,
the files the session wrote to its fixture (an overlay upper dir: an overwritten or deleted input
shows there), and what it wrote to its private home. Items: api/<scenario>, one per scenario; the
detail names the first difference, and DIR/<scenario>/ holds go.json, rs.json and, when not MATCH,
diff.txt.

Hermetic (H5's design; parity_api_session.py): every server process runs in its own user, mount and
network namespace, with a private home that is empty and new for each process at SANDBOX_HOME, the
environment of sandbox.mjs (PATH=/usr/bin:/bin, nothing of this environment), and cwd /. Tripwires
(parity_lsp.GUARDED): ~/.cache/typescript, ~/.npm and ~/.config are each covered by a read-only bait dir
that inotify watches for every access, reads included, while the scenario runs; any access makes the
item ERROR and discards every Go entry the tier staged. Fixtures are content-addressed read-only copies
(STATE/snapshots/api/<hash>/tree, files with a fixed old mtime), checked against the scenario before the
tier and watched while it runs; the ancestor dirs of every root are keyed and watched for the names
module resolution looks up (parity_lsp.ANCESTOR_NAMES). A change to either makes the items ERROR.
The in-memory roots (VIRTUAL) must not exist on disk.

Key (material): the scenario as expanded (its filesystem, options, requests, build steps, the case
flag), its root, the fixture's content hash, the session driver's source (parity_api_session.py: the
sandbox, the protocol client, the requests, the timeouts), the protocol (PROTOCOL), the private home
path, the ancestors' state, the Go binary's sha256, GO_RUNS and CACHE_VERSION. Go's sessions are cached
in ~/.cache/perf-rearch/parity/go-cache/<go sha>/api/<scenario>-<key>.json with that material, staged
(parity_inputs.Stage) and published only when the tier ends with its inputs verified clean. A Go
timeout is not cached.

Judged in the order of the other tiers (docs/perf-rearch/parity.md, "Referees"):
  1. tsc-rs: a session that crashed (exit not 0, killed, a crash line on stderr, output closed before an
     answer), timed out (REQUEST_TIMEOUT per request) or wrote malformed frames is a DIFF with rsFailure
     (crash or timeout; malformed counts as crash); two tsc-rs runs that differ are a DIFF rs-nondet
     (full runs tsc-rs twice);
  2. GO-BROKEN: a Go run crashed, timed out or wrote malformed frames, or Go's GO_RUNS runs differ, and
     tsc-rs is clean and stable (quick runs tsc-rs once more first: rs-confirm);
  3. MATCH: tsc-rs's transcript equals Go's first run;
  4. DIFF.
No GO-PARTITION: the partition rule is about the CLI's --checkers, and no scenario sets checkers. No
JS-AGREES: JS tsc 6.0.3 has no API server (no snapshots, projects, request filesystems, dynamic
^/~ts-uri~/ names or server case flag); its in-process compiler API (ts.createProgram with a
CompilerHost) is another surface, so it is no referee for these answers (summary jsError).
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
import parity_api_session as session  # noqa: E402
import parity_inputs as inputs  # noqa: E402
import parity_lsp  # noqa: E402
import parity_modes as modes  # noqa: E402
import parity_watch  # noqa: E402

HERE = Path(__file__).resolve().parent
HOME = Path.home()
STATE = session.STATE
SCENARIOS = HERE / 'parity-api-scenarios.json'
GO = HOME / '.local/bin/tsgo-oracle-3e9236361fa9'
GO_RUNS = 2
CACHE_VERSION = 1  # bump when the form of a cached Go session changes
RS_RUNS = {'quick': 1, 'full': 2}
FIXED_MTIME = session.FIXED_MTIME  # fixture inputs are always older than anything a build writes
JS_ERROR = ('no JS referee on the api tier: JS tsc 6.0.3 has no API server (snapshots, projects, request '
            'filesystems, ^/~ts-uri~/ names, the server case flag); its in-process compiler API is another surface')


class ArtifactError(ValueError):
    pass


def _sha16(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _expand_texts(value, texts):
    if isinstance(value, str):
        return texts.get(value, value)
    if isinstance(value, list):
        return [_expand_texts(v, texts) for v in value]
    if isinstance(value, dict):
        return {k: _expand_texts(v, texts) for k, v in value.items()}
    return value


def load_scenarios(path=SCENARIOS):
    """[scenario] of the scenarios file, each validated and with its shared texts expanded. Strict: a
    malformed file stops the tier (no scenario runs on a guess)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ArtifactError(f'{path}: {e}') from None
    if not isinstance(data, dict) or set(data) != {'about', 'version', 'texts', 'scenarios'} or data['version'] != 1:
        raise ArtifactError(f'{path}: the top level is exactly {{about, version: 1, texts, scenarios}}')
    texts = data['texts']
    if not all(re.match(r'^@[A-Z0-9_]+@$', k) and isinstance(v, str) for k, v in texts.items()):
        raise ArtifactError(f'{path}: texts maps @NAME@ to a string')
    out, seen = [], set()
    for sc in data['scenarios']:
        sc = _expand_texts(sc, texts)
        try:
            session.validate(sc)
        except session.ScenarioError as e:
            raise ArtifactError(f'{path}: {e}') from None
        if sc['name'] in seen:
            raise ArtifactError(f'{path}: {sc["name"]} is listed twice')
        seen.add(sc['name'])
        out.append(sc)
    return out


def scenarios(tier, path=SCENARIOS):
    """The scenarios of a tier: all for full; for quick, the quick manifest's (each must exist)."""
    every = load_scenarios(path)
    if tier == 'full':
        return every
    names = modes.working_manifest().api
    unknown = names - {s['name'] for s in every}
    if unknown:
        raise ArtifactError(f'the quick manifest names api scenarios that do not exist: {sorted(unknown)}')
    return [s for s in every if s['name'] in names]


SOURCES = (Path(session.__file__), session.INIT)  # what decides a Go answer: the session driver and the sandbox's init


def session_sha():
    h = hashlib.sha256()
    for f in SOURCES:
        h.update(f.name.encode() + b'\0' + f.read_bytes())
    return h.hexdigest()[:16]


def command_identity(cmd):
    """sha256 of a server command (a binary, or a command line such as a self-test's proxy): every word
    that names a file by its content, every other word as it is. A binary's identity is its own sha256,
    so the cache dir is the other tiers' go-cache/<go sha>/."""
    if os.path.isfile(str(cmd)):
        return parity_lsp.sha256_file(str(cmd))
    h = hashlib.sha256()
    for word in str(cmd).split(' '):
        h.update((parity_lsp.sha256_file(word) if os.path.isfile(word) else 'arg ' + word).encode() + b'\0')
    return h.hexdigest()


# ---- inputs ---------------------------------------------------------------------------------

def fixture_snapshot(sc):
    """(dir, content hash) of the scenario's disk fixture as a read-only content-addressed copy, made once
    and checked against the scenario every time (inputs.SnapshotError when it is not exactly that)."""
    files = sc['disk']
    h = inputs.json_hash(files)[:24]
    tree = STATE / 'snapshots/api' / f'{h}-m{session.FIXTURE_DIR_MODE:o}{session.FIXTURE_FILE_MODE:o}' / 'tree'
    if not tree.exists():
        tmp = tree.parent / f'tmp-{os.getpid()}-{time.monotonic_ns()}'
        tmp.mkdir(parents=True)
        for rel, v in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(v, dict):
                os.symlink(v['symlink'], p)
            else:
                p.write_text(v)
        for top, dirs, fs in os.walk(tmp, topdown=False):
            for x in fs + dirs:
                q = os.path.join(top, x)
                if not os.path.islink(q):
                    os.chmod(q, session.FIXTURE_DIR_MODE if os.path.isdir(q) else session.FIXTURE_FILE_MODE)
                os.utime(q, (FIXED_MTIME, FIXED_MTIME), follow_symlinks=False)
        os.chmod(tmp, session.FIXTURE_DIR_MODE)
        os.utime(tmp, (FIXED_MTIME, FIXED_MTIME))
        try:
            tmp.rename(tree)
        except OSError:  # another tier made it at the same time
            inputs.remove_tree(tmp)
    check_fixture(tree, files)
    return tree, h


def check_fixture(tree, files):
    """Raises inputs.SnapshotError unless tree holds exactly files (bytes, link targets, names, the fixture
    modes, the fixed mtime)."""
    seen = {}
    for top, dirs, fs in os.walk(tree):
        for x in dirs + fs:
            p = os.path.join(top, x)
            st = os.lstat(p)
            link = stat.S_ISLNK(st.st_mode)
            want = None if link else session.FIXTURE_DIR_MODE if stat.S_ISDIR(st.st_mode) else session.FIXTURE_FILE_MODE
            if (want is not None and stat.S_IMODE(st.st_mode) != want) or int(st.st_mtime) != FIXED_MTIME:
                raise inputs.SnapshotError(f'{p}: another mode or mtime than the fixture\'s')
            if link:
                seen[os.path.relpath(p, tree)] = {'symlink': os.readlink(p)}
            elif not stat.S_ISDIR(st.st_mode):
                seen[os.path.relpath(p, tree)] = Path(p).read_bytes() if stat.S_ISREG(st.st_mode) else None
    want = {k: v if isinstance(v, dict) else v.encode() for k, v in files.items()}
    if seen != want:
        raise inputs.SnapshotError(f'{tree} is not its scenario\'s fixture: {sorted(set(seen) ^ set(want))[:5] or "bytes differ"}')


def material(sc, fixture_hash, ancestors, go_sha):
    return {'version': CACHE_VERSION, 'scenario': sc, 'root': session.root_of(sc), 'fixture': fixture_hash,
            'session': session_sha(), 'protocol': session.PROTOCOL, 'home': str(session.SANDBOX_HOME),
            'virtualRootExists': os.path.lexists(session.VIRTUAL), 'ancestors': ancestors, 'go': go_sha, 'goRuns': GO_RUNS,
            'timeouts': [session.REQUEST_TIMEOUT, session.EXIT_TIMEOUT, session.EOF_TIMEOUT]}


# ---- comparing ------------------------------------------------------------------------------
# Every comparison is of canonical strings (canon): the answers are raw bytes, and everything else is
# JSON whose types survive json.dumps, so false is never 0 and "2.0" never "9.9".

def canon(value):
    return json.dumps(value, sort_keys=True)


def answers(t):
    """Every request as sent and its answer's raw message, and how the session ended."""
    return {'steps': [{k: s[k] for k in ('method', 'params', 'answer', 'failure') if k in s} for s in t['steps']],
            'exit': t['exit'], 'forced': t['forced'], 'survivors': t['survivors'], 'reaped': t.get('reaped'), 'failure': t['failure']}


def storage(t):
    """What the session left in its writable places after each request, and at its end."""
    return {'steps': [s.get('fs') for s in t['steps']], 'end': t['fs']}


def view(t):
    """Everything compared of a transcript."""
    return {**answers(t), 'stderr': t['stderr']['sha'], 'storage': storage(t)}


def same(a, b):
    return canon(view(a)) == canon(view(b))


def outcome(t):
    return {'timeout': 'timeout', 'crash': 'crash', 'malformed': 'malformed'}.get(t['failure'], f'exit {t["exit"]}')


def attempt(t, why):
    """A tsc-rs process as a grandfather entry pins it (parity_grandfather ATTEMPT, plus why and secs)."""
    return {'why': why, 'secs': t['secs'], 'outcome': outcome(t), 'code': 'timeout' if t['failure'] == 'timeout' else t['exit'],
            'stdout': _sha16(answers(t)), 'stderr': t['stderr']['sha'], 'stderrSite': t['stderr']['site'],
            'written': _sha16(storage(t)), 'inputs': 'intact'}


def go_evidence(t, status):
    """Go's first session as a grandfather entry pins it: stdout fingerprints everything of it the tier
    compares but its storage (the answers, how it ended and its stderr), written its storage."""
    return {'status': status or 'ok', 'outcome': outcome(t), 'panic': t['stderr']['crash'],
            'stdout': _sha16([answers(t), t['stderr']['sha']]), 'written': _sha16(storage(t))}


def first_difference(a, b, path=''):
    """(json path, a's value, b's value) of the first place two JSON values differ, types included."""
    if type(a) is not type(b):
        return path or '/', a, b
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f'{path}/{k}', a.get(k, '<absent>'), b.get(k, '<absent>')
            d = first_difference(a[k], b[k], f'{path}/{k}')
            if d:
                return d
        return None
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f'{path}/{i}')
            if d:
                return d
        return (f'{path}/{min(len(a), len(b))}', a[len(b)] if len(a) > len(b) else '<absent>',
                b[len(a)] if len(b) > len(a) else '<absent>') if len(a) != len(b) else None
    return None if a == b else (path or '/', a, b)


def _readable(v):
    """view() with every raw answer parsed, for the report (never for the comparison)."""
    v = json.loads(json.dumps(v))
    for s in v['steps']:
        if 'answer' in s:
            try:
                s['answer'] = json.loads(s['answer'])
            except ValueError:
                pass
    return v


def describe(go, rs):
    """Where tsc-rs's transcript first differs from Go's, in a line."""
    a, b = view(go), view(rs)
    if canon(a) == canon(b):
        return 'equal'
    d = first_difference(_readable(a), _readable(b)) or first_difference(a, b)
    path, g, r = d
    m = re.match(r'^/steps/(\d+)', path)
    if m:
        k = int(m[1])
        path = f'{(go["steps"] if k < len(go["steps"]) else rs["steps"])[k]["method"]} {path}'
    if path.split(' ')[-1].endswith('/answer') and json.dumps(g) == json.dumps(r):
        return f'{path}: the raw bytes differ (the same JSON value)'
    short = lambda v: json.dumps(v)[:160]  # noqa: E731
    return f'{path}: Go {short(g)}, tsc-rs {short(r)}'


def diff_text(go, rs):
    """The unified diff of two transcripts' views (answers parsed), then of every written file whose text
    differs."""
    a = json.dumps(_readable(view(go)), indent=1, sort_keys=True).splitlines()
    b = json.dumps(_readable(view(rs)), indent=1, sort_keys=True).splitlines()
    out = list(difflib.unified_diff(a, b, 'go', 'tsc-rs', n=4, lineterm=''))
    ga, ra = go.get('writtenText', {}), rs.get('writtenText', {})
    for f in sorted(set(ga) | set(ra)):
        if ga.get(f) != ra.get(f):
            out += list(difflib.unified_diff((ga.get(f) or '').splitlines(), (ra.get(f) or '').splitlines(),
                                             f'go/{f}', f'tsc-rs/{f}', n=2, lineterm=''))
    return '\n'.join(out) + '\n'


def judge(go_runs, rs_runs, confirm):
    """(status, detail, rsFailure) of a scenario (see the top comment). confirm() runs tsc-rs once more.
    The caller has already made a session with an infra failure ERROR."""
    for t in rs_runs:
        if t['failure']:
            return 'DIFF', f'tsc-rs: {t["failure"]}: {t["why"]}', 'timeout' if t['failure'] == 'timeout' else 'crash'
    for k, t in enumerate(rs_runs[1:], 2):
        if not same(t, rs_runs[0]):
            return 'DIFF', f'rs-nondet: tsc-rs run {k} differs from run 1: {describe(rs_runs[0], t)}', None
    go_failed = next((t for t in go_runs if t['failure']), None)
    go_other = next((t for t in go_runs[1:] if not same(t, go_runs[0])), None)
    if go_failed or go_other:
        if len(rs_runs) == 1:
            t = confirm()
            rs_runs.append(t)
            if t['failure']:
                return 'DIFF', f'tsc-rs: {t["failure"]} (rs-confirm): {t["why"]}', 'timeout' if t['failure'] == 'timeout' else 'crash'
            if not same(t, rs_runs[0]):
                return 'DIFF', f'rs-nondet: the confirming tsc-rs run differs: {describe(rs_runs[0], t)}', None
        why = (f'go {go_failed["failure"]}: {go_failed["why"]}' if go_failed
               else f'Go runs differ: {describe(go_runs[0], go_other)}')
        return 'GO-BROKEN', why, None
    if same(go_runs[0], rs_runs[0]):
        return 'MATCH', None, None
    return 'DIFF', describe(go_runs[0], rs_runs[0]), None


# ---- the tier -------------------------------------------------------------------------------

class Tier:
    def __init__(self, args):
        self.args = args
        self.go_sha = command_identity(args.go)
        self.stage = inputs.Stage('api')
        self.cache = STATE / 'go-cache' / self.go_sha[:16] / 'api'

    def run_scenario(self, sc, ancestors, fixture):
        """(item row, info, line)."""
        name = sc['name']
        out = Path(self.args.out) / name
        out.mkdir(parents=True, exist_ok=True)
        fdir, fhash = fixture if fixture else (None, None)
        mat = material(sc, fhash, ancestors, self.go_sha)
        key = inputs.json_hash(mat)[:24]
        cfile = self.cache / f'{name}-{key}.json'
        start = time.monotonic()
        scratch = session.SCRATCH / name
        go = self.stage.load(cfile, mat)
        cached = go is not None
        if go is None:
            go = [session.run_session(self.args.go, sc, scratch / f'go{k}', fdir) for k in range(1, GO_RUNS + 1)]
        rs = [session.run_session(self.args.rs, sc, scratch / f'rs{k}', fdir) for k in range(1, RS_RUNS[self.args.tier] + 1)]
        infra = next((t for t in go + rs if t['failure'] == 'infra'), None)
        if infra is None:
            status, detail, rs_failure = judge(go, rs, lambda: session.run_session(self.args.rs, sc, scratch / 'rs-confirm', fdir))
            infra = next((t for t in rs if t['failure'] == 'infra'), None)
        secs = round(time.monotonic() - start, 2)
        info = {'key': key, 'goCached': cached, 'secs': secs, 'goSecs': [t['secs'] for t in go], 'rsSecs': [t['secs'] for t in rs]}
        (out / 'go.json').write_text(json.dumps(go, indent=1) + '\n')
        (out / 'rs.json').write_text(json.dumps(rs, indent=1) + '\n')
        if infra is not None:  # the harness failed, not a compiler: no result, nothing cached
            info['infra'] = infra['why']
            return ({'status': 'ERROR', 'detail': f'the sandbox or the exec failed: {infra["why"]}'}, info,
                    f'api {name}: ERROR infra {infra["why"][:200]}')
        if not cached and not any(t['failure'] == 'timeout' for t in go):
            self.stage.save(cfile, mat, go)
        row = {'status': status}
        if detail:
            row['detail'] = detail
        if rs_failure:
            row['rsFailure'] = rs_failure
        if status != 'MATCH':
            go_status = 'GO-BROKEN' if any(t['failure'] for t in go) or any(not same(t, go[0]) for t in go[1:]) else None
            labels = [f'run{k}' for k in range(1, RS_RUNS[self.args.tier] + 1)] + (['confirm'] if len(rs) > RS_RUNS[self.args.tier] else [])
            row.update(input=key[:16], go=go_evidence(go[0], go_status), attempts=[attempt(t, w) for t, w in zip(rs, labels)])
            (out / 'diff.txt').write_text(f'{status}: {detail}\n\n' + diff_text(go[0], rs[0]))
        return row, info, f'api {name}: {status} {secs}s{" " + detail[:200] if detail else ""}'


def ancestor_dirs(roots):
    """The dirs above the roots that the sandbox shows from the host (the hidden dirs are empty tmpfs dirs
    there, made by the sandbox): module resolution and the config search look up ANCESTOR_NAMES in them."""
    return parity_lsp.ancestor_dirs(*roots)


def main_tier(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rs', required=True)
    ap.add_argument('--go', default=str(GO))
    ap.add_argument('--out', required=True)
    ap.add_argument('--tier', default='full', choices=('quick', 'full'))
    ap.add_argument('--only', default='')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--scenarios', default=str(SCENARIOS), help='the scenarios file (self-tests only)')
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = [s for s in scenarios(args.tier, args.scenarios) if re.search(args.only, s['name'])]
    tier = Tier(args)
    items, infos, changed = {}, {}, {}
    fixtures, errors = {}, {}
    for sc in chosen:
        if sc.get('disk') is not None:
            try:
                fixtures[sc['name']] = fixture_snapshot(sc)
            except (inputs.SnapshotError, OSError) as e:
                errors[sc['name']] = f'fixture: {e}'
        elif os.path.lexists(session.VIRTUAL):
            errors[sc['name']] = f'{session.VIRTUAL} exists on disk: an in-memory root must name nothing there'
    up_dirs = ancestor_dirs([session.SANDBOX_HOME, *[session.root_of(s) for s in chosen]])
    up_state = parity_lsp.ancestors_state(up_dirs)
    up_watch = parity_watch.Watch(up_dirs, keep=lambda p: os.path.basename(p) in parity_lsp.ANCESTOR_NAMES, recursive=False)
    fx_watch = parity_watch.Watch([str(d) for d, _ in fixtures.values()])
    print(f'api: {len(chosen)} scenarios ({len(fixtures)} with a disk fixture), Go {tier.go_sha[:12]}, session {session_sha()}', flush=True)
    for name, why in errors.items():
        items[f'api/{name}'] = {'status': 'ERROR', 'detail': why}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(tier.run_scenario, sc, up_state, fixtures.get(sc['name'])): sc['name']
                   for sc in chosen if sc['name'] not in errors}
        for f in as_completed(futures):
            name = futures[f]
            try:
                row, info, line = f.result()
            except Exception as e:  # a harness failure is never a pass
                row, info, line = {'status': 'ERROR', 'detail': f'harness: {type(e).__name__}: {e}'}, {}, f'api {name}: ERROR {e}'
            items[f'api/{name}'] = row
            infos[name] = info
            print(line, flush=True)
    fx_events = fx_watch.dirty()
    fx_watch.close()
    for name, (d, _h) in fixtures.items():
        why = fx_events.get(str(d))
        if why is None:
            try:
                check_fixture(d, next(s for s in chosen if s['name'] == name)['disk'])
            except inputs.SnapshotError as e:
                why = str(e)
        if why:
            changed[name] = f'the fixture changed while the tier ran: {why}'
            items[f'api/{name}'] = {'status': 'ERROR', 'detail': changed[name], 'was': items.get(f'api/{name}')}
    up_events = up_watch.all_events()
    up_watch.close()
    if up_events or parity_lsp.ancestors_state(up_dirs) != up_state:
        changed['ancestors'] = f'an ancestor dir changed while the tier ran: {(up_events or ["state"])[0]}'
    for why in [v for k, v in changed.items() if k == 'ancestors']:
        for k in items:
            items[k] = {'status': 'ERROR', 'detail': why, 'was': items[k]}
    for name, info in infos.items():
        if info.get('infra'):
            changed[f'session {name}'] = f'infra: {info["infra"]}'
    if changed or errors:
        print(f'api: inputs changed or unkeyed reads ({len(changed) + len(errors)}): {tier.stage.discard()} staged files discarded', flush=True)
    else:
        print(f'api: {tier.stage.publish()} cache files published', flush=True)
    counts = Counter(v['status'] for v in items.values())
    summary = {'tier': args.tier, 'rs': args.rs, 'go': args.go, 'goSha': tier.go_sha[:16], 'session': session_sha(),
               'protocol': session.PROTOCOL, 'counts': dict(counts), 'scenarios': dict(sorted(infos.items())),
               'ancestors': up_state, 'changed': changed, 'jsError': JS_ERROR, 'wallSecs': round(time.monotonic() - t0, 1),
               'items': dict(sorted(items.items()))}
    (out / 'results.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(f'api: {dict(counts)} in {summary["wallSecs"]}s')
    return 0


def find_scenario(spec):
    if os.path.isfile(spec):
        sc = json.loads(Path(spec).read_text())
        if set(sc) == {'about', 'version', 'texts', 'scenarios'}:  # a scenarios file of one scenario
            sc = _expand_texts(sc['scenarios'][0], sc['texts'])
        session.validate(sc)
        return sc
    found = [s for s in load_scenarios() if s['name'] == spec]
    if not found:
        sys.exit(f'parity_api: no scenario {spec!r} (parity_api.py list)')
    return found[0]


def main_probe(argv):
    if len(argv) != 2:
        sys.exit('usage: parity_api.py probe BIN SCENARIO')
    sc = find_scenario(argv[1])
    fdir = fixture_snapshot(sc)[0] if sc.get('disk') is not None else None
    t = session.run_session(argv[0], sc, session.SCRATCH / sc['name'] / f'probe-{os.getpid()}', fdir)
    print(json.dumps(_readable(view(t)), indent=1, sort_keys=True))
    if t['failure']:
        print(f'probe: {t["failure"]}: {t["why"]}', file=sys.stderr)
    return 0


def main():
    argv = sys.argv[1:]
    try:
        if argv[:1] == ['probe']:
            return main_probe(argv[1:])
        if argv[:1] == ['list']:
            for sc in scenarios(argv[1] if len(argv) > 1 else 'full'):
                print(f'{sc["name"]:<44} {sc["about"][:110]}')
            return 0
        if argv[:1] == ['key']:
            sc = find_scenario(argv[1])
            go = argv[argv.index('--go') + 1] if '--go' in argv else str(GO)
            fh = fixture_snapshot(sc)[1] if sc.get('disk') is not None else None
            print(json.dumps(material(sc, fh, {}, command_identity(go)), indent=1, sort_keys=True))
            return 0
        return main_tier(argv)
    except ArtifactError as e:
        print(f'parity_api: INVALID {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
