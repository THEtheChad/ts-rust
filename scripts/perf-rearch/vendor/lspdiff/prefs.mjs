// The user-preference scenarios (lane B6): odd LSP configuration values, sent through the
// initialization options and workspace/didChangeConfiguration, each followed by the requests whose
// answers depend on them. They pin how a server parses its preferences (Go at the pin:
// ls/lsutil/userpreferences_generated.go, from ts#64554; ls/lsutil/userpreferences.go
// ParseUserPreferences): wrong-typed values, unknown enum strings, null, arrays where a string is
// expected, unknown keys, the flat (raw), `unstable` and nested (VS Code) forms of one key and their
// precedence, the `javascript` < `typescript` < `js/ts` sections and `editor`, and every value of
// every enum preference. A checkpoint after each change takes the refresh requests the change makes
// (Go project/session.go Configure: inlay hints, code lens, diagnostics).
//
// Why a module of its own: client.mjs, scenario.mjs and fixtures.mjs are in the Go cache key of every
// lsp session (parity_lsp.py SESSION_SOURCES), so a scenario added there would make every session's
// Go answers run again. This module registers its scenarios and fixture into scenario.mjs's SCENARIOS
// and fixtures.mjs's FIXTURES when lspdiff.mjs imports it, and parity_lsp.py keys only these sessions
// on it (their `sources`).
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
    "export type Zed = number;",
    "export type alpha = string;",
    "",
  ].join("\n"),
  "src/lib/side.ts": "export {};\n",
  // Auto-import completions (quote style, module specifier and ending, type-only), a missing-import
  // quick fix and an import-statement completion.
  "src/use.ts": ["export const label = defaultLabel;", "export const result = formatV", "export let kind: ShapeK", "import formatE", ""].join("\n"),
  // Inlay hints of every kind.
  "src/hints.ts": [
    'import { formatValue } from "./lib/index";',
    "",
    "function greet(name: string, times: number, loud?: boolean) {",
    '  return name.repeat(times) + (loud ? "!" : "");',
    "}",
    'const name = "x";',
    "const message = greet(name, 3, true);",
    "const doubled = [1, 2].map((n) => n * 2);",
    "class Holder {",
    '  value = formatValue(1, "a");',
    "}",
    "enum Level {",
    "  Low = 1 << 0,",
    "  High = Low + 4,",
    "}",
    "export const all = { message, doubled, Holder, Level };",
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
  // Organize imports: every sort, case and type-order preference.
  "src/imports.ts": [
    'import { zeta, Alpha, beta, type Zed } from "./lib/sorting";',
    'import type { Shape } from "./lib/index";',
    'import { formatValue, type ShapeKind, Color } from "./lib/index";',
    'import { a10, a2, A1, éclair, Eclair, type alpha } from "./lib/sorting";',
    'import "./lib/side";',
    "export const used = [zeta, Alpha, beta, formatValue, Color, a10, a2, A1, éclair, Eclair];",
    "export type Used = [Zed, Shape, ShapeKind, alpha];",
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
    "export { o, fn, t };",
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
    "declare const maybe: { inner: { value: number } } | undefined;",
    "export const v2 = maybe.",
    "",
  ].join("\n"),
  // The custom config file name (customConfigFileName): unused locals are allowed here.
  "tsconfig.alt.json": json({ extends: "./tsconfig.json", compilerOptions: { noUnusedLocals: false } }),
  // JSDoc template completion (completeJSDocs, generateReturns).
  "src/doc.ts": ["/** */", "export function documented(a: number, b: string) {", "  return a + b;", "}", ""].join("\n"),
};

const FILES = ["src/lib/index.ts", "src/use.ts", "src/hints.ts", "src/long.ts", "src/imports.ts", "src/fmt.ts", "src/style.ts", "src/view.tsx", "src/members.ts", "src/doc.ts"];

// The requests whose answers depend on the preferences, labelled with the configuration step.
async function battery(s, label) {
  const ask = (r, what, method, params) => s.ask(`${label} ${r} ${what}`, method, { textDocument: s.td(r), ...params });
  const at = (r, what, method, needle, delta, extra = {}) => ask(r, what, method, { position: s.pos(r, needle, delta), ...extra });
  const whole = (r) => ({ start: { line: 0, character: 0 }, end: endPosition(s.text(r)) });
  await ask("src/hints.ts", "inlayHint", "textDocument/inlayHint", { range: whole("src/hints.ts") });
  await at("src/long.ts", "hover", "textDocument/hover", "big", 0);
  await autoImport(s, label, "formatV");
  await autoImport(s, label, "ShapeK");
  await at("src/use.ts", "import statement completion", "textDocument/completion", "import formatE", 14, { context: { triggerKind: 1 } });
  const missing = { start: s.pos("src/use.ts", "defaultLabel"), end: s.pos("src/use.ts", "defaultLabel", 12) };
  await ask("src/use.ts", "quick fix", "textDocument/codeAction", {
    range: missing,
    context: { diagnostics: [{ range: missing, severity: 1, code: 2304, source: "ts", message: "Cannot find name 'defaultLabel'." }], only: ["quickfix"] },
  });
  // A quick fix whose inserted code is indented by the format settings (indentStyle, indentSize,
  // baseIndentSize: Go format.GetIndentation in the change tracker).
  const square = { start: s.pos("src/members.ts", "Square"), end: s.pos("src/members.ts", "Square", 6) };
  await ask("src/members.ts", "implement interface", "textDocument/codeAction", {
    range: square,
    context: { diagnostics: [{ range: square, severity: 1, code: 2420, source: "ts", message: "Class 'Square' incorrectly implements interface 'Shape'." }], only: ["quickfix"] },
  });
  await ask("src/imports.ts", "organize imports", "textDocument/codeAction", { range: whole("src/imports.ts"), context: { diagnostics: [], only: ["source.organizeImports"] } });
  await ask("src/fmt.ts", "formatting", "textDocument/formatting", { options: { tabSize: 4, insertSpaces: true } });
  await ask("src/fmt.ts", "range formatting", "textDocument/rangeFormatting", { range: { start: { line: 0, character: 0 }, end: { line: 6, character: 0 } }, options: { tabSize: 2, insertSpaces: false } });
  await at("src/fmt.ts", "on-type formatting", "textDocument/onTypeFormatting", "return a+b}", 11, { ch: "}", options: { tabSize: 4, insertSpaces: true } });
  await s.diag("src/style.ts", ` (${label})`);
  await at("src/view.tsx", "jsx attribute completion", "textDocument/completion", "<div ", 5, { context: { triggerKind: 1 } });
  await at("src/members.ts", "class member completion", "textDocument/completion", "Shape {\n  ", 10, { context: { triggerKind: 1 } });
  await at("src/members.ts", "object literal completion", "textDocument/completion", "Shape = {\n  ", 12, { context: { triggerKind: 1 } });
  await at("src/members.ts", "optional chain completion", "textDocument/completion", "maybe.", 6, { context: { triggerKind: 2, triggerCharacter: "." } });
  await at("src/doc.ts", "jsdoc completion", "textDocument/completion", "/** */", 3, { context: { triggerKind: 1 } });
  await ask("src/lib/index.ts", "code lens", "textDocument/codeLens", {});
  // workspaceSymbols.scope (currentProject only with a document) and excludeLibrarySymbols.
  await s.ask(`${label} workspace symbol PropertyKey`, "workspace/symbol", { query: "PropertyKey", textDocument: s.td("src/use.ts") });
  await at("src/hints.ts", "rename", "textDocument/rename", "message =", 0, { newName: "msg" });
}

// An auto-import completion at the end of `needle` in use.ts and a resolve of each of its auto-import
// items (Script.autoImports, with the step's label in the names).
async function autoImport(s, label, needle) {
  const r = "src/use.ts";
  const c = await s.ask(`${label} ${r} auto-import ${needle}`, "textDocument/completion", { textDocument: s.td(r), position: s.pos(r, needle, needle.length), context: { triggerKind: 1 } });
  const items = (c.result?.items ?? []).filter((i) => i.data?.autoImport);
  items.sort((x, y) => (x.label < y.label ? -1 : x.label > y.label ? 1 : JSON.stringify(x) < JSON.stringify(y) ? -1 : 1));
  for (const item of items) await s.ask(`${label} ${r} resolve ${item.label}`, "completionItem/resolve", item);
}

// Sends one workspace/didChangeConfiguration, waits (the checkpoint takes the refreshes it makes), and
// asks the battery.
async function configure(s, label, settings) {
  s.client.notify("workspace/didChangeConfiguration", { settings });
  await s.checkpoint(`${label} configured`);
  await battery(s, label);
}

// Every enum preference at its VS Code path, with every value its Go parser knows (the generated
// parsePreference<Type>; caseSensitivity: parseOrganizeImportsCaseSensitivity).
const ENUMS = {
  "preferences.quoteStyle": ["auto", "double", "single"],
  "preferences.importModuleSpecifier": ["shortest", "project-relative", "relative", "non-relative"],
  "preferences.importModuleSpecifierEnding": ["auto", "minimal", "index", "js"],
  "preferences.jsxAttributeCompletionStyle": ["auto", "braces", "none"],
  "inlayHints.parameterNames.enabled": ["none", "literals", "all"],
  "preferences.organizeImports.sort": ["auto", "ordinal", "ordinalIgnoreCase", "natural", "naturalIgnoreCase"],
  "preferences.organizeImports.caseFirst": ["default", "lower", "upper"],
  "preferences.organizeImports.unicodeCollation": ["ordinal", "unicode"],
  "preferences.organizeImports.typeOrder": ["auto", "last", "inline", "first"],
  "preferences.organizeImports.caseSensitivity": ["auto", "caseInsensitive", "caseSensitive"],
  "workspaceSymbols.scope": ["allOpenProjects", "currentProject"],
  "format.semicolons": ["ignore", "insert", "remove"],
  "format.indentStyle": ["none", "block", "smart"],
};

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

// Enum step k: value k of every enum (cycling), upper-cased on odd k (Go lower-cases before it
// compares), with organize-imports' numeric and accent collation flipped each step.
function enumStep(k) {
  const paths = Object.fromEntries(Object.entries(ENUMS).map(([p, vs]) => [p, k % 2 ? vs[k % vs.length].toUpperCase() : vs[k % vs.length]]));
  paths["preferences.organizeImports.numericCollation"] = k % 2 === 0;
  paths["preferences.organizeImports.accentCollation"] = k % 2 === 1;
  return nest(paths);
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
        format: { indentSize: "4", tabSize: 2.9, indentStyle: 1.7, baseIndentSize: -2, insertSpaceAfterCommaDelimiter: "no", semicolons: ["remove"], placeOpenBraceOnNewLineForFunctions: true },
        maximumHoverLength: 64.9,
        autoClosingTags: { enabled: "yes" },
        suggest: { classMemberSnippets: { enabled: true }, objectLiteralMethodSnippets: { enabled: 1 }, includeAutomaticOptionalChainCompletions: false },
        validate: { enable: true },
        reportStyleChecksAsWarnings: "yes",
      },
      "js/ts": "not an object",
    });
    // Every value of every enum, each spelling, then unknown strings; inverted booleans; the editor
    // section under the language sections.
    for (let k = 0; k < 5; k++) {
      const step = enumStep(k);
      if (k === 1) Object.assign(step, { inlayHints: { ...step.inlayHints, parameterNames: { ...step.inlayHints.parameterNames, suppressWhenArgumentMatchesName: false }, variableTypes: { enabled: true, suppressWhenTypeMatchesName: false } } });
      if (k === 3) step.unstable = { maximumHoverLength: 25, includeCompletionsWithObjectLiteralMethodSnippets: true };
      await configure(s, `enums ${k}`, { editor: { tabSize: 8, insertSpaces: true, semicolons: "remove" }, typescript: step });
    }
    await configure(s, "unknown enums", { typescript: nest(Object.fromEntries(Object.keys(ENUMS).map((p) => [p, "bogus"]))) });
    // Unknown keys and sections only: back to the defaults.
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
    // amd64), the locale, and the custom config file name (trimmed; a path is refused).
    await configure(s, "wide ints", { "js/ts": { unstable: { maximumHoverLength: 1e19 }, format: { indentStyle: 4294967296 } } });
    await configure(s, "locale", { "js/ts": { locale: "de", customConfigFileName: " tsconfig.alt.json ", workspaceSymbols: { excludeLibrarySymbols: false } } });
    await configure(s, "config path", { "js/ts": { locale: "bogus", customConfigFileName: "../tsconfig.alt.json" } });
    // The editor section alone (Go ParseUserPreferences: tabSize is indentSize too, insertSpaces is
    // convertTabsToSpaces, both unless set).
    await configure(s, "editor only", { editor: { tabSize: 6, insertSpaces: false, convertTabsToSpaces: true, trimTrailingWhitespace: false } });
    // Settings that are not an object, and null settings: ignored (the editor-only preferences stay).
    await configure(s, "not an object", ["typescript"]);
    await configure(s, "null settings", null);
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
