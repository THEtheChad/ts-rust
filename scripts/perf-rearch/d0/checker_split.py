#!/usr/bin/env python3
"""D0-1B: which checker checks which file, in order, from a --generateTrace directory.

  checker_split.py TRACE_DIR        prints one line per checker: id, then its files in check order
"""
import json
import sys
from pathlib import Path


def split(trace_dir):
    events = json.loads((Path(trace_dir) / 'trace.json').read_text())
    out = {}
    for e in events:
        if e.get('name') == 'checkSourceFile' and e.get('ph') in ('B', 'X'):
            out.setdefault(e['args']['checkerId'], []).append(e['args']['path'].rsplit('/', 1)[-1])
    return out


if __name__ == '__main__':
    for cid, files in sorted(split(sys.argv[1]).items()):
        print(cid, ' '.join(f for f in files if not f.startswith('lib.')))
