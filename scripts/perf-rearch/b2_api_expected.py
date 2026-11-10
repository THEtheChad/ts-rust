#!/usr/bin/env python3
"""Lane B2: write the Go answers of the API case-sensitivity tests.

usage: scripts/perf-rearch/b2_api_expected.py [<tsgo>] [<cases dir>]
       scripts/perf-rearch/b2_api_expected.py help

<tsgo> defaults to the Go oracle at the pin (~/.local/bin/tsgo-oracle-3e9236361fa9), <cases dir> to
crates/ts_goport/tests/fixtures/api_case_sensitivity. Each case is a directory with request.json, the
createSnapshot params (Go internal/api/proto.go SnapshotRequestChangesParams; the file system is in
memory). The script starts `<tsgo> --api --async --useCaseSensitiveFileNames=false --cwd /` in / and sends the
sequence that crates/ts_goport/tests/api_case_sensitivity.rs sends: initialize, createSnapshot, then
for each project of the answer getSourceFileNames, getProgramDiagnostics, getSemanticDiagnostics,
getConfigFileParsingDiagnostics and emitToString. It writes each result to expected.json in the case
directory, as {"createSnapshot": result, "<method> <project id>": result, ...}. Run it with the
oracle only; the test compares the tsgo answers with these.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ORACLE = os.path.expanduser("~/.local/bin/tsgo-oracle-3e9236361fa9")
CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "crates", "ts_goport", "tests",
                     "fixtures", "api_case_sensitivity")
PROJECT_METHODS = ["getSourceFileNames", "getProgramDiagnostics", "getSemanticDiagnostics",
                   "getConfigFileParsingDiagnostics", "emitToString"]


class Rpc:
    def __init__(self, argv: list[str]):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  cwd="/")
        self.n = 0

    def call(self, method: str, params):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        body = json.dumps(msg).encode()
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.p.stdin.flush()
        while True:
            head = {}
            while True:
                line = self.p.stdout.readline()
                if not line:
                    raise RuntimeError(f"server closed during {method}")
                line = line.strip()
                if not line:
                    break
                k, _, v = line.decode().partition(":")
                head[k.strip().lower()] = v.strip()
            resp = json.loads(self.p.stdout.read(int(head["content-length"])))
            if resp.get("id") == self.n and "method" not in resp:
                if "error" in resp:
                    raise RuntimeError(f"{method}: {resp['error']}")
                return resp.get("result")

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


def expected(tsgo: str, request: dict) -> dict:
    rpc = Rpc([tsgo, "--api", "--async", "--useCaseSensitiveFileNames=false", "--cwd", "/"])
    rpc.call("initialize", None)
    snapshot = rpc.call("createSnapshot", request)
    out = {"createSnapshot": snapshot}
    for project in snapshot.get("projects", []):
        params = {"snapshot": snapshot["snapshot"], "project": project["id"]}
        for method in PROJECT_METHODS:
            out[f"{method} {project['id']}"] = rpc.call(method, params)
    rpc.close()
    return out


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    tsgo = argv[0] if argv else ORACLE
    cases = argv[1] if len(argv) > 1 else CASES
    for name in sorted(os.listdir(cases)):
        case = os.path.join(cases, name)
        with open(os.path.join(case, "request.json")) as f:
            request = json.load(f)
        with open(os.path.join(case, "expected.json"), "w") as f:
            json.dump(expected(tsgo, request), f, indent=1, sort_keys=True)
            f.write("\n")
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
