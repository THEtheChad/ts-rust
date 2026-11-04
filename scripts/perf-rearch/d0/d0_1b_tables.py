#!/usr/bin/env python3
"""D0-1B: tables from report-1b.json (d0_1b.py report): fire rates by class and margin, per project item and
setting, with the soft constraint cut and the cycle kinds kept apart.

  d0_1b_tables.py OUT_DIR
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d0_1b  # noqa: E402

HARD = ['inst_limit', 'tail_limit', 'rel_overflow', 'rel_depth_limit', 'rel_complexity', 'complexity', 'constraint_depth']


def cls(mon, margin):
    depth, vdepth, count, tail, rel_depth, rel_used = d0_1b.MARGINS[margin]
    p, n = mon['peak'], mon['n']
    out = set()
    if (p['depth'] >= depth or p['count'] >= count or p['tail'] >= tail or p['rel_depth'] >= rel_depth
            or p['rel_used'] >= rel_used or any(n.get(k) for k in HARD)):
        out.add('limit')
    if p.get('vdepth', 0) >= vdepth:
        out.add('shadow')
    if n.get('constraint_cut'):
        out.add('ccut')
    if n.get('cycle'):
        out.add('cycle_tr')
    if n.get('cycle_signature') or n.get('cycle_contextual') or n.get('cycle_default'):
        out.add('cycle_sig')
    if n.get('cross_file_diag'):
        out.add('xdiag')
    for k, c in (('deep_flip', 'deep_flip'), ('deep_true', 'deep_true'), ('type_id_tie', 'type_id'),
                 ('union_key_id', 'type_id'), ('symbol_id_tie', 'symbol_id'), ('late_bound_len', 'late_bound')):
        if n.get(k):
            out.add(c)
    return out


POLICIES = {
    'L': {'limit'},
    'L+S': {'limit', 'shadow'},
    'L+S+ccut': {'limit', 'shadow', 'ccut'},
    'L+S+cycle_tr': {'limit', 'shadow', 'cycle_tr'},
    'L+S+cycles': {'limit', 'shadow', 'cycle_tr', 'cycle_sig'},
    'L+S+xdiag': {'limit', 'shadow', 'xdiag'},
    'L+S+cycle_tr+xdiag': {'limit', 'shadow', 'cycle_tr', 'xdiag'},
    'L+S+ccut+cycles+xdiag': {'limit', 'shadow', 'ccut', 'cycle_tr', 'cycle_sig', 'xdiag'},
    '+deep_flip': {'limit', 'shadow', 'ccut', 'cycle_tr', 'cycle_sig', 'xdiag', 'deep_flip'},
    '+ids': {'limit', 'shadow', 'ccut', 'cycle_tr', 'cycle_sig', 'xdiag', 'deep_flip', 'type_id', 'symbol_id',
             'late_bound'},
}


def rates(group, margin, policy, large=None):
    want = POLICIES[policy]
    div = fired_div = clean = fired_clean = 0
    items, items_fired = set(), set()
    misses = []
    for name, settings in group.items():
        if large is not None and name not in large:
            continue
        ref = settings[d0_1b.DEFAULT][0]
        for s, runs in settings.items():
            for r in runs:
                st = d0_1b.d0.compare(ref, r)['status']
                f = cls(r['monitor'], margin) & want
                if st == 'OUTPUT':
                    div += 1
                    fired_div += bool(f)
                    if not f:
                        misses.append(f'{name} {s}')
                else:
                    clean += 1
                    fired_clean += bool(f)
                    items.add(name)
                    if f:
                        items_fired.add(name)
    return {'div': div, 'divFired': fired_div, 'misses': misses, 'clean': clean, 'cleanFired': fired_clean,
            'items': len(items), 'itemsFired': len(items_fired)}


def main():
    out = Path(sys.argv[1])
    projects = d0_1b.load_projects(out)
    corpus = d0_1b.load_corpus(out)
    # large: a project item whose default run does at least 1M instantiations (all checkers)
    large = {n for n, s in projects.items() if s[d0_1b.DEFAULT][0]['monitor']['work'] >= 1_000_000}
    print('large project items (>= 1M instantiations at c4):', len(large), 'of', len(projects))
    print('\nfire rates: divergent fired/divergent | clean runs fired/clean | clean items fired/items')
    for grp_name, grp, lg in (('projects', projects, None), ('large', projects, large), ('corpus', corpus, None)):
        print(f'\n[{grp_name}]')
        hdr = f'{"policy":24s}' + ''.join(f'{m:>34s}' for m in d0_1b.MARGINS)
        print(hdr)
        for pol in POLICIES:
            row = f'{pol:24s}'
            for m in d0_1b.MARGINS:
                r = rates(grp, m, pol, lg)
                row += f'   {r["divFired"]:>3d}/{r["div"]:<3d} {r["cleanFired"]:>5d}/{r["clean"]:<5d} {r["itemsFired"]:>4d}/{r["items"]:<4d}'
            print(row)
    # cycles by kind and per-kind run counts
    kinds = Counter()
    for grp in (projects, corpus):
        pass
    print('\nper-item c4 counts of the cycle and diag kinds (projects, noemit):')
    for name, s in sorted(projects.items()):
        if not name.endswith('/noemit'):
            continue
        n = s[d0_1b.DEFAULT][0]['monitor']['n']
        print(f'  {name:34s}', {k: n.get(k, 0) for k in ('cycle', 'cycle_signature', 'cycle_contextual', 'cycle_default',
                                                          'cross_file_diag', 'constraint_cut', 'deep_flip')})


if __name__ == '__main__':
    main()
