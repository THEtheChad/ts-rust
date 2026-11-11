#!/usr/bin/env python3
"""Lane B7: Go's base-directory and current-directory call sites at the pin, each with its Rust
counterpart and status (base_directory_sites.tsv).

usage: base_directory_sites.py check [GO_INTERNAL]   every Go line that matches BaseDirectory(),
                                                     GetCurrentDirectory() or CurrentDirectory (no
                                                     _test.go) has exactly one row, and every row a line
       base_directory_sites.py table                 the rows as a markdown table
"""
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TSV = HERE / 'base_directory_sites.tsv'
GO = Path.home() / '.explore/repos/microsoft__TypeScript@3e9236361fa9/tsc/internal'
PATTERN = re.compile(r'BaseDirectory\(\)|GetCurrentDirectory\(\)|CurrentDirectory')


def rows():
    out = []
    for line in TSV.read_text().splitlines():
        if line.startswith('#') or not line.strip():
            continue
        site, expr, rust, status, reason = line.split('\t')
        assert status in ('correct', 'fixed', 'n/a'), line
        out.append((site, expr, rust, status, reason))
    return out


def go_sites(root):
    sites = []
    for f in sorted(root.rglob('*.go')):
        if f.name.endswith('_test.go'):
            continue
        for n, text in enumerate(f.read_text(errors='replace').splitlines(), 1):
            if PATTERN.search(text):
                sites.append(f'{f.relative_to(root)}:{n}')
    return sites


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'table':
        print('| Go | expression | Rust | status | why |')
        print('|---|---|---|---|---|')
        for r in rows():
            print('| ' + ' | '.join(c.replace('|', '\\|') for c in r) + ' |')
        return 0
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else GO
    have = Counter(r[0] for r in rows())
    want = set(go_sites(root))
    dup = sorted(k for k, v in have.items() if v > 1)
    missing = sorted(want - set(have))
    extra = sorted(set(have) - want)
    for k in dup:
        print('DUPLICATE', k)
    for k in missing:
        print('MISSING', k)
    for k in extra:
        print('NOT A SITE', k)
    status = Counter(r[3] for r in rows())
    ok = not (dup or missing or extra)
    print(f'{"COMPLETE" if ok else "INCOMPLETE"}: {len(want)} Go sites, {sum(have.values())} rows '
          f'({status["correct"]} correct, {status["fixed"]} fixed, {status["n/a"]} n/a)')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
