"""Runs Go tsc (the oracle) and tsc-rs on one project directory and classifies the difference.

Both compilers run in the same directory, one after the other, with the same arguments
(`-p tsconfig.json --pretty false`, then the case's extra 'args'), so paths in messages are the same. Files that a run writes
(emit) are collected and removed before the next run. A result is compared on exit code,
stdout, stderr and the written files, byte for byte.
"""
import difflib
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


class Result:
    def __init__(self, code, stdout=b'', stderr=b'', tree=None, secs=0.0):
        self.code, self.stdout, self.stderr, self.tree, self.secs = code, stdout, stderr, tree or {}, secs

    def key(self):
        return (self.code, self.stdout, self.stderr, tuple(sorted(self.tree.items())))

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
    return {str(p.relative_to(d)) for p in d.rglob('*') if p.is_file()}


def run(binary, d, inputs, timeout, args=()):
    d = Path(d)
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
    tree = {}
    for p in sorted(d.rglob('*')):
        rel = str(p.relative_to(d))
        if p.is_file() and rel not in inputs:
            tree[rel] = p.read_bytes()
            p.unlink()
    return Result(code, out, err, tree, secs)


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
