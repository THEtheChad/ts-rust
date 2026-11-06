#!/usr/bin/env python3
"""D0-1B: does the partition-sensitivity monitor fire on every run whose output differs from the default split?

Runs like D0-1A (d0_partition.py: the parity project set, the multi-file corpus, and the adversarial fixtures of
make_fixtures.py) with the monitor on (GOPORT_PARTITION_MONITOR, one events file per run), and keeps, per run,
the output key and the monitor's summary: event counts per kind, the peaks of every limit quantity, and the
first time each milestone was crossed. `report` applies firing policies offline (margins on the peaks), so one
run serves every margin.

  d0_1b.py run projects --tsgo BIN --out DIR [--jobs 2] [--runs 1] [--only REGEX]
  d0_1b.py run corpus   --tsgo BIN --out DIR [--jobs 4] [--count N]
  d0_1b.py run fixtures --tsgo BIN --out DIR --fixtures FXDIR
  d0_1b.py report --out DIR

The monitored binary must be passive: parity.sh quick on it prints PARITY OK.
"""
import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import d0_partition as d0  # noqa: E402  (D0-1A: settings, items, compare)

pc, oracle, seeds = d0.pc, d0.oracle, d0.seeds
SETTINGS = d0.SETTINGS
DEFAULT = d0.DEFAULT

# Event kinds by class (partition_monitor.rs). Margin-based quantities are judged on the summary peaks.
LIMIT_HITS = {'inst_limit', 'tail_limit', 'rel_overflow', 'rel_depth_limit', 'rel_complexity', 'complexity',
              'constraint_cut', 'constraint_depth'}
CYCLES = {'cycle', 'cycle_signature', 'cycle_contextual', 'cycle_default'}
IDS = {'deep_flip', 'deep_true', 'type_id_tie', 'union_key_id', 'symbol_id_tie', 'late_bound_len'}
DIAG = {'cross_file_diag'}
PEAKS = ['depth', 'vdepth', 'count', 'tail', 'rel_depth', 'rel_used', 'rel_size', 'constraint_stack',
         'cond_constraint']


def read_monitor(path):
    """The monitor file of one run: per-kind counts (summed over checkers), peaks (max), per-checker summaries,
    the first time and work of each milestone (quantity, value) and of each event kind, and the exit time."""
    out = {'n': Counter(), 'peak': {k: 0 for k in PEAKS}, 'checkers': 0, 'first': {}, 'exit': None,
           'work': 0, 'samples': {}}
    if not path.exists():
        out['missing'] = True
        return out
    for line in path.read_text(errors='replace').splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        k = e.get('k')
        if k == 'summary':
            out['checkers'] += 1
            out['work'] += e.get('work', 0)
            for q, v in e['peak'].items():
                out['peak'][q] = max(out['peak'].get(q, 0), v)
            for kind, n in e['n'].items():
                out['n'][kind] += n
        elif k == 'exit':
            out['exit'] = e['t']
        elif k == 'process':
            pass
        else:
            name = f"ms:{e.get('n')}:{e['v']}" if k == 'ms' else k
            prev = out['first'].get(name)
            if prev is None or e['t'] < prev['t']:
                out['first'][name] = {'t': e['t'], 'w': e['w'], 'fi': e['fi'], 'c': e['c']}
            if k != 'ms' and k not in out['samples']:
                out['samples'][k] = {x: e[x] for x in e if x not in ('k',)}
    out['n'] = dict(out['n'])
    return out


# ---------- runs ----------

def mon_dir(out):
    d = out / 'monitor'
    d.mkdir(parents=True, exist_ok=True)
    return d


def record(r, mon):
    return {'code': r.code, 'stdout': r.stdout.decode('utf-8', 'replace'), 'stdoutSha': d0.sha(r.stdout),
            'stderrSha': d0.sha(r.stderr),
            'tree': {f: (h if isinstance(h, str) else str(h)) for f, h in sorted(r.tree.items())},
            'secs': round(r.secs, 2), 'monitor': mon}


def run_project(job, out, launch):
    name, p, c, v, s, k = job
    fname = name.replace('/', '__') + f'.{k}.json'
    dest = out / 'projects' / s / fname
    if dest.exists():
        return
    item = pc.Item(p, c, v)
    item.scratch = out / 'scratch' / f'{s}-{k}' / item.scratch.name
    mpath = mon_dir(out) / f'{name.replace("/", "__")}.{s}.{k}.jsonl'
    mpath.unlink(missing_ok=True)
    r = item.run_side(['env', f'GOPORT_PARTITION_MONITOR={mpath}', launch[s]])[0]
    rec = record(r, read_monitor(mpath))
    mpath.unlink(missing_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps({'item': name, 'setting': s, 'run': k, 'rec': rec}))
    tmp.replace(dest)


def run_projects(args):
    out = Path(args.out)
    launch = d0.launchers(out, args.tsgo)
    items = list(d0.project_items(args.only))
    jobs = []
    for (n, p, c, v) in items:
        for s in SETTINGS:
            jobs += [(n, p, c, v, s, k) for k in range(args.runs)]
    print(f'projects: {len(items)} items, {len(jobs)} runs', flush=True)

    def one(j):
        try:
            run_project(j, out, launch)
        except Exception as e:
            print(f'ERROR {j[0]} {j[4]}: {e!r}', flush=True)

    done = 0
    with ThreadPoolExecutor(args.jobs) as pool:
        for _ in pool.map(one, jobs):
            done += 1
            if done % 25 == 0:
                print(f'projects {done}/{len(jobs)}', flush=True)
    print(f'projects: done {done}', flush=True)


def corpus_launchers(out, tsgo):
    """Per setting: a launcher that writes the monitor file of the run to monitor/<case dir name>.<setting>.jsonl
    (the runs of one case are sequential, so the name is unique while it is read)."""
    d = out / 'launchers-pm'
    d.mkdir(parents=True, exist_ok=True)
    mdir = mon_dir(out)
    paths = {}
    for s in SETTINGS:
        p = d / s
        p.write_text(f'#!/bin/sh\nGOPORT_PARTITION_MONITOR="{mdir}/$(basename "$PWD").{s}.jsonl" '
                     f'exec sh \'{d0.WRAPPER}\' \'{s}\' \'{tsgo}\' "$@"\n')
        p.chmod(0o755)
        paths[s] = str(p)
    return paths


def corpus_case(i, case, out, launch):
    dest = out / 'corpus' / f'{i}.json'
    if dest.exists():
        return
    d = out / 'work' / f'c{i}'
    inputs = oracle.write_project(case, d)
    extra = case.get('args', [])
    res = {}
    for s in SETTINGS:
        mpath = mon_dir(out) / f'c{i}.{s}.jsonl'
        mpath.unlink(missing_ok=True)
        r = oracle.run(launch[s], d, inputs, d0.TIMEOUT, extra)
        res[s] = [{'code': r.code, 'stdout': r.stdout.decode('utf-8', 'replace'), 'stdoutSha': d0.sha(r.stdout),
                   'stderrSha': d0.sha(r.stderr), 'tree': {f: d0.sha(b) for f, b in sorted(r.tree.items())},
                   'secs': round(r.secs, 2), 'monitor': read_monitor(mpath)}]
        mpath.unlink(missing_ok=True)
    shutil.rmtree(d, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps({'name': case['name'], 'settings': res}))
    tmp.replace(dest)


def run_corpus(args):
    out = Path(args.out)
    launch = corpus_launchers(out, args.tsgo)
    corpus, _ = seeds.load(d0.DEFAULT_UPSTREAM)
    multi = [i for i, c in enumerate(corpus) if d0.multi_source(c)]
    if args.count:
        multi = multi[:args.count]
    print(f'corpus: {len(multi)} multi-file cases x {len(SETTINGS)} settings', flush=True)

    def one(i):
        try:
            corpus_case(i, corpus[i], out, launch)
        except Exception as e:
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


FIXTURE_SETTINGS = ['c1', 'c2', 'c4', 'c8', 'c16']


def run_fixtures(args):
    """Each fixture dir (tsconfig.json at its root) at each setting, in place, with the monitor; plus the Go
    oracle once. The fixtures write nothing (noEmit)."""
    import subprocess
    out = Path(args.out)
    launch = d0.launchers(out, args.tsgo)
    res = {}
    for fx in sorted(Path(args.fixtures).iterdir()):
        if not (fx / 'tsconfig.json').exists():
            continue
        row = {}
        go = subprocess.run([str(Path.home() / '.local/bin/tsgo-oracle-3e9236361fa9'), '-p', '.', '--pretty', 'false'],
                            cwd=fx, capture_output=True, env=pc.ENV)
        row['go'] = {'code': go.returncode, 'stdout': go.stdout.decode('utf-8', 'replace')}
        for s in FIXTURE_SETTINGS:
            runs = []
            k = 0
            mpath = mon_dir(out) / f'fx-{fx.name}.{s}.{k}.jsonl'
            mpath.unlink(missing_ok=True)
            r = subprocess.run(['env', f'GOPORT_PARTITION_MONITOR={mpath}', launch[s], '-p', '.', '--pretty',
                                'false'], cwd=fx, capture_output=True, env=pc.ENV)
            runs.append({'code': r.returncode, 'stdout': r.stdout.decode('utf-8', 'replace'),
                         'stdoutSha': d0.sha(r.stdout), 'stderrSha': d0.sha(r.stderr), 'tree': {},
                         'monitor': read_monitor(mpath)})
            mpath.unlink(missing_ok=True)
            row[s] = runs
        res[fx.name] = row
        print('fixture', fx.name, flush=True)
    (out / 'fixtures.json').write_text(json.dumps(res, indent=1))


# ---------- report ----------

MARGINS = {  # name: (depth, vdepth, count, tail, rel_depth, rel_used)
    'm25': (25, 25, 500_000, 100, 25, 250_000),
    'm50': (50, 50, 1_000_000, 250, 50, 1_000_000),
    'm75': (75, 75, 2_500_000, 500, 75, 1_000_000),
    'm90': (90, 90, 2_500_000, 500, 90, 1_000_000),
    'limit': (100, 100, 5_000_000, 1000, 100, 2_000_000),
}


def classes(mon, margin):
    """The event classes that fire for one run's monitor at one margin set."""
    depth, vdepth, count, tail, rel_depth, rel_used = MARGINS[margin]
    p, n = mon['peak'], mon['n']
    fired = set()
    if (p['depth'] >= depth or p['count'] >= count or p['tail'] >= tail or p['rel_depth'] >= rel_depth
            or p['rel_used'] >= rel_used or any(n.get(k) for k in LIMIT_HITS)):
        fired.add('limit')
    if p.get('vdepth', 0) >= vdepth:
        fired.add('shadow')
    if any(n.get(k) for k in CYCLES):
        fired.add('cycle')
    if n.get('deep_flip'):
        fired.add('deep_flip')
    if n.get('deep_true'):
        fired.add('deep_true')
    if n.get('type_id_tie') or n.get('union_key_id'):
        fired.add('type_id')
    if n.get('symbol_id_tie'):
        fired.add('symbol_id')
    if n.get('late_bound_len'):
        fired.add('late_bound')
    if n.get('cross_file_diag'):
        fired.add('cross_diag')
    return fired


# Policies: the classes that count as a fire.
POLICIES = {
    'limits': {'limit'},
    'limits+shadow': {'limit', 'shadow'},
    'limits+shadow+cycles': {'limit', 'shadow', 'cycle'},
    'limits+shadow+cycles+diag': {'limit', 'shadow', 'cycle', 'cross_diag'},
    'all-but-deep_true': {'limit', 'shadow', 'cycle', 'cross_diag', 'deep_flip', 'type_id', 'symbol_id', 'late_bound'},
    'all': {'limit', 'shadow', 'cycle', 'cross_diag', 'deep_flip', 'deep_true', 'type_id', 'symbol_id', 'late_bound'},
}


def first_fire(mon, margin, policy):
    """The earliest (t ms, checker work) among the events that make the policy fire, from the milestones."""
    depth, vdepth, count, tail, rel_depth, rel_used = MARGINS[margin]
    want = POLICIES[policy]
    cands = []
    f = mon['first']

    def ms(q, m):
        hits = [v for k, v in f.items() if k.startswith(f'ms:{q}:') and int(k.split(':')[2]) >= m]
        return min(hits, key=lambda v: v['t']) if hits else None

    if 'limit' in want:
        for q, m in (('depth', depth), ('count', count), ('tail', tail), ('rel_depth', rel_depth), ('rel_used', rel_used)):
            cands.append(ms(q, m))
        cands += [f.get(k) for k in LIMIT_HITS]
    if 'shadow' in want:
        cands.append(ms('vdepth', vdepth))
    kinds = {'cycle': CYCLES, 'deep_flip': {'deep_flip'}, 'deep_true': {'deep_true'},
             'type_id': {'type_id_tie', 'union_key_id'}, 'symbol_id': {'symbol_id_tie'},
             'late_bound': {'late_bound_len'}, 'cross_diag': DIAG}
    for cls, ks in kinds.items():
        if cls in want:
            cands += [f.get(k) for k in ks]
    cands = [c for c in cands if c]
    return min(cands, key=lambda v: v['t']) if cands else None


def item_rows(item_runs):
    """Per setting and run: divergent (output differs from the default's first run), count-only, and the
    monitor of the run."""
    ref = item_runs[DEFAULT][0]
    rows = {}
    for s, runs in item_runs.items():
        rows[s] = []
        for r in runs:
            cmp = d0.compare(ref, r)
            rows[s].append({'status': cmp['status'], 'codes': cmp['codes'], 'mon': r['monitor']})
    return rows


def load_projects(out):
    res = defaultdict(lambda: defaultdict(list))
    for f in sorted((out / 'projects').glob('*/*.json')):
        d = json.loads(f.read_text())
        res[d['item']][d['setting']].append(d['rec'])
    return {k: dict(v) for k, v in res.items() if DEFAULT in v}


def load_corpus(out):
    res = {}
    for f in sorted((out / 'corpus').glob('*.json'), key=lambda p: int(p.stem)):
        d = json.loads(f.read_text())
        if 'error' in d or len(d['settings']) < len(SETTINGS):
            continue
        if d0.config_stop(d['settings'][DEFAULT][0]['stdout']):
            continue
        res[d['name']] = d['settings']
    return res


def summarize(group, margin, policy):
    """Divergent runs, how many fired, the misses; the clean runs (identical or count-only) and how many fired."""
    want = POLICIES[policy]
    s = {'runs': 0, 'divergent': 0, 'divFired': 0, 'misses': [], 'clean': 0, 'cleanFired': 0, 'cleanItems': set(),
         'cleanItemsFired': set(), 'firstFrac': [], 'byClass': Counter()}
    for name, item_runs in group.items():
        rows = item_rows(item_runs)
        for st, runs in rows.items():
            for r in runs:
                s['runs'] += 1
                fired = classes(r['mon'], margin) & want
                if r['status'] == 'OUTPUT':
                    s['divergent'] += 1
                    if fired:
                        s['divFired'] += 1
                    else:
                        s['misses'].append(f'{name} {st} {r["codes"]}')
                else:
                    s['clean'] += 1
                    s['cleanItems'].add(name)
                    if fired:
                        s['cleanFired'] += 1
                        s['cleanItemsFired'].add(name)
                        for c in fired:
                            s['byClass'][c] += 1
                    ff = first_fire(r['mon'], margin, policy) if fired else None
                    if ff and r['mon'].get('exit'):
                        s['firstFrac'].append(ff['t'] / r['mon']['exit'])
    s['cleanItems'] = len(s['cleanItems'])
    s['cleanItemsFired'] = len(s['cleanItemsFired'])
    fr = sorted(s.pop('firstFrac'))
    s['firstFracMedian'] = round(fr[len(fr) // 2], 3) if fr else None
    s['byClass'] = dict(s['byClass'])
    return s


def report(args):
    out = Path(args.out)
    projects = load_projects(out) if (out / 'projects').exists() else {}
    corpus = load_corpus(out) if (out / 'corpus').exists() else {}
    rep = {'projects': {}, 'corpus': {}, 'projectTable': {}}
    for margin in MARGINS:
        for policy in POLICIES:
            key = f'{margin}/{policy}'
            if projects:
                rep['projects'][key] = summarize(projects, margin, policy)
            if corpus:
                rep['corpus'][key] = summarize(corpus, margin, policy)
    # Per project item x setting: divergent?, fired classes at m50, first event (ms, fraction of the run).
    for name, item_runs in sorted(projects.items()):
        rows = item_rows(item_runs)
        tab = {}
        for st, runs in rows.items():
            cells = []
            for r in runs:
                fired = sorted(classes(r['mon'], 'm50'))
                ff = first_fire(r['mon'], 'm50', 'all')
                cells.append({'div': r['status'], 'fired': fired, 'peak': r['mon']['peak'],
                              'n': r['mon']['n'], 'first': ff, 'exit': r['mon'].get('exit'),
                              'codes': r['codes'] if r['status'] == 'OUTPUT' else {}})
            tab[st] = cells
        rep['projectTable'][name] = tab
    # Corpus: per-class fire counts on clean cases at m50 (by case, any setting), and the per-kind totals.
    kinds = Counter()
    for name, settings in corpus.items():
        for s, runs in settings.items():
            for r in runs:
                for k, n in r['monitor']['n'].items():
                    if n:
                        kinds[k] += 1
    rep['corpusKindRuns'] = dict(kinds)
    if (out / 'fixtures.json').exists():
        fx = json.loads((out / 'fixtures.json').read_text())
        ftab = {}
        for name, row in fx.items():
            ref = row['c4'][0]
            go = row['go']['stdout']
            t = {'goEqualsDefault': go == ref['stdout'], 'go': go[:300]}
            for s in FIXTURE_SETTINGS:
                cells = []
                for r in row[s]:
                    cmp = d0.compare(ref, r)
                    cells.append({'div': cmp['status'], 'codes': cmp['codes'],
                                  'fired_m50': sorted(classes(r['monitor'], 'm50')),
                                  'peak': {k: r['monitor']['peak'][k] for k in ('depth', 'vdepth', 'count', 'tail')},
                                  'n': r['monitor']['n']})
                t[s] = cells
            ftab[name] = t
        rep['fixtures'] = ftab
    (out / 'report-1b.json').write_text(json.dumps(rep, indent=1, default=list) + '\n')
    for grp in ('projects', 'corpus'):
        for key, s in rep[grp].items():
            if key.startswith('m50/') or key.startswith('limit/') or key.endswith('/limits+shadow+cycles+diag'):
                print(grp, key, {k: v for k, v in s.items() if k != 'misses'}, 'misses:', s['misses'][:6])
    print('report-1b.json written to', out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('what', choices=['projects', 'corpus', 'fixtures'])
    r.add_argument('--tsgo', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--jobs', type=int, default=2)
    r.add_argument('--runs', type=int, default=1)
    r.add_argument('--only')
    r.add_argument('--count', type=int, default=0)
    r.add_argument('--fixtures')
    rep = sub.add_parser('report')
    rep.add_argument('--out', required=True)
    args = ap.parse_args()
    if args.cmd == 'run':
        Path(args.out).mkdir(parents=True, exist_ok=True)
        {'projects': run_projects, 'corpus': run_corpus, 'fixtures': run_fixtures}[args.what](args)
    else:
        report(args)


if __name__ == '__main__':
    main()
