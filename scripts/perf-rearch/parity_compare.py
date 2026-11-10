#!/usr/bin/env python3
"""Results of the parity gate: merge the tiers of a run, compare a run with a base, list known diffs.

usage: parity_compare.py merge DIR            DIR/<tier>/results.json -> DIR/results.json
       parity_compare.py compare BASE CUR [--accepted-ref REF | --accepted-dir DIR]
                                              exit 1 (PARITY FAIL) on any failure below
       parity_compare.py known DIR            every item that is not good (the known diffs of a base),
                                              and every item with a referee status; IGNORED and SKIP counted

Good: MATCH (CLI, LSP, API, fuzz, corpus), the referee statuses (parity_referees.py on CLI, fuzz and
corpus: GO-BROKEN, GO-PARTITION, JS-AGREES; parity_lsp.py on LSP: GO-BROKEN, JS-AGREES; parity_api.py
on API: GO-BROKEN), PASS and
FLAKY (tests; FLAKY: passed when run again alone). IGNORED tests are declared skips: neither good nor
a failure. Every item that is not good in BASE is a known-bad item.

What a run is judged by comes from accepted artifacts, never from the candidate (lane H4): the base
(BASE), the quick manifest (parity-quick-manifest.json: what a quick run must have) and the
grandfather file (parity-grandfathered.json: known-bad items pinned to their exact failure), both read
from the accepted git ref (--accepted-ref, else $PARITY_ACCEPTED_REF, else perf/first-principles).
Both are validated strictly; an invalid or missing one fails the gate. Where this checkout's copy
of an artifact differs from the accepted one, compare says so (a shrunk manifest item by item), and
the accepted one still decides. A full run must have every base item.

compare fails (PARITY FAIL) on:
  - LOST: an item that is good in BASE and is not good in CUR (a grandfather entry never excuses it);
  - NOT RUN: an item of BASE, good or known-bad, that CUR's mode must have and CUR lacks;
  - KNOWN-BAD: a known-bad item of BASE that is still not good and that no grandfather entry matches
    exactly (it changed to another failure, became nondeterministic, or was never pinned);
  - NEW: an item that CUR has and BASE does not, that is not good (IGNORED excepted) and that no entry
    matches;
  - CRASH: a good item whose row says tsc-rs crashed or timed out (defensive: the tiers make such an
    item a DIFF);
  - STALE GRANDFATHER: an entry whose item CUR has and does not fail exactly as listed (good now, or
    failing differently), or whose item CUR's mode must have and lacks;
  - ERROR: any ERROR item in CUR (a harness error, a tier that ended without results, an input that
    changed during the run).
Every grandfathered item is printed with its reason, every item that became good by a referee by
status, and every recovered known-bad item.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script dir out
import parity_grandfather as grandfather  # noqa: E402
import parity_modes as modes  # noqa: E402
from parity_modes import ArtifactError, Selector  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_REF = 'perf/first-principles'
QUIET = {'IGNORED'}

REFEREED = ('GO-BROKEN', 'GO-PARTITION', 'JS-AGREES')  # parity_referees.REFEREED
GOOD = {'MATCH', *REFEREED, 'PASS', 'FLAKY'}
TIERS = ('cli', 'tests', 'lsp', 'api', 'fuzz', 'corpus', 'js')


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
    """'crash', 'timeout' or 'input' when tsc-rs failed on the item, else None."""
    if row.get('rsFailure'):
        return row['rsFailure']
    detail = row.get('detail') or ''
    if row['status'] not in GOOD and detail.startswith('tsc-rs: '):  # lsp: a tsc-rs run that ended early
        return 'timeout' if 'timed out' in detail else 'crash'
    return None


class Accepted:
    """The accepted artifacts a run is judged by: the quick manifest and the grandfather entries, from a
    git ref (or, for the self-tests, a directory), with this checkout's copies for the report."""

    def __init__(self, ref=None, directory=None):
        self.errors, self.notes = [], []
        self.manifest, self.grand = None, {}
        if directory:
            self.source = f'directory {directory} (NOT a git ref: self-test only)'
            read = lambda repo_path: ((Path(directory) / Path(repo_path).name).read_text(), f'{directory}/{Path(repo_path).name}')
        else:
            ref = ref or os.environ.get('PARITY_ACCEPTED_REF') or DEFAULT_REF
            self.source = f'git ref {ref}'
            read = lambda repo_path: modes.accepted_file(ref, repo_path)
        try:
            text, origin = read(modes.MANIFEST_REPO_PATH)
            self.manifest = modes.load_manifest(text, origin)
        except (ArtifactError, OSError) as e:
            self.errors.append(f'MANIFEST INVALID: {e}')
        try:
            text, origin = read(grandfather.REPO_PATH)
            self.grand = grandfather.load(text, origin)
        except (ArtifactError, OSError) as e:
            self.errors.append(f'GRANDFATHER INVALID: {e}')
        self._own_copies()

    def _own_copies(self):
        """Report-only: where this checkout's artifacts differ from the accepted ones."""
        try:
            own = modes.working_manifest()
        except (ArtifactError, OSError) as e:
            self.notes.append(f'ARTIFACT  this checkout\'s quick manifest is invalid: {e}')
            own = None
        if own is not None and self.manifest is not None and own.data != self.manifest.data:
            removed = sorted(self.manifest.items() - own.items())
            added = sorted(own.items() - self.manifest.items())
            self.notes.append(f'ARTIFACT  this checkout\'s quick manifest differs from the accepted one '
                              f'({len(removed)} removed, {len(added)} added); the accepted one decides')
            if removed:
                shown = ', '.join(removed[:20]) + (f', ... ({len(removed) - 20} more)' if len(removed) > 20 else '')
                self.notes.append(f'MANIFEST SHRINK (not accepted): {shown}')
        try:
            own_g = grandfather.load(grandfather.GRANDFATHERED.read_text(), str(grandfather.GRANDFATHERED))
        except (ArtifactError, OSError) as e:
            self.notes.append(f'ARTIFACT  this checkout\'s grandfather file is invalid: {e}')
            return
        if own_g != self.grand:
            ch = sorted(k for k in set(own_g) | set(self.grand) if own_g.get(k) != self.grand.get(k))
            self.notes.append(f'ARTIFACT  this checkout\'s grandfather file differs from the accepted one on {len(ch)} '
                              f'entries ({", ".join(ch[:10])}{", ..." if len(ch) > 10 else ""}); the accepted one decides')


def _why(v):
    return v.get('detail') or v.get('sig') or ''


def judge(bi, ci, cur_mode, acc):
    """Every finding of a comparison: a dict of lists (see the top comment)."""
    selected = Selector(cur_mode, acc.manifest) if acc.manifest is not None else (lambda k: True)
    grand = acc.grand
    f = {k: [] for k in ('lost', 'unrun', 'recovered', 'known_bad', 'new_bad', 'crashes', 'excused', 'stale', 'unchecked')}
    f['refereed'] = {s: [] for s in REFEREED}
    for k, now in ci.items():
        was = bi.get(k, {}).get('status')
        if now['status'] in REFEREED and was != now['status']:
            f['refereed'][now['status']].append((k, was, now.get('detail', '')))
    for k, v in bi.items():
        now = ci.get(k)
        if now is None:
            if selected(k) and v['status'] not in QUIET:
                f['unrun'].append((k, v['status']))
        elif v['status'] in GOOD:
            if now['status'] not in GOOD:
                f['lost'].append((k, now['status'], _why(now)))
        elif now['status'] in GOOD:
            f['recovered'].append(k)
    lost = {k for k, _, _ in f['lost']}
    excused = set()
    for k, v in ci.items():
        if v['status'] in GOOD:
            if rs_failure(v):
                f['crashes'].append((k, v['status'], _why(v)))
            continue
        if k in lost or (v['status'] in QUIET and bi.get(k, {}).get('status') in (None, *QUIET)):
            continue
        g = grand.get(k)
        miss = grandfather.match(g, v, cur_mode) if g else 'no grandfather entry'
        if miss is None and bi.get(k, {}).get('status') not in GOOD:
            excused.add(k)
            f['excused'].append((k, v['status'], g['reason'], _why(v)))
        elif k in bi:
            f['known_bad'].append((k, bi[k]['status'], v['status'], miss, _why(v)))
        else:
            f['new_bad'].append((k, v['status'], miss, _why(v)))
    for k, g in sorted(grand.items()):
        if k in excused:
            continue
        if k in ci:
            now = ci[k]
            why = 'good now: remove the entry' if now['status'] in GOOD else grandfather.match(g, now, cur_mode) or (
                'good in the base: a lost item is never excused')
            f['stale'].append((k, why))
        elif selected(k):
            f['stale'].append((k, f'a {cur_mode} run must have it and this one lacks it'))
        else:
            f['unchecked'].append(k)
    return f, selected


def compare(base, cur, accepted=None):
    b, c = load(base), load(cur)
    bi, ci = items_of(b), items_of(c)
    acc = accepted or Accepted()
    cur_mode = c['meta'].get('mode')
    f, selected = judge(bi, ci, cur_mode, acc)
    print(f'base {base} ({b["meta"].get("mode")}, {b["meta"].get("rsSha", "")[:12]}) vs '
          f'{cur} ({cur_mode}, {c["meta"].get("rsSha", "")[:12]})')
    print(f'accepted artifacts: {acc.source}')
    for line in acc.errors + acc.notes:
        print(line)
    required = sum(1 for k, v in bi.items() if v['status'] not in QUIET and selected(k))
    print(f'compared {len(set(bi) & set(ci))} items ({required} base items that a {cur_mode} run must have): '
          f'{len(f["lost"])} lost, {len(f["unrun"])} not run, {len(f["known_bad"])} known-bad not as pinned, '
          f'{len(f["new_bad"])} new items not good, {len(f["crashes"])} crashes on good items, '
          f'{len(f["excused"])} grandfathered, {len(f["stale"])} stale grandfather entries, '
          f'{len(f["recovered"])} recovered, {sum(map(len, f["refereed"].values()))} newly good by a referee '
          f'({", ".join(f"{n} {len(v)}" for n, v in f["refereed"].items())})')
    for k, s, d in f['lost'][:60]:
        print(f'LOST      {k}: {s} {d}')
    by_tier = Counter(k.split('/', 1)[0] for k, _ in f['unrun'])
    if f['unrun']:
        print(f'NOT RUN   {len(f["unrun"])} items ({", ".join(f"{t} {n}" for t, n in sorted(by_tier.items()))}); '
              f'tiers run: {" ".join(c["meta"].get("tiers", [])) or "?"}')
    for k, was in f['unrun'][:30]:
        print(f'NOT RUN   {k} (base {was})')
    for k, was, s, miss, d in f['known_bad'][:60]:
        print(f'KNOWN-BAD {k}: base {was}, now {s}, not as pinned ({miss}) {d[:200]}')
    for k, s, miss, d in f['new_bad'][:60]:
        print(f'NEW {s:<6} {k} ({miss}) {d[:200]}')
    for k, s, d in f['crashes'][:60]:
        print(f'CRASH     {k}: {s} {d}')
    for k in f['recovered'][:30]:
        print(f'RECOVERED {k} ({ci[k]["status"]}; base {bi[k]["status"]})')
    print_refereed(f['refereed'])
    for k, s, reason, d in f['excused']:
        print(f'GRANDFATHERED {k}: {s} ({reason}) {d[:160]}')
    for k, why in f['stale']:
        print(f'STALE GRANDFATHER {k}: {why}')
    if f['unchecked']:
        print(f'grandfather entries a {cur_mode} run does not run: {len(f["unchecked"])}')
    for k in sorted(k for k, v in ci.items() if v['status'] == 'FLAKY'):
        print(f'FLAKY     {k} (failed, then passed alone)')
    errors = [k for k, v in ci.items() if v['status'] == 'ERROR']
    for k in errors[:30]:
        print(f'ERROR     {k} {ci[k].get("detail", "")}')
    ok = not (acc.errors or errors or any(f[k] for k in ('lost', 'unrun', 'known_bad', 'new_bad', 'crashes', 'stale')))
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
        rest = sys.argv[4:]
        if len(sys.argv) < 4 or rest[:1] not in ([], ['--accepted-ref'], ['--accepted-dir']) or len(rest) not in (0, 2):
            print(__doc__)
            return 2
        acc = Accepted(ref=rest[1] if rest[:1] == ['--accepted-ref'] else None,
                       directory=rest[1] if rest[:1] == ['--accepted-dir'] else None)
        return compare(sys.argv[2], sys.argv[3], acc)
    else:
        known(sys.argv[2])
    return 0


if __name__ == '__main__':
    sys.exit(main())
