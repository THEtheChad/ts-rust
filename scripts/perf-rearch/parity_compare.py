#!/usr/bin/env python3
"""Results of the parity gate: merge the tiers of a run, compare a run with a base, list known diffs.

usage: parity_compare.py merge DIR            DIR/<tier>/results.json -> DIR/results.json
       parity_compare.py compare BASE CUR     exit 1 when an item that is good in BASE is not good in CUR
       parity_compare.py known DIR            every item that is not good (the known diffs of a base),
                                              and every item with a referee status; IGNORED and SKIP counted

Good: MATCH (CLI, LSP, fuzz, corpus), the referee statuses (parity_referees.py on CLI, fuzz and
corpus: GO-BROKEN, GO-PARTITION, JS-AGREES; parity_lsp.py on LSP: GO-BROKEN, JS-AGREES), PASS and FLAKY (tests; FLAKY: passed when run again
alone). An item that is MATCH in BASE and has a referee status in CUR is not lost, but compare lists
every such item, grouped by status ("good by a referee"), and so every recovered or new item with a
referee status. compare fails (PARITY FAIL) on:
  - LOST: an item that is good in BASE and is not good in CUR (DIFF, FAIL, ERROR, UNRUN, SKIP,
    IGNORED, GO-UNSTABLE);
  - NOT RUN: an item that is good in BASE, that CUR's mode runs (parity_modes.Selector: a quick run
    against a full base must have every quick item; only full-only items are exempt), and that CUR
    does not have;
  - NEW: an item that CUR has and BASE does not, and that is not good (IGNORED tests excepted);
  - CRASH: an item on which tsc-rs crashed or timed out (its row's rsFailure, or an lsp detail
    "tsc-rs: ..."), whatever BASE says;
  - ERROR: any ERROR item in CUR (a harness error, a tier that ended without results, an input that
    changed during the run).
An item listed in parity-grandfathered.json (next to this file: item id -> status, rsFailure,
detailContains and a reason) is excused from NEW and CRASH only while its status and rsFailure are
the ones listed and its detail contains detailContains (the exact failure, such as the panic line); every
excused item is printed with its reason, and so is every entry that no longer applies. Items that are
not good in BASE are known diffs: listed when they recover.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
from parity_modes import Selector  # noqa: E402

GRANDFATHERED = Path(__file__).resolve().parent / 'parity-grandfathered.json'

REFEREED = ('GO-BROKEN', 'GO-PARTITION', 'JS-AGREES')  # parity_referees.REFEREED
GOOD = {'MATCH', *REFEREED, 'PASS', 'FLAKY'}
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


def rs_failure(row):
    """'crash' or 'timeout' when tsc-rs crashed or timed out on the item, else None."""
    if row.get('rsFailure'):
        return row['rsFailure']
    detail = row.get('detail') or ''
    if row['status'] not in GOOD and detail.startswith('tsc-rs: '):  # lsp: a tsc-rs run that ended early
        return 'timeout' if 'timed out' in detail else 'crash'
    return None


def load_grandfathered(path=GRANDFATHERED):
    return json.loads(Path(path).read_text())['items'] if Path(path).exists() else {}


def _excused(k, row, grand):
    g = grand.get(k)
    return (bool(g) and g.get('status') == row['status'] and g.get('rsFailure') == rs_failure(row)
            and g.get('detailContains', '') in (row.get('detail') or row.get('sig') or ''))


def compare(base, cur, grand_file=GRANDFATHERED):
    b, c = load(base), load(cur)
    bi, ci = items_of(b), items_of(c)
    grand = load_grandfathered(grand_file)
    cur_mode = c['meta'].get('mode')
    selected = Selector(cur_mode)
    lost, unrun, recovered, new_bad, crashes, excused = [], [], [], [], [], []
    refereed = {s: [] for s in REFEREED}  # status -> [(item, base status or None, detail)]
    for k, now in ci.items():
        was = bi.get(k, {}).get('status')
        if now['status'] in REFEREED and was != now['status']:
            refereed[now['status']].append((k, was, now.get('detail', '')))
    for k, v in bi.items():
        now = ci.get(k)
        if v['status'] in GOOD:
            if now is None:
                if selected(k):
                    unrun.append(k)
            elif now['status'] not in GOOD:
                lost.append((k, now['status'], now.get('detail') or now.get('sig') or ''))
        elif now is not None and now['status'] in GOOD:
            recovered.append(k)
    lost_keys = {k for k, _, _ in lost}
    for k, v in ci.items():
        why = v.get('detail') or v.get('sig') or ''
        if k not in bi and v['status'] not in GOOD | {'IGNORED'}:
            (excused if _excused(k, v, grand) else new_bad).append((k, v['status'], why))
        elif k in bi and k not in lost_keys and rs_failure(v):
            (excused if _excused(k, v, grand) else crashes).append((k, v['status'], why))
    stale = sorted(k for k, g in grand.items() if k not in ci or not _excused(k, ci[k], grand))
    print(f'base {base} ({b["meta"].get("mode")}, {b["meta"].get("rsSha", "")[:12]}) vs '
          f'{cur} ({cur_mode}, {c["meta"].get("rsSha", "")[:12]})')
    required = sum(1 for k, v in bi.items() if v['status'] in GOOD and selected(k))
    print(f'compared {len(set(bi) & set(ci))} items ({required} good base items that a {cur_mode} run must have): '
          f'{len(lost)} lost, {len(unrun)} not run, {len(recovered)} recovered, {len(new_bad)} new items not good, '
          f'{len(crashes)} known items where tsc-rs crashed or timed out, {len(excused)} grandfathered, '
          f'{sum(map(len, refereed.values()))} newly good by a referee '
          f'({", ".join(f"{n} {len(v)}" for n, v in refereed.items())})')
    for k, s, d in lost[:60]:
        print(f'LOST      {k}: {s} {d}')
    by_tier = Counter(k.split('/', 1)[0] for k in unrun)
    if unrun:
        print(f'NOT RUN   {len(unrun)} items ({", ".join(f"{t} {n}" for t, n in sorted(by_tier.items()))}); '
              f'tiers run: {" ".join(c["meta"].get("tiers", [])) or "?"}')
    for k in unrun[:30]:
        print(f'NOT RUN   {k}')
    for k, s, d in new_bad[:60]:
        print(f'NEW {s:<6} {k} {d}')
    for k, s, d in crashes[:60]:
        print(f'CRASH     {k}: {s} {d}')
    for k in recovered[:30]:
        print(f'RECOVERED {k} ({ci[k]["status"]})')
    print_refereed(refereed)
    for k, s, d in excused:
        print(f'GRANDFATHERED {k}: {s} ({grand[k].get("reason", "no reason")}) {d[:160]}')
    for k in stale:
        print(f'STALE GRANDFATHER {k}: listed as {grand[k].get("status")}/{grand[k].get("rsFailure")}, '
              f'now {ci[k]["status"] + "/" + str(rs_failure(ci[k])) if k in ci else "absent"}')
    for k in sorted(k for k, v in ci.items() if v['status'] == 'FLAKY'):
        print(f'FLAKY     {k} (failed, then passed alone)')
    errors = [k for k, v in ci.items() if v['status'] == 'ERROR']
    for k in errors[:30]:
        print(f'ERROR     {k} {ci[k].get("detail", "")}')
    ok = not lost and not unrun and not errors and not new_bad and not crashes
    print('PARITY OK' if ok else 'PARITY FAIL')
    return 0 if ok else 1


TITLES = {'GO-BROKEN': 'Go itself is broken here (crash, timeout or runs that differ); tsc-rs is stable',
          'GO-PARTITION': 'now matches another Go partition (--checkers N)',
          'JS-AGREES': 'JS tsc 6.0.3 sides with tsc-rs on every point where it differs from Go'}


def print_refereed(refereed):
    """Every item whose referee status is new in CUR, grouped by status. Never cut: each one from
    MATCH is a change of tsc-rs's output that a reviewer checks."""
    for status, rows in refereed.items():
        if not rows:
            continue
        from_match = sum(1 for _, was, _ in rows if was == 'MATCH')
        print(f'{status}: {TITLES[status]} ({len(rows)}; {from_match} were MATCH in the base)')
        for k, was, d in sorted(rows, key=lambda r: (r[1] != 'MATCH', r[0])):
            print(f'  {was or "new":<11} -> {status:<12} {k}: {d}')


def known(d):
    """Every item that is not good, and every item with a referee status (good, but not Go's default
    output); IGNORED and SKIP are counted per tier prefix."""
    quiet = Counter()
    for k, v in sorted(items_of(load(d)).items()):
        if v['status'] in ('IGNORED', 'SKIP'):
            quiet[(v['status'], k.split('/', 1)[0])] += 1
        elif v['status'] not in GOOD or v['status'] in REFEREED:  # good, but not Go's default output
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
