#!/usr/bin/env python3
"""The grandfather file of the parity gate (parity-grandfathered.json; lane H4): known items that are
not good, each pinned to its exact failure by structured evidence.

usage: parity_grandfather.py check [FILE]
       parity_grandfather.py evidence BIN --mode quick|full --out RUN [--from FILE] [--add ITEM ...]
                                      [--only REGEX]
       parity_grandfather.py regen --full RUN [--quick RUN] [--old RUN] [--from FILE] [--out FILE]
                                   [--add ITEM=REASON ...] [--only REGEX]

An entry excuses its item (parity_compare.py) only while the item fails exactly as recorded:
  status, rsFailure   the item's status (DIFF) and how tsc-rs failed: crash, timeout, or null for
                      an output DIFF (tsc-rs answers, but not as Go's cached default run does);
  input               the case's content identity (parity_fuzz.case_key: every file, the options,
                      the arguments, the directory, the oracle's argv, the timeout, the Go runs);
  go                  what Go's cached default result is: its status (GO-FAIL, GO-UNSTABLE or ok),
                      outcome (timeout, crash or exit N), panic line, and the sha256 of its stdout
                      and of its written files (not stderr: a Go crash trace holds addresses);
  attempt             the fingerprint every tsc-rs attempt must have: outcome, exit code (negative:
                      a signal; "timeout"), sha256 of stdout, of stderr in its site form
                      (`stderrSite`, below) and of the written files, and whether the inputs stayed
                      intact;
  attempts            the attempts a run of each mode makes (by label: run1, run1 retry, run2,
                      confirm), "full" always and "quick" when the quick manifest has the item;
  reason, evidence    why it is known and which run recorded the evidence.
Any difference fails the gate: another status, input, Go result, outcome, output or attempt list, an
attempt that differs from the others (nondeterminism is never excused), and an item that is good
now (the entry is stale and must be removed). Entries exist for the fuzz, corpus and api tiers only,
the tiers whose rows carry per-attempt evidence. On the api tier (lane H7, parity_api.py) `input` is
the scenario's key (its expanded definition, fixture, session driver, protocol and environment), `go`
fingerprints Go's first session (stdout: every request and answer and how it ended; written: the files
it wrote and its home writes), and each attempt is a tsc-rs session fingerprinted the same way.

The site form of stderr (version 3, lane H6; domain-tagged in version 4): where Go prints its goroutine trace after a panic that
nothing recovers (or a fatal runtime error), tsc-rs prints one port-site line, `<TAB><file>:<line>`
(crates/ts_goport/src/gopanic.rs: print_go_panic, go_fatal_newosproc), whose Rust line number moves
with any edit above the call. stderr_site() replaces that line number, and nothing else, by `<line>`.
It applies only when stderr ends with the Go panic block exactly as gopanic.rs prints it: a line that
starts `panic: ` or `fatal error: `, any non-blank lines after it (more panic lines, the signal line),
one blank line, then the site line `<TAB><path>.rs:<digits>` as the last line. Any other stderr is
hashed as it is. The file path, the panic lines, the signal line, every other byte and the exit code
stay pinned: a crash at another line of the same file matches its entry, a crash in another file or
with another panic line does not. parity_fuzz.py records the form on every tsc-rs attempt
(`stderrSite`, beside the raw `stderr` hash), and both regen (pinning) and match (comparing) read
that field, so pins and runs use the same function. Version 2 pinned the raw stderr hash under
`stderr`: a version 2 file is invalid, and a row without `stderrSite` is never pinned or matched.

The file is validated strictly: the top level is exactly {about, version: 4, items}, every entry has
exactly the keys above with the right types, and an item id must name a fuzz, corpus or api item. A key
anywhere else (an entry beside `items`, a typo, a field the matcher does not know) is an error, and
compare fails on an invalid file. parity_compare.py reads the file at the accepted ref
(parity_modes.accepted_file), never the candidate's copy.

evidence runs the fuzz tier (parity_fuzz.py, as parity.sh runs it in that mode: the mode's tsc-rs
runs, 12 jobs) on exactly the fuzz cases a grandfather file lists, and the api tier (parity_api.py, in
that mode) on exactly its api scenarios, with BIN as tsc-rs, into a run dir with meta.json and
results.json (no compare). That is the evidence regen reads: give it the base's binary, once per mode.

regen rebuilds the entries of the items a grandfather file lists (--from, default this checkout's;
an older format is read for its item ids and reasons only) from the rows a full run (and, for items
the quick manifest names, a quick run) recorded, and refuses an item whose row lacks the evidence,
is not a DIFF with an rsFailure, or has attempts that differ. --old RUN prints, per item, whether the
recorded outcome kinds and Go's panic line agree with an older run's rows.

--only REGEX (both): only the items it matches. evidence runs only them; regen rebuilds only them and
keeps every other entry of --from byte for byte. That is how a lane adds entries (--add) without
re-pinning the others to its own binary: a regen of an entry from another binary would accept whatever
that binary does now. Kept entries are never relabelled: --from must be a valid file of this version
(a version 3 file with --only would otherwise come out as version 4 with its version 3 pins).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from parity_modes import ArtifactError, _exact_keys, working_manifest  # noqa: E402

GRANDFATHERED = HERE / 'parity-grandfathered.json'
REPO_PATH = 'scripts/perf-rearch/parity-grandfathered.json'
VERSION = 4  # 4: stderrSite domain-tagged (site/raw); 3: attempt.stderrSite untagged (lane H6); 2: the raw stderr hash
SITE_LINE = b'<line>'
# The Go panic block at the end of tsc-rs's stderr (see the top comment). Only group `line` is replaced.
SITE = re.compile(rb'^(?:panic: |fatal error: )[^\n]*\n(?:[^\n]+\n)*\n\t[A-Za-z0-9_./-]+\.rs:(?P<line>[0-9]+)\n\Z', re.M)
ITEM = re.compile(r'^(fuzz/\d+:\d+|corpus/[^\s]+|api/[a-z0-9][a-z0-9.-]*)$')
HEX16 = re.compile(r'^[0-9a-f]{16}$')
ENTRY = {'status': str, 'rsFailure': (str, type(None)), 'reason': str, 'input': str, 'go': dict, 'attempt': dict, 'attempts': dict,
         'evidence': str}
GO = {'status': str, 'outcome': str, 'panic': (str, type(None)), 'stdout': str, 'written': str}
ATTEMPT = {'outcome': str, 'code': (int, str), 'stdout': str, 'stderrSite': str, 'written': str, 'inputs': str}
LABEL = re.compile(r'^(run\d+( retry)?|confirm)$')


def _typed(where, d, spec):
    if not isinstance(d, dict):
        raise ArtifactError(f'{where}: not an object')
    unknown, missing = sorted(set(d) - set(spec)), sorted(set(spec) - set(d))
    if unknown or missing:
        raise ArtifactError(f'{where}: ' + '; '.join(x for x in (unknown and f'unknown keys {unknown}',
                                                               missing and f'missing keys {missing}') if x))
    for k, t in spec.items():
        if not isinstance(d[k], t) or isinstance(d[k], bool):
            raise ArtifactError(f'{where}.{k}: a {type(d[k]).__name__}')


def validate(data, origin):
    """{item: entry} of a grandfather file's parsed JSON. Raises ArtifactError."""
    _exact_keys(origin, data, {'about': str, 'version': int, 'items': dict})
    if data['version'] != VERSION:
        raise ArtifactError(f'{origin}.version: {data["version"]}, not {VERSION}')
    for item, e in data['items'].items():
        where = f'{origin}: {item}'
        if not ITEM.match(item):
            raise ArtifactError(f'{where}: not a fuzz, corpus or api item (only they carry per-attempt evidence)')
        _typed(where, e, ENTRY)
        _typed(f'{where}.go', e['go'], GO)
        _typed(f'{where}.attempt', e['attempt'], ATTEMPT)
        if e['status'] != 'DIFF' or e['rsFailure'] not in ('crash', 'timeout', None):
            raise ArtifactError(f'{where}: only a DIFF can be grandfathered (rsFailure crash, timeout or null)')
        if not all(HEX16.match(e['go'][k]) for k in ('stdout', 'written')):
            raise ArtifactError(f'{where}.go: malformed fingerprints')
        if not e['reason'].strip() or not HEX16.match(e['input']):
            raise ArtifactError(f'{where}: an empty reason or a malformed input identity')
        a = e['attempt']
        if not all(HEX16.match(a[k]) for k in ('stdout', 'stderrSite', 'written')) or a['inputs'] != 'intact':
            raise ArtifactError(f'{where}.attempt: malformed fingerprints, or inputs that did not stay intact')
        if not e['attempts'] or set(e['attempts']) - {'quick', 'full'} or 'full' not in e['attempts']:
            raise ArtifactError(f'{where}.attempts: must name the full mode, and only quick and full')
        for mode, labels in e['attempts'].items():
            if not isinstance(labels, list) or not labels or not all(isinstance(x, str) and LABEL.match(x) for x in labels):
                raise ArtifactError(f'{where}.attempts.{mode}: not a list of attempt labels')
    return data['items']


def load(text, origin):
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ArtifactError(f'{origin}: not JSON: {e}') from None
    return validate(data, origin)


def stderr_site(stderr):
    """tsc-rs's stderr (bytes) with the line number of its port-site line replaced by SITE_LINE, when it
    ends with the Go panic block of gopanic.rs; else unchanged (see the top comment)."""
    m = SITE.search(stderr)
    return stderr if m is None else stderr[:m.start('line')] + SITE_LINE + stderr[m.end('line'):]


def stderr_site_hash(stderr):
    """An attempt's `stderrSite`: sha256 (16 hex digits) of stderr_site(stderr), domain-tagged by whether
    the site line was normalized (version 4). Without the tag, a stderr that ends in a literal `<line>`
    (not matched by SITE, so hashed as it is) collided with a normalized one (Astra, H6 review)."""
    m = SITE.search(stderr)
    tagged = b'site\0' + stderr_site(stderr) if m is not None else b'raw\0' + stderr
    return hashlib.sha256(tagged).hexdigest()[:16]


def fingerprint(a):
    """An attempt's pinned outcome: every field of ATTEMPT."""
    return {k: a.get(k) for k in ATTEMPT}


def match(entry, row, mode):
    """None when the row fails exactly as the entry says in a run of `mode`, else why not."""
    if row['status'] != entry['status']:
        return f'status {row["status"]}, listed {entry["status"]}'
    if row.get('rsFailure') != entry['rsFailure']:
        return f'rsFailure {row.get("rsFailure")}, listed {entry["rsFailure"]}'
    why = f'{row.get("sig") or ""} {row.get("detail") or ""}'
    if 'rs-nondet' in why or 'rs-schedule' in why:
        return 'tsc-rs is nondeterministic here (never excused)'
    if row.get('input') != entry['input']:
        return f'input {row.get("input")}, listed {entry["input"]} (the case changed)'
    if row.get('go') != entry['go']:
        return f'Go {row.get("go")}, listed {entry["go"]}'
    want = entry['attempts'].get(mode)
    if want is None:
        return f'no attempts listed for a {mode} run'
    got = [a.get('why') for a in row.get('attempts') or []]
    if got != want:
        return f'attempts {got}, listed {want}'
    for a in row['attempts']:
        if fingerprint(a) != entry['attempt']:
            return f'attempt {a.get("why")} {fingerprint(a)}, listed {entry["attempt"]}'
    return None


# ---- regen ----------------------------------------------------------------------------------

def _rows(run):
    r = json.loads((Path(run) / 'results.json').read_text())
    return {k: v for t in r['tiers'].values() for k, v in t['items'].items()}, r['meta']


def _entry(item, row, quick_row, reason, evidence):
    if row is None or row.get('status') != 'DIFF' or row.get('rsFailure') not in ('crash', 'timeout', None):
        raise ArtifactError(f'{item}: not a DIFF in the evidence run: {row}')
    why = f'{row.get("sig") or ""} {row.get("detail") or ""}'
    if 'rs-nondet' in why or 'rs-schedule' in why:
        raise ArtifactError(f'{item}: tsc-rs is nondeterministic here: never pinned')
    for k in ('input', 'go', 'attempts'):
        if k not in row:
            raise ArtifactError(f'{item}: the evidence row has no {k} (made by a harness before lane H4)')
    if not all('stderrSite' in a for a in [*row['attempts'], *(quick_row or {}).get('attempts', [])]):
        raise ArtifactError(f'{item}: an evidence attempt has no stderrSite (made by a harness before lane H6)')
    prints = [fingerprint(a) for a in row['attempts']] + [fingerprint(a) for a in (quick_row or {}).get('attempts', [])]
    if any(p != prints[0] for p in prints):
        raise ArtifactError(f'{item}: its attempts differ ({prints}): nondeterministic, not pinned')
    attempts = {'full': [a['why'] for a in row['attempts']]}
    if quick_row is not None:
        if quick_row.get('input') != row['input'] or quick_row.get('go') != row['go'] or quick_row.get('status') != 'DIFF':
            raise ArtifactError(f'{item}: the quick and full evidence rows disagree')
        attempts['quick'] = [a['why'] for a in quick_row['attempts']]
    return {'status': 'DIFF', 'rsFailure': row.get('rsFailure'), 'reason': reason, 'input': row['input'], 'go': row['go'],
            'attempt': prints[0], 'attempts': attempts, 'evidence': evidence}


def regen(args):
    src = Path(args.src)
    old = json.loads(src.read_text())
    listed = dict(old.get('items', {}))
    for spec in args.add or []:
        item, _, reason = spec.partition('=')
        if not reason.strip():
            raise ArtifactError(f'--add {spec}: give ITEM=REASON')
        listed[item] = {'reason': reason}
    full, full_meta = _rows(args.full)
    quick, quick_meta = _rows(args.quick) if args.quick else ({}, {})
    if quick and quick_meta.get('mode') != 'quick' or full_meta.get('mode') != 'full':
        raise ArtifactError('--full must be a full run and --quick a quick run')
    if args.quick and quick_meta.get('rsSha') != full_meta.get('rsSha'):
        raise ArtifactError('the quick and full evidence runs are of different binaries')
    manifest = working_manifest()
    kept = {}
    if args.only:  # every other entry as it is: only from a file of this version, validated whole first
        kept = {k: v for k, v in listed.items() if not re.search(args.only, k)}
        listed = {k: v for k, v in listed.items() if re.search(args.only, k)}
        if kept and old.get('version') != VERSION:
            raise ArtifactError(f'--only keeps {len(kept)} entries as they are, but {src} is version {old.get("version")}, '
                                f'not {VERSION}: an old pin would be relabelled. Regenerate every entry (no --only)')
        if kept:
            validate(old, str(src))
    evidence = f'{Path(args.full).name}{" + " + Path(args.quick).name if args.quick else ""}: {full_meta.get("rs")} ({full_meta.get("rsSha", "")[:12]})'
    items = {}
    for item, e in sorted(listed.items(), key=lambda kv: (kv[0].split(':')[0], int(kv[0].split(':')[-1]) if kv[0].split(':')[-1].isdigit() else 0)):
        q = None
        if manifest.requires(item):
            if not args.quick:
                raise ArtifactError(f'{item} is in the quick manifest: give --quick RUN')
            q = quick.get(item)
        items[item] = _entry(item, full.get(item), q, e['reason'], evidence)
    items = dict(sorted({**kept, **items}.items(), key=lambda kv: (kv[0].split('/')[0], kv[0].split(':')[0],
                                                                    int(kv[0].split(':')[-1]) if kv[0].split(':')[-1].isdigit() else 0,
                                                                    kv[0])))
    out = {'about': ABOUT, 'version': VERSION, 'items': items}
    validate(out, 'regen')
    Path(args.out).write_text(json.dumps(out, indent=1) + '\n')
    print(f'{len(items) - len(kept)} entries from {evidence}{f", {len(kept)} kept as they were" if kept else ""} -> {args.out}')
    if args.old:
        olds, _ = _rows(args.old)
        for item, e in items.items():
            o = olds.get(item) or {}
            kinds = sorted({a.get('outcome') for a in o.get('attempts', [])})
            panic = e['go']['panic'] or ''
            agree = kinds == [e['attempt']['outcome']] and (not panic or panic[:60] in (o.get('detail') or ''))
            print(f'{"same" if agree else "DIFFERS"} {item}: now {e["attempt"]["outcome"]} {e["attempt"]["code"]}, '
                  f'Go {e["go"]["outcome"]}; {Path(args.old).name}: {kinds}')
    return 0


def evidence(args):
    """A fuzz run of exactly the listed fuzz cases and an api run of exactly the listed api scenarios
    (see the top comment)."""
    import parity_compare
    from parity_modes import MODES
    listed = [*json.loads(Path(args.src).read_text()).get('items', {}), *(args.add or [])]
    listed = [k for k in listed if re.search(args.only or '', k)]
    cases = sorted(int(k.split(':')[1]) for k in listed if re.match(r'^fuzz/1:\d+$', k))
    scenarios = sorted(k.split('/', 1)[1] for k in listed if re.match(r'^api/[a-z0-9][a-z0-9.-]*$', k))
    if len(cases) + len(scenarios) != len(listed):
        raise ArtifactError('evidence runs only fuzz cases of seed 1 and api scenarios; the file lists others')
    out = Path(args.out)
    if (out / 'results.json').exists():
        raise ArtifactError(f'{out} has results.json (results are never replaced)')
    out.mkdir(parents=True, exist_ok=True)
    bin_ = os.path.realpath(args.bin)
    go = str(Path.home() / '.local/bin/tsgo-oracle-3e9236361fa9')
    tiers = (['fuzz'] if cases else []) + (['api'] if scenarios else [])
    meta = {'mode': args.mode, 'rs': bin_, 'rsSha': hashlib.sha256(Path(bin_).read_bytes()).hexdigest(), 'go': go,
            'tiers': tiers, 'evidence': f'parity_grandfather.py evidence: the {len(cases)} fuzz cases and {len(scenarios)} '
                                         f'api scenarios of {args.src}',
            'harnessHead': subprocess.run(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
            'start': time.strftime('%FT%TZ', time.gmtime())}
    (out / 'meta.json').write_text(json.dumps(meta, indent=1))
    rc = 0
    if cases:
        rc |= subprocess.run([sys.executable, '-I', str(HERE / 'parity_fuzz.py'), '--rs', bin_, '--go', go, '--out', str(out / 'fuzz'),
                              '--seed', '1', '--cases', ','.join(map(str, cases)), '--jobs', '12',
                              '--rs-runs', str(MODES[args.mode]['rs_runs'])]).returncode
    if scenarios:
        rc |= subprocess.run([sys.executable, '-I', str(HERE / 'parity_api.py'), '--rs', bin_, '--go', go, '--out', str(out / 'api'),
                              '--tier', args.mode, '--only', '^(' + '|'.join(map(re.escape, scenarios)) + ')$']).returncode
    meta['end'] = time.strftime('%FT%TZ', time.gmtime())
    (out / 'meta.json').write_text(json.dumps(meta, indent=1))
    parity_compare.merge(out)
    return rc


ABOUT = ('Known parity items that are not good, each pinned to its exact failure (lane H4; parity_grandfather.py has the '
         'format and the matching rules). An entry excuses its item only while every recorded field is the same: '
         'status, rsFailure, the case identity, Go\'s result, the attempt list of the mode and the fingerprint of '
         'every attempt (outcome, exit code, sha256 of stdout, of stderr in its site form and of the written files, '
         'inputs intact; the site form replaces the Rust line number of tsc-rs\'s port-site line, version 4 (lane H6, domain-tagged)). '
         'Anything else fails the gate, and so does an entry whose item is good now. parity_compare.py reads this '
         'file at the accepted ref. Regenerate entries with `parity_grandfather.py regen`; add an item only with a '
         'reason a reviewer can check. The 35 fuzz cases of the full batch where Go itself crashes or times out and '
         'tsc-rs does too (listed by lane H3, from runs/h2-head-efef52257-full-2; evidence regenerated by lane H4 and, '
         'in the site form, by lane H6), and fuzz/1:5391. The api entries (lane H7, `regen --only \'^api/\'`) are known-bad '
         'tracking entries: confirmed output differences of tsc-rs from Go on the API path whose fix lane is open; that lane '
         'removes them.')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('file', nargs='?', default=str(GRANDFATHERED))
    e = sub.add_parser('evidence')
    e.add_argument('bin')
    e.add_argument('--mode', required=True, choices=('quick', 'full'))
    e.add_argument('--out', required=True)
    e.add_argument('--from', dest='src', default=str(GRANDFATHERED))
    e.add_argument('--add', action='append', help='ITEM: one more case to run')
    e.add_argument('--only', help='REGEX: run only the listed items it matches')
    r = sub.add_parser('regen')
    r.add_argument('--full', required=True)
    r.add_argument('--quick')
    r.add_argument('--old')
    r.add_argument('--add', action='append', help='ITEM=REASON: one more item to pin (a known-bad base item)')
    r.add_argument('--from', dest='src', default=str(GRANDFATHERED))
    r.add_argument('--out', default=str(GRANDFATHERED))
    r.add_argument('--only', help='REGEX: rebuild only the items it matches; keep every other entry as it is')
    args = ap.parse_args()
    try:
        if args.cmd == 'check':
            items = load(Path(args.file).read_text(), args.file)
            print(f'ok {args.file}: {len(items)} entries')
            return 0
        if args.cmd == 'evidence':
            return evidence(args)
        return regen(args)
    except ArtifactError as e:
        print(f'INVALID {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
