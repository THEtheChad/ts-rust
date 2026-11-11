"""One API session of the parity gate's api tier (lane H7): a `tsgo --api --async` process (JSON-RPC
with Content-Length framing, Go cmd/tsc/api.go), scripted by a scenario, in a sealed sandbox.

Everything here and in parity_sandbox_init.py decides what a server is asked and what it can see, so the
sha of both files is in the key of every cached Go session (parity_api.py SOURCES). The diff, the
referees and the reporting live in parity_api.py and do not change a cached answer.

A scenario (parity-api-scenarios.json) is:
  name          the item is api/<name> (a quick run runs the scenarios the quick manifest names)
  about         what it covers (free text, keyed like everything else)
  caseSensitive true or false: --useCaseSensitiveFileNames=<value>; absent: the OS default (Linux: true)
  fs            the createSnapshot fileSystem: {kind: "full"} (canonical and total, Go
                requestfilesystem.KindFull: the session reads nothing else for it) or, only with a disk
                fixture, {kind: "layer"} (it falls back to the host filesystem, which in the sandbox is the
                fixture and nothing of the real host); files, and optional directories and symlinks
                (host: true is refused)
  disk          {path: text | {"symlink": relative target}}: a fixture written read-only to a
                content-hashed snapshot and mounted (an overlay) at the scenario's disk root, for the
                requests that use the host filesystem (the build orchestrator, layer filesystems)
  createPrograms, openProjects   the createSnapshot changes
  requests      per project of the createSnapshot answer, in its order: the methods to call with
                {snapshot, project} (default PER_PROJECT)
  updates       a list of {changes, requests?, release?}: updateSnapshot {snapshot: the current one,
                changes}, then the requests per project of its answer (default: the projects so far),
                then, with release: true, release of the snapshot it replaced
  build         a list of build-orchestrator steps: {"create": {rootNames, cwd?, compilerOptions?,
                buildOptions?}} first, then {"build": project}, {"buildReferences": project},
                {"clean": project} and {"dispose": null} in any order
In every string, @ROOT@ is the scenario's root: VIRTUAL/<name> for an in-memory scenario (a path that does
not exist in the sandbox), SCRATCH/<name>/root for a scenario with a disk fixture. The server runs with
--cwd @ROOT@. Dynamic names (^/~ts-uri~/...) are written out as they are.

The session (run_session): initialize, createSnapshot, the per-project requests, the updates, the build
steps; then stdin is closed, the server must exit 0 by itself within EXIT_TIMEOUT, and both of its
output streams must reach EOF. Every request has REQUEST_TIMEOUT. The transcript holds, per request,
the request as sent and the answer's whole message as raw bytes (the JSON-RPC envelope included),
compared byte for byte, and after every request the state of the session's storage (`fs`: every file of
the fixture's overlay upper dir and of the private home, sha256 or "deleted"/"dir"/"link ..."), so a
file written and then removed between two requests is seen. Then the exit, a stderr summary (sha, bytes,
the first CRASH_RE line, the site-form sha) and the final storage state. failure says why it is not
clean:
  crash      the server died, exited non-zero, was killed after the exit wait, printed a crash line,
             closed its output before an answer, left a process behind it (a survivor, killed then),
             or held its output open after it ended
  timeout    a request had no answer in time
  malformed  a frame that is not Content-Length JSON (a duplicate key, NaN, a header other than
             Content-Length/Content-Type), an envelope that is not exactly {"jsonrpc": "2.0", "id": <the
             request's integer id>, "result"} or {..., "error": {"code": <integer>, "message": <string>,
             "data"?}}, a server request (no callbacks are enabled), or frames after the last answer
  infra      the sandbox could not be made or the server could not be exec'd (a mount, the namespace,
             a file that is not executable): the harness's failure, never the compiler's. The tier makes
             the item ERROR and caches nothing.
A transcript of a failed session keeps the answers it got.

Sandbox (parity_sandbox_init.py, the lsp tier's too; PID 1 of a new user, mount, network and PID namespace): the user's files
(/home), /tmp, /var/tmp, /run, /mnt, /media, /srv, /opt and /dev/shm are covered by empty tmpfs dirs with a
fixed mtime, so nothing there (the real ~/.cache/typescript, ~/.npm, ~/.config, other lanes' trees) exists
for the server, not even for stat; the whole tree is read-only; the only writable places are the private
home (new and empty for every process, at SANDBOX_HOME) and the fixture's overlay, both read by the caller
after every request. The command's files under a hidden dir are bound back read-only at their real paths.
The server gets the environment of sandbox.mjs (PATH=/usr/bin:/bin, HOME, XDG dirs and TMPDIR in its home,
LANG=C.UTF-8, nothing else), cwd /, no fd but 0, 1 and 2, no network, and no privileges (parity_sandbox_init.py
drop(): a nested user namespace that owns nothing, no capability, uid and gid 1000, no_new_privs). When it
ends, every process left in the namespace is a survivor (a failure) and is killed before anything is
collected. The home's skeleton (HOME_DIRS) is listed like everything else and checked exactly.
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

import parity_sandbox_init as sandbox_init

STATE = Path.home() / '.cache/perf-rearch/parity'
SANDBOX_HOME = STATE / 'api-home'
SCRATCH = STATE / 'scratch/api'
VIRTUAL = '/parity-api'
INIT = Path(sandbox_init.__file__).resolve()  # the sandbox's PID 1, shared with the lsp tier
STORAGE = INIT.parent / 'parity_sandbox_storage.py'  # its storage: made, recorded and listed by the init
# The wire protocol this harness speaks: JSON-RPC 2.0 over stdio (--async), Content-Length framing, the
# request and answer shapes of the pin's internal/api/proto.go (protocol 5 in scripts/goport/api_oracle.py's
# numbering). Keyed: a change to it is a change to every answer.
PROTOCOL = 'jsonrpc-async; tsc 3e9236361fa9 internal/api protocol 5'
REQUEST_TIMEOUT = int(os.environ.get('PARITY_API_REQUEST_TIMEOUT', '60'))  # lowered only by parity_h7_test.py; keyed
EXIT_TIMEOUT = 10
EOF_TIMEOUT = 10
MAX_HEADER_LINE, MAX_HEADERS, MAX_FRAME = 256, 4, 256 << 20
PER_PROJECT = ['getSourceFileNames', 'getConfigFileNames', 'getConfigFileParsingDiagnostics', 'getProgramDiagnostics',
               'getGlobalDiagnostics', 'getSyntacticDiagnostics', 'getSemanticDiagnostics', 'getSuggestionDiagnostics',
               'getDeclarationDiagnostics', 'emitToString', 'emit']
SERVER_PATH = '/usr/bin:/bin'
FIXED_MTIME = 978307200  # 2001-01-01: every dir the sandbox makes, and every fixture file
# A disk fixture's modes: a checkout's. The server is not root in its sandbox (parity_sandbox_init.py drop()),
# so it can write only where a user may; the fixture's immutability comes from its content check before
# and after the tier and the inotify watch while it runs, not from its modes.
FIXTURE_DIR_MODE, FIXTURE_FILE_MODE = 0o755, 0o644
# Crash lines on stderr (vendor/lspdiff/sandbox.mjs CRASH_RE): Go's panic, fatal error, goroutine trace
# and signal lines; Rust's panic and stack overflow; an abort.
CRASH_RE = re.compile(r"^(panic: |fatal error: |goroutine \d+ \[|SIG[A-Z]+: |\[signal |thread '.*' panicked at |.*\bpanicked at "
                      r"|.*has overflowed its stack|Aborted\b|.*core dumped)")
SCENARIO_KEYS = {'name', 'about', 'caseSensitive', 'fs', 'disk', 'createPrograms', 'openProjects', 'requests', 'updates', 'build'}
BUILD_STEPS = {'create': 'createBuildOrchestrator', 'build': 'build', 'buildReferences': 'buildReferences',
               'clean': 'cleanBuild', 'dispose': 'disposeBuildOrchestrator'}


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


def _validate_fs(name, fs, disk):
    if fs.get('kind') not in ('full', 'layer') or set(fs) - {'kind', 'files', 'directories', 'symlinks'}:
        raise ScenarioError(f'{name}: fs is {{kind: "full" or "layer", files, directories?, symlinks?}}')
    if fs['kind'] == 'layer' and disk is None:
        raise ScenarioError(f'{name}: a layer filesystem falls back to the host: give a disk fixture')
    if any(v.get('host') for v in (fs.get('symlinks') or {}).values()):
        raise ScenarioError(f'{name}: a host symlink reads the disk')


def _validate_disk(name, disk):
    for p, v in disk.items():
        if p.startswith('/') or '..' in p.split('/') or not p:
            raise ScenarioError(f'{name}: disk paths are relative to the root')
        if isinstance(v, dict):
            t = v.get('symlink')
            if set(v) != {'symlink'} or not isinstance(t, str) or t.startswith('/'):
                raise ScenarioError(f'{name}: a disk symlink is {{"symlink": relative target}}')
            depth = len(p.split('/')) - 1
            if t.split('/').count('..') > depth or os.path.normpath(os.path.join(os.path.dirname(p), t)).startswith('..'):
                raise ScenarioError(f'{name}: the symlink {p} leaves the fixture')
        elif not isinstance(v, str):
            raise ScenarioError(f'{name}: disk maps paths to texts or symlinks')


def validate(sc):
    """Raises ScenarioError on a scenario this harness cannot run hermetically or exactly."""
    name = sc.get('name', '?') if isinstance(sc, dict) else '?'
    if not isinstance(sc, dict) or set(sc) - SCENARIO_KEYS or not {'name', 'about'} <= set(sc):
        raise ScenarioError(f'{name}: keys {sorted(sc) if isinstance(sc, dict) else sc}')
    if not re.match(r'^[a-z0-9][a-z0-9.-]*$', sc['name']):
        raise ScenarioError(f'{name}: bad name')
    if 'caseSensitive' in sc and not isinstance(sc['caseSensitive'], bool):
        raise ScenarioError(f'{name}: caseSensitive must be true or false')
    disk = sc.get('disk')
    if sc.get('fs') is not None:
        _validate_fs(name, sc['fs'], disk)
    if disk is not None:
        _validate_disk(name, disk)
    if not (sc.get('fs') or disk is not None):
        raise ScenarioError(f'{name}: no filesystem (fs or disk)')
    for r in sc.get('requests') or []:
        if r not in PER_PROJECT:
            raise ScenarioError(f'{name}: unknown request {r}')
    for u in sc.get('updates') or []:
        if not isinstance(u, dict) or set(u) - {'changes', 'requests', 'release'} or 'changes' not in u:
            raise ScenarioError(f'{name}: an update is {{changes, requests?, release?}}')
        if (u['changes'].get('fileSystem') or {}).get('host'):
            raise ScenarioError(f'{name}: bad update')
        if u['changes'].get('fileSystem') is not None:
            _validate_fs(name, u['changes']['fileSystem'], disk)
        if any(r not in PER_PROJECT for r in u.get('requests') or []):
            raise ScenarioError(f'{name}: unknown request in an update')
    build = sc.get('build') or []
    for step in build:
        if not isinstance(step, dict) or len(step) != 1 or set(step) - set(BUILD_STEPS):
            raise ScenarioError(f'{name}: bad build step {step}')
    if build and 'create' not in build[0]:
        raise ScenarioError(f'{name}: the build steps start with create')
    if build and disk is None:
        raise ScenarioError(f'{name}: the build orchestrator uses the host filesystem: give a disk fixture')


def server_args(sc):
    root = root_of(sc)
    args = ['--api', '--async', '--cwd', root]
    if 'caseSensitive' in sc:
        args.append(f'--useCaseSensitiveFileNames={"true" if sc["caseSensitive"] else "false"}')
    return args


def command(cmd):
    """(argv, files): the server command with every word that names a file replaced by its realpath, and
    those files (the sandbox's init binds back the ones a hidden dir would cover, read-only)."""
    argv, files = [], []
    for word in str(cmd).split(' '):
        if os.path.isfile(word):
            word = os.path.realpath(word)
            files.append(word)
        argv.append(word)
    return argv, files


# ---- the JSON-RPC client ----------------------------------------------------------------------

class Malformed(Exception):
    pass


def _no_dup(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Malformed(f'a duplicate key in {keys}')
    return dict(pairs)


def _no_constant(c):
    raise Malformed(f'{c} is not JSON')


def parse(body):
    """A frame's JSON, strictly: no duplicate key, no NaN or Infinity."""
    try:
        return json.loads(body, object_pairs_hook=_no_dup, parse_constant=_no_constant)
    except ValueError as e:
        raise Malformed(f'a frame that is not JSON: {e}') from None


def envelope_error(msg, request_id):
    """Why msg is not exactly the JSON-RPC answer to request_id, else None."""
    if not isinstance(msg, dict):
        return 'not an object'
    if 'method' in msg:
        return f'a server request or notification ({msg.get("method")!r}): no callbacks are enabled'
    keys = set(msg)
    if keys not in ({'jsonrpc', 'id', 'result'}, {'jsonrpc', 'id', 'error'}):
        return f'envelope keys {sorted(keys)}'
    if msg['jsonrpc'] != '2.0' or not isinstance(msg['jsonrpc'], str):
        return f'jsonrpc {msg["jsonrpc"]!r}'
    if type(msg['id']) is not int or msg['id'] != request_id:
        return f'id {msg["id"]!r}, not {request_id}'
    if 'error' in msg:
        e = msg['error']
        if (not isinstance(e, dict) or set(e) - {'code', 'message', 'data'} or type(e.get('code')) is not int
                or not isinstance(e.get('message'), str)):
            return f'error object {json.dumps(e)[:120]}'
    return None


class Rpc:
    """JSON-RPC over a server's stdio. A reader thread splits stdout into frames strictly: only a
    Content-Length header (and Content-Type), a blank line, then exactly that many bytes of JSON."""

    def __init__(self, argv, env, pass_fds):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                  cwd='/', start_new_session=True, pass_fds=pass_fds)
        self.n = 0
        self.frames, self.cond, self.eof, self.bad = [], threading.Condition(), False, None
        # EOF is a fact a read reports (b''), set by the readers themselves, never inferred from a thread
        # that ended: a reader that dies of an exception leaves its flag False and says why in self.bad.
        self.out_eof = self.err_eof = False
        self.err = bytearray()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.err_reader = threading.Thread(target=self._read_err, daemon=True)
        self.reader.start()
        self.err_reader.start()

    def _fail(self, why):
        with self.cond:
            self.bad = self.bad or why
            self.cond.notify_all()

    def _read_err(self):
        try:
            for chunk in iter(lambda: self.p.stderr.read1(65536), b''):
                self.err += chunk
            self.err_eof = True
        except BaseException as e:  # noqa: BLE001 (any failure of the reader fails the session)
            self._fail(f'the stderr reader failed: {type(e).__name__}: {str(e)[:200]}')

    def _frame(self, f):
        """One frame's body, or None at a clean end (EOF at a frame boundary). Bounded: header lines of at
        most MAX_HEADER_LINE bytes, at most MAX_HEADERS of them, a Content-Length of at most 10 digits and
        MAX_FRAME bytes."""
        head = {}
        line = f.readline(MAX_HEADER_LINE + 1)
        if not line:
            return None
        while line not in (b'\r\n', b'\n'):
            if len(line) > MAX_HEADER_LINE:
                raise Malformed(f'a header line longer than {MAX_HEADER_LINE} bytes: {line[:80]!r}...')
            if not line.endswith(b'\n'):
                raise Malformed(f'a header line without its end: {line[:80]!r}')
            k, sep, v = line.decode('ascii', 'replace').partition(':')
            if not sep or k.strip().lower() not in ('content-length', 'content-type') or k.strip().lower() in head:
                raise Malformed(f'not a header line: {line[:80]!r}')
            head[k.strip().lower()] = v.strip()
            if len(head) > MAX_HEADERS:
                raise Malformed(f'more than {MAX_HEADERS} header lines')
            line = f.readline(MAX_HEADER_LINE + 1)
        if not re.match(r'^[0-9]{1,10}$', head.get('content-length', '')):
            raise Malformed(f'no Content-Length of at most 10 digits: {str(head)[:120]}')
        n = int(head['content-length'])
        if n > MAX_FRAME:
            raise Malformed(f'a Content-Length of {n} bytes, more than {MAX_FRAME}')
        body = f.read(n)
        if len(body) != n:
            raise Malformed(f'a frame cut short: {len(body)} of {n} bytes')
        return body

    def _read(self):
        f = self.p.stdout
        try:
            while True:
                body = self._frame(f)
                if body is None:
                    self.out_eof = True
                    break
                msg = parse(body)
                with self.cond:
                    self.frames.append((body, msg))
                    self.cond.notify_all()
        except Malformed as e:
            self._fail(str(e))
        except BaseException as e:  # noqa: BLE001 (any failure of the reader fails the session)
            self._fail(f'the stdout reader failed: {type(e).__name__}: {str(e)[:200]}')
        if not self.out_eof:
            try:  # drain to EOF, so that EOF still means "nobody holds it"
                for _ in iter(lambda: f.read(65536), b''):
                    pass
                self.out_eof = True
            except BaseException as e:  # noqa: BLE001
                self._fail(f'the stdout reader failed while draining: {type(e).__name__}: {str(e)[:200]}')
        with self.cond:
            self.eof = True
            self.cond.notify_all()

    def call(self, method, params):
        """(raw answer bytes, failure (kind, why) or None)."""
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
                    raw, got = self.frames.pop(0)
                    why = envelope_error(got, self.n)
                    if why:
                        return raw, ('malformed', f'{method}: {why}')
                    return raw, None
                if self.bad:
                    return None, ('malformed', f'{method}: {self.bad}')
                if self.eof:
                    return None, ('crash', f'{method}: the server closed its output before answering')
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, ('timeout', f'{method}: no answer in {REQUEST_TIMEOUT} s')
                self.cond.wait(left)

    def finish(self):
        """Closes stdin, waits for the exit and for EOF on both streams: (code, forced, trailing, eof)."""
        try:
            self.p.stdin.close()
        except OSError:
            pass
        forced = False
        try:
            self.p.wait(timeout=EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            forced = True
            self.kill()
            self.p.wait()
        self.reader.join(EOF_TIMEOUT)
        self.err_reader.join(EOF_TIMEOUT)
        eof = self.out_eof and self.err_eof and not self.reader.is_alive() and not self.err_reader.is_alive()
        with self.cond:
            trailing = self.bad or (f'{len(self.frames)} frames after the last answer' if self.frames else None)
        return self.p.returncode, forced, trailing, eof

    def kill(self):
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ---- the session's storage --------------------------------------------------------------------
# The storage (the private home and the fixture's overlay upper dir) is the sandbox's own tmpfs: the init
# makes it, records every write to it and lists it (parity_sandbox_storage.py, shared with the lsp tier).
# A listing here is the api tier's view of it: {'disk': the upper dir ({} without a fixture), 'home',
# 'skeleton'}.

def api_listing(listing):
    return {'disk': listing['disk'][0] if listing['disk'] else {}, 'home': listing['home'], 'skeleton': listing['skeleton']}


def _clear(d):
    if os.path.lexists(d):
        subprocess.run(['chmod', '-R', 'u+rwx', str(d)], check=False)
        subprocess.run(['rm', '-rf', str(d)], check=False)


def _projects(raw):
    try:
        return [p.get('id') for p in (json.loads(raw).get('result') or {}).get('projects') or [] if isinstance(p, dict)]
    except (ValueError, AttributeError):
        return []


def _result(raw, key):
    try:
        return (json.loads(raw).get('result') or {}).get(key)
    except (ValueError, AttributeError):
        return None


# ---- one session ------------------------------------------------------------------------------

class Session:
    def __init__(self, binary, sc, work, fixture):
        self.sc, self.work, self.root = sc, work, root_of(sc)
        self.steps, self.failure = [], None
        _clear(work)
        work.mkdir(parents=True)
        argv, files = command(binary)
        self.report_path = work / 'report.json'
        report_w = os.open(self.report_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        # The control pipe: "list" to the init, a listing back (after every request).
        c_r, self.control_w = os.pipe()
        self.listing_r, l_w = os.pipe()
        mounts = [{'overlay': str(fixture), 'at': self.root}] if fixture is not None else []
        spec = {'report': report_w, 'control': [c_r, l_w], 'home': str(SANDBOX_HOME), 'mounts': mounts, 'expose': files,
                'cwd': '/', 'mtime': FIXED_MTIME, 'cmd': [*argv, *server_args(sc)]}
        self.start = time.monotonic()
        try:
            self.rpc = Rpc(['/usr/bin/python3', '-I', str(INIT), 'launch', json.dumps(spec)],
                           server_env(str(SANDBOX_HOME)), [report_w, c_r, l_w])
        finally:
            for fd in (report_w, c_r, l_w):
                os.close(fd)
        self.listings = os.fdopen(self.listing_r, 'r')

    def list_storage(self):
        """The storage now, from the init (None when it no longer answers: the session has ended)."""
        try:
            os.write(self.control_w, b'list\n')
            line = self.listings.readline()
            return api_listing(json.loads(line)) if line else None
        except (OSError, ValueError):
            return None

    def ask(self, method, params):
        if self.failure:
            return None
        raw, fail = self.rpc.call(method, params)
        step = {'method': method, 'params': params}
        if raw is not None:
            step['answer'] = raw.decode('utf-8', 'surrogateescape')
        if fail:
            self.failure = fail
            step['failure'] = fail[1]
        else:  # after a failure the session is ending: its storage is in the final listing only
            step['fs'] = self.list_storage()
        self.steps.append(step)
        return raw if fail is None else None

    def per_project(self, sid, projects, requests):
        for pid in projects:
            for method in requests or PER_PROJECT:
                self.ask(method, {'snapshot': sid, 'project': pid})

    def script(self):
        sc, root = self.sc, self.root
        self.ask('initialize', None)
        params = {}
        if sc.get('fs'):
            params['fileSystem'] = expand(sc['fs'], root)
        for k in ('createPrograms', 'openProjects'):
            if sc.get(k) is not None:
                params[k] = expand(sc[k], root)
        sid, projects = None, []
        if params:
            snap = self.ask('createSnapshot', params)
            if snap is not None:
                sid, projects = _result(snap, 'snapshot'), _projects(snap)
                self.per_project(sid, projects, sc.get('requests'))
        for u in sc.get('updates') or []:
            if sid is None:
                break
            got = self.ask('updateSnapshot', {'snapshot': sid, 'changes': expand(u['changes'], root)})
            if got is None:
                break
            old, sid = sid, _result(got, 'snapshot')
            projects = _projects(got) or projects
            self.per_project(sid, projects, u.get('requests'))
            if u.get('release'):
                self.ask('release', {'snapshot': old})
        orchestrator = None
        for step in sc.get('build') or []:
            (kind, arg), = step.items()
            if kind == 'create':
                got = self.ask(BUILD_STEPS[kind], expand(arg, root))
                orchestrator = _result(got, 'buildOrchestratorID') if got else None
            elif kind == 'dispose':
                self.ask(BUILD_STEPS[kind], {'buildOrchestratorID': orchestrator})
            else:
                self.ask(BUILD_STEPS[kind], {'buildOrchestratorID': orchestrator, **({'project': expand(arg, root)} if arg else {})})

    def end(self):
        if self.failure and self.failure[0] == 'timeout':
            self.rpc.kill()
        code, forced, trailing, eof = self.rpc.finish()
        os.close(self.control_w)
        self.listings.close()
        rep_raw = self.report_path.read_bytes()
        try:
            rep = json.loads(rep_raw) if rep_raw else {}
        except ValueError:
            rep = {'setup': f'an unreadable report: {rep_raw[:200]!r}'}
        failure = self.failure
        if rep.get('setup') or (not rep and not (failure and failure[0] == 'timeout')):
            failure = ('infra', rep.get('setup') or f'no report from the sandbox (exit {code}): '
                       f'{bytes(self.rpc.err).decode("utf-8", "replace")[-300:]}')
        if 'status' in rep:
            code = os.waitstatus_to_exitcode(rep['status'])
        err = bytes(self.rpc.err)
        crash = next((ln[:300] for ln in err.decode('utf-8', 'replace').splitlines() if CRASH_RE.match(ln)), None)
        if not failure:
            if trailing:
                failure = ('malformed', trailing)
            elif rep.get('overflow'):
                failure = ('crash', 'the record of its writes lost events (fanotify queue overflow)')
            elif rep.get('survivors') or rep.get('reaped'):
                failure = ('crash', f'processes survived the server: {rep.get("survivors", [])[:5]}, {rep.get("reaped")} killed after it')
            elif not eof:
                failure = ('crash', f'its output was still held open {EOF_TIMEOUT} s after it ended')
            elif forced:
                failure = ('crash', f'no exit in {EXIT_TIMEOUT} s after stdin closed: killed')
            elif code:
                failure = ('crash', f'exit {code}')
            elif crash:
                failure = ('crash', f'stderr: {crash}')
        st = rep.get('storage') or {}
        final = api_listing(st) if st else {}
        texts = st.get('texts', {})
        _clear(self.work)
        import parity_grandfather  # the site form a grandfather entry pins (never part of a cached Go answer's key)
        return {'steps': self.steps, 'exit': code, 'forced': forced, 'survivors': rep.get('survivors', []), 'reaped': rep.get('reaped'),
                'stderr': {'sha': hashlib.sha256(err).hexdigest()[:16], 'site': parity_grandfather.stderr_site_hash(err),
                           'bytes': len(err), 'crash': crash, 'tail': err[-600:].decode('utf-8', 'replace')},
                'fs': final, 'writes': rep.get('writes', {}), 'writtenText': texts, 'failure': failure[0] if failure else None,
                'why': failure[1] if failure else None, 'secs': round(time.monotonic() - self.start, 2)}


def run_session(binary, sc, work, fixture=None):
    """One server process on one scenario: the transcript (see the top comment). work: a scratch dir of
    this process (made new, removed after); fixture: the disk fixture's read-only snapshot dir, or None."""
    s = Session(binary, sc, Path(work), fixture)
    s.script()
    return s.end()
