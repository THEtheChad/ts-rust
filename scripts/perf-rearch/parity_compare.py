#!/usr/bin/env python3
"""Results of the parity gate: merge the tiers of a run, compare a run with a base, list known diffs.

usage: parity_compare.py merge DIR            DIR/<tier>/results.json -> DIR/results.json
       parity_compare.py compare BASE CUR     exit 1 when an item that is good in BASE is not good in CUR
       parity_compare.py known DIR            every item that is not good (the known diffs of a base);
                                              IGNORED and SKIP only counted

Good: MATCH (CLI, LSP, fuzz, corpus), PASS and FLAKY (tests; FLAKY: passed when run again alone). compare fails on an item that is good in BASE
and is DIFF, FAIL, ERROR, UNRUN, SKIP or IGNORED in CUR (a regression), and, when both runs are the
same mode (quick or full), on such an item that CUR does not have (not run), and on any ERROR item
in CUR (a harness error, or a tier that ended without results). Items that CUR has and
BASE does not are new: listed, never a failure. An item that is GO-UNSTABLE in CUR is not compared
(Go results are cached, so this needs a new Go cache). Items that are not good in BASE are known
diffs: listed when they recover.
"""
import json
import sys
from collections import Counter
from pathlib import Path

GOOD = {'MATCH', 'PASS', 'FLAKY'}
TIERS = ('cli', 'tests', 'lsp', 'fuzz', 'corpus', 'js')


def load(d):
    return json.loads((Path(d) / 'results.json').read_text())


def merge(d):
    d = Path(d)
    meta = json.loads((d / 'meta.json').read_text()) if (d / 'meta.json').exists() else {}
    tiers = {}
    for t in TIERS:
        f = d / t / 'results.json'
        if not f.exists() and t in meta.get('tiers', []):
            tiers[t] = {'summary': {'error': 'tier ended without results.json'}, 'items': {f'{t}/(tier)': {'status': 'ERROR'}}}
        elif f.exists():
            r = json.loads(f.read_text())
            items = r.pop('items')
            tiers[t] = {'summary': r, 'items': items}
        elif (d / t).exists():
            tiers[t] = {'summary': {'error': 'tier ended without results.json'}, 'items': {f'{t}/(tier)': {'status': 'ERROR'}}}
    out = {'meta': meta, 'tiers': tiers}
    (d / 'results.json').write_text(json.dumps(out, indent=1) + '\n')
    for t, v in tiers.items():
        c = Counter(i['status'] for i in v['items'].values())
        print(f'{t:<7} {dict(sorted(c.items()))}  {v["summary"].get("wallSecs") or v["summary"].get("secs", {}).get("wall", "")}s')


def items_of(r):
    return {k: v for t in r['tiers'].values() for k, v in t['items'].items()}


def compare(base, cur):
    b, c = load(base), load(cur)
    bi, ci = items_of(b), items_of(c)
    same_mode = b['meta'].get('mode') == c['meta'].get('mode')
    lost, unrun, unstable, recovered, new_bad = [], [], [], [], []
    for k, v in bi.items():
        now = ci.get(k)
        if v['status'] in GOOD:
            if now is None:
                if same_mode:
                    unrun.append(k)
            elif now['status'] == 'GO-UNSTABLE':
                unstable.append(k)
            elif now['status'] not in GOOD:
                lost.append((k, now['status'], now.get('detail') or now.get('sig') or ''))
        elif now is not None and now['status'] in GOOD:
            recovered.append(k)
    for k, v in ci.items():
        if k not in bi and v['status'] not in GOOD | {'GO-UNSTABLE', 'SKIP', 'IGNORED'}:
            new_bad.append((k, v['status'], v.get('detail') or v.get('sig') or ''))
    print(f'base {base} ({b["meta"].get("mode")}, {b["meta"].get("rsSha", "")[:12]}) vs '
          f'{cur} ({c["meta"].get("mode")}, {c["meta"].get("rsSha", "")[:12]})')
    print(f'compared {len(set(bi) & set(ci))} items: {len(lost)} lost, {len(unrun)} not run, '
          f'{len(recovered)} recovered, {len(unstable)} Go-unstable now, {len(new_bad)} new items not good')
    for k, s, d in lost[:60]:
        print(f'LOST      {k}: {s} {d}')
    for k in unrun[:30]:
        print(f'NOT RUN   {k}')
    for k, s, d in new_bad[:30]:
        print(f'NEW {s:<6} {k} {d}')
    for k in recovered[:30]:
        print(f'RECOVERED {k}')
    for k in sorted(k for k, v in ci.items() if v['status'] == 'FLAKY'):
        print(f'FLAKY     {k} (failed, then passed alone)')
    errors = [k for k, v in ci.items() if v['status'] == 'ERROR']
    for k in errors[:30]:
        print(f'ERROR     {k} {ci[k].get("detail", "")}')
    ok = not lost and not unrun and not errors
    print('PARITY OK' if ok else 'PARITY FAIL')
    return 0 if ok else 1


def known(d):
    """Every item that is not good, except IGNORED and SKIP (counted per tier prefix)."""
    quiet = Counter()
    for k, v in sorted(items_of(load(d)).items()):
        if v['status'] in ('IGNORED', 'SKIP'):
            quiet[(v['status'], k.split('/', 1)[0])] += 1
        elif v['status'] not in GOOD:
            print(f'{v["status"]:<11} {k} {v.get("detail") or v.get("sig") or ""}')
    for (status, prefix), n in sorted(quiet.items()):
        print(f'{status:<11} {n} items of {prefix}/ (not listed)')


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ('merge', 'compare', 'known'):
        print(__doc__)
        return 2
    if sys.argv[1] == 'merge':
        merge(sys.argv[2])
    elif sys.argv[1] == 'compare':
        return compare(sys.argv[2], sys.argv[3])
    else:
        known(sys.argv[2])
    return 0


if __name__ == '__main__':
    sys.exit(main())
