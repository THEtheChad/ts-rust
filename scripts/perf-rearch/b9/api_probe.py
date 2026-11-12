#!/usr/bin/env python3
"""Lane B9: send raw {method, params} requests to one `tsgo --api --async` server and print each answer.

usage: python3 -I api_probe.py <tsgo binary> <script.json> [cwd]

The script is a JSON list of {"method": ..., "params": ...}. In params, the string "$snapshot" is the
snapshot id of the last createSnapshot or updateSnapshot answer and "$project" the id of its first project,
and "$data" the `data` of the last answer that had one (getSourceFile).
Every request runs in order; the server's stderr is printed after it exits. It reads no file but the
script, and the scenarios should use an in-memory `fileSystem` ({"kind": "full"}) so the server writes
nothing. The parity api harness has no request form of its own yet (owned by lane B8): this is evidence,
not a gate item.
"""
import json
import subprocess
import sys


def frame(obj):
    body = json.dumps(obj).encode()
    return b'Content-Length: %d\r\n\r\n' % len(body) + body


def read_message(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        k, _, v = line.partition(b':')
        if k.strip().lower() == b'content-length':
            length = int(v)
    return stream.read(length)


def subst(value, env):
    if isinstance(value, str):
        return env.get(value, value)
    if isinstance(value, list):
        return [subst(v, env) for v in value]
    if isinstance(value, dict):
        return {k: subst(v, env) for k, v in value.items()}
    return value


def main():
    binary, script = sys.argv[1], json.load(open(sys.argv[2]))
    cwd = sys.argv[3] if len(sys.argv) > 3 else '/probe'
    proc = subprocess.Popen([binary, '--api', '--async', '--cwd', cwd], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    env = {}
    for i, req in enumerate(script, 1):
        params = subst(req.get('params'), env)
        proc.stdin.write(frame({'jsonrpc': '2.0', 'id': i, 'method': req['method'], 'params': params}))
        proc.stdin.flush()
        raw = read_message(proc.stdout)
        if raw is None:
            print(f'{req["method"]}: <no answer>')
            break
        print(f'{req["method"]} {json.dumps(params)[:200]}\n  -> {raw.decode("utf-8", "replace")[:1500]}')
        msg = json.loads(raw)
        result = msg.get('result')
        if isinstance(result, dict) and isinstance(result.get('data'), str):
            env['$data'] = result['data']
        if isinstance(result, dict) and 'snapshot' in result:
            env['$snapshot'] = result['snapshot']
            projects = result.get('projects') or []
            if projects:
                env['$project'] = projects[0].get('id')
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    err = proc.stderr.read().decode('utf-8', 'replace')
    print(f'exit {proc.returncode}; stderr {len(err)} bytes')
    for line in err.splitlines()[:12]:
        print('  ' + line[:300])


if __name__ == '__main__':
    main()
