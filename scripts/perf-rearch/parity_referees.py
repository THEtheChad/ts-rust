"""The referees of the parity gate: when tsc-rs may differ from Go's default run (4 checkers).

docs/perf-rearch/brief.md ("Partition-dependent output", "Better than Go") has the contract and
docs/perf-rearch/parity.md ("Referees") the details. The CLI, fuzz and corpus tiers (not LSP) use
this module. On a mismatch with Go's default run they check, in order:

1. tsc-rs's determinism: runs that differ are a DIFF (rs-nondet) whatever Go does. A tier that ran
   tsc-rs once runs it again before it reports any status of this module.
2. GO-BROKEN (`judge_broken`): Go crashed, timed out, or its repeated default runs differ, and
   tsc-rs neither crashes nor times out.
3. GO-PARTITION (`blocker`, `classify_partition`): Go's output differs across `--checkers N`
   for N in CHECKERS and tsc-rs equals one of those outputs exactly.
4. JS-AGREES (`js_delta`): JS tsc 6.0.3 sides with tsc-rs on every point where tsc-rs and Go
   differ (diagnostics, written files, exit code).
5. Otherwise DIFF.

`referee` runs steps 3 and 4 with the tier's own runners; the tiers do steps 1 and 2.
A run here is any object with `code`, `stdout` and `stderr` (bytes) and `tree` ({path: content or
hash}); a side is a list of runs (two for the CLI tier's incr and build variants).
"""
import difflib
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'vendor/difffuzz'))
import oracle  # noqa: E402

GO_BROKEN, GO_PARTITION, JS_AGREES = 'GO-BROKEN', 'GO-PARTITION', 'JS-AGREES'
REFEREED = (GO_BROKEN, GO_PARTITION, JS_AGREES)
CHECKERS = (1, 2, 3, 4, 6, 8, 12, 16)
UNSTABLE = 'unstable'  # an N whose Go runs differ: a race, not a partition
FAILED = 'failed'  # an N whose Go run timed out: load, not an output (not cached)
JS_TSC = Path.home() / '.cache/perf-rearch/js-tsc/node_modules/typescript/bin/tsc'


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


JS_VERSION = '6.0.3'  # the JS referee (brief: "Better than Go")


def js_package(path):
    """The typescript package dir of a JS entry point (bin/tsc, lib/tsc.js, lib/tsserver.js)."""
    d = Path(path).resolve().parent
    while d != d.parent:
        pj = d / 'package.json'
        if pj.exists():
            try:
                if json.loads(pj.read_text()).get('name') == 'typescript':
                    return d
            except ValueError:
                pass
        d = d.parent
    raise ValueError(f'{path} is not inside a typescript package')


def js_identity(path):
    """The JS referee's identity for its caches: the sha256 over the whole typescript package that
    `path` (bin/tsc or lib/tsserver.js) runs from: package.json, bin/ and lib/ (the tsc.js and
    tsserver.js shims, the _tsc.js and _tsserver.js implementations, typescript.js, every other
    module and the lib.*.d.ts files the compiler reads). The package must be typescript 6.0.3 by its
    package.json and by the version string compiled into _tsc.js and _tsserver.js's typescript.js;
    otherwise ValueError (a referee that is not JS 6.0.3 is no referee)."""
    pkg = js_package(path)
    version = json.loads((pkg / 'package.json').read_text()).get('version')
    if version != JS_VERSION:
        raise ValueError(f'{pkg} is typescript {version}, not {JS_VERSION}')
    for impl in ('lib/_tsc.js', 'lib/typescript.js'):
        f = pkg / impl
        if f.exists() and f'var version = "{JS_VERSION}";' not in f.read_text(errors='replace'):
            raise ValueError(f'{f} does not declare version {JS_VERSION}')
    h = hashlib.sha256(f'typescript {version}\0'.encode())
    files = [pkg / 'package.json'] + sorted(p for sub in ('bin', 'lib') for p in (pkg / sub).rglob('*') if p.is_file())
    for f in files:
        h.update(f'{f.relative_to(pkg)}\0{file_sha(f)}\n'.encode())
    return h.hexdigest()[:16]


def js_sha(js):
    """The JS compiler's cache identity (js_identity)."""
    return js_identity(js)


# ---- step 2: GO-BROKEN ----------------------------------------------------------------------

def _panic_line(r):
    return next((ln for ln in r.stderr.decode('utf-8', 'replace').splitlines() if ln.startswith('panic: ')), None)


def crashed(r):
    """A crash: oracle.crashed (a Rust or Go panic trace, exit 70, a signal), or a Go-style
    `panic: ` line on stderr without the trace, which is how tsc-rs reproduces a Go crash."""
    return oracle.crashed(r) or _panic_line(r) is not None


def go_broken_reason(go_runs, unstable):
    """Why Go's default result is broken, or None. go_runs: Go's (first) side; unstable: why its
    repeated runs differ, or None."""
    if unstable:
        return f'Go runs differ: {unstable}'
    for r in go_runs:
        if r.code == 'timeout':
            return 'Go timed out'
        if crashed(r):
            panic = _panic_line(r)
            return f'Go crash: {panic[:160]}' if panic else f'Go {oracle.crash_sig(r)}'
    return None


def judge_broken(reason, rs_runs):
    """Step 2 for Go broken by `reason`: (GO-BROKEN, detail), or (DIFF, detail) when tsc-rs crashes
    or times out too. tsc-rs's determinism is the caller's step 1."""
    for r in rs_runs:
        if r.code == 'timeout':
            return 'DIFF', f'go-broken ({reason}), tsc-rs timed out too'
        if crashed(r):
            panic = _panic_line(r)
            return 'DIFF', f'go-broken ({reason}), tsc-rs ' + (f'crash: {panic[:120]}' if panic else oracle.crash_sig(r))
    return GO_BROKEN, reason


# ---- step 3: GO-PARTITION -------------------------------------------------------------------

# Lowercase: tsc options are case-insensitive.
_BLOCKING = {
    '--checkers': 'sets --checkers',
    '--singlethreaded': 'sets --singleThreaded',
    '-b': 'build mode', '--build': 'build mode',
    '-w': 'watch mode', '--watch': 'watch mode',
    '--showconfig': '--showConfig prints the checker count',
}


def blocker(args):
    """Why the partition exception cannot apply to an item with these arguments, or None."""
    for a in args:
        reason = _BLOCKING.get(str(a).lower())
        if reason:
            return reason
    return None


def go_args(n):
    return ['--checkers', str(n)]


def classify_partition(per_n, rs_key):
    """per_n: {N: output key, UNSTABLE or FAILED}; rs_key: tsc-rs's output key (same key function).

    (GO-PARTITION, detail, info) when the stable outputs differ across N and tsc-rs equals one;
    (DIFF, 'go-partition-miss ...', info) when they differ and it equals none; (None, None, info)
    when Go gives one output at every stable N.
    """
    stable = {n: k for n, k in per_n.items() if k not in (UNSTABLE, FAILED)}
    outputs = []
    for k in stable.values():
        if k not in outputs:
            outputs.append(k)
    info = {'outputs': len(outputs),
            'unstable': sorted(n for n, k in per_n.items() if k == UNSTABLE),
            'failed': sorted(n for n, k in per_n.items() if k == FAILED)}
    info = {k: v for k, v in info.items() if v or k == 'outputs'}
    if len(outputs) <= 1:
        return None, None, info
    groups = {}
    for n, k in sorted(stable.items()):
        groups.setdefault(outputs.index(k), []).append(n)
    info['groups'] = [groups[i] for i in sorted(groups)]
    matched = sorted(n for n, k in stable.items() if k == rs_key)
    if matched:
        info['matched'] = matched
        return GO_PARTITION, f'matches Go --checkers {",".join(map(str, matched))} ({len(outputs)} Go outputs over N)', info
    return 'DIFF', f'go-partition-miss ({len(outputs)} Go outputs over N)', info


# ---- step 4: JS-AGREES ----------------------------------------------------------------------

ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
# file(line,col): error TS1234: text        (--pretty false)
PLAIN_RE = re.compile(r'^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): (?P<cat>error|warning|message) TS(?P<code>\d+): (?P<text>.*)$')
# file:line:col - error TS1234: text       (--pretty true, colors stripped)
PRETTY_RE = re.compile(r'^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+) - (?P<cat>error|warning|message) TS(?P<code>\d+): (?P<text>.*)$')
GLOBAL_RE = re.compile(r'^(?P<cat>error|warning|message) TS(?P<code>\d+): (?P<text>.*)$')
# related information (pretty only): Go "  file:line:col - text", JS 6 "  file:line:col" and the
# text on its own line after the code frame
RELATED_RE = re.compile(r'^  (?P<file>\S.*?):(?P<line>\d+):(?P<col>\d+)(?: - (?P<text>.*))?$')
SUMMARY_RE = re.compile(r'^Found (\d+) errors?\b')
TABLE_ROW_RE = re.compile(r'^ +(\d+)  (.+?)(?::\d+)?$')
TABLE_HEADER = 'Errors  Files'


def _header(line):
    m = PLAIN_RE.match(line) or PRETTY_RE.match(line)
    if m:
        return m['file'], int(m['line']), int(m['col']), m['cat'], int(m['code']), m['text']
    m = GLOBAL_RE.match(line)
    return ('', 0, 0, m['cat'], int(m['code']), m['text']) if m else None


def _is_frame(raw):
    """A code-frame line of --pretty (gutter in reverse video), or its squiggle line."""
    return raw.lstrip(' ').startswith('\x1b[7m')


def _clean(raw):
    return ANSI_RE.sub('', raw).rstrip('\r')


class Token:
    """A piece of a stdout: kind 'diag' (a diagnostic block: its header through its message chain,
    code frames and related information, without the blank lines after it), 'summary' (the blank
    lines before `Found N errors`, that line, and its table of --pretty), or 'line' (any other line,
    blank lines included). lines: its raw lines; '\n'.join of every token's lines is the stdout."""

    def __init__(self, kind, lines, diag=None):
        self.kind, self.lines, self.diag = kind, lines, diag

    @property
    def raw(self):
        return '\n'.join(self.lines)

    def blank(self):
        return self.kind == 'line' and not _clean(self.lines[0]).strip()


def tokenize(stdout):
    """stdout (bytes) as Tokens. Lossless: '\n'.join of the tokens' lines is the stdout."""
    lines = stdout.decode('utf-8', 'surrogateescape').split('\n')
    toks, i = [], 0
    while i < len(lines):
        line = _clean(lines[i])
        head = _header(line)
        if head:
            j = i + 1
            while j < len(lines) and not _header(_clean(lines[j])) and not SUMMARY_RE.match(_clean(lines[j])) and (
                    not _clean(lines[j]).strip() or lines[j].startswith(' ') or _is_frame(lines[j])):
                j += 1
            while j > i + 1 and not _clean(lines[j - 1]).strip():  # the blank lines after it are separators
                j -= 1
            _, diag = _diagnostic(lines[i + 1:j], 0, head)
            toks.append(Token('diag', lines[i:j], diag))
            i = j
            continue
        if SUMMARY_RE.match(line):
            lead = []
            while toks and toks[-1].blank():
                lead.insert(0, toks.pop().lines[0])
            j = i + 1
            if j < len(lines) and not _clean(lines[j]).strip():
                j += 1
                if j < len(lines) and _clean(lines[j]).strip() == TABLE_HEADER:
                    j += 1
                    while j < len(lines) and TABLE_ROW_RE.match(_clean(lines[j])):
                        j += 1
                    if j < len(lines) and not _clean(lines[j]).strip():
                        j += 1
            toks.append(Token('summary', lead + lines[i:j]))
            i = j
            continue
        toks.append(Token('line', [lines[i]]))
        i += 1
    return toks


def parse_output(stdout):
    """Splits a tsc stdout into (diagnostics, rest, summary).

    A diagnostic is (file, line, col, category, code, text, related): text is the message with
    its chain lines, related a tuple of (file, line, col, text). Code frames are left out, and Go's
    and JS's related-information layouts give the same tuple: this is the view for comparing with
    JS, never with Go (`js_delta` compares Go and tsc-rs on the raw tokens). rest: the other lines,
    blank lines dropped; summary: (count, {file: count}) of --pretty's `Found N errors`, or None.
    """
    diags, rest, summary = [], [], None
    for t in tokenize(stdout):
        if t.kind == 'diag':
            diags.append(t.diag)
        elif t.kind == 'summary':
            body = [_clean(x) for x in t.lines if _clean(x).strip()]
            files = {}
            for row in body[1:]:
                m = TABLE_ROW_RE.match(row)
                if m:
                    files[m[2]] = int(m[1])
            summary = (int(SUMMARY_RE.match(body[0])[1]), files)
        elif not t.blank():
            rest.append(_clean(t.lines[0]))
    return diags, rest, summary


def _diagnostic(lines, i, head):
    """The body of a diagnostic from line i: (next line index, diagnostic)."""
    text, related, blank = [head[5]], [], False
    while i < len(lines):
        raw = lines[i]
        line = _clean(raw)
        if _is_frame(raw):
            i += 1
            continue
        if not line.strip():
            blank = True
            i += 1
            continue
        if not line.startswith('  ') or _header(line):
            break
        rel = RELATED_RE.match(line) if blank else None
        if rel:
            related.append([rel['file'], int(rel['line']), int(rel['col']), rel['text']])
        elif blank and related and related[-1][3] is None:
            related[-1][3] = line.strip()  # JS 6: the related text after its code frame
        elif not blank:
            text.append(line)
        else:
            break
        i += 1
    return i, (*head[:5], '\n'.join(text), tuple(tuple(r) for r in related))


def _loc(d):
    return d[0], d[1], d[2], d[4]


def _errors(diags):
    """(file, line) of each error, in output order: what the summary counts."""
    return [(d[0], d[1]) for d in diags if d[3] == 'error']


def render_summary(errors, lead):
    """Go's `Found N errors` block (diagnosticwriter.WriteErrorSummaryText, en) for these errors, as
    lines, after `lead` blank lines; None when there is no error (Go prints none)."""
    if not errors:
        return None
    by_file = {}
    for f, line in errors:
        if f:
            by_file.setdefault(f, []).append(line)
    files = sorted(by_file)  # Go sorts by file name; equal to the shown relative name's order
    where = lambda f: f'{f}\x1b[90m:{by_file[f][0]}\x1b[0m'  # noqa: E731
    total, n = len(errors), len(files)
    if total == 1:
        msg = 'Found 1 error.' if (len(errors) > sum(map(len, by_file.values())) or not files) else f'Found 1 error in {where(files[0])}'
    elif n == 0:
        msg = f'Found {total} errors.'
    elif n == 1:
        msg = f'Found {total} errors in the same file, starting at: {where(files[0])}'
    else:
        msg = f'Found {total} errors in {n} files.'
    out = [''] * lead + [msg, '']
    if n > 1:
        widest = len(str(max(len(v) for v in by_file.values())))
        pad = max(len(TABLE_HEADER.split(' ')[0]), widest)
        out += [' ' * max(widest - len(TABLE_HEADER.split(' ')[0]), 0) + TABLE_HEADER]
        out += [f'{len(by_file[f]):>{pad}}  {where(f)}' for f in files] + ['']
    return out


def _lead(tok):
    return next(i for i, x in enumerate(tok.lines) if _clean(x).strip())


def _drop(toks, idx):
    """toks without the diagnostic blocks at idx, each with the blank separator that joined it to its
    neighbour block (after it, or before it for the last block of a run)."""
    out = list(toks)
    for p in sorted(idx, reverse=True):
        after = p + 2 < len(out) and out[p + 1].blank() and out[p + 2].kind == 'diag'
        before = p >= 2 and out[p - 1].blank() and out[p - 2].kind == 'diag'
        if after:
            del out[p:p + 2]
        elif before:
            del out[p - 1:p + 1]
        else:
            del out[p]
    return out


def _stdout_points(g, r, j, tag):
    """The stdout part of one run pair: (failure or None, (go-only, rs-only) diagnostics). Every line
    is accounted for: Go's and tsc-rs's tokens must be equal byte for byte once the diagnostic blocks
    only one side prints are taken out; a block only tsc-rs prints must equal JS's block of that
    diagnostic byte for byte (code frame included; not decidable in --pretty with related
    information, where JS 6 lays the block out differently); the summary must be the one Go writes
    for tsc-rs's own errors."""
    gt, rt, jt = tokenize(g.stdout), tokenize(r.stdout), tokenize(j.stdout)
    gd = [(k, t) for k, t in enumerate(gt) if t.kind == 'diag']
    rd = [(k, t) for k, t in enumerate(rt) if t.kind == 'diag']
    sm = difflib.SequenceMatcher(None, [t.diag for _, t in gd], [t.diag for _, t in rd], autojunk=False)
    go_only, rs_only, drop_g, drop_r = [], [], [], []
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == 'equal':
            for (_, x), (_, y) in zip(gd[a1:a2], rd[b1:b2]):
                if x.raw != y.raw:
                    return f'{tag}the block of {_point(x.diag)} differs from Go\'s (frame or formatting; not refereed)', None
            continue
        go_only += [t for _, t in gd[a1:a2]]
        rs_only += [t for _, t in rd[b1:b2]]
        drop_g += [k for k, _ in gd[a1:a2]]
        drop_r += [k for k, _ in rd[b1:b2]]
    if {t.diag for t in go_only} & {t.diag for t in rs_only}:
        return f'{tag}the diagnostics both print are in another order (not refereed)', None
    gred, rred = _drop(gt, drop_g), _drop(rt, drop_r)
    gs = [k for k, t in enumerate(gred) if t.kind == 'summary']
    if len(gs) > 1 or sum(t.kind == 'summary' for t in rred) > 1:
        return f'{tag}more than one "Found N errors" summary (not refereed)', None
    ge, re_ = _errors([t.diag for _, t in gd]), _errors([t.diag for _, t in rd])
    if ge != re_ or [t.raw for t in gred if t.kind == 'summary'] != [t.raw for t in rred if t.kind == 'summary']:
        if not gs:
            if any(t.kind == 'summary' for t in rred):
                return f'{tag}tsc-rs prints a "Found N errors" summary where Go prints none (its layout has no referee)', None
        else:
            tok = gred[gs[0]]
            lead = _lead(tok)
            if render_summary(ge, lead) != tok.lines:
                return f'{tag}Go\'s "Found N errors" summary is not the one the referee renders (cannot check tsc-rs\'s)', None
            want = render_summary(re_, lead)
            gred = gred[:gs[0]] + ([Token('summary', want)] if want else []) + gred[gs[0] + 1:]
    if [(t.kind, t.raw) for t in gred] != [(t.kind, t.raw) for t in rred]:
        what = 'the "Found N errors" summary does not count tsc-rs\'s own errors' if any(
            t.kind == 'summary' for t in gred + rred) and [t.raw for t in gred if t.kind != 'summary'] == [
            t.raw for t in rred if t.kind != 'summary'] else 'non-diagnostic stdout differs from Go (blank lines included)'
        return f'{tag}{what} (not refereed)', None
    return None, (go_only, rs_only, Counter(t.raw for t in jt if t.kind == 'diag'))


def _rs_block(t, rs_only, js_blocks, tag):
    """A block only tsc-rs prints must be JS's block of it byte for byte (its code frame included)."""
    if t.diag[6] and any(_is_frame(x) for x in t.lines):
        return f'{tag}tsc-rs\'s {_point(t.diag)} has related information in --pretty (JS 6 lays it out differently: no referee)'
    if js_blocks[t.raw] < sum(1 for x in rs_only if x.raw == t.raw):
        return f'{tag}tsc-rs\'s block of {_point(t.diag)} is not JS\'s byte for byte (frame or formatting)'
    return None


def _delta_run(g, r, j, k):
    """JS's verdict on one run pair (Go g, tsc-rs r, JS j): (failure or None, decided points)."""
    tag = '' if k is None else f'run{k + 1} '
    if g.stderr != r.stderr:
        return f'{tag}stderr differs from Go (not refereed)', []
    fail, only = _stdout_points(g, r, j, tag)
    if fail:
        return fail, []
    go_tok, rs_tok, js_blocks = only
    go_only, rs_only = Counter(t.diag for t in go_tok), Counter(t.diag for t in rs_tok)
    rd, jd = parse_output(r.stdout)[0], parse_output(j.stdout)[0]
    rc, jc = Counter(rd), Counter(jd)
    rloc, jloc = Counter(map(_loc, rd)), Counter(map(_loc, jd))
    points = []
    for d in go_only:
        if jc[d] > rc[d] or jloc[_loc(d)] > rloc[_loc(d)]:
            return f'{tag}JS also prints Go\'s {_point(d)}', points
        points.append(f'{tag}go-only {_point(d)}')
    for d in rs_only:
        if jc[d] < rc[d]:
            return f'{tag}JS does not print tsc-rs\'s {_point(d)}', points
        points.append(f'{tag}rs-only {_point(d)}')
    for t in rs_tok:
        fail = _rs_block(t, rs_tok, js_blocks, tag)
        if fail:
            return fail, points
    for f in sorted(set(g.tree) | set(r.tree)):
        if g.tree.get(f) != r.tree.get(f):
            if j.tree.get(f) != r.tree.get(f):
                return f'{tag}written file {f} differs from Go and from JS', points
            points.append(f'{tag}file {f}')
    if g.code != r.code:
        if j.code != r.code:
            return f'{tag}exit {g.code}>{r.code}, JS exit {j.code}', points
        points.append(f'{tag}exit {g.code}>{r.code}')
    return None, points


def _point(d):
    where = f'{d[0]}({d[1]},{d[2]})' if d[0] else 'global'
    return f'TS{d[4]} at {where}'


def js_delta(go_side, rs_side, js_side):
    """Step 4: (True, detail, info) when JS sides with tsc-rs on every point where tsc-rs and Go
    differ, else (False, failure, info). info['points'] lists the points JS decided (cut at 50)."""
    if js_side is None or len(js_side) != len(go_side) or any(j.code == 'timeout' or crashed(j) for j in js_side):
        return False, 'JS tsc failed (timeout or crash)', {}
    if len(go_side) != len(rs_side):
        return False, 'run count', {}
    points = []
    for k, (g, r, j) in enumerate(zip(go_side, rs_side, js_side)):
        fail, pts = _delta_run(g, r, j, k if len(go_side) > 1 else None)
        points += pts
        if fail:
            return False, fail, {'points': points[:50]}
    if not points:
        return False, 'no point for JS to decide (the difference is not a diagnostic, file or exit code)', {}
    kinds = Counter(p.split(' ', 2)[1 if p.startswith('run') else 0] for p in points)
    codes = Counter(m[0] for m in (re.search(r'\bTS\d+\b', p) for p in points) if m)
    detail = (f'JS sides with tsc-rs on {len(points)} points: '
              + ', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))
              + (f' ({", ".join(f"{c} x{n}" for c, n in codes.most_common(4))})' if codes else ''))
    return True, detail, {'points': points[:50], 'pointCount': len(points)}


# ---- steps 3 and 4 --------------------------------------------------------------------------

def referee(args, go_at_n, js_run, go_side, rs_side, rs_key, all_referees=False):
    """Steps 3 and 4 for an item that differs from Go's default run (Go not broken, tsc-rs
    deterministic). go_at_n(N): Go's output key at --checkers N (or UNSTABLE/FAILED); js_run():
    JS's side (None when it failed). With all_referees, step 4 runs even when step 3 decided.

    Returns (status, detail, info): status GO-PARTITION, JS-AGREES or DIFF (detail None: the
    default DIFF stands); info has 'partition' and 'js' entries for the checks that ran.
    """
    info, decided, miss = {}, None, None
    reason = blocker(args)
    if reason:
        info['partition'] = {'skipped': reason}
    else:
        status, detail, pinfo = classify_partition({n: go_at_n(n) for n in CHECKERS}, rs_key)
        info['partition'] = pinfo
        if status == GO_PARTITION:
            decided = (status, detail)
        elif status:
            miss = detail
    if decided is None or all_referees:
        ok, detail, jinfo = js_delta(go_side, rs_side, js_run())
        info['js'] = {'agrees': ok, 'detail': detail, **jinfo}
        if ok and decided is None:
            decided = (JS_AGREES, detail)
    if decided:
        return (*decided, info)
    js = info.get('js', {}).get('detail')
    return 'DIFF', '; '.join(x for x in (miss, f'js: {js}' if js else None) if x) or None, info
