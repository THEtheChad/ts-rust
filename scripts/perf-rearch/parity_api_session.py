"""One API session of the parity gate's api tier (lane H7): a `tsgo --api --async` process (JSON-RPC
with Content-Length framing, Go cmd/tsc/api.go), scripted by a scenario, in a hermetic sandbox.

Everything here decides what a server is asked and what it can see, so the sha of this file is in the
key of every cached Go session (parity_api.py). The diff, the referees and the reporting live in
parity_api.py and do not change a cached answer.

A scenario (parity-api-scenarios.json) is:
  name          the item is api/<name> (a quick run runs the scenarios the quick manifest names)
  about         what it covers (free text, keyed like everything else)
  caseSensitive true or false: --useCaseSensitiveFileNames=<value>; absent: the OS default (Linux: true)
  fs            the createSnapshot fileSystem, always {kind: "full"} (canonical and total, Go
                requestfilesystem.KindFull: the session reads no disk for it), with files and optional
                directories and symlinks (a symlink with host: true is refused: it would read the disk)
  disk          {path: text}: a fixture written read-only to a content-hashed snapshot and mounted (an
                overlay, as the cli tier) at the scenario's disk root, for the requests that use the
                host filesystem (the build orchestrator). The upper dir is what the session wrote
  createPrograms, openProjects   the createSnapshot changes (with fs or disk)
  requests      per project of the createSnapshot answer, in its order: the methods to call with
                {snapshot, project} (default PER_PROJECT)
  build         a list of build-orchestrator steps: {"create": {rootNames, cwd?, compilerOptions?,
                buildOptions?}} then {"build": project}, {"buildReferences": project}, {"clean": project}
In every string, @ROOT@ is the scenario's root: VIRTUAL/<name> for an in-memory scenario (a path that does
not exist on disk), SCRATCH/<name>/root for a scenario with a disk fixture. The server runs with
--cwd @ROOT@. Dynamic names (^/~ts-uri~/...) are written out as they are.

The session (run_session): initialize, createSnapshot, the per-project requests, the build steps, then
stdin closed and the server must exit 0 by itself within EXIT_TIMEOUT. Every request has
REQUEST_TIMEOUT. The transcript holds every request as sent and its raw answer (result or error), the
exit, a stderr summary (sha, bytes, the first CRASH_RE line) and the files the session wrote to its
disk fixture (sha256, or "deleted" for a whiteout). failure says why the session is not clean:
  crash      the process died, exited non-zero, was killed after the exit wait, or printed a crash line
  timeout    a request had no answer in time
  malformed  a frame that is not Content-Length JSON-RPC, an answer to no request, a server request
             (no callbacks are enabled), or bytes after the last frame
A transcript of a failed session keeps the answers it got.

Sandbox (H5's design, as vendor/lspdiff/sandbox.mjs): the server runs in its own user, mount and network
namespace (unshare -rmn: only a loopback that is down), with
  - a private home, empty and new for every process, bound at SANDBOX_HOME (the same path every run),
    HOME, the XDG dirs and TMPDIR under it, PATH=/usr/bin:/bin, LANG=C.UTF-8, and nothing else of this
    environment (no GOPORT_*, no GO*, no NODE_OPTIONS);
  - the bait dirs of the caller (parity_lsp.GUARDED: ~/.cache/typescript, ~/.npm, ~/.config) bound
    read-only over the real dirs; the caller watches them for every access;
  - the disk fixture, if any, as an overlay (lower: its read-only snapshot) at its root.
The process's cwd is /. HOME_DIRS made in the home are not counted as writes; anything else written
there is (`homeWrites`) and goes with the home.
"""
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import threading
import time
from pathlib import Path

STATE = Path.home() / '.cache/perf-rearch/parity'
SANDBOX_HOME = STATE / 'api-home'
SCRATCH = STATE / 'scratch/api'
VIRTUAL = '/parity-api'
# The wire protocol this harness speaks: JSON-RPC 2.0 over stdio (--async), Content-Length framing, the
# request and answer shapes of the pin's internal/api/proto.go (protocol 5 in scripts/goport/api_oracle.py's
# numbering). Keyed: a change to it is a change to every answer.
PROTOCOL = 'jsonrpc-async; tsc 3e9236361fa9 internal/api protocol 5'
REQUEST_TIMEOUT = 60
EXIT_TIMEOUT = 10
PER_PROJECT = ['getSourceFileNames', 'getConfigFileNames', 'getConfigFileParsingDiagnostics', 'getProgramDiagnostics',
               'getGlobalDiagnostics', 'getSyntacticDiagnostics', 'getSemanticDiagnostics', 'getSuggestionDiagnostics',
               'getDeclarationDiagnostics', 'emitToString', 'emit']
SERVER_PATH = '/usr/bin:/bin'
HOME_DIRS = ['.cache', '.config', '.local/share', '.local/state', 'tmp']
# Crash lines on stderr (vendor/lspdiff/sandbox.mjs CRASH_RE): Go's panic, fatal error, goroutine trace
# and signal lines; Rust's panic and stack overflow; an abort.
CRASH_RE = re.compile(r"^(panic: |fatal error: |goroutine \d+ \[|SIG[A-Z]+: |\[signal |thread '.*' panicked at |.*\bpanicked at "
                      r"|.*has overflowed its stack|Aborted\b|.*core dumped)")
SCENARIO_KEYS = {'name', 'about', 'caseSensitive', 'fs', 'disk', 'createPrograms', 'openProjects', 'requests', 'build'}
BUILD_STEPS = {'create', 'build', 'buildReferences', 'clean'}


class ScenarioError(ValueError):
    pass


def server_env(home):
    """The whole environment of a server process (sandbox.mjs serverEnv)."""
    return {'PATH': SERVER_PATH, 'HOME': home, 'XDG_CACHE_HOME': f'{home}/.cache', 'XDG_CONFIG_HOME': f'{home}/.config',
            'XDG_DATA_HOME': f'{home}/.local/share', 'XDG_STATE_HOME': f'{home}/.local/state', 'TMPDIR': f'{home}/tmp',
            'LANG': 'C.UTF-8'}


def root_of(sc):
    return str(SCRATCH / sc['name'] / 'root') if sc.get('disk') is not None else f'{VIRTUAL}/{sc["name"]}'


def expand(value, root):
    """value with @ROOT@ replaced by root in every string, keys included."""
    if isinstance(value, str):
        return value.replace('@ROOT@', root)
    if isinstance(value, list):
        return [expand(v, root) for v in value]
    if isinstance(value, dict):
        return {expand(k, root): expand(v, root) for k, v in value.items()}
    return value


def validate(sc):
    """Raises ScenarioError on a scenario this harness cannot run hermetically or exactly."""
    name = sc.get('name', '?')
    if not isinstance(sc, dict) or set(sc) - SCENARIO_KEYS or not {'name', 'about'} <= set(sc):
        raise ScenarioError(f'{name}: keys {sorted(sc) if isinstance(sc, dict) else sc}')
    if not re.match(r'^[a-z0-9][a-z0-9.-]*$', sc['name']):
        raise ScenarioError(f'{name}: bad name')
    if 'caseSensitive' in sc and not isinstance(sc['caseSensitive'], bool):
        raise ScenarioError(f'{name}: caseSensitive must be true or false')
    fs = sc.get('fs')
    if fs is not None:
        if fs.get('kind') != 'full' or set(fs) - {'kind', 'files', 'directories', 'symlinks'}:
            raise ScenarioError(f'{name}: fs must be {{kind: "full", files, directories?, symlinks?}} (a layer reads the disk)')
        if any(v.get('host') for v in (fs.get('symlinks') or {}).values()):
            raise ScenarioError(f'{name}: a host symlink reads the disk')
    if sc.get('disk') is not None and not all(isinstance(v, str) for v in sc['disk'].values()):
        raise ScenarioError(f'{name}: disk maps paths to texts')
    if sc.get('disk') is not None and any(p.startswith('/') or '..' in p.split('/') for p in sc['disk']):
        raise ScenarioError(f'{name}: disk paths are relative to the root')
    if not (fs or sc.get('disk') is not None):
        raise ScenarioError(f'{name}: no filesystem (fs or disk): the session would read the real disk')
    if fs and sc.get('disk') is not None:
        raise ScenarioError(f'{name}: fs or disk, not both')
    for r in sc.get('requests') or []:
        if r not in PER_PROJECT:
            raise ScenarioError(f'{name}: unknown request {r}')
    for step in sc.get('build') or []:
        if len(step) != 1 or set(step) - BUILD_STEPS:
            raise ScenarioError(f'{name}: bad build step {step}')
    if sc.get('build') and 'create' not in sc['build'][0]:
        raise ScenarioError(f'{name}: the build steps start with create')
    if sc.get('build') and sc.get('disk') is None:
        raise ScenarioError(f'{name}: the build orchestrator uses the host filesystem: give a disk fixture')


def server_args(sc):
    root = root_of(sc)
    args = ['--api', '--async', '--cwd', root]
    if 'caseSensitive' in sc:
        args.append(f'--useCaseSensitiveFileNames={"true" if sc["caseSensitive"] else "false"}')
    return args


def sandbox_argv(cmd, home, baits, overlay=None):
    """unshare -rmn -- sh -c '<binds> && exec "$@"' ... cmd. baits: [(bait, real)]; overlay: (lower, upper,
    work, target) or None."""
    script, pos = [], []
    for bait, real in baits:
        b = len(pos)
        script.append(f'mount --bind "${{{b + 1}}}" "${{{b + 2}}}" && mount -o remount,bind,ro "${{{b + 2}}}"')
        pos += [str(bait), str(real)]
    if overlay is not None:
        b = len(pos)
        script.append(f'mount -t overlay overlay -o "lowerdir=${{{b + 1}}},upperdir=${{{b + 2}}},workdir=${{{b + 3}}}" "${{{b + 4}}}"')
        pos += [str(x) for x in overlay]
    b = len(pos)
    script.append(f'mount --bind "${{{b + 1}}}" "${{{b + 2}}}"')
    pos += [str(home), str(SANDBOX_HOME)]
    script += [f'shift {len(pos)}', 'exec "$@"']
    return ['unshare', '-rmn', '--', 'sh', '-c', ' && '.join(script), 'api-sandbox', *pos, *cmd]


class Malformed(Exception):
    pass


class Rpc:
    """JSON-RPC over a server's stdio. A reader thread splits stdout into frames strictly: only a
    Content-Length header (and Content-Type), a blank line, then exactly that many bytes of JSON."""

    def __init__(self, argv, env):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                  cwd='/', start_new_session=True)
        self.n = 0
        self.frames, self.cond, self.eof, self.bad = [], threading.Condition(), False, None
        self.err = bytearray()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.err_reader = threading.Thread(target=self._read_err, daemon=True)
        self.reader.start()
        self.err_reader.start()

    def _read_err(self):
        for chunk in iter(lambda: self.p.stderr.read1(65536), b''):
            self.err += chunk

    def _read(self):
        f = self.p.stdout
        try:
            while True:
                head = {}
                line = f.readline()
                if not line:
                    break
                while line not in (b'\r\n', b'\n'):
                    if not line.endswith(b'\n'):
                        raise Malformed(f'a header line without its end: {line[:80]!r}')
                    k, sep, v = line.decode('ascii', 'replace').partition(':')
                    if not sep or k.strip().lower() not in ('content-length', 'content-type'):
                        raise Malformed(f'not a header line: {line[:80]!r}')
                    head[k.strip().lower()] = v.strip()
                    line = f.readline()
                if not re.match(r'^\d+$', head.get('content-length', '')):
                    raise Malformed(f'no Content-Length: {head}')
                n = int(head['content-length'])
                body = f.read(n)
                if len(body) != n:
                    raise Malformed(f'a frame cut short: {len(body)} of {n} bytes')
                try:
                    msg = json.loads(body)
                except ValueError as e:
                    raise Malformed(f'a frame that is not JSON: {e}') from None
                with self.cond:
                    self.frames.append(msg)
                    self.cond.notify_all()
        except Malformed as e:
            with self.cond:
                self.bad = str(e)
        with self.cond:
            self.eof = True
            self.cond.notify_all()

    def call(self, method, params):
        """(answer message or None, failure or None)."""
        self.n += 1
        msg = {'jsonrpc': '2.0', 'id': self.n, 'method': method}
        if params is not None:
            msg['params'] = params
        body = json.dumps(msg).encode()
        try:
            self.p.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # the reader sees the end
        deadline = time.monotonic() + REQUEST_TIMEOUT
        with self.cond:
            while True:
                if self.frames:
                    got = self.frames.pop(0)
                    if got.get('id') != self.n or 'method' in got or ('result' in got) == ('error' in got):
                        return None, ('malformed', f'{method}: not the answer to request {self.n}: {json.dumps(got)[:200]}')
                    return got, None
                if self.bad:
                    return None, ('malformed', f'{method}: {self.bad}')
                if self.eof:
                    return None, ('crash', f'{method}: the server closed its output before answering')
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, ('timeout', f'{method}: no answer in {REQUEST_TIMEOUT} s')
                self.cond.wait(left)

    def finish(self):
        """Closes stdin and waits for the exit: (code or -signal, forced, trailing failure or None)."""
        try:
            self.p.stdin.close()
        except OSError:
            pass
        forced = False
        try:
            self.p.wait(timeout=EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            forced = True
            os.killpg(self.p.pid, signal.SIGKILL)
            self.p.wait()
        self.reader.join(5)
        self.err_reader.join(5)
        with self.cond:
            trailing = self.bad or (f'{len(self.frames)} frames after the last answer' if self.frames else None)
        return self.p.returncode, forced, trailing

    def kill(self):
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _walk_upper(upper):
    """({relative path: sha256 or 'deleted'}, {relative path: text}) of an overlay upper dir (a whiteout
    is a deleted input). The texts (files up to 256 KB) are for the diff report, never compared."""
    out, texts = {}, {}
    for top, _dirs, files in os.walk(upper):
        for f in files:
            p = os.path.join(top, f)
            st = os.lstat(p)
            rel = os.path.relpath(p, upper)
            if stat.S_ISCHR(st.st_mode):
                out[rel] = 'deleted'
            elif stat.S_ISLNK(st.st_mode):
                out[rel] = 'link ' + os.readlink(p)
            else:
                data = Path(p).read_bytes()
                out[rel] = hashlib.sha256(data).hexdigest()[:16]
                if len(data) <= 1 << 18:
                    texts[rel] = data.decode('utf-8', 'replace')
    return dict(sorted(out.items())), dict(sorted(texts.items()))


def _clear(d):
    if os.path.lexists(d):
        subprocess.run(['chmod', '-R', 'u+rwx', str(d)], check=False)
        subprocess.run(['rm', '-rf', str(d)], check=False)


def _project_ids(answer):
    return [p.get('id') for p in (answer.get('result') or {}).get('projects') or [] if isinstance(p, dict)]


def run_session(binary, sc, work, baits, fixture=None, env_extra=None):
    """One server process on one scenario: the transcript (see the top comment). work: a scratch dir of
    this process (removed first); fixture: the disk fixture's read-only snapshot dir, or None."""
    sc_root = root_of(sc)
    _clear(work)
    home = work / 'home'
    for d in HOME_DIRS:
        (home / d).mkdir(parents=True, exist_ok=True)
    overlay = None
    if fixture is not None:
        upper, wdir = work / 'upper', work / 'work'
        upper.mkdir(parents=True)
        wdir.mkdir()
        Path(sc_root).mkdir(parents=True, exist_ok=True)
        overlay = (fixture, upper, wdir, sc_root)
    argv = sandbox_argv([*str(binary).split(' '), *server_args(sc)], home, baits, overlay)
    steps, failure = [], None
    start = time.monotonic()
    rpc = Rpc(argv, {**server_env(str(SANDBOX_HOME)), **(env_extra or {})})

    def ask(method, params):
        nonlocal failure
        if failure:
            return None
        got, fail = rpc.call(method, params)
        step = {'method': method, 'params': params}
        if fail:
            failure = fail
            step['failure'] = fail[1]
        else:
            step.update({'result': got['result']} if 'result' in got else {'error': got['error']})
        steps.append(step)
        return got

    ask('initialize', None)
    snap_params = {}
    if sc.get('fs'):
        snap_params['fileSystem'] = expand(sc['fs'], sc_root)
    for k in ('createPrograms', 'openProjects'):
        if sc.get(k) is not None:
            snap_params[k] = expand(sc[k], sc_root)
    if snap_params.get('fileSystem') or snap_params.get('createPrograms') or snap_params.get('openProjects'):
        snap = ask('createSnapshot', snap_params)
        if snap is not None and 'result' in snap:
            sid = snap['result'].get('snapshot')
            for pid in _project_ids(snap):
                for method in sc.get('requests') or PER_PROJECT:
                    ask(method, {'snapshot': sid, 'project': pid})
    orchestrator = None
    for step in sc.get('build') or []:
        (kind, arg), = step.items()
        if kind == 'create':
            got = ask('createBuildOrchestrator', expand(arg, sc_root))
            orchestrator = ((got or {}).get('result') or {}).get('buildOrchestratorID') if got else None
            continue
        method = {'build': 'build', 'buildReferences': 'buildReferences', 'clean': 'cleanBuild'}[kind]
        ask(method, {'buildOrchestratorID': orchestrator, **({'project': expand(arg, sc_root)} if arg else {})})
    if failure and failure[0] == 'timeout':
        rpc.kill()
    code, forced, trailing = rpc.finish()
    if not failure and trailing:
        failure = ('malformed', trailing)
    err = bytes(rpc.err)
    crash = next((ln[:300] for ln in err.decode('utf-8', 'replace').splitlines() if CRASH_RE.match(ln)), None)
    if not failure:
        if forced:
            failure = ('crash', f'no exit in {EXIT_TIMEOUT} s after stdin closed: killed')
        elif code:
            failure = ('crash', f'exit {code}')
        elif crash:
            failure = ('crash', f'stderr: {crash}')
    written, written_text = _walk_upper(overlay[1]) if overlay else ({}, {})
    made = {'/'.join(d.split('/')[:k + 1]) for d in HOME_DIRS for k in range(len(d.split('/')))}
    home_writes = sum(1 for top, ds, fs in os.walk(home) for x in ds + fs
                      if os.path.relpath(os.path.join(top, x), home) not in made)
    _clear(work)
    import parity_grandfather  # the site form a grandfather entry pins (never part of a cached Go answer's key)
    return {'steps': steps, 'exit': code, 'forced': forced,
            'stderr': {'sha': hashlib.sha256(err).hexdigest()[:16], 'site': parity_grandfather.stderr_site_hash(err),
                       'bytes': len(err), 'crash': crash, 'tail': err[-600:].decode('utf-8', 'replace')},
            'written': written, 'writtenText': written_text, 'homeWrites': home_writes, 'failure': failure[0] if failure else None,
            'why': failure[1] if failure else None, 'secs': round(time.monotonic() - start, 2)}
