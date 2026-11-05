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
import hashlib
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


def js_sha(js):
    """The JS compiler's identity for its cache: lib/tsc.js (bin/tsc only requires it)."""
    lib = Path(js).resolve().parent.parent / 'lib/tsc.js'
    return file_sha(lib if lib.exists() else js)[:16]


# ---- step 2: GO-BROKEN ----------------------------------------------------------------------

def go_broken_reason(go_runs, unstable):
    """Why Go's default result is broken, or None. go_runs: Go's (first) side; unstable: why its
    repeated runs differ, or None."""
    if unstable:
        return f'Go runs differ: {unstable}'
    for r in go_runs:
        if r.code == 'timeout':
            return 'Go timed out'
        if oracle.crashed(r):
            return f'Go {oracle.crash_sig(r)}'
    return None


def judge_broken(reason, rs_runs):
    """Step 2 for Go broken by `reason`: (GO-BROKEN, detail), or (DIFF, detail) when tsc-rs crashes
    or times out too. tsc-rs's determinism is the caller's step 1."""
    for r in rs_runs:
        if r.code == 'timeout':
            return 'DIFF', f'go-broken ({reason}), tsc-rs timed out too'
        if oracle.crashed(r):
            return 'DIFF', f'go-broken ({reason}), tsc-rs {oracle.crash_sig(r)}'
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


def _header(line):
    m = PLAIN_RE.match(line) or PRETTY_RE.match(line)
    if m:
        return m['file'], int(m['line']), int(m['col']), m['cat'], int(m['code']), m['text']
    m = GLOBAL_RE.match(line)
    return ('', 0, 0, m['cat'], int(m['code']), m['text']) if m else None


def _is_frame(raw):
    """A code-frame line of --pretty (gutter in reverse video), or its squiggle line."""
    return raw.lstrip(' ').startswith('\x1b[7m')


def parse_output(stdout):
    """Splits a tsc stdout into (diagnostics, rest, summary).

    A diagnostic is (file, line, col, category, code, text, related): text is the message with
    its chain lines, related a tuple of (file, line, col, text). Code frames are dropped. Both
    output formats parse, and Go's and JS's related-information layouts give the same tuple.
    rest: the other lines (blank lines dropped); summary: the "Found N errors" block of --pretty,
    (count, {file: count}) or None.
    """
    lines = stdout.decode('utf-8', 'surrogateescape').split('\n')
    diags, rest, summary = [], [], None
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = ANSI_RE.sub('', raw).rstrip('\r')
        head = _header(line)
        m = SUMMARY_RE.match(line)
        if head:
            i, diag = _diagnostic(lines, i + 1, head)
            diags.append(diag)
            continue
        if m:
            summary, i = (int(m[1]), {}), i + 1
            while i < len(lines):
                row = TABLE_ROW_RE.match(ANSI_RE.sub('', lines[i]))
                if row:
                    summary[1][row[2]] = int(row[1])
                elif ANSI_RE.sub('', lines[i]).strip() not in ('', 'Errors  Files'):
                    break
                i += 1
            continue
        if line.strip():
            rest.append(line)
        i += 1
    return diags, rest, summary


def _diagnostic(lines, i, head):
    """The body of a diagnostic from line i: (next line index, diagnostic)."""
    text, related, blank = [head[5]], [], False
    while i < len(lines):
        raw = lines[i]
        line = ANSI_RE.sub('', raw).rstrip('\r')
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


def _summary_ok(diags, summary):
    errors = [d for d in diags if d[3] == 'error']
    if summary is None:
        return True
    count, files = summary
    per_file = Counter(d[0] for d in errors if d[0])
    return count == len(errors) and (not files or files == dict(per_file))


def _delta_run(g, r, j, k):
    """JS's verdict on one run pair (Go g, tsc-rs r, JS j): (failure or None, decided points)."""
    tag = '' if k is None else f'run{k + 1} '
    if g.stderr != r.stderr:
        return f'{tag}stderr differs from Go (not refereed)', []
    gd, grest, _ = parse_output(g.stdout)
    rd, rrest, rsum = parse_output(r.stdout)
    jd, _, _ = parse_output(j.stdout)
    if grest != rrest:
        return f'{tag}non-diagnostic stdout differs from Go (not refereed)', []
    if not _summary_ok(rd, rsum):
        return f'{tag}tsc-rs\'s "Found N errors" summary does not count its diagnostics', []
    gc, rc, jc = Counter(gd), Counter(rd), Counter(jd)
    go_only, rs_only = gc - rc, rc - gc
    common_g = list(gd)
    for d, n in go_only.items():
        for _ in range(n):
            common_g.remove(d)
    common_r = list(rd)
    for d, n in rs_only.items():
        for _ in range(n):
            common_r.remove(d)
    if common_g != common_r:
        return f'{tag}the diagnostics both print are in another order (not refereed)', []
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
    if js_side is None or len(js_side) != len(go_side) or any(j.code == 'timeout' or oracle.crashed(j) for j in js_side):
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
