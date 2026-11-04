"""Delta debugging of a finding: drops files and options, then reduces each file by lines,
by repeated substrings (deep nesting) and by tokens, until a round removes nothing.

The predicate is "tsc-rs differs from Go with the same signature" (oracle.signature), so a
crash keeps its panic location and an output difference keeps its first differing diagnostic.
"""
import re
import time

from mutators import tokens

REPEAT_RE = re.compile(r'(.{1,16}?)\1{7,}', re.S)


def ddmin(items, test):
    """Removes chunks of items while test(remaining) holds (complement-only ddmin)."""
    n = 2
    while len(items) >= 2:
        size = -(-len(items) // n)
        for i in range(0, len(items), size):
            rest = items[:i] + items[i + size:]
            if test(rest):
                items, n = rest, max(n - 1, 2)
                break
        else:
            if n >= len(items):
                break
            n = min(n * 2, len(items))
    if len(items) == 1 and test([]):
        items = []
    return items


class Reducer:
    def __init__(self, oracle, sig, workdir, budget):
        self.oracle, self.sig, self.workdir = oracle, sig, workdir
        self.deadline = time.monotonic() + budget
        self.cache, self.tests = {}, 0

    def holds(self, case):
        if time.monotonic() > self.deadline:
            return False
        key = repr((sorted(case['files'].items()), sorted(case['options'].items()), case.get('args')))
        if key not in self.cache:
            self.tests += 1
            sig = self.oracle.check(case, self.workdir, confirm=False)[0]
            self.cache[key] = sig == self.sig
        return self.cache[key]

    def with_file(self, case, name, text):
        return {**case, 'files': {**case['files'], name: text}}

    def reduce_text(self, case, name, split):
        parts = split(case['files'][name])
        kept = ddmin(parts, lambda p: self.holds(self.with_file(case, name, ''.join(p))))
        return self.with_file(case, name, ''.join(kept))

    def shrink_repeats(self, case, name):
        """Halves long runs of a repeated piece (deep nesting) while the predicate holds."""
        changed = True
        while changed:
            changed = False
            text = case['files'][name]
            for m in REPEAT_RE.finditer(text):
                unit, count = m.group(1), len(m.group(0)) // len(m.group(1))
                for k in (1, 2, count // 4, count // 2):
                    if 0 < k < count:
                        new = text[:m.start()] + unit * k + text[m.start() + len(unit) * count:]
                        # Shrink the matching closing run too, when there is one of the same length.
                        closers = {'(': ')', '[': ']', '{': '}', '<': '>'}
                        close = ''.join(closers.get(ch, '') for ch in reversed(unit))
                        cand = [new]
                        if close and close * count in new:
                            cand.insert(0, new.replace(close * count, close * k, 1))
                        hit = next((c for c in cand if self.holds(self.with_file(case, name, c))), None)
                        if hit is not None:
                            case, changed = self.with_file(case, name, hit), True
                            break
                if changed:
                    break
        return case

    def run(self, case):
        case = {**case, 'files': dict(case['files']), 'options': dict(case['options'])}
        if not self.holds(case):
            return None
        names = ddmin(sorted(case['files']), lambda ns: self.holds({**case, 'files': {n: case['files'][n] for n in ns}}))
        case['files'] = {n: case['files'][n] for n in names}
        keys = ddmin(sorted(case['options']), lambda ks: self.holds({**case, 'options': {k: case['options'][k] for k in ks}}))
        case['options'] = {k: case['options'][k] for k in keys}
        if case.get('args'):
            case['args'] = ddmin(list(case['args']), lambda a: self.holds({**case, 'args': a}))
        quiet = {**case, 'options': {**case['options'], 'noEmit': True}}
        if 'noEmit' not in case['options'] and self.holds(quiet):  # no emit noise when emit plays no part
            case = quiet
        for _ in range(3):
            before = repr(case)
            for name in list(case['files']):
                case = self.reduce_text(case, name, lambda t: t.splitlines(True))
                case = self.shrink_repeats(case, name)
                case = self.reduce_text(case, name, tokens)
                case = self.reduce_text(case, name, list) if len(case['files'][name]) < 300 else case
            if repr(case) == before or time.monotonic() > self.deadline:
                break
        return case
