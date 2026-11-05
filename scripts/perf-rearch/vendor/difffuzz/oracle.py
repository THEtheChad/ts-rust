"""Runs Go tsc (the oracle) and tsc-rs on one project directory and classifies the difference.

Both compilers run in the same directory, one after the other, with the same arguments
(`-p tsconfig.json --pretty false`, then the case's extra 'args'), so paths in messages are the same. Files that a run writes
(emit) are collected and removed before the next run. A result is compared on exit code,
stdout, stderr and the written files, byte for byte.

Inputs (parity gate, lane H4): write_project returns {path: bytes} of every input file it wrote, and
run() with that map watches the project's directories with inotify while the compiler runs and
checks every input's bytes when it ends. A write to an input (even one undone before the end), a
chmod, a delete or a rename is in the result's `input_changes`; a run that changed none has [].
What the run left in place of a changed input is `inputs_after` ({path: sha256 of the new bytes, or
"deleted"}), and it is part of the result's key: an emit that overwrites an input (Go does on some
allowJs cases) is output, compared like the written files. Callers restore the inputs
(write_project again) before each run.
"""
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

SOURCE_EXT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
ARGS = ['-p', 'tsconfig.json', '--pretty', 'false']
ENV = {k: v for k, v in os.environ.items() if not k.startswith('GOPORT_')}
PANIC_RE = re.compile(r"panicked at ([^\n]+?):(\d+):\d+")
CODE_RE = re.compile(r'\b(error|warning|message) (TS\d+)')
POS_RE = re.compile(r'^(.*?)\((\d+),(\d+)\): ')


try:
    import parity_watch  # the parity gate's inotify wrapper (scripts/perf-rearch), when on the path
except ImportError:  # difffuzz on its own: inputs checked by content only
    parity_watch = None


class Result:
    def __init__(self, code, stdout=b'', stderr=b'', tree=None, secs=0.0, input_changes=None, inputs_after=None):
        self.code, self.stdout, self.stderr, self.tree, self.secs = code, stdout, stderr, tree or {}, secs
        self.input_changes = input_changes or []
        self.inputs_after = inputs_after or {}
        self.watch_error = None

    def __setstate__(self, state):  # results pickled before input_changes, inputs_after and watch_error
        self.__dict__.update({'input_changes': [], 'inputs_after': {}, 'watch_error': None, **state})

    def fingerprint(self):
        """Exit code and the sha256 (16 hex digits) of stdout, stderr and the written files."""
        sha = lambda b: hashlib.sha256(b).hexdigest()[:16]
        tree = hashlib.sha256(json.dumps(sorted((k, sha(v)) for k, v in self.tree.items())
                                         + sorted(['input', k, v] for k, v in self.inputs_after.items())).encode()).hexdigest()[:16]
        return {'code': self.code, 'stdout': sha(self.stdout), 'stderr': sha(self.stderr), 'written': tree}

    def key(self):
        base = (self.code, self.stdout, self.stderr, tuple(sorted(self.tree.items())))
        return (*base, tuple(sorted(self.inputs_after.items()))) if self.inputs_after else base

    def text(self):
        out = f'exit: {self.code}\n--- stdout\n{self.stdout.decode(errors="replace")}'
        if self.stderr:
            out += f'--- stderr\n{self.stderr.decode(errors="replace")}'
        for name, data in sorted(self.tree.items()):
            out += f'--- written: {name}\n{data.decode(errors="replace")}\n'
        return out


def tsconfig(case):
    options = dict(case['options'])
    if not options.get('noEmit'):
        options['outDir'] = 'out'
    roots = [f for f in case['files'] if f.endswith(SOURCE_EXT) and 'node_modules/' not in f and f != 'tsconfig.json']
    return {'compilerOptions': options, 'files': roots}


def write_project(case, d):
    d = Path(d)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for rel, text in case['files'].items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode('utf-8', 'surrogateescape'))
    if 'tsconfig.json' not in case['files']:  # a case may bring its own (mutated) tsconfig.json text
        (d / 'tsconfig.json').write_text(json.dumps(tsconfig(case), indent=2) + '\n')
    return {str(p.relative_to(d)): p.read_bytes() for p in d.rglob('*') if p.is_file()}


def _stat_ids(d, inputs):
    """{path: (inode, size, mode, mtime, ctime)} of the inputs: any write to one, even of the same bytes,
    sets its ctime, which user space cannot set back."""
    out = {}
    for rel in inputs:
        try:
            st = os.lstat(d / rel)
            out[rel] = (st.st_ino, st.st_size, st.st_mode, st.st_mtime_ns, st.st_ctime_ns)
        except OSError:
            out[rel] = None
    return out


def _input_changes(d, inputs, watch, before):
    """What a run did to its inputs: (inotify events on input paths, every input whose stat identity or
    bytes changed or that is gone; {path: sha256 of what is there now or "deleted"}). The watch is
    finished first (its reader stopped and joined, the queue drained), so no event is lost."""
    changes = list(dict.fromkeys(watch.all_events())) if watch is not None else []
    after = {}
    now_ids = _stat_ids(d, inputs)
    for rel, data in inputs.items():
        p = d / rel
        if p.is_symlink() or not p.is_file():
            changes.append(f'deleted {rel}')
            after[rel] = 'deleted'
            continue
        now = p.read_bytes()
        if now != data:
            changes.append(f'modified {rel}')
            after[rel] = hashlib.sha256(now).hexdigest()
        elif now_ids[rel] != before[rel]:
            changes.append(f'rewritten {rel} (its stat identity changed)')
    return changes, after


class WatchUnavailable(Exception):
    """The inotify watch of a checked run's inputs could not be built: the run is not made."""


def run(binary, d, inputs, timeout, args=()):
    """One compiler run in d. inputs: {path: bytes} from write_project (checked, see the top comment),
    or a set of paths (not checked). A checked run needs its inotify watch: when it cannot be built
    (no inotify, the instance or watch limit) the run raises WatchUnavailable, which the tier reports
    as a harness ERROR. The stat identity and bytes of the inputs are checked as well."""
    d = Path(d)
    watch, watch_error, before = None, None, None
    if isinstance(inputs, dict):
        before = _stat_ids(d, inputs)
        if parity_watch is not None:
            top = str(d.resolve())
            names = {os.path.join(top, rel) for rel in inputs}
            try:
                watch = parity_watch.Watch([top], keep=lambda path: path in names)
            except parity_watch.WatchError as e:
                watch_error = str(e)
        else:
            watch_error = 'no inotify module'
        if watch_error is not None:
            # Fail closed (Astra recheck, 2026-11-05): a stat check of the input files cannot see a
            # transient swap of a parent directory, so without the watch the run is no evidence.
            raise WatchUnavailable(f'inotify watch unavailable, no run: {watch_error}')
    start = time.monotonic()
    # A CPU limit the kernel enforces, so a compiler on an exponential input still ends when this process is
    # killed before its own timeout fires (the new session keeps the caller's group kill from reaching it).
    # It is set before exec and inherited by tsc-rs's tsgo-worker, and a run that ends within `timeout` of
    # wall time on every core never reaches it.
    cpu = math.ceil(timeout * (os.cpu_count() or 1)) + 5
    proc = subprocess.Popen(['prlimit', f'--cpu={cpu}', '--', binary, *ARGS, *args], cwd=d, env=ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        code = 'timeout'
    secs = time.monotonic() - start
    changes, after = [], {}
    if isinstance(inputs, dict):
        try:
            changes, after = _input_changes(d, inputs, watch, before)
        finally:
            if watch is not None:
                watch.close()
    tree = {}
    for p in sorted(d.rglob('*')):
        rel = str(p.relative_to(d))
        if p.is_file() and rel not in inputs:
            tree[rel] = p.read_bytes()
            p.unlink()
    r = Result(code, out, err, tree, secs, changes, after)
    r.watch_error = watch_error
    return r


def crashed(r):
    """Panic (exit 70 in tsc-rs), signal, or a Go panic trace."""
    return r.code == 70 or (isinstance(r.code, int) and r.code < 0) or b'goroutine ' in r.stderr \
        or b'panicked at' in r.stderr or b'has overflowed its stack' in r.stderr


def crash_sig(r):
    err = r.stderr.decode(errors='replace')
    if 'has overflowed its stack' in err:
        return 'crash:stack-overflow'
    m = PANIC_RE.search(err)
    if m:
        return f'crash:{m.group(1)}:{m.group(2)}'
    if isinstance(r.code, int) and r.code < 0:
        return f'crash:signal-{-r.code}'
    return f'crash:exit-{r.code}'


def line_class(line):
    m = CODE_RE.search(line)
    if m:
        return m.group(2)
    if line.startswith(' '):
        return 'related'
    return re.sub(r'\d+', '0', re.sub(r"'[^']*'", "'_'", line))[:40]


def first_diff(a, b):
    """First non-equal opcode of two line lists: (lines of a, lines of b)."""
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != 'equal':
            return a[i1:i2], b[j1:j2]
    return [], []


def diag_sig(go, rs, root=''):
    g, r = first_diff(go.decode(errors='replace').splitlines(), rs.decode(errors='replace').splitlines())
    if root:  # absolute paths into the project would make the signature depend on the directory
        g, r = [x.replace(root, '<root>/') for x in g], [x.replace(root, '<root>/') for x in r]
    if g and r:
        cg, cr = line_class(g[0]), line_class(r[0])
        if cg != cr:
            return f'diag:{cg}>{cr}'
        pg, pr = POS_RE.match(g[0]), POS_RE.match(r[0])
        if pg and pr and pg.group(1) != pr.group(1):
            return f'diag:file~{cg}'
        return f'diag:{"msg" if pg and pr and pg.groups() == pr.groups() else "pos"}~{cg}'
    return f'diag:-{line_class(g[0])}' if g else f'diag:+{line_class(r[0])}'


def signature(go, rs, root=''):
    """None when the results are equal, else a short signature of the first difference.

    Signatures that start with 'oracle:' are differences where Go itself fails (panic or
    hang); they are not tsc-rs findings.
    """
    if go.key() == rs.key():
        return None
    if go.code == 'timeout':
        return 'oracle:timeout' if rs.code != 'timeout' else None
    if crashed(go):
        return 'oracle:crash'
    if rs.code == 'timeout':
        return 'hang'
    if crashed(rs):
        return crash_sig(rs)
    if go.stdout != rs.stdout:
        return diag_sig(go.stdout, rs.stdout, root)
    if go.stderr != rs.stderr:
        g, r = first_diff(go.stderr.decode(errors='replace').splitlines(), rs.stderr.decode(errors='replace').splitlines())
        return 'stderr:' + line_class((g or r or [''])[0])
    if go.tree != rs.tree:
        for name in sorted(set(go.tree) | set(rs.tree)):
            if go.tree.get(name) != rs.tree.get(name):
                ext = re.sub(r'^[^.]*', '', Path(name).name)
                kind = 'missing' if name not in rs.tree else 'extra' if name not in go.tree else 'content'
                return f'emit:{ext}:{kind}'
    return f'exit:{go.code}>{rs.code}'


class Oracle:
    def __init__(self, go_bin, rs_bin, timeout):
        self.go_bin, self.rs_bin, self.timeout = go_bin, rs_bin, timeout

    def run_both(self, case, d):
        inputs = write_project(case, d)
        go = run(self.go_bin, d, inputs, self.timeout, case.get('args', []))
        rs = run(self.rs_bin, d, inputs, self.timeout, case.get('args', []))
        # A hang on one side only may be machine load: give that side a longer second try.
        if go.code == 'timeout' and rs.code != 'timeout':
            go = run(self.go_bin, d, inputs, self.timeout * 2, case.get('args', []))
        elif rs.code == 'timeout' and go.code != 'timeout':
            rs = run(self.rs_bin, d, inputs, self.timeout * 2, case.get('args', []))
        return go, rs, inputs

    def check(self, case, d, confirm=True):
        """(signature or None, go result, rs result, note). With confirm, a difference is checked
        against five more Go runs; a case where Go's output changes is dropped (note 'go-nondet')."""
        go, rs, inputs = self.run_both(case, d)
        sig = signature(go, rs, str(Path(d).resolve()) + '/')
        if sig is None or not confirm or sig.startswith('oracle:'):
            return sig, go, rs, None
        # Go's output can vary by map order (keyword suggestions) or threads (resolution traces).
        for _ in range(8 if '--traceResolution' in case.get('args', []) else 5):
            again = run(self.go_bin, d, inputs, self.timeout * 2, case.get('args', []))
            if again.code != 'timeout' and again.key() != go.key():  # a Go timeout here is load: no info
                return None, go, rs, 'go-nondet'
        rs2 = run(self.rs_bin, d, inputs, self.timeout * 2, case.get('args', []))
        if rs2.key() == go.key():  # e.g. a timeout under load that does not repeat
            return None, go, rs, 'rs-flaky'
        note = 'rs-nondet' if rs2.key() != rs.key() else None
        return sig, go, rs, note


def write_finding(d, case, sig, go, rs, extra=None):
    d = Path(d)
    write_project(case, d / 'repro')
    (d / 'go.out').write_text(go.text())
    (d / 'rs.out').write_text(rs.text())
    diff = difflib.unified_diff(go.text().splitlines(True), rs.text().splitlines(True), 'go (oracle)', 'tsc-rs')
    (d / 'diff').write_text(''.join(diff))
    info = {'signature': sig, 'seed': case['name'], 'mutations': case.get('mutations', []), 'args': case.get('args', []),
            'go_exit': go.code, 'rs_exit': rs.code, **(extra or {})}
    (d / 'info.json').write_text(json.dumps(info, indent=2) + '\n')
    (d / 'case.json').write_text(json.dumps(
        {**case, 'files': {k: v.encode('utf-8', 'surrogateescape').decode('latin-1') for k, v in case['files'].items()},
         'encoding': 'latin-1'}, indent=1) + '\n')


def read_case(path):
    case = json.loads(Path(path).read_text())
    if case.pop('encoding', None) == 'latin-1':
        case['files'] = {k: v.encode('latin-1').decode('utf-8', 'surrogateescape') for k, v in case['files'].items()}
    return case
