"""Mutators. Text mutators take (rng, text, donor) and return new text; donor is the text of
another seed (for splicing). Case mutators change the options or file names of a case.

They aim at shapes the test corpus does not cover: moved and duplicated comments and
@ts-ignore/@ts-expect-error/@ts-nocheck directives, JSDoc type expressions (nested, with
comments inside), deep nesting, Unicode identifiers and escapes, line endings, BOMs, truncation,
and token edits. Text may hold raw bytes as surrogate escapes (see oracle.write_project).
"""
import math
import re

TOKEN_RE = re.compile(r'''//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[\w$]+|\s+|.''', re.S)
COMMENT_RE = re.compile(r'//[^\n]*|/\*.*?\*/', re.S)
IDENT_RE = re.compile(r'\b[A-Za-z_]\w*\b')
KEYWORDS = set('''break case catch class const continue debugger default delete do else enum export extends
false finally for function if import in instanceof new null return super switch this throw true try typeof
var void while with as implements interface let package private protected public static yield any boolean
number string symbol type from of declare module namespace readonly keyof infer is unique never unknown
abstract async await get set constructor require undefined object accessor satisfies out using'''.split())


def tokens(text):
    return TOKEN_RE.findall(text)


def boundaries(text):
    """Offsets between tokens."""
    out, pos = [0], 0
    for t in tokens(text):
        pos += len(t)
        out.append(pos)
    return out


def line_starts(text):
    return [0] + [m.end() for m in re.finditer(r'\n', text)]


def insert_at(text, pos, s):
    return text[:pos] + s + text[pos:]


DIRECTIVES = ['// @ts-ignore', '// @ts-expect-error', '// @ts-nocheck', '// @ts-check', '/// @ts-ignore',
              '/* @ts-ignore */', '/* @ts-expect-error */', '// @ts-expect-error: reason', '//@ts-ignore',
              '/** @ts-ignore */', '//   @ts-ignore extra', '// @ts-ignore-next', '// @TS-IGNORE',
              '/*\n @ts-expect-error */', '// @ts-nocheck foo']


def m_directive(rng, text, donor):
    d = rng.choice(DIRECTIVES)
    for _ in range(rng.randint(1, 3)):
        if rng.random() < 0.6:
            text = insert_at(text, rng.choice(line_starts(text)), d + rng.choice(['\n', '\r\n']))
        else:
            sep = '\n' if d.startswith('//') else rng.choice([' ', '\n', ''])
            text = insert_at(text, rng.choice(boundaries(text)), d + sep)
    return text


def m_comment_move(rng, text, donor):
    comments = list(COMMENT_RE.finditer(text))
    if not comments:
        return m_directive(rng, text, donor)
    m = rng.choice(comments)
    c = m.group(0) + ('\n' if m.group(0).startswith('//') else '')
    if rng.random() < 0.5:
        text = text[:m.start()] + text[m.end():]
    return insert_at(text, rng.choice(boundaries(text)), c)


def jsdoc_type(rng, depth=0):
    leaf = ['number', 'string', '*', '?', 'any', 'Object', 'Function', 'T', 'Array', 'null', 'undefined', '"lit"',
            '1', 'typeof x', 'import("./x").Y', 'Promise', 'unknown', 'never', 'this', 'keyof T']
    if depth > 4 or rng.random() < 0.3:
        return rng.choice(leaf)
    t = lambda: jsdoc_type(rng, depth + 1)
    forms = [lambda: f'?{t()}', lambda: f'!{t()}', lambda: f'{t()}=', lambda: f'...{t()}', lambda: f'Array<{t()}>',
             lambda: f'{t()}[]', lambda: f'{{a: {t()}, b?: {t()}}}', lambda: f'function({t()}, {t()}): {t()}',
             lambda: f'({t()}|{t()})', lambda: f'{{\n *   // @ts-ignore\n *   a: {t()}\n * }}',
             lambda: f'{t()} /* c */', lambda: f'/* @ts-ignore */ {t()}', lambda: f'Object<string, {t()}>',
             lambda: f'[{t()}, {t()}]', lambda: f'{{[k: string]: {t()}}}', lambda: f'function(new:{t()})',
             lambda: f'{t()} extends {t()} ? {t()} : {t()}', lambda: f'{{\n * // @ts-expect-error\n * x: {t()} }}',
             lambda: f'Array.<{t()}>', lambda: f'(x: {t()}) => {t()}']
    return rng.choice(forms)()


def jsdoc_block(rng):
    t = jsdoc_type(rng)
    tags = [f'@type {{{t}}}', f'@param {{{t}}} x', f'@returns {{{t}}}', f'@typedef {{{t}}} Td',
            f'@template T\n * @param {{{t}}} [y]', f'@satisfies {{{t}}}', f'@property {{{t}}} p',
            f'@callback Cb\n * @param {{{t}}} a', f'@this {{{t}}}', f'@enum {{{t}}}', f'@overload\n * @param {{{t}}} z',
            f'@import {{ Y }} from "./x"', f'@see {{@link {t}}}', f'@deprecated', f'@extends {{{t}}}']
    body = '\n * '.join(rng.choice(tags) for _ in range(rng.randint(1, 3)))
    return f'/**\n * {body}\n */' if '\n' in body or rng.random() < 0.5 else f'/** {body} */'


def m_jsdoc(rng, text, donor):
    block = jsdoc_block(rng)
    if rng.random() < 0.3:
        # #49 shape: directives before, then a directive inside a JSDoc {} type.
        block = (f'// @ts-ignore\nlet __a: string = 1;\n/**\n * @type {{{{\n *   // @ts-ignore\n *   a: {jsdoc_type(rng)}\n'
                 f' * }}}}\n */\nlet __b = {{ a: 1 }};')
    pos = rng.choice(line_starts(text)) if rng.random() < 0.8 else rng.choice(boundaries(text))
    return insert_at(text, pos, block + '\n')


def m_jsdoc_host(rng, text, donor):
    """A JSDoc block on a random host, with stray TS syntax: in .js files Go's reparser turns
    @template/@param/@type/... into TS nodes, unless the host already has them."""
    tps = rng.choice(['', '', '<T>', '<T,>', '<T = 1>'])  # no '<>': known (empty list keeps @template out)
    params = rng.choice(['()', '(a)', '(a, b)', '(a: number)', '(a = 1)', '(...a)', '({ a })', '(a?)'])
    ret = rng.choice(['', '', ': T', ': void'])
    hosts = [f'function f{tps}{params}{ret} {{ return a; }}', f'class K{tps} {{ m{tps}{params}{ret} {{}} }}',
             f'const f = {tps}{params}{ret} => a;', f'const f = function{tps}{params}{ret} {{}};',
             f'const o = {{ m{tps}{params}{ret} {{}}, p: 1 }};', f'module.exports.f = function{params} {{}};',
             f'K.prototype.m = function{params} {{}};', f'export default function{tps}{params}{ret} {{}}',
             f'let v = 1;', f'const C = class{tps} {{ constructor{tps}{params} {{}} }};',
             f'class K {{ /** @type {{number}} */ p; static {{ }} get g(){{ return 1; }} }}',
             f'export const f = async{tps}{params} => {{}};', f'function* g{tps}{params} {{}}',
             f'/** @this {{K}} */ function h() {{}}', f'exports.x = 1;']
    tags = ['@template T', '@template {string} T', '@template T, U', '@template', '@template {} T',
            f'@param {{{jsdoc_type(rng)}}} a', '@param {T} a', '@param a', '@param {number} [b=1]',
            f'@returns {{{jsdoc_type(rng)}}}', '@return {T}', f'@type {{{jsdoc_type(rng)}}}', '@type {(a: T) => T}',
            '@overload', '@typedef {object} Q', '@callback Cb', '@this {T}', '@satisfies {Function}',
            '@extends {Array<T>}', '@implements {I}', '@override', '@private', '@readonly', '@async',
            '@param {...number} a', '@param {{ a: T }} a', '@param {T} a.b', '@throws {Error}', '@abstract']
    body = '\n * '.join(rng.choice(tags) for _ in range(rng.randint(1, 4)))
    snippet = f'/**\n * {body}\n */\n{rng.choice(hosts)}'
    return insert_at(text, rng.choice(line_starts(text)), snippet + '\n')


def depth(rng, lo=8, hi=3000):
    return int(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def nest(rng, n):
    """A statement with nesting depth n."""
    def tmpl(n):
        s = 'x'
        for _ in range(n):
            s = '`a${' + s + '}b`'
        return s
    forms = [
        lambda: f'let __d = {"(" * n}1{")" * n};',
        lambda: f'let __d = {"f(" * n}{")" * n};',
        lambda: f'let __d = {"{a:" * n}1{"}" * n};',
        lambda: f'let __d = {"[" * n}{"]" * n};',
        lambda: f'let __d = {tmpl(min(n, 500))};',
        lambda: f'type __D<T> = {"T extends 0 ? 1 : " * n}2;',
        lambda: f'type __D<T> = {"T extends 0 ? (" * n}1{") : 2" * n};',
        lambda: f'let __d = {" + ".join(["1"] * n)};',
        lambda: f'let __d = {" + ".join(["\"s\""] * n)};',
        lambda: f'declare let a: any; let __d = {" && ".join(["a"] * n)};',
        lambda: f'let __d = {"() => " * n}0;',
        lambda: f'function __d() {{{"if (1) {" * n}{"}" * n}}}',
        lambda: f'{"function f() {" * n}{"}" * n}',
        lambda: f'{"namespace N {" * n}{"}" * n}',
        lambda: f'let __d = {"!" * n}1;',
        lambda: f'let __d = {"typeof " * n}1;',
        lambda: f'declare let a: any; let __d = a{".b" * n};',
        lambda: f'declare let a: any; let __d = a{"?.b" * n};',
        lambda: f'declare let a: any; let __d = a{"()" * n};',
        lambda: f'declare let a: any; let __d = a{"[0]" * n};',
        lambda: f'let __d: {"Array<" * n}number{">" * n};',
        lambda: f'let __d: {" | ".join(f"{i}" for i in range(n))};',
        lambda: f'let __d: {"{a:" * n}1{"}" * n};',
        lambda: f'let __d: {"[" * n}{"]" * n};',
        lambda: f'let __d: {"(" * n}number{")" * n};',
        lambda: f'let __d: {"keyof " * n}number;',
        lambda: f'let __d = {"new " * n}Object;',
        lambda: f'let __d = {"class { m() { return " * n}1{"} }" * n};',
        lambda: f'let __d = {"1 ? " * n}2{" : 3" * n};',
        lambda: f'let __d = {"<any>" * n}1;',
        lambda: f'let __d = 1{" as any" * n};',
        lambda: f'let __d = 1{"!" * n};',
        lambda: f'let __d = {"/* c */" * n}1;',
        lambda: f'let __d = {", ".join(["1"] * n)};',
        lambda: f'let __d = {"{...{a:1, " * n}{"}}" * n};',
        lambda: f'let __d = {"await " * n}1;',
        lambda: f'let __d = {"-" * n}1;',
    ]
    return rng.choice(forms)()


def m_deep(rng, text, donor):
    n = depth(rng)
    if rng.random() < 0.75:
        return insert_at(text, rng.choice(line_starts(text)), nest(rng, n) + '\n')
    # Wrap a token in n parens, calls or arrays.
    b = boundaries(text)
    if len(b) < 3:
        return text
    i = rng.randrange(len(b) - 1)
    o, c = rng.choice([('(', ')'), ('f(', ')'), ('[', ']'), ('{a:', '}'), ('<', '>'), ('${', '}')])
    return text[:b[i]] + o * n + text[b[i]:b[i + 1]] + c * n + text[b[i + 1]:]


def m_jsx_deep(rng, text, donor):
    n = depth(rng, 8, 1500)
    tag = rng.choice(['div', 'a', 'A.B', 'x:y', ''])
    inner = rng.choice(['{1}', 'text', '{/* c */}', '', '{<b/>}'])
    s = f'let __j = {f"<{tag}>" * n}{inner}{f"</{tag}>" * n};'
    return insert_at(text, rng.choice(line_starts(text)), s + '\n')


UNICODE_SWAPS = ['é', 'ÿ', 'ā', 'α', 'א', '中', '\U0001d465', '‍', '‌', '́',
                 '\\u0061', '\\u{61}', '\\u{1D465}', '\\u00e9', '\\u{10FFFF}', '\\u0030', '℘', '℮',
                 '\U00010400', '\U0001f600', ' ', ' ', '﻿', '\\x61', '\\', '\udcff']


def m_unicode(rng, text, donor):
    idents = [m for m in IDENT_RE.finditer(text) if m.group(0) not in KEYWORDS]
    s = rng.choice(UNICODE_SWAPS)
    if idents and rng.random() < 0.7:
        name = rng.choice(idents).group(0)
        k = rng.randint(0, len(name))
        new = name[:k] + s + name[k:] if rng.random() < 0.6 else name[:k] + s + name[k + 1:]
        if rng.random() < 0.5:
            return re.sub(r'\b' + re.escape(name) + r'\b', lambda _: new, text)
        m = rng.choice([m for m in idents if m.group(0) == name])
        return text[:m.start()] + new + text[m.end():]
    return insert_at(text, rng.randint(0, len(text)), s)


def m_newlines(rng, text, donor):
    mode = rng.choice(['crlf', 'cr', 'mixed', 'ls', 'strip'])
    lines = text.split('\n')
    if mode == 'crlf':
        return '\r\n'.join(lines)
    if mode == 'cr':
        return '\r'.join(lines)
    if mode == 'mixed':
        return ''.join(l + rng.choice(['\n', '\r\n', '\r', ' ', ' ']) for l in lines)
    if mode == 'ls':
        return text.replace('\n', rng.choice([' ', ' ', '\u0085']), rng.randint(1, 5))
    return text.replace('\n', ' ', rng.randint(1, 5))


def m_bom(rng, text, donor):
    mode = rng.choice(['utf8', 'utf8', 'double', 'inside', 'utf16le', 'utf16be', 'stray'])
    if mode == 'utf8':
        return '﻿' + text
    if mode == 'double':
        return '﻿﻿' + text
    if mode == 'inside':
        return insert_at(text, rng.choice(boundaries(text)), '﻿')
    if mode == 'stray':
        return '\udcef\udcbb' + text
    try:
        data = text.encode('utf-16-le' if mode == 'utf16le' else 'utf-16-be')
    except UnicodeEncodeError:
        return '﻿' + text
    return ((b'\xff\xfe' if mode == 'utf16le' else b'\xfe\xff') + data).decode('utf-8', 'surrogateescape')


def m_truncate(rng, text, donor):
    data = text.encode('utf-8', 'surrogateescape')
    if not data:
        return text
    return data[:rng.randrange(len(data))].decode('utf-8', 'surrogateescape')


PUNCT = ['{', '}', '(', ')', '[', ']', '<', '>', '`', '${', '/*', '*/', '"', "'", '=>', '?.', '@', '#', '\\', ';',
         ',', '...', '?', ':', '/', '</', '/>', '=', '!', '<<', '>>', '>>>', 'as', 'satisfies', 'async', 'yield']


def m_tokens(rng, text, donor):
    toks = tokens(text)
    if len(toks) < 2:
        return text + rng.choice(PUNCT)
    real = [i for i, t in enumerate(toks) if not t.isspace()] or [0]
    for _ in range(rng.randint(1, 4)):
        i = rng.choice(real)
        if i >= len(toks):
            continue
        op = rng.randrange(5)
        if op == 0:
            toks[i] = ''
        elif op == 1:
            toks[i] = toks[i] * rng.choice([2, 2, 3])
        elif op == 2 and i + 2 < len(toks):
            toks[i], toks[i + 2] = toks[i + 2], toks[i]
        elif op == 3:
            toks[i] = rng.choice(PUNCT) + (' ' if rng.random() < 0.5 else '') + toks[i]
        else:
            toks[i] = toks[rng.choice(real)]
    return ''.join(toks)


def m_splice(rng, text, donor):
    lines = donor.split('\n')
    a = rng.randrange(len(lines))
    chunk = '\n'.join(lines[a:a + rng.randint(1, 20)])
    return insert_at(text, rng.choice(line_starts(text)), chunk + '\n')


def m_comment_out(rng, text, donor):
    starts = line_starts(text)
    a = rng.choice(starts)
    b = rng.choice([s for s in starts if s >= a] + [len(text)])
    if rng.random() < 0.5:
        return text[:a] + '/*' + text[a:b] + '*/' + text[b:]
    return text[:a] + '/**' + text[a:b] + '*/' + text[b:]


TEXT_MUTATORS = [(m_jsdoc_host, 4), (m_directive, 4), (m_comment_move, 3), (m_jsdoc, 4), (m_deep, 4), (m_jsx_deep, 1),
                 (m_unicode, 3), (m_newlines, 2), (m_bom, 1), (m_truncate, 3), (m_tokens, 4), (m_splice, 2),
                 (m_comment_out, 1)]

OPTION_VALUES = {
    'strict': [True, False], 'target': ['es5', 'es2015', 'es2017', 'es2020', 'es2022', 'esnext'],
    'module': ['commonjs', 'esnext', 'nodenext', 'node16', 'preserve', 'amd', 'system', 'umd', 'es2015', 'none'],
    'jsx': ['preserve', 'react', 'react-jsx', 'react-jsxdev', 'react-native'], 'declaration': [True, False],
    'exactOptionalPropertyTypes': [True], 'noImplicitAny': [True, False], 'strictNullChecks': [True, False],
    'noUncheckedIndexedAccess': [True], 'isolatedDeclarations': [True], 'verbatimModuleSyntax': [True],
    'experimentalDecorators': [True], 'moduleResolution': ['bundler', 'node16', 'nodenext', 'node10'],
    'esModuleInterop': [True, False], 'noEmit': [True, False], 'removeComments': [True], 'sourceMap': [True],
    'noImplicitReturns': [True], 'noUnusedLocals': [True], 'allowUnreachableCode': [False, True],
    'useDefineForClassFields': [True, False], 'importHelpers': [True], 'checkJs': [True], 'allowJs': [True],
    'emitDeclarationOnly': [True], 'noPropertyAccessFromIndexSignature': [True], 'isolatedModules': [True],
}


def mutate_options(rng, case):
    options = dict(case['options'])
    for _ in range(rng.randint(1, 3)):
        k = rng.choice(list(OPTION_VALUES))
        options[k] = rng.choice(OPTION_VALUES[k])
    if options.get('emitDeclarationOnly'):
        options['declaration'] = True
    return {**case, 'options': options}, 'options:' + ','.join(f'{k}={v}' for k, v in sorted(options.items()))


def to_js(rng, case, only=None):
    """Renames one .ts/.tsx source (only, if given) to .js/.jsx and turns on allowJs and checkJs."""
    srcs = [f for f in case['files'] if re.search(r'(?<!\.d)\.tsx?$', f) and only in (None, f)]
    if not srcs:
        return case, 'to-js:none'
    f = rng.choice(srcs)
    g = re.sub(r'\.ts(x?)$', r'.js\1', f)
    if g in case['files']:
        return case, 'to-js:none'
    files = {(g if k == f else k): v for k, v in case['files'].items()}
    return {**case, 'files': files, 'options': {**case['options'], 'allowJs': True, 'checkJs': True}}, f'to-js:{g}'


def mutate_tsconfig(rng, case):
    """Replaces the generated tsconfig.json with mutated JSON text (comments, trailing commas,
    BOMs, line endings, wrong option types, truncation, token edits)."""
    import json
    from oracle import tsconfig
    cfg = tsconfig(case)
    if rng.random() < 0.4:
        k = rng.choice(list(OPTION_VALUES) + ['lib', 'types', 'paths', 'rootDirs', 'noErrorTruncation', 'newLine'])
        cfg['compilerOptions'][k] = rng.choice([1, 'x', None, [], {}, True, ['es5', 'bad'], {'*': ['x']}, '\u0000'])
    text = json.dumps(cfg, indent=rng.choice([None, 2, '\t']), ensure_ascii=rng.random() < 0.5)
    m = rng.choice([m_comment_out, m_directive, m_newlines, m_bom, m_truncate, m_tokens, m_unicode, 'comma', 'comment'])
    if m == 'comma':
        text = re.sub(r'([\]}"\d])(\s*[\]}])', r'\1,\2', text, count=rng.randint(1, 3))
    elif m == 'comment':
        text = insert_at(text, rng.choice(boundaries(text)), rng.choice(['// c\n', '/* c */', '/*', '//', '\n']))
    else:
        text = m(rng, text, text)
    return {**case, 'files': {**case['files'], 'tsconfig.json': text}}, f'tsconfig:{getattr(m, "__name__", m)}'


ARG_VARIANTS = [['--pretty', 'true'], ['--pretty', 'true'], ['--pretty', 'true'], ['--listFiles'], ['--explainFiles'],
                ['--listEmittedFiles'], ['--traceResolution'], ['--showConfig'], ['--noErrorTruncation'],
                ['--pretty', 'true', '--noErrorTruncation'], ['--listFilesOnly'], ['--strict'], ['--noCheck']]


def mutate(rng, case, donors):
    """1 to 4 stacked mutations of a copy of case; records them in case['mutations']."""
    case = {**case, 'files': dict(case['files']), 'mutations': []}
    funcs, weights = zip(*TEXT_MUTATORS)
    for _ in range(rng.choice([1, 1, 1, 2, 2, 3, 4])):
        r = rng.random()
        if r < 0.12:
            case, note = mutate_options(rng, case)
        elif r < 0.18:
            case, note = to_js(rng, case)
        elif r < 0.23 and 'tsconfig.json' not in case['files']:
            case, note = mutate_tsconfig(rng, case)
        else:
            srcs = [f for f in case['files'] if not f.endswith('.json')] or list(case['files'])
            f = rng.choice(srcs)
            m = rng.choices(funcs, weights)[0]
            donor = rng.choice(donors)
            case['files'][f] = m(rng, case['files'][f], rng.choice(list(donor['files'].values())))
            note = f'{m.__name__[2:]}:{f}'
            if m is m_jsdoc_host and rng.random() < 0.6:  # JSDoc becomes types in JS files only
                case, js = to_js(rng, case, f)
                note += ' ' + js
        case['mutations'] = case.get('mutations', []) + [note]
    return case
