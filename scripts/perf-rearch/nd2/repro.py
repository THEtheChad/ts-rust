#!/usr/bin/env python3
"""Lane ND2 repro: the watch-ts-pattern-f12 scenario, Go and tsc-rs, many runs, one variant line per run.

    python3 scripts/perf-rearch/nd2/repro.py --rs BIN --out DIR [--rounds 1] [--rs-runs 6] [--go-runs 0]

Each round is one lspdiff invocation in the parity sandbox (snapshots of ts-pattern and the typings
cache bound read-only, a fresh overlay per server run, as parity_lsp.py runs it), with no Go cache, so
Go runs --go-runs times (0: none, then lspdiff needs --go-cache: the round reads the gate's). Every run's
log is classified by the three items that varied under load (0016 load server messages, 0018 the
auto-import completion, 0026 completion server messages), plus every other answer's hash, so a run
that differs anywhere else shows too. Run it beside nd2/burn.sh for load.
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import parity_inputs as inputs  # noqa: E402
import parity_lsp  # noqa: E402

NAME = 'watch-ts-pattern-f12'
KEYS = ('load server messages', 'auto-import completion', 'completion server messages')


def classify(log):
    """A short variant tag of a run, and the hash of all its answers."""
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    tag = []
    for k in KEYS:
        row = next((r for r in rows if r['step'].endswith(k)), None)
        if row is None:
            tag.append(f'{k}=absent')
            continue
        res = row.get('result')
        if row['method'] == 'textDocument/completion':
            items = res.get('items', []) if isinstance(res, dict) else (res or [])
            tag.append(f'ai={len(items)}')
        else:
            tag.append('msgs=' + '+'.join((m.get('request') or m.get('notification', '?')).split('/')[-2] for m in res or []) or 'msgs=-')
    whole = hashlib.sha256(json.dumps([[r['step'], r.get('result'), r.get('error')] for r in rows], sort_keys=True).encode()).hexdigest()[:10]
    return ' '.join(tag), whole


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rs', required=True)
    ap.add_argument('--go', default=str(Path.home() / '.local/bin/tsgo-lsp-oracle-3e9236361fa9'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--rounds', type=int, default=1)
    ap.add_argument('--rs-runs', type=int, default=6)
    ap.add_argument('--go-runs', type=int, default=0)
    ap.add_argument('--server-logs', action='store_true')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sess = next(s for s in parity_lsp.sessions('full') if s['name'] == NAME)
    root = next(Path(os.path.expanduser(d)) for d in parity_lsp.PROJECTS[sess['project']] if Path(os.path.expanduser(d)).is_dir())
    snaps = inputs.Inputs()
    psnap, tsnap = snaps.of(root), snaps.of(parity_lsp.TYPINGS)
    binds = [(str(psnap.path), psnap.label), (str(tsnap.path), tsnap.label)]
    mounts = [{'lower': str(psnap.path), 'target': psnap.label, 'writable': False},
              {'lower': str(tsnap.path), 'target': tsnap.label, 'writable': True}]
    sandbox = {'scratch': str(Path.home() / f'.cache/perf-rearch/nd2/sandbox-{os.getpid()}'), 'mounts': mounts}
    tally = collections.Counter()
    for rnd in range(args.rounds):
        rout = out / f'r{rnd:03d}'
        cmd = [*parity_lsp.outer(binds), 'node', '--max-old-space-size=12288', str(parity_lsp.LSPDIFF), 'diff', '--project', str(root),
               '--files', str(sess['files']), '--go', args.go, '--rs', args.rs, '--out', str(rout), '--scenario', sess['scenario'],
               '--rs-runs', str(args.rs_runs), '--go-runs', str(max(args.go_runs, 1)), '--go-stage', str(rout / 'go-stage')]
        if args.go_runs == 0:  # no Go runs: read the gate's cached Go logs of this harness, if any
            info = parity_lsp.lspdiff_info()
            material = parity_lsp.session_material(sess, root, psnap, tsnap, parity_lsp.scenario_sources_sha(), info, [f'g{parity_lsp.go_runs(sess)}'])
            go_sha = parity_lsp.sha256_file(args.go)[:16]
            cache = Path.home() / f'.cache/perf-rearch/parity/go-cache/{go_sha}/lsp/{NAME}-{inputs.json_hash(material)[:24]}'
            cmd[cmd.index('--go-runs') + 1] = str(parity_lsp.go_runs(sess))
            cmd += ['--go-cache', str(cache)]
        cmd += ['--server-logs'] if args.server_logs else []
        r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, 'LSPDIFF_SANDBOX': json.dumps(sandbox)})
        (out / f'r{rnd:03d}.log').write_text(r.stdout + r.stderr)
        d = next(rout.glob('*-diff-watch'), None)
        if d is None:
            print(f'round {rnd}: lspdiff exit {r.returncode}: {r.stderr[-400:]}', flush=True)
            continue
        load = open('/proc/loadavg').read().split()[0]
        for log in sorted(d.glob('*.jsonl')):
            run = log.stem
            if args.go_runs == 0 and run.startswith('go'):
                continue
            tag, whole = classify(log)
            side = 'go' if run.startswith('go') else 'rs'
            tally[(side, tag, whole)] += 1
            print(f'round {rnd} load {load} {run:8} {tag} all={whole}', flush=True)
    print('tally:')
    for (side, tag, whole), n in sorted(tally.items()):
        print(f'  {side} {n:3}  {tag} all={whole}')
    snaps.verify()


if __name__ == '__main__':
    main()
