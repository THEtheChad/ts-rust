#!/usr/bin/env python3
"""What each parity mode runs: the one definition parity.sh runs from and parity_compare.py checks
a run against (`required`: the base items a run of a mode must have).

usage: parity_modes.py shell quick|full     the mode's settings as shell assignments (parity.sh)
       parity_modes.py in MODE ITEM...      whether each item is one MODE runs

quick runs a subset of full: the cli items of the quick configs and variants (parity-projects.json
"quick", parity_cli.VARIANTS), the go_baselines tests and TestLocal rows (no workspace binaries),
the lsp sessions of parity_lsp.SESSIONS['quick'], and fuzz cases 0 to QUICK_FUZZ - 1 of seed 1 (no
corpus). full runs every tier on everything.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODES = {
    'quick': {'tiers': ['cli', 'tests', 'lsp', 'fuzz'], 'rs_runs': 1, 'fuzz_count': 1500},
    'full': {'tiers': ['cli', 'tests', 'lsp', 'fuzz', 'corpus'], 'rs_runs': 2, 'fuzz_count': 30000},
}
FUZZ_SEED = '1'
TIER_OF = {'cli': 'cli', 'test': 'tests', 'test-local': 'tests', 'lsp': 'lsp', 'fuzz': 'fuzz', 'corpus': 'corpus'}


def tier_of(item):
    return TIER_OF.get(item.split('/', 1)[0])


def _cli_spec(projects_file=HERE / 'parity-projects.json'):
    from parity_cli import VARIANTS
    spec = json.loads(Path(projects_file).read_text())
    configs = {(p['name'], c['label']): bool(c.get('quick')) for p in spec['projects'] for c in p['configs']}
    return configs, {v: quick for v, (quick, _, _) in VARIANTS.items()}


def _lsp_spec():
    from parity_lsp import SESSIONS
    return {s['name'] for s in SESSIONS['quick']}, {s['name'] for s in SESSIONS['full']}


class Selector:
    """in_mode(item): whether a run of `mode` must have the item. An item is left out only when it is
    positively one that the mode does not run (a known full-only cli config or variant, a known
    full-only lsp session, a workspace test binary, a fuzz case past the quick count, a corpus case,
    or a tier the mode has not); anything else, an unknown name included, is required."""

    def __init__(self, mode):
        self.mode = mode if mode in MODES else 'full'  # an unknown mode is held to full
        if self.mode == 'quick':
            self.cli_configs, self.cli_variants = _cli_spec()
            self.lsp_quick, self.lsp_full = _lsp_spec()

    def __call__(self, item):
        return self.mode == 'full' or not self.full_only(item)

    def full_only(self, item):
        tier = tier_of(item)
        if tier is None:
            return False
        if tier not in MODES['quick']['tiers']:
            return True  # corpus
        parts = item.split('/')
        if tier == 'cli':
            if len(parts) != 4 or (parts[1], parts[2]) not in self.cli_configs or parts[3] not in self.cli_variants:
                return False
            return not (self.cli_configs[(parts[1], parts[2])] and self.cli_variants[parts[3]])
        if tier == 'tests':
            return parts[0] == 'test' and len(parts) >= 3 and not parts[1].startswith('go_baselines')
        if tier == 'lsp':
            name = parts[1] if len(parts) > 1 else ''
            return name in self.lsp_full and name not in self.lsp_quick
        if tier == 'fuzz':
            m = re.match(r'^fuzz/([^:]+):(\d+)$', item)
            return bool(m) and m[1] == FUZZ_SEED and int(m[2]) >= MODES['quick']['fuzz_count']
        return False


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == 'shell' and sys.argv[2] in MODES:
        m = MODES[sys.argv[2]]
        print(f'tiers="{" ".join(m["tiers"])}" rs_runs={m["rs_runs"]} fuzz_count={m["fuzz_count"]}')
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == 'in':
        sel = Selector(sys.argv[2])
        for item in sys.argv[3:]:
            print(f'{"yes" if sel(item) else "no "} {item}')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
