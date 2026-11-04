#!/usr/bin/env python3
"""D0-1B: writes the adversarial multi-file fixtures (projects designed to diverge across checker splits).

  make_fixtures.py OUT_DIR        writes OUT_DIR/<name>/ (tsconfig.json and sources) for each fixture

Fixtures:
  chain-generic   K segments of an eager instantiation chain (one per file, globals), resolvable at the generic
                  level, so checking the segment files nests the whole chain (the margin sees it).
  chain-deferred  the same chain, but each segment is deferred until instantiated with a concrete type; warmer files
                  instantiate one segment each, last segment first. A cold file needs all K segments (> depth 100); a
                  warm checker needs one at a time (each < 50). The case Astra's finding 8 describes.
  chain-deferred-mod  chain-deferred with modules and imports (the split follows import affinity).
  chain-one-warmer  chain-deferred with every warmer in one heavy file: Go's split (4 checkers) checks a.ts cold
                  and reports TS5115; 1 and 2 checkers check it warm and report nothing.
  count-split     a statement whose instantiation count passes 5M only when its helper types are cold.
  tail-split      a tail-recursive conditional chain (limit 1000) split into segments (tail count is per call).
  cycle-entry     a circular reference whose diagnostics depend on which file enters the cycle first.
  id-tie-union    unions of types that compare equal up to their ids (anonymous objects with the same symbol).
  late-bound      unique-symbol keyed members printed in a truncated type in an error message.
"""
import json
import sys
from pathlib import Path


def tsconfig(files, extra=None):
    opts = {'strict': True, 'noEmit': True, 'lib': ['es5'], 'types': []}
    opts.update(extra or {})
    return json.dumps({'compilerOptions': opts, 'files': files}, indent=1) + '\n'


def tup(n):
    return '[' + ','.join(['0'] * n) + ']'


def chain_generic(d, k=4, levels=13):
    files = {}
    for i in range(1, k + 1):
        base = f'S{i + 1}<T>' if i < k else '[]'
        files[f'seg{i}.ts'] = (f'type L{i} = {tup(levels)};\n'
                               f'type D{i}<N extends unknown[], T> = N extends [unknown, ...infer R] ? [T, ...D{i}<R, T>] : {base};\n'
                               f'type S{i}<T> = D{i}<L{i}, T>;\n')
        files[f'b{i}.ts'] = f'type W{i} = S{i}<string>;\n'
    files['a.ts'] = 'type A = S1<string>;\n'
    order = [f'seg{i}.ts' for i in range(1, k + 1)] + ['a.ts'] + [f'b{i}.ts' for i in range(k, 0, -1)]
    return files, order


def chain_deferred(d, k=4, levels=13, modules=False):
    """Each segment's length tuple comes from `Len<T>`, a conditional on T, so the segment stays deferred while T is
    generic. W_i = S_i<string> instantiates segment i, whose bottom needs S_{i+1}<string>."""
    files = {}
    imp = (lambda i: f"import type {{ S{i} }} from './seg{i}';\n") if modules else (lambda i: '')
    exp = 'export ' if modules else ''
    for i in range(1, k + 1):
        base = f'S{i + 1}<T>' if i < k else '[]'
        head = imp(i + 1) if i < k else ''
        files[f'seg{i}.ts'] = (head + f'type L{i}<T> = T extends string ? {tup(levels)} : [];\n'
                               f'type D{i}<N extends unknown[], T> = N extends [unknown, ...infer R] ? [T, ...D{i}<R, T>] : {base};\n'
                               f'{exp}type S{i}<T> = D{i}<L{i}<T>, T>;\n')
        files[f'b{i}.ts'] = imp(i) + f'{exp}type W{i} = S{i}<string>;\n'
    files['a.ts'] = imp(1) + f'{exp}type A = S1<string>;\n'
    order = [f'b{i}.ts' for i in range(k, 0, -1)] + ['a.ts']
    if not modules:
        order = [f'seg{i}.ts' for i in range(1, k + 1)] + order
    return files, order


def chain_one_warmer(d, k=6, levels=10):
    """chain-deferred with all warmers in one heavy file w.ts (W6 first). The split puts w.ts and a.ts in
    different checkers at 4 or more checkers, so Go's a.ts is cold and reports TS5115; one or two checkers check
    w.ts first and a.ts is warm (no error). In the warm run every instantiation stays near depth 24."""
    files, _ = chain_deferred(d, k, levels)
    for i in range(1, k + 1):
        del files[f'b{i}.ts']
    w = ''.join(f'type W{i} = S{i}<string>;\n' for i in range(k, 0, -1))
    w += ''.join(f'declare const pad_{i}: {{ a: number; b: string; c: boolean }};\n' for i in range(300))
    files['w.ts'] = w
    return files, [f'seg{i}.ts' for i in range(1, k + 1)] + ['w.ts', 'a.ts']


def count_split(d):
    """A cross product of string literal unions: each helper distributes over 20x20 members. Warm helpers make
    the final statement cheap; cold, the statement's own count is large."""
    letters = [f'"{chr(97 + i)}{j}"' for i in range(5) for j in range(6)]
    u = ' | '.join(letters)
    files = {
        'h.ts': f'type U = {u};\ntype M<A, B> = A extends string ? B extends string ? {{ k: `${{A}}-${{B}}` }} : never : never;\n'
                'type P1 = M<U, U>;\n',
        'w.ts': 'type W1 = M<U, U>[];\n',
        'a.ts': 'type Q = M<U, U> | M<U, "x">;\nconst q: Q = { k: "zz" };\n',
    }
    return files, ['h.ts', 'w.ts', 'a.ts']


def tail_split(d, k=3, levels=400):
    files = {}
    for i in range(1, k + 1):
        nxt = f'T{i + 1}<X, []>' if i < k else 'X'
        files[f'seg{i}.ts'] = (f'type T{i}<X, A extends unknown[]> = X extends string ? '
                               f'(A["length"] extends {levels} ? {nxt} : T{i}<X, [...A, 0]>) : never;\n')
        files[f'b{i}.ts'] = f'type V{i} = T{i}<"s", []>;\n'
    files['a.ts'] = 'type VA = T1<"s", []>;\n'
    order = [f'seg{i}.ts' for i in range(1, k + 1)] + [f'b{i}.ts' for i in range(k, 0, -1)] + ['a.ts']
    return files, order


def cycle_entry(d):
    files = {
        'x.ts': 'export const x = () => y();\nimport { y } from "./y";\n',
        'y.ts': 'import { z } from "./z";\nexport function y() { return z(); }\n',
        'z.ts': 'import { x } from "./x";\nexport function z() { return x(); }\n',
        'p.ts': 'import { z } from "./z";\nexport const pz = z();\n',
        'q.ts': 'import { x } from "./x";\nexport const qx = x();\n',
        't.ts': 'export type A = B;\nexport type B = import("./u").C;\n',
        'u.ts': 'export type C = import("./t").A;\n',
        'v.ts': 'import type { C } from "./u";\nexport const vc: C = 1;\n',
    }
    return files, ['p.ts', 'q.ts', 'v.ts', 'x.ts', 'y.ts', 'z.ts', 't.ts', 'u.ts']


def id_tie_union(d):
    """Anonymous object types from one declaration (same symbol), instantiated with different arguments in different
    files: their union order falls through to the mapper comparison, and a union with a fresh literal pair can reach
    the id fallback. The error prints the union."""
    files = {
        'f.ts': 'export function box<T>(v: T) { return { v }; }\nexport const mk = <T,>() => ({} as { [K in "a" | "b"]: T });\n',
        'p.ts': 'import { box, mk } from "./f";\nexport const p1 = box(1);\nexport const p2 = mk<string>();\n',
        'q.ts': 'import { box, mk } from "./f";\nexport const q1 = box("s");\nexport const q2 = mk<number>();\n',
        'a.ts': ('import { box, mk } from "./f";\nimport { p1, p2 } from "./p";\nimport { q1, q2 } from "./q";\n'
                 'declare const c: boolean;\nconst u = c ? (c ? q1 : p1) : (c ? q2 : p2);\nconst n: number = u;\n'
                 'const w = [box(true), box(1), box("s"), mk<boolean>()];\nconst m: string = w;\n'),
    }
    return files, ['p.ts', 'q.ts', 'a.ts', 'f.ts']


def late_bound(d):
    syms = ''.join(f'export declare const s{i}: unique symbol;\n' for i in range(12))
    members = ''.join(f'  [s{i}]: {{ value{i}: string }};\n' for i in range(12))
    files = {
        'syms.ts': syms,
        'shape.ts': 'import { ' + ', '.join(f's{i}' for i in range(12)) + ' } from "./syms";\n'
                    f'export interface Big {{\n{members}}}\nexport declare const big: Big;\n',
        'p.ts': 'import { big } from "./shape";\nexport const pb = { ...big, extra: 1 };\n',
        'a.ts': 'import { pb } from "./p";\nconst n: number = pb;\n',
    }
    return files, ['p.ts', 'a.ts', 'syms.ts', 'shape.ts']


FIXTURES = {
    'chain-generic': chain_generic,
    'chain-deferred': chain_deferred,
    'chain-deferred-mod': lambda d: chain_deferred(d, modules=True),
    'chain-deferred-k6': lambda d: chain_deferred(d, k=6, levels=9),
    'chain-one-warmer': chain_one_warmer,
    'count-split': count_split,
    'tail-split': tail_split,
    'cycle-entry': cycle_entry,
    'id-tie-union': id_tie_union,
    'late-bound': late_bound,
}


def main():
    out = Path(sys.argv[1])
    only = sys.argv[2:] or list(FIXTURES)
    for name in only:
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        files, order = FIXTURES[name](d)
        for f, text in files.items():
            (d / f).write_text(text)
        (d / 'tsconfig.json').write_text(tsconfig(order))
        print(name)


if __name__ == '__main__':
    main()
