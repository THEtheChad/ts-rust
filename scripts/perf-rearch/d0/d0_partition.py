#!/usr/bin/env python3
"""D0-1A: how often tsc-rs's output depends on how checking is split across checkers.

Runs the parity project set (`projects`) and the multi-file compiler corpus (`corpus`) under each
checker setting (tsgo-setting.sh), --runs times each, and compares every run with the default
(c4: no flag, Go's 4 checkers). `report` reads the raw results and writes report.json and report.md.

  d0_partition.py run projects --tsgo BIN --out DIR [--jobs 2] [--runs 3] [--only REGEX]
  d0_partition.py run corpus   --tsgo BIN --out DIR [--jobs 4] [--runs 3] [--count N]
  d0_partition.py probe --tsgo BIN --out DIR          (checker counts per project config)
  d0_partition.py report --out DIR

Projects: every config of parity-projects.json (the parity gate's set) in the variants noemit
(--noEmit), emit (written files hashed, on the parity gate's overlay so the project never changes),
and extdiag (--extendedDiagnostics; the time and memory lines are masked as in the gate). A config
that lists a variant in skipVariants is skipped for that variant; a build-only config (hono
solution) has no variant here. Corpus: the multi-file cases of the pin's compiler and conformance
suite (difffuzz's corpus, each case's own options), run in one fixed directory per case.

Compared per run: exit code, stdout, stderr and the written files (sha256). Stdout is compared
byte for byte, so a stdout difference is a real difference (extdiag masks only those lines).
Results are resumable: an item with its result file is skipped.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERF = HERE.parent
sys.path.insert(0, str(PERF))
sys.path.insert(0, str(PERF / 'vendor/difffuzz'))
import parity_cli as pc  # noqa: E402  (the parity gate's overlay, runner and output masking)
import oracle  # noqa: E402  (difffuzz: corpus case writer and runner)
import seeds  # noqa: E402
from difffuzz import DEFAULT_UPSTREAM  # noqa: E402

SETTINGS = ['c1', 'c2', 'c4', 'c8', 'c16', 'fast', 'faststeal', 'fastnolazy']
DEFAULT = 'c4'
VARIANTS = ['noemit', 'emit', 'extdiag']
TIMEOUT = 600
WRAPPER = HERE / 'tsgo-setting.sh'
PROJECTS = PERF / 'parity-projects.json'

# Diagnostic codes by class (the checker audit's list). Limit: instantiation depth and count, stack
# depth, infinite circularity. Cycle: circularity and initializer cycles. Everything else is other.
LIMIT = {2589, 2321, 5114, 5115}
CYCLE = {2456, 2502, 2506, 2313, 7022, 7023, 7024}
REC_RE = re.compile(r'^(?:[^\n]*?\(\d+,\d+\): )?(?:error|warning|message) TS(\d+):')
STAT_RE = re.compile(r'^([A-Za-z][A-Za-z /()]*):[ \t]+(\S+)[ \t]*$')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def classify(code):
    return 'limit' if code in LIMIT else 'cycle' if code in CYCLE else 'other'


def launchers(out, tsgo):
    """One launcher per setting: oracle.run and Item.run_side take one executable path."""
    d = out / 'launchers'
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    for s in SETTINGS:
        p = d / s
        p.write_text(f"#!/bin/sh\nexec sh '{WRAPPER}' '{s}' '{tsgo}' \"$@\"\n")
        p.chmod(0o755)
        paths[s] = str(p)
    return paths


def project_items(only):
    for p in pc.load_projects(PROJECTS):
        if p['root'] is None:
            print(f'SKIP project {p["name"]}: no dir found', flush=True)
            continue
        for c in p['configs']:
            for v in VARIANTS:
                if v in c.get('skipVariants', []):
                    continue
                name = f'{p["name"]}/{c["label"]}/{v}'
                if only and not re.search(only, name):
                    continue
                yield name, p, c, v


def run_project(job, out, runs, launch):
    name, p, c, v, s = job
    fname = name.replace('/', '__') + '.json'
    dest = out / 'projects' / s / fname
    if dest.exists():
        return
    item = pc.Item(p, c, v)
    item.scratch = out / 'scratch' / s / item.scratch.name
    recs = []
    for _ in range(runs):
        r = item.run_side([launch[s]])[0]
        recs.append({'code': r.code, 'stdout': r.stdout.decode('utf-8', 'replace'),
                     'stdoutSha': sha(r.stdout), 'stderrSha': sha(r.stderr),
                     'tree': {f: (h if isinstance(h, str) else str(h)) for f, h in sorted(r.tree.items())},
                     'secs': round(r.secs, 2)})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps({'item': name, 'setting': s, 'runs': recs}))
    tmp.replace(dest)


def run_projects(args):
    out = Path(args.out)
    launch = launchers(out, args.tsgo)
    items = list(project_items(args.only))
    jobs = [(n, p, c, v, s) for (n, p, c, v) in items for s in SETTINGS]
    print(f'projects: {len(items)} items x {len(SETTINGS)} settings x {args.runs} runs', flush=True)
    done = 0
    def one(j):
        try:
            run_project(j, out, args.runs, launch)
        except Exception as e:  # a harness error leaves no result file: it is rerun, and reported
            print(f'ERROR {j[0]} {j[4]}: {e!r}', flush=True)

    with ThreadPoolExecutor(args.jobs) as pool:
        for _ in pool.map(one, jobs):
            done += 1
            if done % 25 == 0:
                print(f'projects {done}/{len(jobs)}', flush=True)
    print(f'projects: done {done}', flush=True)


PROBE_SETTINGS = ['c1', 'c4', 'fast']


def probe_config(p, c, out, launch):
    """The checker count of each probed setting for one config: one `types_<n>.json` trace per checker
    (--generateTrace, as tsgo_fast.rs counts them). Runs on the overlay, so the project never changes."""
    name = f'{p["name"]}__{c["label"]}'
    dest = out / 'probe' / f'{name}.json'
    if dest.exists():
        return
    root = p['root']
    cwd = root / c.get('cwd', '.')
    counts = {}
    for s in PROBE_SETTINGS:
        scratch = out / 'probe-scratch' / name / s
        pc.clear(scratch)
        upper, work, trace = scratch / 'upper', scratch / 'work', scratch / 'trace'
        upper.mkdir(parents=True)
        work.mkdir()
        trace.mkdir()
        args = ['-p', c['config'], '--noEmit', '--pretty', 'false', '--extendedDiagnostics',
                '--generateTrace', str(trace)]
        code, _, _, _ = pc.execute([*pc.OVERLAY, str(root), str(upper), str(work), str(cwd), launch[s], *args], cwd)
        counts[s] = {'exit': code, 'checkers': sum(1 for f in trace.iterdir() if f.name.startswith('types_'))}
        pc.clear(scratch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({'config': name, 'checkers': counts}))


def run_probe(args):
    out = Path(args.out)
    launch = launchers(out, args.tsgo)
    items = list(project_items(None))
    seen = {}
    for name, p, c, v in items:
        seen.setdefault(f'{p["name"]}/{c["label"]}', (p, c))
    print(f'probe: {len(seen)} configs x {PROBE_SETTINGS}', flush=True)
    for key_, (p, c) in seen.items():
        probe_config(p, c, out, launch)
        print(f'probed {key_}', flush=True)


def multi_source(case):
    return sum(1 for f in case['files'] if f.endswith(oracle.SOURCE_EXT) and 'node_modules/' not in f) > 1


def corpus_run(i, case, out, runs, launch):
    dest = out / 'corpus' / f'{i}.json'
    if dest.exists():
        return
    d = out / 'work' / f'c{i}'
    inputs = oracle.write_project(case, d)
    extra = case.get('args', [])
    res = {}
    for s in SETTINGS:
        recs = []
        for _ in range(runs):
            r = oracle.run(launch[s], d, inputs, TIMEOUT, extra)
            recs.append({'code': r.code, 'stdout': r.stdout.decode('utf-8', 'replace'),
                         'stdoutSha': sha(r.stdout), 'stderrSha': sha(r.stderr),
                         'tree': {f: sha(b) for f, b in sorted(r.tree.items())}, 'secs': round(r.secs, 2)})
        res[s] = recs
    shutil.rmtree(d, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps({'name': case['name'], 'settings': res}))
    tmp.replace(dest)


def run_corpus(args):
    out = Path(args.out)
    launch = launchers(out, args.tsgo)
    corpus, _ = seeds.load(DEFAULT_UPSTREAM)
    multi = [i for i, c in enumerate(corpus) if multi_source(c)]
    if args.count:
        multi = multi[:args.count]
    print(f'corpus: {len(multi)} multi-file cases (of {len(corpus)}) x {len(SETTINGS)} settings x {args.runs} runs',
          flush=True)
    def one(i):
        try:
            corpus_run(i, corpus[i], out, args.runs, launch)
        except Exception as e:  # recorded as an error result, never as a match
            d = out / 'corpus' / f'{i}.json'
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_text(json.dumps({'name': corpus[i]['name'], 'error': repr(e)[:300]}))

    done = 0
    with ThreadPoolExecutor(args.jobs) as pool:
        for _ in pool.map(one, multi):
            done += 1
            if done % 200 == 0:
                print(f'corpus {done}/{len(multi)}', flush=True)
    print(f'corpus: done {done}', flush=True)


# ---------- report ----------

CONFIG_REC_RE = re.compile(r'^(?:tsconfig\.json\(\d+,\d+\)|error TS5\d{3}): ')


def config_stop(stdout):
    """True when every diagnostic is a tsconfig error or a global option error (5xxx): the run stops
    before checking, so the checker partition cannot matter."""
    recs, _ = parse_records(stdout)
    return bool(recs) and all(CONFIG_REC_RE.match(t.split('\n')[0]) for _, t in recs)


def key(run):
    return (run['code'], run['stdoutSha'], run['stderrSha'], tuple(sorted(run['tree'].items())))


def parse_records(text):
    """Diagnostic records (code, text) with their continuation lines, and the other lines."""
    recs, other = [], []
    for line in text.splitlines():
        m = REC_RE.match(line)
        if m:
            recs.append([int(m.group(1)), line])
        elif line[:1] in (' ', '\t') and recs:
            recs[-1][1] += '\n' + line
        else:
            other.append(line)
    return recs, other


def stats(lines):
    """Numeric statistic lines (name -> value). Masked lines (Memory) are not numeric."""
    vals = {}
    rest = []
    for line in lines:
        m = STAT_RE.match(line)
        if m and m.group(2).replace(',', '').isdigit():
            vals[m.group(1)] = int(m.group(2).replace(',', ''))
        else:
            rest.append(line)
    return vals, rest


def compare(ref, run):
    """One run against the default's first run: output differences (exit, stdout diagnostics, other
    stdout lines, stderr, written files) kept apart from statistic-count differences."""
    row = {'exit': None, 'diagRefOnly': 0, 'diagSetOnly': 0, 'codes': {}, 'otherLines': 0,
           'files': [], 'stderr': False, 'stats': {}}
    if ref['code'] != run['code']:
        row['exit'] = [ref['code'], run['code']]
    ra, oa = parse_records(ref['stdout'])
    rb, ob = parse_records(run['stdout'])
    ca = Counter(t for _, t in ra)
    cb = Counter(t for _, t in rb)
    for t, n in (ca - cb).items():
        code = next(c for c, x in ra if x == t)
        row['diagRefOnly'] += n * (t.count('\n') + 1)
        row['codes'][f'ref TS{code} ({classify(code)})'] = row['codes'].get(f'ref TS{code} ({classify(code)})', 0) + n
    for t, n in (cb - ca).items():
        code = next(c for c, x in rb if x == t)
        row['diagSetOnly'] += n * (t.count('\n') + 1)
        row['codes'][f'set TS{code} ({classify(code)})'] = row['codes'].get(f'set TS{code} ({classify(code)})', 0) + n
    va, ra_rest = stats(oa)
    vb, rb_rest = stats(ob)
    row['otherLines'] = sum((Counter(ra_rest) - Counter(rb_rest)).values()) \
        + sum((Counter(rb_rest) - Counter(ra_rest)).values())
    row['stats'] = {k: vb[k] - va[k] for k in va if k in vb and va[k] != vb[k]}
    rt, st = ref['tree'], run['tree']
    row['files'] = sorted(f for f in set(rt) | set(st) if rt.get(f) != st.get(f))
    row['stderr'] = ref['stderrSha'] != run['stderrSha']
    row['output'] = bool(row['exit'] or row['diagRefOnly'] or row['diagSetOnly'] or row['otherLines']
                         or row['files'] or row['stderr'])
    row['counts'] = bool(row['stats'])
    row['status'] = 'OUTPUT' if row['output'] else 'COUNTS' if row['counts'] else 'identical'
    return row


def worst(statuses):
    return 'OUTPUT' if 'OUTPUT' in statuses else 'COUNTS' if 'COUNTS' in statuses else 'identical'


def summarize(item_runs):
    """Per setting: the status of each run against the default, and within-setting variation."""
    ref = item_runs[DEFAULT]
    out = {'defaultNondet': len({key(r) for r in ref}) > 1, 'settings': {}}
    for s in SETTINGS:
        runs = item_runs[s]
        rows = [compare(ref[0], r) for r in runs]
        out['settings'][s] = {
            'runs': len(runs),
            'statuses': [r['status'] for r in rows],
            'status': worst([r['status'] for r in rows]),
            'withinSettingNondet': len({key(r) for r in runs}) > 1,
            'detail': next((r for r in rows if r['status'] != 'identical'), None),
        }
    return out


def merge(a, b):
    for k, v in b.items():
        a[k] = a.get(k, 0) + v


def report(args):
    out = Path(args.out)
    result = {'projects': {}, 'corpus': {}}
    pdir = out / 'projects'
    if pdir.exists():
        for f in sorted((pdir / DEFAULT).glob('*.json')):
            item_runs = {}
            for s in SETTINGS:
                p = pdir / s / f.name
                if p.exists():
                    item_runs[s] = json.loads(p.read_text())['runs']
            if len(item_runs) < len(SETTINGS):
                continue
            proj = f.stem.split('__')[0]
            result['projects'].setdefault(proj, {})[f.stem] = summarize(item_runs)
    cdir = out / 'corpus'
    cfiles = sorted(cdir.glob('*.json'), key=lambda p: int(p.stem)) if cdir.exists() else []
    agg = {s: {'cases': 0, 'OUTPUT': 0, 'COUNTS': 0, 'nondet': 0, 'codes': {}, 'differingCases': []} for s in SETTINGS}
    errors, notChecked, defaultNondet = [], [], 0
    for f in cfiles:
        d = json.loads(f.read_text())
        if 'error' in d:
            errors.append(d['name'])
            continue
        runs = d['settings']
        if len(runs) < len(SETTINGS):
            continue
        # a case that stops at the config (tsconfig errors only, no checking) is counted apart
        if config_stop(runs[DEFAULT][0]['stdout']):
            notChecked.append(d['name'])
        summ = summarize(runs)
        defaultNondet += summ['defaultNondet']
        for s in SETTINGS:
            a = agg[s]
            a['cases'] += 1
            st = summ['settings'][s]
            if st['status'] != 'identical':
                a[st['status']] += 1
            if st['withinSettingNondet']:
                a['nondet'] += 1
            if st['status'] == 'OUTPUT':
                a['differingCases'].append(d['name'])
                merge(a['codes'], st['detail']['codes'])
    result['corpus'] = {'cases': len(cfiles), 'errors': errors, 'configStopCases': len(notChecked),
                        'defaultNondetCases': defaultNondet, 'perSetting': {s: {k: v for k, v in agg[s].items()
                                                                              if k != 'differingCases'} for s in SETTINGS},
                        'differingCases': {s: agg[s]['differingCases'] for s in SETTINGS},
                        'configStopCaseNames': notChecked}
    (out / 'report.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({'corpus': {k: v for k, v in result['corpus'].items() if k not in ('differingCases', 'configStopCaseNames')}}, indent=1))
    n = sum(len(v) for v in result['projects'].values())
    print(f'projects: {n} items in {len(result["projects"])} projects; report.json written to {out}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('what', choices=['projects', 'corpus'])
    r.add_argument('--tsgo', required=True, help='the tsgo binary (absolute path)')
    r.add_argument('--out', required=True)
    r.add_argument('--jobs', type=int, default=2)
    r.add_argument('--runs', type=int, default=3)
    r.add_argument('--only', help='regex on item names (projects)')
    r.add_argument('--count', type=int, default=0, help='first N multi-file cases (corpus; 0 = all)')
    pr = sub.add_parser('probe', help='checker counts per config (projects)')
    pr.add_argument('--tsgo', required=True)
    pr.add_argument('--out', required=True)
    rep = sub.add_parser('report')
    rep.add_argument('--out', required=True)
    args = ap.parse_args()
    if args.cmd == 'run':
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (run_projects if args.what == 'projects' else run_corpus)(args)
    elif args.cmd == 'probe':
        Path(args.out).mkdir(parents=True, exist_ok=True)
        run_probe(args)
    else:
        report(args)


if __name__ == '__main__':
    main()
