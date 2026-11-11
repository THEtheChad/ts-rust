#!/usr/bin/env python3
"""Lane B7 probe: a CLI fixture of parity-fixtures.json through Go and tsc-rs, step by step.

usage: symlink_probe.py <go bin> <rs bin> [--fixture NAME] [--only REGEX] [--out DIR]

Each scenario is a list of steps (a tsc command line, or an edit of a fixture file) run on a fresh
copy of the fixture at one fixed path (DIR/w/<fixture>), first for Go, then for tsc-rs, so paths in
the output are the same. Compared per step: exit code, stdout and stderr (the -b wall clock masked);
per scenario: every file the steps wrote (bytes). Prints MATCH or DIFF per scenario, with the first
differing lines; DIR/<scenario>/{go,rs}/ keeps every output.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / 'parity-fixtures.json'
CLOCK_RE = re.compile(rb'^(?:\[\d{1,2}:\d{2}:\d{2}(?: [AP]M)?\] |\d{1,2}:\d{2}:\d{2}(?: [AP]M)? - )', re.M)
ENV = {k: v for k, v in os.environ.items() if not k.startswith(('GOPORT_', 'TSGO_', 'TS_'))}

EDIT_A = ('edit', 'packages/a/src/index.ts', 'export const edited: number = 5;\n')
TOUCH_INNER = ('touch', 'packages/a/src/inner.ts')
B = 'packages/b/tsconfig.json'
BP = 'packages/b/tsconfig.preserve.json'
P = ['--pretty', 'false']

SCENARIOS = {
    'build-verbose': [['-b', 'tsconfig.json', '--verbose', *P], ['-b', 'tsconfig.json', '--verbose', *P], EDIT_A,
                      ['-b', 'tsconfig.json', '--verbose', *P], TOUCH_INNER, ['-b', 'tsconfig.json', '--verbose', *P]],
    'build-explain': [['-b', 'tsconfig.json', '--explainFiles', *P], EDIT_A, ['-b', 'tsconfig.json', '--explainFiles', *P]],
    'build-listfiles': [['-b', 'tsconfig.json', '--listFiles', *P], EDIT_A, ['-b', 'tsconfig.json', '--listFiles', *P]],
    'build-trace': [['-b', 'tsconfig.json', '--traceResolution', *P], EDIT_A, ['-b', 'tsconfig.json', '--traceResolution', *P]],
    'build-preserve-only': [['-b', BP, '--verbose', '--explainFiles', *P], EDIT_A, ['-b', BP, '--verbose', *P]],
    'p-clean': [['-p', B, '--explainFiles', *P], ['-p', BP, '--explainFiles', *P],
                ['-p', B, '--noEmit', '--traceResolution', *P], ['-p', BP, '--noEmit', '--traceResolution', *P]],
    'p-built': [['-b', 'tsconfig.json', *P],
                ['-p', B, '--noEmit', '--explainFiles', *P], ['-p', BP, '--noEmit', '--explainFiles', *P],
                ['-p', B, '--noEmit', '--listFiles', *P], ['-p', BP, '--noEmit', '--listFiles', *P],
                ['-p', B, '--noEmit', '--traceResolution', *P], ['-p', BP, '--noEmit', '--traceResolution', *P],
                ['-p', B, '--declaration', '--declarationMap', '--sourceMap', '--outDir', 'out-b', *P],
                ['-p', BP, '--declaration', '--declarationMap', '--outDir', 'out-bp', *P]],
}


def materialize(files, root):
    if root.exists():
        subprocess.run(['chmod', '-R', 'u+rwx', str(root)], check=False)
        shutil.rmtree(root)
    for rel, v in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(v, dict):
            os.symlink(v['symlink'], p)
        else:
            p.write_text(v)
            os.utime(p, (978307200, 978307200))


def snapshot(root):
    out = {}
    for top, dirs, fs in os.walk(root):
        for f in fs:
            p = Path(top) / f
            if p.is_symlink():
                continue
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def run(bin_, root, steps, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    before = snapshot(root)
    results = []
    for i, step in enumerate(steps):
        if step[0] == 'edit':
            time.sleep(0.05)
            with open(root / step[1], 'a') as f:
                f.write(step[2])
            results.append(('edit', step[1]))
            continue
        if step[0] == 'touch':
            time.sleep(0.05)
            os.utime(root / step[1])
            results.append(('touch', step[1]))
            continue
        r = subprocess.run([bin_, *step], cwd=root, env=ENV, capture_output=True, timeout=600)
        out = CLOCK_RE.sub(b'<clock> - ', r.stdout)
        (outdir / f'{i}.stdout').write_bytes(out)
        (outdir / f'{i}.stderr').write_bytes(r.stderr)
        results.append((r.returncode, out, r.stderr))
    after = snapshot(root)
    written = {k: v for k, v in after.items() if before.get(k) != v}
    for k, v in written.items():
        p = outdir / 'files' / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(v)
    return results, written


def first_diff(a, b):
    d = list(difflib.unified_diff(a.decode(errors='replace').splitlines(), b.decode(errors='replace').splitlines(),
                                  'go', 'rs', lineterm='', n=1))
    return '\n'.join(d[:16])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('go')
    ap.add_argument('rs')
    ap.add_argument('--fixture', default='symlink-monorepo')
    ap.add_argument('--only', default='')
    ap.add_argument('--out', default=str(Path.home() / '.cache/perf-rearch/scratch/b7/probe'))
    a = ap.parse_args()
    files = json.loads(FIXTURES.read_text())['fixtures'][a.fixture]
    out = Path(a.out)
    root = out / 'w' / a.fixture
    ndiff = 0
    for name, steps in SCENARIOS.items():
        if a.only and not re.search(a.only, name):
            continue
        sides = {}
        for side, bin_ in (('go', a.go), ('rs', a.rs)):
            materialize(files, root)
            sides[side] = run(bin_, root, steps, out / name / side)
        (gr, gw), (rr, rw) = sides['go'], sides['rs']
        diffs = []
        for i, (g, r) in enumerate(zip(gr, rr)):
            if g == r:
                continue
            if g[0] != r[0]:
                diffs.append(f'step {i} {steps[i]}: exit Go {g[0]}, tsc-rs {r[0]}')
            for k, label in ((1, 'stdout'), (2, 'stderr')):
                if g[k] != r[k]:
                    diffs.append(f'step {i} {steps[i]} {label}:\n{first_diff(g[k], r[k])}')
        for k in sorted(set(gw) | set(rw)):
            if gw.get(k) != rw.get(k):
                if k not in gw or k not in rw:
                    diffs.append(f'file {k}: only {"Go" if k in gw else "tsc-rs"} wrote it')
                else:
                    diffs.append(f'file {k}:\n{first_diff(gw[k], rw[k])}')
        codes = [g[0] for g in gr if isinstance(g[0], int)]
        print(f'{"MATCH" if not diffs else "DIFF"} {name} (Go exits {codes}, {len(gw)} files written)')
        for d in diffs:
            print('  ' + d.replace('\n', '\n  '))
        ndiff += bool(diffs)
    print(f'{ndiff} DIFF')
    return 1 if ndiff else 0


if __name__ == '__main__':
    sys.exit(main())
