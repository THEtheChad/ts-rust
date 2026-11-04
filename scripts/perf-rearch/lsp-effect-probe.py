#!/usr/bin/env python3
"""lsp-effect-probe.py: lane E0 probe of the Effect switch in the language server.

Usage: lsp-effect-probe.py <out.json> <root> <file> <line> <col> -- <server cmd...>

Starts the server (stdio), sends initialize (root <root>, pull diagnostics),
opens <file> (relative to <root>), then asks, in order:
  textDocument/diagnostic, hover at <line>:<col> (0-based),
  codeAction over that line with no filter (quick fixes and refactors),
  codeAction for each diagnostic on that line with only ["quickfix"],
  documentSymbol, inlayHint over the file,
and writes every answer (ids and the root path removed) to <out.json>.
Prints the codeActionKinds of the initialize answer and the diagnostic codes.
Environment (TSC_RS_EFFECT) passes through to the server.
"""

import json
import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 8 or "--" not in sys.argv:
        print(__doc__)
        return 2
    sep = sys.argv.index("--")
    out, root, rel, line, col = sys.argv[1:6]
    line, col = int(line), int(col)
    cmd = sys.argv[sep + 1 :]
    root = os.path.realpath(root)
    path = os.path.join(root, rel)
    uri = "file://" + path
    text = open(path, encoding="utf-8").read()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    next_id = [0]

    def send(msg):
        body = json.dumps(msg).encode()
        proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        proc.stdin.flush()

    def read():
        length = 0
        while True:
            header = proc.stdout.readline()
            if not header:
                raise EOFError("server closed")
            header = header.strip()
            if not header:
                break
            if header.lower().startswith(b"content-length:"):
                length = int(header.split(b":")[1])
        return json.loads(proc.stdout.read(length))

    def request(method, params):
        next_id[0] += 1
        rid = next_id[0]
        send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = read()
            if msg.get("id") == rid and "method" not in msg:
                return msg.get("result", {"error": msg.get("error")})
            if "method" in msg and "id" in msg:  # server request: answer null
                send({"jsonrpc": "2.0", "id": msg["id"], "result": None})

    doc = {"uri": uri}
    answers = {}
    init = request(
        "initialize",
        {
            "processId": os.getpid(),
            "rootUri": "file://" + root,
            "capabilities": {
                "textDocument": {
                    "diagnostic": {"dynamicRegistration": False},
                    "codeAction": {
                        "codeActionLiteralSupport": {
                            "codeActionKind": {"valueSet": ["quickfix", "refactor", "source"]}
                        }
                    },
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                }
            },
        },
    )
    answers["initialize.codeActionProvider"] = init.get("capabilities", {}).get("codeActionProvider")
    send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    send(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {"textDocument": {"uri": uri, "languageId": "typescript", "version": 1, "text": text}},
        }
    )
    diags = request("textDocument/diagnostic", {"textDocument": doc})
    answers["diagnostic"] = diags
    pos = {"line": line, "character": col}
    answers["hover"] = request("textDocument/hover", {"textDocument": doc, "position": pos})
    line_range = {"start": {"line": line, "character": 0}, "end": {"line": line + 1, "character": 0}}
    items = diags.get("items", []) if isinstance(diags, dict) else []
    on_line = [d for d in items if d["range"]["start"]["line"] == line]
    answers["codeAction.all"] = request(
        "textDocument/codeAction",
        {"textDocument": doc, "range": line_range, "context": {"diagnostics": on_line}},
    )
    answers["codeAction.quickfix"] = [
        request(
            "textDocument/codeAction",
            {"textDocument": doc, "range": d["range"], "context": {"diagnostics": [d], "only": ["quickfix"]}},
        )
        for d in on_line
    ]
    answers["documentSymbol"] = request("textDocument/documentSymbol", {"textDocument": doc})
    lines = text.count("\n")
    answers["inlayHint"] = request(
        "textDocument/inlayHint",
        {"textDocument": doc, "range": {"start": {"line": 0, "character": 0}, "end": {"line": lines, "character": 0}}},
    )
    request("shutdown", None)
    send({"jsonrpc": "2.0", "method": "exit"})
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    raw = json.dumps(answers, indent=1, sort_keys=True).replace(root, "<root>")
    with open(out, "w", encoding="utf-8") as f:
        f.write(raw + "\n")
    kinds = (answers["initialize.codeActionProvider"] or {}).get("codeActionKinds")
    codes = sorted({str(d.get("code")) for d in items})
    print(f"codeActionKinds={kinds}")
    print(f"diagnostic codes={codes} quickfix actions={sum(len(a or []) for a in answers['codeAction.quickfix'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
