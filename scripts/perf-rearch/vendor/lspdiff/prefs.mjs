// The user-preference scenario (lane B6): odd LSP configuration values, sent through the
// initialization options and workspace/didChangeConfiguration, each followed by the requests whose
// answers depend on them. It pins how a server parses its preferences (Go at the pin:
// ls/lsutil/userpreferences_generated.go, from ts#64554; ls/lsutil/userpreferences.go
// ParseUserPreferences) and what the preferences change.
//
// Two parts:
// - Parse steps: wrong-typed values, unknown enum strings, null, arrays where a string is expected,
//   unknown keys, the flat (raw), `unstable` and nested (VS Code) forms of one key and their
//   precedence, the `javascript` < `typescript` < `js/ts` sections and `editor`, ints beyond 32 and 64
//   bits. A checkpoint after each change takes the refresh requests it makes (Go project/session.go
//   Configure: inlay hints, code lens, diagnostics), then the whole battery runs.
// - Probes (PROBES): one preference at a time, every value of it (enums: each value, every other one
//   upper-cased; booleans: true and false), each followed by the requests that read it. Each probe is
//   chosen so that Go's answers differ between the preference's values; prefs_sensitivity.py checks
//   that on a Go log (its step names are `probe <path>=<value> ...`).
//
// Why a module of its own: client.mjs, scenario.mjs and fixtures.mjs are in the Go cache key of every
// lsp session (parity_lsp.py SESSION_SOURCES), so a scenario added there would make every session's
// Go answers run again. This module registers its scenario and fixture into scenario.mjs's SCENARIOS
// and fixtures.mjs's FIXTURES when lspdiff.mjs imports it, and parity_lsp.py keys only this session
// on it (its `sources`).
//
// Initialization options: client.mjs initializeParams sends its exported `userPreferences` object as
// initializationOptions.userPreferences (ATA off adds unstable.disableAutomaticTypeAcquisition to a
// copy). runPrefsScenario swaps that object's contents for the scenario's `init` for the length of
// the run and restores them after, so the other sessions keep theirs. lspdiff runs one scenario per
// process.
import { userPreferences } from "./client.mjs";
import { FIXTURES } from "./fixtures.mjs";
import { endPosition } from "./project.mjs";
import { SCENARIOS, runScenario } from "./scenario.mjs";

const json = (v) => JSON.stringify(v, null, 2) + "\n";

FIXTURES["prefs-small"] = {
  "package.json": json({ name: "prefs-small", version: "1.0.0", type: "module" }),
  "tsconfig.json": json({
    compilerOptions: {
      strict: true,
      target: "es2022",
      module: "preserve",
      moduleResolution: "bundler",
      jsx: "preserve",
      noEmit: true,
      noUnusedLocals: true,
      noImplicitReturns: true,
      paths: { "@lib/*": ["./src/lib/*"] },
    },
    include: ["src/**/*"],
  }),
  // A second project: it owns a symbol the first does not see (workspaceSymbols.scope).
  "other/tsconfig.json": json({ compilerOptions: { strict: true, target: "es2022", noEmit: true }, include: ["*.ts"] }),
  "other/owned.ts": "export interface OtherOwnedSymbol {\n  x: number;\n}\n",
  "src/lib/index.ts": [
    "export function formatValue(value: number, prefix: string): string {",
    "  return prefix + value;",
    "}",
    "export interface Shape {",
    "  area(): number;",
    "  name: string;",
    "}",
    'export type ShapeKind = "circle" | "square";',
    "export enum Color {",
    "  Red = 1,",
    "  Green = 2,",
    "  Blue = 4,",
    "}",
    'export const defaultLabel = "label";',
    "function helper(): number {",
    "  return 0;",
    "}",
    "export const helperValue = helper();",
    "export class Widget implements Shape {",
    '  name = "widget";',
    "  area(): number {",
    "    return 1;",
    "  }",
    "  resize(): void {}",
    "}",
    "",
  ].join("\n"),
  "src/lib/extra.ts": "export function formatExtra(value: number): number {\n  return value;\n}\n",
  "src/lib/sorting.ts": [
    "export const zeta = 1;",
    "export const Alpha = 2;",
    "export const beta = 3;",
    "export const a10 = 4;",
    "export const a2 = 5;",
    "export const A1 = 6;",
    "export const éclair = 7;",
    "export const Eclair = 8;",
    "export const ecran = 9;",
    "export const Beta = 10;",
    "export const Éclair = 11;",
    "export const eclair = 12;",
    "export type Zed = number;",
    "export type alpha = string;",
    "",
  ].join("\n"),
  "src/lib/side.ts": "export {};\n",
  // src/lib is a package of its own (a package.json), so `project-relative` gives a relative specifier
  // inside it and a non-relative one from outside (Go modulespecifiers: the nearest package.json).
  "src/lib/package.json": json({ name: "prefs-lib", private: true }),
  "src/lib/consumer.ts": 'export const fromInside = formatValue(2, "y");\n',
  // Auto-import completions (quote style, module specifier and ending, type-only), a missing-import
  // quick fix and an import-statement completion. No import: quote style and ending `auto` find none.
  "src/use.ts": ["export const label = defaultLabel;", "export const result = formatV", "export let kind: ShapeK", "import formatE", 'export const top = formatValue(3, "z");', ""].join("\n"),
  // The same quick fix in a file whose import of another module is single-quoted with a `.js` ending, so
  // `auto` infers both from it.
  "src/quoted.ts": ["import { formatExtra } from './lib/extra.js';", "export const c = formatExtra(1);", "export const q = defaultLabel;", ""].join("\n"),
  // The same quick fix two dirs down: a relative and a `paths` specifier differ.
  "src/deep/nested/use2.ts": 'export const v = formatValue(1, "x");\n',
  // Inlay hints of every kind.
  "src/hints.ts": [
    'import { formatValue } from "./lib/index";',
    "",
    "function greet(name: string, times: number, loud?: boolean) {",
    '  return name.repeat(times) + (loud ? "!" : "");',
    "}",
    'const name = "x";',
    "const message = greet(name, 3, true);",
    "const other = 5;",
    "const sum = compute(other, 2);",
    "const doubled = [1, 2].map((n) => n * 2);",
    "const shapeBox = makeShape();",
    "class Holder {",
    '  value = formatValue(1, "a");',
    "}",
    "enum Level {",
    "  Low = 1 << 0,",
    "  High = Low + 4,",
    "}",
    "interface ShapeBox {",
    "  size: number;",
    "}",
    "function makeShape() {",
    "  const box: ShapeBox = { size: 1 };",
    "  return box;",
    "}",
    "export const all = { message, sum, doubled, shapeBox, Holder, Level };",
    "export function compute(a: number, b: number) {",
    "  return a + b;",
    "}",
    "",
  ].join("\n"),
  // A long hover (maximumHoverLength).
  "src/long.ts": [
    "export const big = {",
    "  alpha: 1,",
    '  beta: "two",',
    "  gamma: [3],",
    "  delta: { epsilon: true, zeta: null as string | null },",
    "  eta: () => 4,",
    "  theta: new Map<string, number>(),",
    "  iota: 5n,",
    "  kappa: { lambda: [1, 2, 3] as const, mu: { nu: { xi: { omicron: { pi: { rho: 1 } } } } } },",
    "};",
    "",
  ].join("\n"),
  // A 1,100-character string literal type: Go's hover cutoff is maximumHoverLength * 10, in 64 bits.
  "src/longlit.ts": `export const lit = "${"abcdefghij".repeat(110)}";\n`,
  // Organize imports: every sort, case and type-order preference.
  "src/imports.ts": [
    'import { zeta, Alpha, Beta, type Zed } from "./lib/sorting";',
    'import type { Shape } from "./lib/index";',
    'import { formatValue, type ShapeKind, Color } from "./lib/index";',
    'import { a10, a2, A1, éclair, ecran, Eclair, beta, eclair, Éclair, type alpha } from "./lib/sorting";',
    'import "./lib/side";',
    "export const used = [zeta, Alpha, Beta, beta, formatValue, Color, a10, a2, A1, éclair, ecran, Eclair, eclair, Éclair];",
    "export type Used = [Zed, Shape, ShapeKind, alpha];",
    "",
  ].join("\n"),
  // Organize imports where the existing order is case-sensitive with type imports first, so the `auto`
  // detections find other answers than in imports.ts.
  "src/imports2.ts": [
    'import { type Zed, A1, Alpha, Eclair, a10, a2, beta, Beta } from "./lib/sorting";',
    'import { formatValue } from "./lib/index";',
    "export const used2 = [A1, Alpha, Eclair, a10, a2, beta, Beta, formatValue];",
    "export type Used2 = Zed;",
    "",
  ].join("\n"),
  // Formatting: the format.* preferences.
  "src/fmt.ts": [
    "function  f(a:number,b:number){return a+b}",
    "switch (f(1,2)) {",
    "case 3: f(1,2)",
    "break;",
    "default:",
    "}",
    "for(let i=0;i<2;i++){f(i,i);}",
    "const o = {a:1,b:[1,2]};",
    "const fn = function(x:number){return x;};",
    "const t = `${ o.a }`;",
    "const y = <number>o.a;",
    "if(t){fn(y)}",
    "function g(x:number){",
    "return x;",
    "}",
    "export { o, fn, t, g };",
    "",
  ].join("\n"),
  // Diagnostics: validate.enabled, reportStyleChecksAsWarnings.
  "src/style.ts": ["export function g(x: number) {", "  const unused = 1;", "  if (x) {", "    return 1;", "  }", "}", ""].join("\n"),
  // JSX attribute completion style.
  "src/view.tsx": [
    "declare global {",
    "  namespace JSX {",
    "    interface IntrinsicElements {",
    "      div: { id?: string; hidden?: boolean; onClick?: () => void };",
    "    }",
    "  }",
    "}",
    "export const v = <div ></div>;",
    "",
  ].join("\n"),
  // Class member and object literal method snippets, optional chain completions.
  "src/members.ts": [
    'import type { Shape } from "./lib/index";',
    "export class Square implements Shape {",
    "  ",
    "}",
    "export const obj: Shape = {",
    "  ",
    "};",
    "export namespace ns {",
    "  export class Circle implements Shape {",
    "    constructor() {}",
    "  }",
    "}",
    "declare const maybe: { inner: { value: number } } | undefined;",
    "export const v2 = maybe.",
    "",
  ].join("\n"),
  // The custom config file name (customConfigFileName): unused locals are allowed here.
  "tsconfig.alt.json": json({ extends: "./tsconfig.json", compilerOptions: { noUnusedLocals: false } }),
  // JSDoc template completion (completeJSDocs, generateReturns).
  "src/doc.ts": ["/** */", "export function documented(a: number, b: string) {", "  return a + b;", "}", ""].join("\n"),
};

const FILES = [
  "src/lib/index.ts",
  "src/use.ts",
  "src/quoted.ts",
  "src/deep/nested/use2.ts",
  "src/hints.ts",
  "src/long.ts",
  "src/longlit.ts",
  "src/imports.ts",
  "src/imports2.ts",
  "src/fmt.ts",
  "src/style.ts",
  "src/view.tsx",
  "src/members.ts",
  "src/doc.ts",
  "other/owned.ts",
];

const whole = (s, r) => ({ start: { line: 0, character: 0 }, end: endPosition(s.text(r)) });

// A missing-import quick fix for `name` in r (Go's code fix finds it by the diagnostic's code and range).
function missingImport(r, name) {
  return (s, label) => {
    const range = { start: s.pos(r, name), end: s.pos(r, name, name.length) };
    return s.ask(`${label} ${r} quick fix ${name}`, "textDocument/codeAction", {
      textDocument: s.td(r),
      range,
      context: { diagnostics: [{ range, severity: 1, code: 2304, source: "ts", message: `Cannot find name '${name}'.` }], only: ["quickfix"] },
    });
  };
}

// An auto-import completion at the end of `needle` in use.ts and a resolve of each of its auto-import
// items (Script.autoImports, with the step's label in the names).
function autoImport(needle) {
  return async (s, label) => {
    const r = "src/use.ts";
    const c = await s.ask(`${label} ${r} auto-import ${needle}`, "textDocument/completion", { textDocument: s.td(r), position: s.pos(r, needle, needle.length), context: { triggerKind: 1 } });
    const items = (c.result?.items ?? []).filter((i) => i.data?.autoImport);
    items.sort((x, y) => (x.label < y.label ? -1 : x.label > y.label ? 1 : JSON.stringify(x) < JSON.stringify(y) ? -1 : 1));
    for (const item of items) await s.ask(`${label} ${r} resolve ${item.label}`, "completionItem/resolve", item);
  };
}

function at(r, what, method, needle, delta, extra = {}) {
  return (s, label) => s.ask(`${label} ${r} ${what}`, method, { textDocument: s.td(r), position: s.pos(r, needle, delta), ...extra });
}

function onFile(r, what, method, params) {
  return (s, label) => s.ask(`${label} ${r} ${what}`, method, { textDocument: s.td(r), ...(typeof params === "function" ? params(s) : params) });
}

const completion = (r, what, needle, delta) => at(r, what, "textDocument/completion", needle, delta, { context: { triggerKind: 1 } });

// The requests that read the preferences, by name.
const ASK = {
  inlayHints: onFile("src/hints.ts", "inlayHint", "textDocument/inlayHint", (s) => ({ range: whole(s, "src/hints.ts") })),
  hover: at("src/long.ts", "hover", "textDocument/hover", "big", 0),
  hoverLiteral: at("src/longlit.ts", "hover", "textDocument/hover", "lit", 0),
  autoImportValue: autoImport("formatV"),
  autoImportType: autoImport("ShapeK"),
  importStatement: completion("src/use.ts", "import statement completion", "import formatE", 14),
  quickFix: missingImport("src/use.ts", "defaultLabel"),
  quickFixQuoted: missingImport("src/quoted.ts", "defaultLabel"),
  quickFixNested: missingImport("src/deep/nested/use2.ts", "formatValue"),
  quickFixTop: missingImport("src/use.ts", "formatValue"),
  quickFixInside: missingImport("src/lib/consumer.ts", "formatValue"),
  // A quick fix whose inserted code the change tracker indents with the preferences' format settings
  // (tabSize, indentSize, convertTabsToSpaces: Go format.getIndentationString).
  implementInterface: (s, label) => {
    const r = "src/members.ts";
    const range = { start: s.pos(r, "Square"), end: s.pos(r, "Square", 6) };
    return s.ask(`${label} ${r} implement interface`, "textDocument/codeAction", {
      textDocument: s.td(r),
      range,
      context: { diagnostics: [{ range, severity: 1, code: 2420, source: "ts", message: "Class 'Square' incorrectly implements interface 'Shape'." }], only: ["quickfix"] },
    });
  },
  // Implement interface on a class with a constructor: the members go after it with no indentation
  // given, so the change tracker asks the smart indenter (Go format.GetIndentation, indentStyle).
  implementInterfaceAfterConstructor: (s, label) => {
    const r = "src/members.ts";
    const range = { start: s.pos(r, "Circle"), end: s.pos(r, "Circle", 6) };
    return s.ask(`${label} ${r} implement interface after constructor`, "textDocument/codeAction", {
      textDocument: s.td(r),
      range,
      context: { diagnostics: [{ range, severity: 1, code: 2420, source: "ts", message: "Class 'Circle' incorrectly implements interface 'Shape'." }], only: ["quickfix"] },
    });
  },
  organizeImports: onFile("src/imports.ts", "organize imports", "textDocument/codeAction", (s) => ({ range: whole(s, "src/imports.ts"), context: { diagnostics: [], only: ["source.organizeImports"] } })),
  organizeImports2: onFile("src/imports2.ts", "organize imports", "textDocument/codeAction", (s) => ({ range: whole(s, "src/imports2.ts"), context: { diagnostics: [], only: ["source.organizeImports"] } })),
  formatting: onFile("src/fmt.ts", "formatting", "textDocument/formatting", { options: { tabSize: 4, insertSpaces: true } }),
  rangeFormatting: onFile("src/fmt.ts", "range formatting", "textDocument/rangeFormatting", { range: { start: { line: 0, character: 0 }, end: { line: 6, character: 0 } }, options: { tabSize: 2, insertSpaces: false } }),
  onTypeFormatting: at("src/fmt.ts", "on-type formatting", "textDocument/onTypeFormatting", "return a+b}", 11, { ch: "}", options: { tabSize: 4, insertSpaces: true } }),
  diagnostics: (s, label) => s.ask(`${label} src/style.ts diagnostic`, "textDocument/diagnostic", { textDocument: s.td("src/style.ts") }),
  jsxAttribute: completion("src/view.tsx", "jsx attribute completion", "<div ", 5),
  classMember: completion("src/members.ts", "class member completion", "Shape {\n  ", 10),
  objectLiteral: completion("src/members.ts", "object literal completion", "Shape = {\n  ", 12),
  optionalChain: at("src/members.ts", "optional chain completion", "textDocument/completion", "maybe.", 6, { context: { triggerKind: 2, triggerCharacter: "." } }),
  jsdoc: completion("src/doc.ts", "jsdoc completion", "/** */", 3),
  codeLens: onFile("src/lib/index.ts", "code lens", "textDocument/codeLens", {}),
  // workspaceSymbols.scope: currentProject applies only with a document (Go lsp/server.go
  // handleWorkspaceSymbol); OtherOwnedSymbol is in the other project only. excludeLibrarySymbols:
  // PropertyKey is in the lib only.
  symbolOwned: (s, label) => s.ask(`${label} workspace symbol OtherOwned`, "workspace/symbol", { query: "OtherOwned", textDocument: s.td("src/use.ts") }),
  symbolLib: (s, label) => s.ask(`${label} workspace symbol PropertyKey`, "workspace/symbol", { query: "PropertyKey", textDocument: s.td("src/use.ts") }),
  rename: at("src/hints.ts", "rename", "textDocument/rename", "message =", 0, { newName: "msg" }),
};

// The whole battery: every request.
async function battery(s, label) {
  for (const ask of Object.values(ASK)) await ask(s, label);
}

// Sends one workspace/didChangeConfiguration, waits (the checkpoint takes the refreshes it makes), and
// asks `asks` (default: the whole battery).
async function configure(s, label, settings, asks) {
  s.client.notify("workspace/didChangeConfiguration", { settings });
  await s.checkpoint(`${label} configured`);
  if (asks) for (const ask of asks) await ask(s, label);
  else await battery(s, label);
}

// A { "a.b.c": v } map as nested objects.
function nest(paths) {
  const out = {};
  for (const [path, value] of Object.entries(paths)) {
    const parts = path.split(".");
    let o = out;
    for (const p of parts.slice(0, -1)) o = o[p] ??= {};
    o[parts[parts.length - 1]] = value;
  }
  return out;
}

const BOOL = [true, false];
// Organize imports with Unicode collation and case-sensitive sorting, where caseFirst, numeric and
// accent collation apply (Go ls/lsutil/organizeimports.go).
const UNICODE = { "preferences.organizeImports.unicodeCollation": "unicode", "preferences.organizeImports.caseSensitivity": "caseSensitive" };
const INLAY_ALL = { "inlayHints.parameterNames.enabled": "all" };
// Go's auto-import registry keeps the module specifiers it computed until the exclude regexes change
// (Go ls/autoimport/registry.go: specifierCache.Clear), so the quote and specifier probes send a new
// regex that excludes nothing with every value; their specifiers are computed afresh.
const SPECIFIERS_AFRESH = (i) => ({ "preferences.autoImportSpecifierExcludeRegexes": [`^b6-never-${i}$`] });

// The probes: [path, values, requests, other settings, alike]. Every value is sent under js/ts, with the
// other settings (a function of the step gives them per value); an enum's every other value is
// upper-cased (Go lower-cases before it compares). `alike`: groups of values that Go defines to behave
// the same (each with its Go reason); prefs_sensitivity.py requires them to answer alike, and every
// other pair of values to answer differently.
const PROBES = [
  ["preferences.quoteStyle", ["auto", "double", "single"], ["quickFix", "quickFixQuoted"], SPECIFIERS_AFRESH],
  ["preferences.importModuleSpecifier", ["shortest", "project-relative", "relative", "non-relative"], ["quickFixTop", "quickFixNested", "quickFixInside"], SPECIFIERS_AFRESH],
  ["preferences.importModuleSpecifierEnding", ["auto", "minimal", "index", "js"], ["quickFix", "quickFixQuoted"], SPECIFIERS_AFRESH],
  ["preferences.jsxAttributeCompletionStyle", ["auto", "braces", "none"], ["jsxAttribute"]],
  ["inlayHints.parameterNames.enabled", ["none", "literals", "all"], ["inlayHints"]],
  ["inlayHints.parameterNames.suppressWhenArgumentMatchesName", BOOL, ["inlayHints"], INLAY_ALL],
  ["inlayHints.parameterTypes.enabled", BOOL, ["inlayHints"]],
  ["inlayHints.variableTypes.enabled", BOOL, ["inlayHints"]],
  ["inlayHints.variableTypes.suppressWhenTypeMatchesName", BOOL, ["inlayHints"], { "inlayHints.variableTypes.enabled": true }],
  ["inlayHints.propertyDeclarationTypes.enabled", BOOL, ["inlayHints"]],
  ["inlayHints.functionLikeReturnTypes.enabled", BOOL, ["inlayHints"]],
  ["inlayHints.enumMemberValues.enabled", BOOL, ["inlayHints"]],
  ["preferences.organizeImports.sort", ["auto", "ordinal", "ordinalIgnoreCase", "natural", "naturalIgnoreCase"], ["organizeImports", "organizeImports2"]],
  ["preferences.organizeImports.caseSensitivity", ["auto", "caseInsensitive", "caseSensitive"], ["organizeImports", "organizeImports2"]],
  ["preferences.organizeImports.typeOrder", ["auto", "last", "inline", "first"], ["organizeImports", "organizeImports2"]],
  ["preferences.organizeImports.unicodeCollation", ["ordinal", "unicode"], ["organizeImports"]],
  // Go's default is lower first (ls/lsutil/organizeimports.go compareOrganizeImportsCase: its default
  // case), so `default` and `lower` answer alike by definition.
  ["preferences.organizeImports.caseFirst", ["default", "lower", "upper"], ["organizeImports"], { ...UNICODE, "preferences.organizeImports.typeOrder": "inline" }, [["default", "lower"]]],
  ["preferences.organizeImports.numericCollation", BOOL, ["organizeImports"], UNICODE],
  ["preferences.organizeImports.accentCollation", BOOL, ["organizeImports"], UNICODE],
  ["workspaceSymbols.scope", ["allOpenProjects", "currentProject"], ["symbolOwned"]],
  ["workspaceSymbols.excludeLibrarySymbols", BOOL, ["symbolLib"]],
  ["format.semicolons", ["ignore", "insert", "remove"], ["formatting"]],
  ["format.indentStyle", ["none", "block", "smart"], ["implementInterfaceAfterConstructor"]],
  ["format.insertSpaceAfterCommaDelimiter", BOOL, ["formatting"]],
  ["format.insertSpaceBeforeAndAfterBinaryOperators", BOOL, ["formatting"]],
  ["format.insertSpaceAfterKeywordsInControlFlowStatements", BOOL, ["formatting"]],
  ["format.insertSpaceAfterFunctionKeywordForAnonymousFunctions", BOOL, ["formatting"]],
  ["format.insertSpaceAfterOpeningAndBeforeClosingTemplateStringBraces", BOOL, ["formatting"]],
  ["format.insertSpaceAfterTypeAssertion", BOOL, ["formatting"]],
  ["format.placeOpenBraceOnNewLineForFunctions", BOOL, ["formatting"]],
  ["format.placeOpenBraceOnNewLineForControlBlocks", BOOL, ["formatting"]],
  ["format.indentSwitchCase", BOOL, ["formatting"]],
  ["format.enabled", BOOL, ["formatting"]],
  ["format.convertTabsToSpaces", BOOL, ["implementInterface"], { "format.indentSize": 8, "format.tabSize": 4 }],
  ["suggest.autoImports", BOOL, ["autoImportValue"]],
  ["suggest.includeCompletionsForImportStatements", BOOL, ["importStatement"]],
  ["suggest.includeAutomaticOptionalChainCompletions", BOOL, ["optionalChain"]],
  ["suggest.classMemberSnippets.enabled", BOOL, ["classMember"]],
  ["suggest.objectLiteralMethodSnippets.enabled", BOOL, ["objectLiteral"]],
  ["suggest.jsdoc.enabled", BOOL, ["jsdoc"]],
  ["suggest.jsdoc.generateReturns", BOOL, ["jsdoc"]],
  ["preferences.preferTypeOnlyAutoImports", BOOL, ["autoImportType"]],
  ["preferences.useAliasesForRenames", BOOL, ["rename"]],
  ["referencesCodeLens.enabled", BOOL, ["codeLens"]],
  ["referencesCodeLens.showOnAllFunctions", BOOL, ["codeLens"], { "referencesCodeLens.enabled": true }],
  ["implementationsCodeLens.enabled", BOOL, ["codeLens"]],
  ["implementationsCodeLens.showOnInterfaceMethods", BOOL, ["codeLens"], { "implementationsCodeLens.enabled": true }],
  ["implementationsCodeLens.showOnAllClassMethods", BOOL, ["codeLens"], { "implementationsCodeLens.enabled": true }],
  ["validate.enabled", BOOL, ["diagnostics"]],
  ["reportStyleChecksAsWarnings", BOOL, ["diagnostics"]],
];

export { PROBES };

// A probe's steps wait for the server to be quiet (Script.quiet) and one checkpoint ends the probe: the
// inlay-hint and code-lens refreshes are sent in order from the configuration handler (Go
// lsp/server.go sendClientRequestFireAndForget). A diagnostics refresh is debounced (Go
// Session.ScheduleDiagnosticsRefresh), so the probes whose values schedule one (DIAGNOSTICS_REFRESH)
// take a checkpoint after every value: then no two changes fall in one debounce window.
const DIAGNOSTICS_REFRESH = new Set(["validate.enabled", "reportStyleChecksAsWarnings"]);

async function probes(s) {
  await s.checkpoint("probes");
  for (const [path, values, asks, others = {}] of PROBES) {
    for (const [i, v] of values.entries()) {
      const value = typeof v === "string" && i % 2 ? v.toUpperCase() : v;
      const label = `probe ${path}=${JSON.stringify(v)}`;
      const context = typeof others === "function" ? others(`${path}-${i}`) : others;
      s.client.notify("workspace/didChangeConfiguration", { settings: { "js/ts": nest({ ...context, [path]: value }) } });
      if (DIAGNOSTICS_REFRESH.has(path)) await s.checkpoint(`${label} configured`);
      else await s.quiet();
      for (const a of asks) await ASK[a](s, label);
    }
    if (!DIAGNOSTICS_REFRESH.has(path)) await s.checkpoint(`probe ${path}`);
  }
}

SCENARIOS.prefs = {
  fixture: "prefs-small",
  ata: false,
  // initializationOptions.userPreferences (Go lsp/server.go RequestConfiguration: parsed as the
  // `js/ts` section). One key in its flat (raw), unstable and nested forms: the nested one wins, then
  // unstable, then flat; wrong-typed values, an unknown key, an int beyond 32 bits, null on a path.
  init: {
    includeInlayParameterNameHints: 1,
    includeInlayVariableTypeHints: "yes",
    includeInlayFunctionLikeReturnTypeHints: true,
    includeInlayEnumMemberValueHints: true,
    quotePreference: "bogus",
    importModuleSpecifierPreference: ["relative"],
    importModuleSpecifierEnding: "INDEX",
    maximumHoverLength: 4294967306,
    organizeImportsIgnoreCase: "caseInsensitive",
    unknownFlatKey: 1,
    unstable: { quotePreference: "single", includeInlayParameterNameHints: "LITERALS", includeCompletionsWithClassMemberSnippets: true, organizeImportsTypeOrder: "first" },
    inlayHints: { parameterNames: { enabled: "all", suppressWhenArgumentMatchesName: "no" }, propertyDeclarationTypes: { enabled: true } },
    preferences: { quoteStyle: null, organizeImports: { caseSensitivity: "CaseSensitive" } },
    referencesCodeLens: { enabled: true, showOnAllFunctions: true },
  },
  async run(s) {
    for (const r of FILES) {
      s.open(r);
      await s.quiet();
    }
    await s.checkpoint("load");
    await battery(s, "init");

    // The same key in each section with other values: js/ts > typescript > javascript; in one
    // section nested > unstable > flat.
    await configure(s, "sections", {
      javascript: { preferences: { quoteStyle: "double", importModuleSpecifier: "relative" }, inlayHints: { variableTypes: { enabled: true } } },
      typescript: { quotePreference: "double", unstable: { quotePreference: "auto" }, preferences: { quoteStyle: "single", importModuleSpecifierEnding: "js" }, inlayHints: { parameterNames: { enabled: "literals" } } },
      "js/ts": { quotePreference: "double", importModuleSpecifierPreference: "non-relative", implementationsCodeLens: { enabled: true } },
    });
    // null: skipped on a raw name; on a nested path it keeps the fallback path from being read
    // (suggest.jsdoc.enabled, format.enabled), and a null section is no section.
    await configure(s, "null", {
      javascript: null,
      typescript: {
        quotePreference: null,
        maximumHoverLength: null,
        suggest: { jsdoc: { enabled: null }, completeJSDocs: false },
        format: { enabled: null, enable: false, semicolons: null },
        inlayHints: null,
        preferences: { organizeImports: { caseSensitivity: null }, importModuleSpecifier: null },
        unstable: null,
      },
      "js/ts": { unstable: { quotePreference: "single", includeInlayVariableTypeHints: true } },
    });
    // Wrong types: arrays and numbers where strings are expected, strings for booleans and numbers,
    // fractional and huge numbers, an object for a raw name, a section that is not an object.
    await configure(s, "types", {
      javascript: ["not", "an", "object"],
      editor: { tabSize: 3, insertSpaces: false },
      typescript: {
        preferences: { quoteStyle: ["single"], importModuleSpecifier: 2, autoImportFileExcludePatterns: "src/lib/extra.ts", autoImportSpecifierExcludeRegexes: ["^\\./lib/extra", 7], preferTypeOnlyAutoImports: "true" },
        inlayHints: { parameterNames: { enabled: true }, variableTypes: { enabled: 1 }, functionLikeReturnTypes: { enabled: "true" }, enumMemberValues: { enabled: true } },
        format: { indentSize: "4", tabSize: 2.9, indentStyle: 1.7, baseIndentSize: 2.5, insertSpaceAfterCommaDelimiter: "no", semicolons: ["remove"], placeOpenBraceOnNewLineForFunctions: true },
        maximumHoverLength: 64.9,
        autoClosingTags: { enabled: "yes" },
        suggest: { classMemberSnippets: { enabled: true }, objectLiteralMethodSnippets: { enabled: 1 }, includeAutomaticOptionalChainCompletions: false },
        validate: { enable: true },
        reportStyleChecksAsWarnings: "yes",
      },
      "js/ts": "not an object",
    });
    // A negative baseIndentSize: Go's formatter panics (strings: negative Repeat count), and its LSP
    // answers the request with an InternalError. Apart from the other format probes, which then answer.
    await configure(s, "negative base indent", { "js/ts": { format: { baseIndentSize: -2 } } }, [ASK.formatting, ASK.organizeImports, ASK.implementInterface]);
    // Unknown enum strings, then unknown keys and sections only: back to the defaults.
    const ENUM_PATHS = PROBES.filter(([, values]) => typeof values[0] === "string").map(([path]) => path);
    await configure(s, "unknown enums", { typescript: nest(Object.fromEntries(ENUM_PATHS.map((p) => [p, "bogus"]))) });
    await configure(s, "unknown keys", { typo: { quoteStyle: "single" }, TypeScript: { preferences: { quoteStyle: "single" } }, "js/ts": { notAPreference: true } });
    // Off switches: validation, formatting, JSDoc completion, code lenses, maximumHoverLength 0
    // (Go: the default 500).
    await configure(s, "off", {
      "js/ts": {
        validate: { enabled: false },
        format: { enabled: false },
        suggest: { jsdoc: { enabled: false }, autoImports: false, includeCompletionsForImportStatements: false },
        referencesCodeLens: { enabled: false },
        unstable: { maximumHoverLength: 0, allowRenameOfImportPath: true, providePrefixAndSuffixTextForRename: false },
      },
    });
    // Numbers beyond 32 and 64 bits (Go int is 64 bits; a float64 beyond it converts to the minimum on
    // amd64): 1844674407370955264 * 10 wraps to 1024 in Go's hover cutoff; a tab size beyond 32 bits
    // gives 0 tabs and 4 spaces at column 4, and a negative one panics in strings.Repeat.
    await configure(s, "wide ints", { "js/ts": { unstable: { maximumHoverLength: 1e19 }, format: { indentStyle: 4294967296 } } });
    await configure(s, "hover cutoff wraps", { "js/ts": { unstable: { maximumHoverLength: 1844674407370955264 } } }, [ASK.hover, ASK.hoverLiteral]);
    await configure(s, "wide tab size", { "js/ts": { format: { tabSize: 4294967296, convertTabsToSpaces: false, indentSize: 4 } } }, [ASK.implementInterface]);
    await configure(s, "negative wide tab size", { "js/ts": { format: { tabSize: -4294967292, convertTabsToSpaces: false, indentSize: 4 } } }, [ASK.implementInterface]);
    await configure(s, "locale", { "js/ts": { locale: "de", customConfigFileName: " tsconfig.alt.json ", workspaceSymbols: { excludeLibrarySymbols: false } } });
    await configure(s, "config path", { "js/ts": { locale: "bogus", customConfigFileName: "../tsconfig.alt.json" } });
    // The editor section alone (Go ParseUserPreferences: tabSize is indentSize too, insertSpaces is
    // convertTabsToSpaces, both unless set).
    await configure(s, "editor only", { editor: { tabSize: 6, insertSpaces: false, convertTabsToSpaces: true, trimTrailingWhitespace: false } });
    // Settings that are not an object, and null settings: ignored (the editor-only preferences stay).
    await configure(s, "not an object", ["typescript"]);
    await configure(s, "null settings", null);
    // One preference at a time, every value.
    await probes(s);
  },
};

export const PREFS_SCENARIOS = new Set(["prefs"]);

// runScenario with the scenario's initialization options (see the top comment).
export async function runPrefsScenario(cmd, name, root, opts) {
  const saved = { ...userPreferences };
  const swap = (to) => {
    for (const k of Object.keys(userPreferences)) delete userPreferences[k];
    Object.assign(userPreferences, to);
  };
  swap(SCENARIOS[name].init);
  try {
    return await runScenario(cmd, name, root, opts);
  } finally {
    swap(saved);
  }
}
