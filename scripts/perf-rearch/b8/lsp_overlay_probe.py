#!/usr/bin/env python3
"""lsp_overlay_probe.py (lane B8): checker-side file probes against open documents that are not on disk.

Usage: lsp_overlay_probe.py <case> <scratch dir> -- <server cmd...>
       cases: index-sibling, import-ext, import-ext-disk, import-ext-deleted

Writes the case's disk files into a fresh <scratch dir>/<case>, starts the server (stdio LSP), opens the
case's documents (an `overlay` document exists only as the open text, never on disk), and asks:
pull diagnostics of each open file, hover at the case's position, and the quick fixes of each diagnostic
of the first file. Prints the answers as JSON with ids and the root path removed, so two servers' outputs
can be diffed. The probes in question (Go `Program.FileExists`: modulespecifiers tryGetAnyFileFromPath,
checker getSuggestedImportExtension) must see the open documents, as Go's do (the snapshot's file system).
"""
import json
import os
import shutil
import subprocess
import sys

GLOBALS = ''.join(f'interface {n} {{}}\n' for n in ['Array<T>', 'Boolean', 'Function', 'CallableFunction',
                                                     'NewableFunction', 'IArguments', 'Number', 'Object', 'RegExp', 'String'])
CASES = {
    # src/lib.ts exists only as an open document: the module specifier of Foo keeps /index (Go util.go:184).
    'index-sibling': {
        'disk': {
            'tsconfig.json': '{"compilerOptions":{"declaration":true,"isolatedDeclarations":true,"noLib":true,'
                             '"module":"esnext","moduleResolution":"bundler","types":[]},"files":["src/main.ts","src/globals.d.ts"]}',
            'src/globals.d.ts': GLOBALS,
            'src/main.ts': 'import { make } from "./reexport";\nexport const v = make();\n',
            'src/reexport.ts': 'export { make } from "./lib/index";\n',
            'src/lib/index.ts': 'export interface Foo { a: number }\nexport function make(): Foo { return { a: 1 }; }\n',
        },
        'open': ['src/main.ts'],
        'overlay': {'src/lib.ts': 'export const sibling = 0;\n'},
        'hover': ('src/main.ts', 1, 13),
    },
    # src/foo.mts exists only as an open document: TS2835 suggests './foo.mjs' (Go checker.go:15759).
    'import-ext': {
        'disk': {
            'tsconfig.json': '{"compilerOptions":{"noLib":true,"module":"node16","moduleResolution":"node16","types":[]},'
                             '"files":["src/main.mts","src/globals.d.ts"]}',
            'src/globals.d.ts': GLOBALS,
            'src/main.mts': 'import { x } from "./foo";\nexport const y = x;\n',
        },
        'open': ['src/main.mts'],
        'overlay': {'src/foo.mts': 'export const x = 1;\n'},
        'hover': ('src/main.mts', 1, 13),
    },
    # Control: src/foo.mts on disk, not open: TS2835 in both servers.
    'import-ext-disk': {
        'disk': {
            'tsconfig.json': '{"compilerOptions":{"noLib":true,"module":"node16","moduleResolution":"node16","types":[]},'
                             '"files":["src/main.mts","src/globals.d.ts"]}',
            'src/globals.d.ts': GLOBALS,
            'src/main.mts': 'import { x } from "./foo";\nexport const y = x;\n',
            'src/foo.mts': 'export const x = 1;\n',
        },
        'open': ['src/main.mts'],
        'overlay': {},
        'hover': ('src/main.mts', 1, 13),
    },
    # src/foo.mts on disk when the server starts, deleted from disk before the first request (no watch event),
    # and open with its text: the snapshot sees the open document.
    'import-ext-deleted': {
        'disk': {
            'tsconfig.json': '{"compilerOptions":{"noLib":true,"module":"node16","moduleResolution":"node16","types":[]},'
                             '"files":["src/main.mts","src/globals.d.ts"]}',
            'src/globals.d.ts': GLOBALS,
            'src/main.mts': 'import { x } from "./foo";\nexport const y = x;\n',
            'src/foo.mts': 'export const x = 1;\n',
        },
        'open': ['src/main.mts', 'src/foo.mts'],
        'overlay': {},
        'delete': ['src/foo.mts'],
        'hover': ('src/main.mts', 1, 13),
    },
}


def main():
    sep = sys.argv.index('--')
    case, scratch = sys.argv[1:sep]
    cmd = sys.argv[sep + 1:]
    spec = CASES[case]
    root = os.path.realpath(os.path.join(scratch, case))
    shutil.rmtree(root, ignore_errors=True)
    for rel, text in spec['disk'].items():
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        with open(os.path.join(root, rel), 'w') as f:
            f.write(text)
    uri = lambda rel: 'file://' + os.path.join(root, rel)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=root)
    next_id = [0]

    def send(msg):
        body = json.dumps(msg).encode()
        proc.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
        proc.stdin.flush()

    def read():
        length = 0
        while True:
            header = proc.stdout.readline()
            if not header:
                raise EOFError('server closed')
            header = header.strip()
            if not header:
                break
            if header.lower().startswith(b'content-length:'):
                length = int(header.split(b':')[1])
        return json.loads(proc.stdout.read(length))

    def request(method, params):
        next_id[0] += 1
        rid = next_id[0]
        send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        while True:
            msg = read()
            if msg.get('id') == rid and 'method' not in msg:
                return msg.get('result', msg.get('error'))
            if 'id' in msg and 'method' in msg:  # a server request: answer null
                send({'jsonrpc': '2.0', 'id': msg['id'], 'result': None})

    request('initialize', {'processId': None, 'rootUri': 'file://' + root,
                           'workspaceFolders': [{'uri': 'file://' + root, 'name': 'p'}],
                           'capabilities': {'textDocument': {'diagnostic': {'dynamicRegistration': False},
                                                             'hover': {'contentFormat': ['markdown', 'plaintext']},
                                                             'codeAction': {'codeActionLiteralSupport': {'codeActionKind': {'valueSet': ['quickfix']}}}}},
                           'initializationOptions': {'disableAutomaticTypeAcquisition': True}})
    send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
    docs = [(rel, spec['disk'][rel]) for rel in spec['open']] + list(spec['overlay'].items())
    for rel, text in docs:
        lang = 'typescript'
        send({'jsonrpc': '2.0', 'method': 'textDocument/didOpen',
              'params': {'textDocument': {'uri': uri(rel), 'languageId': lang, 'version': 1, 'text': text}}})
    for rel in spec.get('delete', []):
        os.remove(os.path.join(root, rel))
    out = {}
    for rel, _ in docs:
        out[f'diagnostics {rel}'] = request('textDocument/diagnostic', {'textDocument': {'uri': uri(rel)}})
    rel, line, col = spec['hover']
    out['hover'] = request('textDocument/hover', {'textDocument': {'uri': uri(rel)}, 'position': {'line': line, 'character': col}})
    first = spec['open'][0]
    for i, d in enumerate((out[f'diagnostics {first}'] or {}).get('items', [])):
        out[f'quickfix {i}'] = request('textDocument/codeAction', {'textDocument': {'uri': uri(first)}, 'range': d['range'],
                                                                   'context': {'diagnostics': [d], 'only': ['quickfix']}})
    request('shutdown', None)
    send({'jsonrpc': '2.0', 'method': 'exit'})
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    print(json.dumps(out, indent=1, sort_keys=True).replace(root, '<root>'))


if __name__ == '__main__':
    main()
