"""Seed cases from the TypeScript compiler test corpus (tsc/testdata/tests/cases at the pin).

A case is {'name': str, 'files': {relpath: str}, 'options': {tsconfig option: value}}.
Test files are split the way Go's testrunner (internal/testrunner/test_case_parser.go) splits them:
`// @filename: x` starts a file, any other `// @key: value` line is a global option and is
dropped from the content. A leading `/` in file names is made relative to the project root.

Skipped: cases with @link/@symlink/@currentDirectory, a file named tsconfig.json or
package.json-driven layouts that need a real directory tree outside the project (paths with
`..`), and duplicated file names. Option values with variations (`es5, es2015` or `*`) take the
first value. Harness-only options and options that move output (outDir, outFile, ...) are
dropped: the fuzzer controls output. Option names are made canonical (and typed) from
tsc/internal/tsoptions/declarations_generated.go; unknown names are dropped.
"""
import re
from pathlib import Path

OPTION_RE = re.compile(r'^//\s*@(\w+)\s*:\s*([^\r\n]*)')
LINE_RE = re.compile(r'\r?\n')
SKIP_CASE = {'link', 'symlink', 'currentdirectory'}
DROP = {'outdir', 'outfile', 'out', 'declarationdir', 'rootdir', 'tsbuildinfofile', 'incremental',
        'composite', 'build', 'sourceroot', 'maproot', 'watch', 'listfiles', 'listemittedfiles',
        'explainfiles', 'traceresolution', 'generatecpuprofile', 'generatetrace',
        'diagnostics', 'extendeddiagnostics', 'pretty', 'project', 'help', 'all', 'version', 'init',
        'showconfig', 'locale', 'paths', 'plugins', 'baseurl', 'nolib', 'singlethreaded', 'checkers',
        'builders', 'quiet', 'preservewatchoutput'}
SOURCE_EXT = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')


def option_table(upstream):
    """{lowercase name: (canonical name, kind)} from the Go option declarations."""
    src = (Path(upstream) / 'internal/tsoptions/declarations_generated.go').read_text()
    table = {}
    for m in re.finditer(r'Name:\s*"(\w+)",\s*\n\s*Kind:\s*CommandLineOptionType(\w+)', src):
        table.setdefault(m.group(1).lower(), (m.group(1), m.group(2)))
    return table


def convert(value, kind):
    value = value.split(',')[0].strip() if kind not in ('List', 'ListOrElement') else value
    if value in ('*', ''):
        return None
    if kind == 'Boolean':
        return {'true': True, 'false': False}.get(value.lower())
    if kind == 'Number':
        return int(value) if value.isdigit() else None
    if kind in ('List', 'ListOrElement'):
        return [v.strip() for v in value.split(',') if v.strip()]
    if kind == 'Object':
        return None
    return value


def parse_case(path, table):
    text = Path(path).read_text(encoding='utf-8', errors='surrogateescape')
    if text.startswith('﻿'):
        text = text[1:]
    files, options, name, lines = {}, {}, None, []

    def flush():
        if name is None and not ''.join(lines).strip():
            return True
        rel = (name or Path(path).name).strip().lstrip('/')
        if not rel or '..' in rel.split('/') or rel in files or rel.lower().endswith('tsconfig.json'):
            return False
        files[rel] = '\n'.join(lines)
        return True

    for line in LINE_RE.split(text):
        m = OPTION_RE.match(line)
        if not m:
            lines.append(line)
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key in SKIP_CASE:
            return None
        if key == 'filename':
            if not flush():
                return None
            name, lines = value, []
        elif key not in DROP and key in table:
            canon, kind = table[key]
            v = convert(value, kind)
            if v is not None:
                options[canon] = v
    if not flush() or not any(f.endswith(SOURCE_EXT) for f in files):
        return None
    return {'name': str(path), 'files': files, 'options': options}


def load(upstream, limit=None):
    """Parses every compiler and conformance case under the upstream tsc/ checkout."""
    upstream = Path(upstream)
    table = option_table(upstream)
    root = upstream / 'testdata/tests/cases'
    paths = sorted(p for d in ('compiler', 'conformance') for p in (root / d).rglob('*')
                   if p.suffix in ('.ts', '.tsx', '.js') and p.is_file())
    cases = []
    for p in paths[:limit]:
        c = parse_case(p, table)
        if c:
            c['name'] = str(p.relative_to(root))
            cases.append(c)
    return cases, table
