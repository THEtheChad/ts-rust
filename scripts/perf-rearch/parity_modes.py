#!/usr/bin/env python3
"""What each parity mode runs and what a run of it must have (lane H4).

usage: parity_modes.py shell quick|full     the mode's settings as shell assignments (parity.sh)
       parity_modes.py in MODE ITEM...      whether a run of MODE must have each item (by the
                                            manifest of this checkout)
       parity_modes.py check [FILE]         validate a quick manifest (default: this checkout's)

full runs every tier on everything, and a full run must have every base item. quick runs a subset,
and its required items are the quick manifest's (parity-quick-manifest.json): the cli items it names,
every answer of the lsp sessions it names, the api scenarios it names (lane H7), fuzz cases 0 to
count-1 of its seed, the tests of the binaries whose names start with its binaryPrefixes, and the
TestLocal rows. No corpus. The tiers run at least the manifest's items in quick mode (parity_cli.py,
parity_lsp.py, parity_api.py and parity_tests.py read it; parity.sh passes its fuzz count), so
execution may cover more than the manifest, never less. Version 2 of the manifest adds "api".

parity_compare.py does not judge a run by the candidate's own manifest: it reads the accepted one,
`accepted_file(ref, name)` from git (the accepted ref, default perf/first-principles), so a lane
that shrinks the manifest still has to run the accepted items until the change is reviewed and
merged. The manifest is validated strictly (`load_manifest`): unknown keys, a missing key or a
value of the wrong type is an error, never a default.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MANIFEST = HERE / 'parity-quick-manifest.json'
MANIFEST_REPO_PATH = 'scripts/perf-rearch/parity-quick-manifest.json'
MODES = {
    'quick': {'tiers': ['cli', 'tests', 'lsp', 'api', 'fuzz'], 'rs_runs': 1},
    'full': {'tiers': ['cli', 'tests', 'lsp', 'api', 'fuzz', 'corpus'], 'rs_runs': 2, 'fuzz_count': 30000},
}
TIER_OF = {'cli': 'cli', 'test': 'tests', 'test-local': 'tests', 'lsp': 'lsp', 'api': 'api', 'fuzz': 'fuzz', 'corpus': 'corpus'}
MANIFEST_KEYS = {'about': str, 'version': int, 'cli': list, 'lsp': list, 'api': list, 'fuzz': dict, 'tests': dict}
MANIFEST_VERSION = 2  # 2: lane H7's api scenarios
CLI_ITEM = re.compile(r'^cli/[^/]+/[^/]+/[^/]+$')


class ArtifactError(Exception):
    pass


def tier_of(item):
    return TIER_OF.get(item.split('/', 1)[0])


def _exact_keys(where, d, spec):
    if not isinstance(d, dict):
        raise ArtifactError(f'{where}: not an object')
    unknown, missing = sorted(set(d) - set(spec)), sorted(set(spec) - set(d))
    if unknown or missing:
        raise ArtifactError(f'{where}: ' + '; '.join(x for x in (unknown and f'unknown keys {unknown}',
                                                               missing and f'missing keys {missing}') if x))
    for k, t in spec.items():
        if not isinstance(d[k], t) or (t is int and isinstance(d[k], bool)):
            raise ArtifactError(f'{where}.{k}: a {type(d[k]).__name__}, not a {t.__name__}')


class Manifest:
    """A validated quick manifest; `requires(item)`: whether a quick run must have the item."""

    def __init__(self, data, origin):
        self.origin = origin
        _exact_keys('manifest', data, MANIFEST_KEYS)
        if data['version'] != MANIFEST_VERSION:
            raise ArtifactError(f'manifest.version: {data["version"]}, not {MANIFEST_VERSION}')
        _exact_keys('manifest.fuzz', data['fuzz'], {'seed': str, 'count': int})
        _exact_keys('manifest.tests', data['tests'], {'binaryPrefixes': list, 'testLocal': bool})
        for k, pat in (('cli', CLI_ITEM), ('lsp', re.compile(r'^[\w.-]+$')), ('api', re.compile(r'^[a-z0-9][a-z0-9.-]*$'))):
            for x in data[k]:
                if not isinstance(x, str) or not pat.match(x):
                    raise ArtifactError(f'manifest.{k}: not an item name: {x!r}')
            if len(set(data[k])) != len(data[k]):
                raise ArtifactError(f'manifest.{k}: a name is listed twice')
        if data['fuzz']['count'] < 0 or not re.match(r'^\d+$', data['fuzz']['seed']):
            raise ArtifactError('manifest.fuzz: bad seed or count')
        if not all(isinstance(x, str) and re.match(r'^[\w-]+$', x) for x in data['tests']['binaryPrefixes']):
            raise ArtifactError('manifest.tests.binaryPrefixes: not binary names')
        self.data = data
        self.cli = set(data['cli'])
        self.lsp = set(data['lsp'])
        self.api = set(data['api'])
        self.fuzz_seed, self.fuzz_count = data['fuzz']['seed'], data['fuzz']['count']
        self.test_prefixes = tuple(data['tests']['binaryPrefixes'])
        self.test_local = data['tests']['testLocal']

    def requires(self, item):
        tier = tier_of(item)
        parts = item.split('/')
        if tier == 'cli':
            return item in self.cli
        if tier == 'lsp':
            return len(parts) > 1 and parts[1] in self.lsp
        if tier == 'api':
            return len(parts) == 2 and parts[1] in self.api
        if tier == 'fuzz':
            m = re.match(r'^fuzz/([^:]+):(\d+)$', item)
            return bool(m) and m[1] == self.fuzz_seed and int(m[2]) < self.fuzz_count
        if tier == 'tests':
            if parts[0] == 'test-local':
                return self.test_local
            return len(parts) >= 2 and parts[1].startswith(self.test_prefixes)
        if tier == 'corpus':
            return False
        return True  # an unknown name is required

    def items(self):
        """What the manifest names, for the shrink report: cli items, lsp sessions, fuzz cases, tests."""
        return ({f'cli {x}' for x in self.cli} | {f'lsp session {x}' for x in self.lsp} | {f'api/{x}' for x in self.api}
                | {f'fuzz/{self.fuzz_seed}:{i}' for i in range(self.fuzz_count)}
                | {f'tests binary {p}*' for p in self.test_prefixes} | ({'test-local rows'} if self.test_local else set()))


def load_manifest(text, origin):
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ArtifactError(f'{origin}: not JSON: {e}') from None
    try:
        return Manifest(data, origin)
    except ArtifactError as e:
        raise ArtifactError(f'{origin}: {e}') from None


def working_manifest():
    """This checkout's manifest: what the tiers run (never what compare requires)."""
    return load_manifest(MANIFEST.read_text(), str(MANIFEST))


def accepted_file(ref, repo_path):
    """(text, origin) of a file at the accepted git ref of this harness's repo. Raises ArtifactError."""
    top = subprocess.run(['git', '-C', str(HERE), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    sha = subprocess.run(['git', '-C', str(HERE), 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'],
                         capture_output=True, text=True)
    if top.returncode or sha.returncode:
        raise ArtifactError(f'the accepted ref {ref!r} does not resolve in {HERE}')
    r = subprocess.run(['git', '-C', top.stdout.strip(), 'show', f'{sha.stdout.strip()}:{repo_path}'],
                       capture_output=True)
    if r.returncode:
        raise ArtifactError(f'{repo_path} is not in the accepted ref {ref} ({sha.stdout.strip()[:12]})')
    return r.stdout.decode(), f'{ref} ({sha.stdout.strip()[:12]}):{repo_path}'


class Selector:
    """in_mode(item): whether a run of `mode` must have the item: every item for full (an unknown mode
    is held to full), the manifest's items for quick."""

    def __init__(self, mode, manifest=None):
        self.mode = mode if mode in MODES else 'full'
        self.manifest = manifest
        if self.mode == 'quick' and manifest is None:
            raise ArtifactError('a quick run is judged by a quick manifest')

    def __call__(self, item):
        return self.mode == 'full' or self.manifest.requires(item)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == 'shell' and sys.argv[2] in MODES:
        m = MODES[sys.argv[2]]
        count = m.get('fuzz_count') or working_manifest().fuzz_count
        print(f'tiers="{" ".join(m["tiers"])}" rs_runs={m["rs_runs"]} fuzz_count={count}')
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == 'in':
        sel = Selector(sys.argv[2], working_manifest())
        for item in sys.argv[3:]:
            print(f'{"yes" if sel(item) else "no "} {item}')
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == 'check':
        path = Path(sys.argv[2]) if len(sys.argv) > 2 else MANIFEST
        try:
            m = load_manifest(path.read_text(), str(path))
        except ArtifactError as e:
            print(f'INVALID {e}')
            return 1
        print(f'ok {path}: {len(m.cli)} cli items, {len(m.lsp)} lsp sessions, {len(m.api)} api scenarios, fuzz {m.fuzz_count}, '
              f'tests {",".join(m.test_prefixes)}{" + TestLocal" if m.test_local else ""}')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
