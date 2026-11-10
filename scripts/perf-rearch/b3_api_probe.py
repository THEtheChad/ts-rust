#!/usr/bin/env python3
"""Lane B3: run one synthetic-program API probe on a tsgo binary and print what it emits.

usage: scripts/perf-rearch/b3_api_probe.py <tsgo> <case.json> [--insensitive] [--cwd DIR]
       scripts/perf-rearch/b3_api_probe.py help

<case.json> holds {"fileSystem": {...}, "createPrograms": [...]} as createSnapshot takes them
(Go internal/api/proto.go SnapshotRequestChangesParams). The probe starts `<tsgo> --api --async`
(JSON-RPC, Content-Length framing; `--useCaseSensitiveFileNames=false` with --insensitive), sends
initialize, createSnapshot with the case, then emitToString for each project of the answer, and prints one
JSON object: {"initialize", "projects", "emit": [{"project", "emitSkipped", "diagnostics",
"outputFiles": [[fileName, sourceFileName]]}]}. Texts are left out so two runs diff on names.
Compare a Rust binary with the Go oracle (~/.local/bin/tsgo-oracle-3e9236361fa9) by diffing the two
outputs. Inputs live in memory; nothing is written to disk.
"""

from __future__ import annotations

import json
import subprocess
import sys


class Rpc:
    def __init__(self, argv: list[str]):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
                    raise RuntimeError("server closed: " + self.p.stderr.read().decode(errors="replace")[-2000:])
                line = line.strip()
                if not line:
                    break
                k, _, v = line.decode().partition(":")
                head[k.strip().lower()] = v.strip()
            resp = json.loads(self.p.stdout.read(int(head["content-length"])))
            if resp.get("id") == self.n and "method" not in resp:
                return resp

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    tsgo, case = argv[0], json.load(open(argv[1]))
    cmd = [tsgo, "--api", "--async"]
    if "--insensitive" in argv:
        cmd.append("--useCaseSensitiveFileNames=false")
    if "--cwd" in argv:
        cmd += ["--cwd", argv[argv.index("--cwd") + 1]]
    rpc = Rpc(cmd)
    out = {"initialize": rpc.call("initialize", None).get("result")}
    snap = rpc.call("createSnapshot", case)
    if "error" in snap:
        out["createSnapshot_error"] = str(snap["error"].get("message", snap["error"])).split("\n")[0]
        print(json.dumps(out, indent=1))
        rpc.close()
        return 0
    res = snap["result"]
    projects = [p.get("id") for p in res.get("projects", [])]
    out["projects"] = projects
    out["emit"] = []
    for pid in projects:
        r = rpc.call("emitToString", {"snapshot": res["snapshot"], "project": pid})
        if "error" in r:
            out["emit"].append({"project": pid, "error": str(r["error"].get("message", r["error"])).split("\n")[0]})
            continue
        e = r["result"]
        out["emit"].append({
            "project": pid, "emitSkipped": e.get("emitSkipped"),
            "diagnostics": [(d.get("fileName"), d.get("code"), d.get("text") or d.get("message")) for d in e.get("diagnostics") or []],
            "outputFiles": sorted([f["fileName"], f.get("sourceFileName")] for f in e.get("outputFiles") or []),
        })
    rpc.close()
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
