// JS tsserver as a session target: the LSP referee of the parity gate (docs/perf-rearch/parity.md,
// "JS tsserver as the LSP referee"). TsserverClient has LspClient's interface (request, notify,
// settle, shutdown, events), so session.mjs and scenario.mjs replay the very same steps on
// tsserver: the same files with the same text, the same edits, the same requests at the same
// positions. Each LSP message is translated to tsserver's own protocol (typescript/lib/tsserver.js
// over stdio; no third-party adapter), and each answer is converted to the LSP shape that Go gives,
// so referee.mjs can normalize all three servers with one function per kind.
//
// Conversions are mechanical: tsserver positions are 1-based { line, offset } in UTF-16 code
// units, LSP positions 0-based { line, character } in UTF-16 code units; JS's bundled libs
// (<typescript>/lib/lib.*.d.ts) are Go's bundled:///libs/lib.*.d.ts. A request kind with no
// tsserver equivalent (codeAction, the watch items) is a failed answer without asking.
//
// A failed answer is { failed: <why> } in the session log (session.mjs and scenario.mjs keep the field,
// and so does the JS cache): tsserver answered success:false (also its "No content available."), sent
// a malformed message or none, gave a response whose body does not have the schema of its command
// (BODY below, lane H4: every field the mapping reads, with its type), exited, timed out
// (LSPDIFF_REQUEST_TIMEOUT_MS), or its configure step failed. referee.mjs never takes a failed answer
// as evidence. Only prepareRename's refusal is an LSP error answer, as Go's is.
import { readFileSync, realpathSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fileUri } from "./client.mjs";
import { ServerProcess } from "./sandbox.mjs";
import { SETTINGS } from "./settings.mjs";

export const DEFAULT_TSSERVER = join(process.env.HOME ?? "", ".cache/perf-rearch/js-tsc/node_modules/typescript/lib/tsserver.js");
const REQUEST_TIMEOUT_MS = SETTINGS.LSPDIFF_REQUEST_TIMEOUT_MS;

// ---- response schemas (lane H4) ------------------------------------------------------------
// A schema is a type name, [schema] (an array of it), { key: schema } (an object with at least those
// keys; "key?" may be absent or undefined) or a function (value) -> bool. check() returns the first
// path where a value does not fit, or null. Extra keys are allowed: the mapping never reads them.
const isInt = (v) => Number.isInteger(v);
const LOC = { line: isInt, offset: isInt };
const SPAN = { start: LOC, end: LOC };
const FILE_SPAN = { file: "string", start: LOC, end: LOC };
const PARTS = (v) => v === undefined || typeof v === "string" || (Array.isArray(v) && v.every((p) => p && typeof p.text === "string"));
const TAGS = (v) => v === undefined || (Array.isArray(v) && v.every((t) => t && typeof t.name === "string" && PARTS(t.text)));
const NAV = { text: "string", kind: "string", spans: (v) => Array.isArray(v) && v.length > 0 && v.every((x) => check(SPAN, x) === null), "nameSpan?": SPAN, "childItems?": (v) => Array.isArray(v) && v.every((x) => check(NAV, x) === null) };
const INLAY = { position: LOC, kind: "string", "text?": "string", "displayParts?": [{ text: "string", "span?": FILE_SPAN }], "whitespaceBefore?": "boolean", "whitespaceAfter?": "boolean" };
const DIAG = { start: LOC, end: LOC, text: "string", code: isInt, category: "string", "reportsUnnecessary?": "boolean", "reportsDeprecated?": "boolean", "relatedInformation?": [{ "span?": FILE_SPAN, message: "string" }] };
export const BODY = {
  syntacticDiagnosticsSync: [DIAG],
  semanticDiagnosticsSync: [DIAG],
  suggestionDiagnosticsSync: [DIAG],
  "encodedSemanticClassifications-full": { spans: (v) => Array.isArray(v) && v.length % 3 === 0 && v.every(isInt) },
  provideInlayHints: [(v) => check(INLAY, v) === null && (typeof v.text === "string" || Array.isArray(v.displayParts))],
  navtree: NAV,
  getOutliningSpans: [{ textSpan: SPAN, kind: "string" }],
  quickinfo: { displayString: "string", start: LOC, end: LOC, documentation: PARTS, tags: TAGS },
  definitionAndBoundSpan: { definitions: [{ ...FILE_SPAN, "contextStart?": LOC, "contextEnd?": LOC }], textSpan: SPAN },
  typeDefinition: [FILE_SPAN],
  implementation: [FILE_SPAN],
  references: { refs: [{ ...FILE_SPAN, "isDefinition?": "boolean" }] },
  documentHighlights: [{ file: "string", highlightSpans: [{ start: LOC, end: LOC, kind: "string" }] }],
  signatureHelp: {
    items: [{ prefixDisplayParts: PARTS, suffixDisplayParts: PARTS, separatorDisplayParts: PARTS, documentation: PARTS, parameters: [{ displayParts: PARTS, documentation: PARTS }] }],
    selectedItemIndex: isInt,
    argumentIndex: isInt,
  },
  rename: (v) => {
    if (!v || typeof v !== "object" || !v.info || typeof v.info.canRename !== "boolean") return false;
    if (!v.info.canRename) return typeof v.info.localizedErrorMessage === "string";
    return check({ info: { displayName: "string", triggerSpan: SPAN }, locs: [{ file: "string", locs: [{ start: LOC, end: LOC, "prefixText?": "string", "suffixText?": "string" }] }] }, v) === null;
  },
  completionInfo: { isMemberCompletion: "boolean", entries: [{ name: "string", kind: "string", sortText: "string", "insertText?": "string", "kindModifiers?": "string", "source?": "string", "hasAction?": "boolean" }] },
  completionEntryDetails: [{ name: "string", displayParts: PARTS, "documentation?": PARTS, "tags?": TAGS, "codeActions?": [{ description: "string", changes: [{ fileName: "string", textChanges: [{ start: LOC, end: LOC, newText: "string" }] }] }] }],
};

export function check(schema, v, path = "body") {
  if (typeof schema === "function") return schema(v) ? null : path;
  if (typeof schema === "string") return typeof v === schema ? null : `${path} (${v === null ? "null" : typeof v}, not ${schema})`;
  if (Array.isArray(schema)) {
    if (!Array.isArray(v)) return `${path} (not an array)`;
    for (const [i, x] of v.entries()) {
      const bad = check(schema[0], x, `${path}[${i}]`);
      if (bad) return bad;
    }
    return null;
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return `${path} (not an object)`;
  for (const [k, sub] of Object.entries(schema)) {
    const optional = k.endsWith("?");
    const key = optional ? k.slice(0, -1) : k;
    if (v[key] === undefined) {
      if (optional) continue;
      return `${path}.${key} (missing)`;
    }
    const bad = check(sub, v[key], `${path}.${key}`);
    if (bad) return bad;
  }
  return null;
}

// Why tsserver gave no usable answer: a failed answer, never evidence.
export class JsFailure extends Error {}
// prepareRename's refusal: an LSP error answer with Go's message (not a failure).
class LspError extends Error {}

// Go's inferred-project options (project/project.go NewInferredProject), so a file in no tsconfig
// gets the same program on both servers.
const INFERRED_OPTIONS = {
  allowJs: true,
  module: "esnext",
  moduleResolution: "bundler",
  target: "es2025",
  jsx: "react-jsx",
  allowImportingTsExtensions: true,
  strictNullChecks: true,
  strictFunctionTypes: true,
  sourceMap: true,
  allowNonTsExtensions: true,
  resolveJsonModule: true,
};

// Go's legend for the session's client capabilities (ls/semantictokens.go tokenTypes and
// tokenModifiers, filtered by what client.mjs advertises, in Go's order). referee.mjs decodes all
// three servers' data with it.
export const TOKEN_TYPES = ["namespace", "class", "enum", "interface", "struct", "typeParameter", "type", "parameter", "variable", "property", "enumMember", "decorator", "event", "function", "method", "macro", "comment", "string", "keyword", "number", "regexp", "operator"];
export const TOKEN_MODIFIERS = ["declaration", "definition", "readonly", "static", "deprecated", "abstract", "async", "modification", "documentation", "defaultLibrary"];
// tsserver's 2020 classification (services/classifier2020.ts TokenType, TokenModifier); "member"
// is LSP "method" (Go: "Previously called member"), and "local" is not in the client's legend.
const TS_TYPES = ["class", "enum", "interface", "namespace", "typeParameter", "type", "parameter", "variable", "enumMember", "property", "function", "method"];
const TS_MODIFIERS = ["declaration", "static", "async", "readonly", "defaultLibrary", "local"];

// ScriptElementKind -> CompletionItemKind (Go ls/completions.go getCompletionsSymbolKind).
function completionKind(kind) {
  switch (kind) {
    case "primitive type":
    case "keyword":
      return 14;
    case "const":
    case "let":
    case "var":
    case "local var":
    case "alias":
    case "parameter":
      return 6;
    case "property":
    case "getter":
    case "setter":
      return 5;
    case "function":
    case "local function":
      return 3;
    case "method":
    case "construct":
    case "call":
    case "index":
      return 2;
    case "enum":
      return 13;
    case "enum member":
      return 20;
    case "module":
    case "external module name":
      return 9;
    case "class":
    case "type":
      return 7;
    case "interface":
      return 8;
    case "warning":
      return 1;
    case "script":
      return 17;
    case "directory":
      return 19;
    case "string":
      return 21;
    default:
      return 10;
  }
}

// navtree ScriptElementKind -> SymbolKind (Go ls/symbols.go getSymbolKindFromNode, by the node
// kinds each ScriptElementKind stands for).
function symbolKind(kind) {
  switch (kind) {
    case "module":
      return 3;
    case "class":
    case "local class":
    case "type":
      return 5;
    case "interface":
      return 11;
    case "enum":
      return 10;
    case "function":
    case "local function":
      return 12;
    case "getter":
    case "setter":
    case "property":
    case "index":
      return 7;
    case "method":
    case "call":
      return 6;
    case "constructor":
    case "construct":
      return 9;
    case "type parameter":
      return 26;
    case "enum member":
      return 22;
    default:
      return 13;
  }
}

// lsconv styleCheckDiagnostics: unused declarations and imports, unreachable code, unused label,
// fallthrough, not all code paths return.
const STYLE_CHECKS = new Set([6133, 6138, 6192, 6196, 7027, 7028, 7029, 7030]);
const SEVERITY = { error: 1, warning: 2, message: 3, suggestion: 4 };
const HIGHLIGHT = { none: 1, definition: 2, reference: 2, writtenReference: 3 };
const SCRIPT_KIND = { typescript: "TS", typescriptreact: "TSX", javascript: "JS", javascriptreact: "JSX" };

const J = (v) => JSON.stringify(v ?? null);
const pos = (p) => ({ line: p.line - 1, character: p.offset - 1 });
const loc = (p) => ({ line: p.line + 1, offset: p.character + 1 });
const range = (s, e) => ({ start: pos(s), end: pos(e) });
const parts = (v) => (Array.isArray(v) ? v.map((p) => p.text).join("") : v ?? "");
// JSDoc display parts as Go's markdown words: a {@link X} is the name X (Go renders a link to it;
// referee.mjs keeps only its text), so the "{@link " and "}" parts are dropped.
const docParts = (v) => (Array.isArray(v) ? v.map((p) => (p.kind === "link" ? "" : p.text)).join("") : v ?? "");

// UTF-16 offset <-> LSP position in a document's text.
function lineStarts(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) starts.push(i + 1);
  return starts;
}
function offsetToPos(starts, offset) {
  let lo = 0;
  let hi = starts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (starts[mid] <= offset) lo = mid;
    else hi = mid - 1;
  }
  return { line: lo, character: offset - starts[lo] };
}
function posToOffset(starts, p) {
  return (starts[p.line] ?? starts[starts.length - 1]) + p.character;
}

// Applies LSP contentChanges to a text.
function applyChanges(text, changes) {
  for (const c of changes) {
    if (!c.range) {
      text = c.text;
      continue;
    }
    const starts = lineStarts(text);
    text = text.slice(0, posToOffset(starts, c.range.start)) + c.text + text.slice(posToOffset(starts, c.range.end));
  }
  return text;
}

// Markdown documentation as Go renders it from tsserver's documentation and tags: referee.mjs
// strips the markup again, so only the words reach the comparison.
function docMarkdown(documentation, tags) {
  let s = docParts(documentation);
  for (const t of tags ?? []) {
    const text = docParts(t.text);
    s += `${s ? "\n\n" : ""}*@${t.name}*${text ? ` — ${text}` : ""}`;
  }
  return s;
}

export class TsserverClient {
  // path: tsserver.js. Same options as LspClient where they apply (cwd, env, stderr).
  constructor(path = DEFAULT_TSSERVER, { cwd, env, stderr, binds = [] } = {}) {
    this.libDir = dirname(realpathSync(path));
    const args = [
      "--max-old-space-size=8192",
      path,
      "--disableAutomaticTypingAcquisition", // ATA off, as the standard LSP sessions
      "--canUseWatchEvents", // file-system events come from the client (workspace/didChangeWatchedFiles)
      "--suppressDiagnosticEvents",
      "--noGetErrOnBackgroundUpdate",
      "--locale",
      "en",
    ];
    // In the sandbox, tsserver.js reads the rest of its package (lib/, package.json): bound read-only.
    this.server = new ServerProcess([process.execPath, ...args], { cwd, env, stderr, binds: [...binds, { src: dirname(this.libDir) }] });
    this.proc = this.server.proc;
    this.seq = 0;
    this.pending = new Map(); // seq -> { resolve, reject, command }
    this.events = []; // tsserver events, as LspClient's events (never a watch registration)
    this.lastMessageAt = performance.now();
    this.buf = Buffer.alloc(0);
    this.docs = new Map(); // uri -> { path, text }
    this.watchers = new Map(); // id -> { path, dir, recursive, real }
    this.exited = this.server.exited;
    this.proc.stdout.on("data", (chunk) => this.#onData(chunk));
    this.proc.stdin.on("error", () => {}); // writes after an exit: the exit handler reports it
    this.dead = null; // why every later answer fails
    this.proc.on("exit", (code, signal) => this.#die(`tsserver exited (${code ?? signal})`));
  }

  // From here on every request fails with `why`, and the pending ones now.
  #die(why) {
    this.dead ??= why;
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(new JsFailure(`${this.dead} during ${p.command}`));
    }
    this.pending.clear();
  }

  #onData(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    for (;;) {
      const headerEnd = this.buf.indexOf("\r\n\r\n");
      if (headerEnd < 0) return;
      const length = Number(/Content-Length: *(\d+)/i.exec(this.buf.subarray(0, headerEnd).toString("ascii"))?.[1]);
      if (this.buf.length < headerEnd + 4 + length) return;
      if (!Number.isInteger(length)) {
        this.#die("tsserver sent a message without a Content-Length");
        this.server.kill();
        return;
      }
      const body = this.buf.subarray(headerEnd + 4, headerEnd + 4 + length).toString("utf8");
      this.buf = this.buf.subarray(headerEnd + 4 + length);
      let msg;
      try {
        msg = JSON.parse(body);
      } catch {
        this.#die(`tsserver sent malformed JSON: ${body.slice(0, 120)}`);
        this.server.kill();
        return;
      }
      this.#onMessage(msg);
    }
  }

  #onMessage(msg) {
    this.lastMessageAt = performance.now();
    if (msg.type === "response") {
      const p = this.pending.get(msg.request_seq);
      if (!p) return;
      this.pending.delete(msg.request_seq);
      clearTimeout(p.timer);
      p.resolve(msg);
      return;
    }
    if (msg.type !== "event") return;
    const b = msg.body ?? {};
    if (msg.event === "createFileWatcher") this.watchers.set(b.id, { path: b.path });
    else if (msg.event === "createDirectoryWatcher") this.watchers.set(b.id, { dir: b.path, recursive: !!b.recursive });
    else if (msg.event === "closeFileWatcher") this.watchers.delete(b.id);
    this.events.push({ kind: "notification", method: `tsserver/${msg.event}`, params: b, at: this.lastMessageAt });
  }

  // One tsserver command; resolves with the response message. Commands without a response
  // (open, change, close, watchChange) are written with send().
  #command(command, args) {
    if (this.dead) return Promise.reject(new JsFailure(`${this.dead} before ${command}`));
    const seq = ++this.seq;
    const promise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#die(`tsserver timed out (no response to ${command} in ${REQUEST_TIMEOUT_MS} ms)`);
        this.server.kill();
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(seq, { resolve, reject, command, timer });
    });
    this.#write({ seq, type: "request", command, arguments: args });
    return promise;
  }

  #send(command, args) {
    this.#write({ seq: ++this.seq, type: "request", command, arguments: args });
  }

  #write(msg) {
    this.proc.stdin.write(JSON.stringify(msg) + "\n");
  }

  // The body of a successful response; JsFailure for success:false (its message too, "No content
  // available." included), a response without a body, or a body that does not fit its command's
  // schema (BODY; a command without one is a failure too).
  async #body(command, args) {
    const r = await this.#command(command, args);
    if (r?.success !== true) throw new JsFailure(`${command}: success ${J(r?.success)}${r?.message ? `: ${r.message}` : ""}`);
    if (r.body === undefined) throw new JsFailure(`${command}: a response without a body`);
    if (!BODY[command]) throw new JsFailure(`${command}: no response schema`);
    const bad = check(BODY[command], r.body);
    if (bad) throw new JsFailure(`${command}: a malformed response: ${bad}`);
    return r.body;
  }

  file(uri) {
    return this.docs.get(uri)?.path ?? fileURLToPath(uri);
  }

  // A tsserver file name as an LSP uri: JS's bundled libs are Go's bundled:///libs.
  uri(file) {
    if (dirname(file) === this.libDir && /^lib\..*\.d\.ts$/.test(basename(file))) return `bundled:///libs/${basename(file)}`;
    return fileUri(file);
  }

  location(span) {
    return { uri: this.uri(span.file), range: range(span.start, span.end) };
  }

  notify(method, params) {
    this.lastMessageAt = performance.now();
    switch (method) {
      case "textDocument/didOpen": {
        const { uri, text, languageId } = params.textDocument;
        const path = fileURLToPath(uri);
        this.docs.set(uri, { path, text });
        this.#send("open", { file: path, fileContent: text, ...(SCRIPT_KIND[languageId] ? { scriptKindName: SCRIPT_KIND[languageId] } : {}) });
        return;
      }
      case "textDocument/didChange": {
        const doc = this.docs.get(params.textDocument.uri);
        // One tsserver change per LSP change, in order: positions are in the text before each.
        for (const c of params.contentChanges) {
          const starts = lineStarts(doc.text);
          const r = c.range ?? { start: { line: 0, character: 0 }, end: offsetToPos(starts, doc.text.length) };
          this.#send("change", { file: doc.path, ...loc(r.start), endLine: r.end.line + 1, endOffset: r.end.character + 1, insertString: c.text });
          doc.text = applyChanges(doc.text, [c]);
        }
        return;
      }
      case "textDocument/didClose": {
        const doc = this.docs.get(params.textDocument.uri);
        this.docs.delete(params.textDocument.uri);
        this.#send("close", { file: doc?.path ?? fileURLToPath(params.textDocument.uri) });
        return;
      }
      case "workspace/didChangeWatchedFiles":
        return this.#watchChange(params.changes);
      default:
        return; // initialized, exit, $/cancelRequest
    }
  }

  // Routes file-system events to the watchers tsserver asked for (--canUseWatchEvents): a file
  // watcher on that path, a directory watcher on its directory (or above, when recursive). A
  // watcher path behind a symlink matches the event's realpath too (the scenarios name realpaths).
  #watchChange(changes) {
    const kinds = { 1: "created", 2: "updated", 3: "deleted" };
    const byId = new Map();
    for (const ch of changes) {
      const path = fileURLToPath(ch.uri);
      for (const [id, w] of this.watchers) {
        const hit = w.path ? [w.path, this.#real(w.path)].includes(path) : [w.dir, this.#real(w.dir)].some((d) => d && (w.recursive ? path.startsWith(d + "/") : dirname(path) === d));
        if (!hit) continue;
        const e = byId.get(id) ?? { id };
        (e[kinds[ch.type]] ??= []).push(path);
        byId.set(id, e);
      }
    }
    if (byId.size) this.#send("watchChange", [...byId.values()]);
  }

  #real(path) {
    try {
      return realpathSync(path);
    } catch {
      return undefined;
    }
  }

  // The barriers of a scenario's waits (scenario.mjs Script.settle; lanes B4 and H9): tsserver runs its
  // commands in order and has no work-done progress in its protocol, so neither has anything to wait for.
  async caughtUp() {
    return 0;
  }

  progress = { pending: () => [] };

  async settle(quietMs = 1000, maxMs = 30000) {
    const start = performance.now();
    for (;;) {
      const now = performance.now();
      if ((this.pending.size === 0 && now - this.lastMessageAt >= quietMs) || now - start >= maxMs) return now - start;
      await new Promise((r) => setTimeout(r, 50));
    }
  }

  // An LSP request: resolves like LspClient's ({ id, method, result, error, ms }), with `raw`, the
  // tsserver bodies the result came from.
  // A failed answer is { failed } (JsFailure, or any other error of the mapping), never a result.
  async request(method, params) {
    const start = performance.now();
    const raw = {};
    let result;
    if (this.dead && method !== "shutdown") return { method, failed: this.dead, raw, ms: 0 };
    try {
      result = await this.#answer(method, params ?? {}, raw);
    } catch (e) {
      if (e instanceof LspError) return { method, error: { message: e.message }, raw, ms: performance.now() - start };
      return { method, failed: String(e?.message ?? e), raw, ms: performance.now() - start };
    }
    return { method, result, raw, ms: performance.now() - start };
  }

  async #answer(method, p, raw) {
    const ask = async (command, args) => (raw[command] = await this.#body(command, args));
    const file = p.textDocument ? this.file(p.textDocument.uri) : undefined;
    const at = p.position ? { file, ...loc(p.position) } : undefined;
    switch (method) {
      case "initialize": {
        // Pull diagnostics carry tags and related information only when the client's
        // textDocument.diagnostic capabilities ask for them (Go lsconv DiagnosticToLSPPull).
        const caps = p.capabilities?.textDocument?.diagnostic ?? {};
        this.pullTags = caps.tagSupport?.valueSet ?? [];
        this.pullRelated = !!caps.relatedInformation;
        const prefs = { ...(p.initializationOptions?.userPreferences?.unstable ?? {}) };
        delete prefs.disableAutomaticTypeAcquisition;
        const configured = await this.#command("configure", {
          hostInfo: "lspdiff",
          // Go's defaults where tsserver's differ (ls/lsutil/userpreferences.go
          // NewDefaultUserPreferences; rename.go: prefix and suffix text unless turned off).
          preferences: {
            ...prefs,
            useLabelDetailsInCompletionEntries: true,
            interactiveInlayHints: true,
            displayPartsForJSDoc: true,
            providePrefixAndSuffixTextForRename: true,
            allowRenameOfImportPath: true,
            includeCompletionsForImportStatements: true,
            provideRefactorNotApplicableReason: true,
          },
        });
        const inferred = await this.#command("compilerOptionsForInferredProjects", { options: INFERRED_OPTIONS });
        // A tsserver with other settings than Go's would answer other questions: every answer fails.
        for (const [step, r] of [["configure", configured], ["compilerOptionsForInferredProjects", inferred]]) {
          if (r?.success !== true) this.dead ??= `${step} failed: ${r?.message ?? J(r?.success)}`;
        }
        return { capabilities: {} };
      }
      case "shutdown":
        return null;
      case "textDocument/diagnostic": {
        const args = { file, includeLinePosition: false };
        const all = [];
        for (const c of ["syntacticDiagnosticsSync", "semanticDiagnosticsSync", "suggestionDiagnosticsSync"]) all.push(...((await ask(c, args)) ?? []));
        return { kind: "full", items: all.map((d) => this.#diagnostic(d)) };
      }
      case "textDocument/semanticTokens/full": {
        const doc = this.docs.get(p.textDocument.uri);
        const text = doc?.text ?? readFileSync(file, "utf8");
        const body = await ask("encodedSemanticClassifications-full", { file, start: 0, length: text.length, format: "2020" });
        return semanticTokens(text, body?.spans ?? []);
      }
      case "textDocument/inlayHint": {
        const doc = this.docs.get(p.textDocument.uri);
        const starts = lineStarts(doc?.text ?? readFileSync(file, "utf8"));
        const s = posToOffset(starts, p.range.start);
        const hints = (await ask("provideInlayHints", { file, start: s, length: posToOffset(starts, p.range.end) - s })) ?? [];
        return hints.map((h) => this.#inlayHint(h));
      }
      case "textDocument/documentSymbol": {
        const tree = await ask("navtree", { file });
        return documentSymbols(tree?.childItems);
      }
      case "textDocument/foldingRange": {
        const spans = (await ask("getOutliningSpans", { file })) ?? [];
        return spans.map((s) => ({
          startLine: s.textSpan.start.line - 1,
          startCharacter: s.textSpan.start.offset - 1,
          endLine: s.textSpan.end.line - 1,
          endCharacter: s.textSpan.end.offset - 1,
          ...(["comment", "imports", "region"].includes(s.kind) ? { kind: s.kind } : {}),
        }));
      }
      case "textDocument/hover": {
        const q = await ask("quickinfo", at);
        if (!q?.displayString) return null;
        const doc = docMarkdown(q.documentation, q.tags);
        return { contents: { kind: "markdown", value: "```typescript\n" + q.displayString + "\n```\n" + doc }, range: range(q.start, q.end) };
      }
      case "textDocument/definition": {
        const d = await ask("definitionAndBoundSpan", at);
        if (!d?.definitions?.length) return null;
        const origin = range(d.textSpan.start, d.textSpan.end);
        return d.definitions.map((def) => ({
          originSelectionRange: origin,
          targetUri: this.uri(def.file),
          targetRange: def.contextStart ? range(def.contextStart, def.contextEnd) : range(def.start, def.end),
          targetSelectionRange: range(def.start, def.end),
        }));
      }
      case "textDocument/typeDefinition":
      case "textDocument/implementation": {
        const list = await ask(method === "textDocument/typeDefinition" ? "typeDefinition" : "implementation", at);
        return list?.length ? list.map((s) => this.location(s)) : null;
      }
      case "textDocument/references": {
        const r = await ask("references", at);
        return (r?.refs ?? []).filter((x) => p.context?.includeDeclaration !== false || !x.isDefinition).map((x) => this.location(x));
      }
      case "textDocument/documentHighlight": {
        const list = (await ask("documentHighlights", { ...at, filesToSearch: [file] })) ?? [];
        return list.flatMap((f) => f.highlightSpans.map((s) => ({ range: range(s.start, s.end), kind: HIGHLIGHT[s.kind] ?? 1 })));
      }
      case "textDocument/signatureHelp": {
        // The trigger reason as Go derives it from the context (ls/signaturehelp.go, VS Code's
        // toTsTriggerReason); no context is no reason (not "invoked").
        const c = p.context;
        let triggerReason;
        if (c?.triggerKind === 2 && c.triggerCharacter) triggerReason = { kind: c.isRetrigger ? "retrigger" : "characterTyped", triggerCharacter: c.triggerCharacter };
        else if (c?.triggerKind === 3) triggerReason = c.isRetrigger ? { kind: "retrigger" } : { kind: "characterTyped", triggerCharacter: c.triggerCharacter };
        else if (c) triggerReason = { kind: "invoked" };
        const h = await ask("signatureHelp", { ...at, ...(triggerReason ? { triggerReason } : {}) });
        if (!h?.items?.length) return null;
        return {
          signatures: h.items.map((it) => {
            const params = it.parameters.map((x) => ({ label: parts(x.displayParts), ...(docParts(x.documentation) ? { documentation: { kind: "markdown", value: docParts(x.documentation) } } : {}) }));
            const label = parts(it.prefixDisplayParts) + params.map((x) => x.label).join(parts(it.separatorDisplayParts)) + parts(it.suffixDisplayParts);
            // Go: the comment only, no tags (ls/signaturehelp.go, commentOnly).
            const doc = docParts(it.documentation);
            return { label, ...(doc ? { documentation: { kind: "markdown", value: doc } } : {}), parameters: params };
          }),
          activeSignature: h.selectedItemIndex,
          activeParameter: h.argumentIndex,
        };
      }
      case "textDocument/prepareRename": {
        const r = await ask("rename", { ...at, findInStrings: false, findInComments: false });
        // Go answers an element it cannot rename with an error carrying the same message.
        if (r?.info && !r.info.canRename && r.info.localizedErrorMessage) throw new LspError(r.info.localizedErrorMessage);
        if (!r?.info?.canRename) return null;
        return { range: range(r.info.triggerSpan.start, r.info.triggerSpan.end), placeholder: r.info.displayName };
      }
      case "textDocument/rename": {
        const r = await ask("rename", { ...at, findInStrings: false, findInComments: false });
        if (!r?.info?.canRename) return null;
        const changes = new Map(); // uri -> edits (a Map: no file name is ever taken for a property of {})
        for (const group of r.locs) {
          for (const l of group.locs) {
            const u = this.uri(group.file);
            changes.set(u, [...(changes.get(u) ?? []), { range: range(l.start, l.end), newText: (l.prefixText ?? "") + p.newName + (l.suffixText ?? "") }]);
          }
        }
        return { changes: Object.fromEntries(changes) };
      }
      case "textDocument/completion": {
        const trigger = p.context?.triggerCharacter;
        const c = await ask("completionInfo", { ...at, ...(trigger ? { triggerCharacter: trigger } : {}), triggerKind: p.context?.triggerKind ?? 1 });
        if (!c) return null;
        return { isIncomplete: !!c.isIncomplete, items: c.entries.map((e) => completionItem(e, c, at)) };
      }
      case "completionItem/resolve": {
        const d = p.data ?? {};
        const at2 = d.tsserver;
        if (!at2) throw new JsFailure("completionItem/resolve: the item has no tsserver position");
        const list = await ask("completionEntryDetails", { file: at2.file, line: at2.line, offset: at2.offset, entryNames: [{ name: d.name, ...(d.source ? { source: d.source } : {}), ...(d.entryData ? { data: d.entryData } : {}) }] });
        const det = list?.[0];
        if (!det) throw new JsFailure("completionEntryDetails gave no entry");
        const edits = (det.codeActions ?? []).flatMap((a) => a.changes.filter((c) => c.fileName === at2.file).flatMap((c) => c.textChanges.map((t) => ({ range: range(t.start, t.end), newText: t.newText }))));
        const doc = docMarkdown(det.documentation, det.tags);
        const detail = det.codeActions?.length ? det.codeActions.map((a) => a.description).join("\n") : parts(det.displayParts);
        return { ...p, detail, ...(doc ? { documentation: { kind: "markdown", value: doc } } : {}), ...(edits.length ? { additionalTextEdits: edits } : {}) };
      }
      default:
        throw new JsFailure(`${method} has no tsserver equivalent`);
    }
  }

  #diagnostic(d) {
    let severity = SEVERITY[d.category] ?? 1;
    // Go's reportStyleChecksAsWarnings (on by default; lsconv styleCheckDiagnostics).
    if (severity === 1 && STYLE_CHECKS.has(d.code)) severity = 2;
    const out = {
      range: range(d.start, d.end),
      severity,
      code: d.code,
      source: "ts",
      message: d.text,
    };
    const tags = [...(d.reportsUnnecessary ? [1] : []), ...(d.reportsDeprecated ? [2] : [])].filter((t) => this.pullTags?.includes(t));
    if (tags.length) out.tags = tags;
    if (this.pullRelated && d.relatedInformation?.length) {
      out.relatedInformation = d.relatedInformation.map((r) => ({
        location: r.span ? this.location(r.span) : { uri: "", range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } } },
        message: r.message,
      }));
    }
    return out;
  }

  #inlayHint(h) {
    const label = h.displayParts?.length
      ? h.displayParts.map((x) => ({ value: x.text, ...(x.span ? { location: this.location(x.span) } : {}) }))
      : [{ value: h.text }];
    const kind = { Type: 1, Parameter: 2 }[h.kind];
    return { position: pos(h.position), label, ...(kind ? { kind } : {}), ...(h.whitespaceBefore ? { paddingLeft: true } : {}), ...(h.whitespaceAfter ? { paddingRight: true } : {}) };
  }

  // Ends the session: tsserver's `exit` command, then it must exit by itself with code 0 in
  // LSPDIFF_SHUTDOWN_MS. The session result (sandbox.mjs) has every outcome; lspdiff takes JS's answers
  // as evidence only from a clean session.
  async shutdown() {
    const shutdown = this.dead ? `not asked: ${this.dead}` : "ok";
    if (!this.dead) this.#send("exit", {});
    this.proc.stdin.end();
    let killed = false;
    const asked = performance.now();
    const timer = setTimeout(() => {
      killed = this.proc.exitCode === null && this.proc.signalCode === null;
      if (killed) this.server.kill();
    }, SETTINGS.LSPDIFF_SHUTDOWN_MS);
    const { signal } = await this.server.exited;
    clearTimeout(timer);
    const forced = killed && signal === "SIGKILL"; // it was still running, and our kill ended it
    const exitMs = Math.round(performance.now() - asked);
    return this.server.result({ shutdown, forced, exitMs });
  }
}

// The 2020 classification triples [start, length, (type + 1) << 8 | modifiers] as LSP relative
// data in Go's legend.
function semanticTokens(text, spans) {
  const starts = lineStarts(text);
  const data = [];
  let [pl, pc] = [0, 0];
  for (let i = 0; i < spans.length; i += 3) {
    const type = TOKEN_TYPES.indexOf(TS_TYPES[(spans[i + 2] >> 8) - 1]);
    if (type < 0) continue;
    let mods = 0;
    TS_MODIFIERS.forEach((m, bit) => {
      const j = TOKEN_MODIFIERS.indexOf(m);
      if (spans[i + 2] & (1 << bit) && j >= 0) mods |= 1 << j;
    });
    const { line, character } = offsetToPos(starts, spans[i]);
    data.push(line - pl, line === pl ? character - pc : character, spans[i + 1], type, mods);
    [pl, pc] = [line, character];
  }
  return data.length ? { data } : null;
}

// navtree items as Go's DocumentSymbols (ls/symbols.go): imports (alias items) are not symbols, and
// children are in source order (navtree sorts them by name).
const before = (a, b) => a.line - b.line || a.offset - b.offset;
function documentSymbols(items) {
  return (items ?? [])
    .filter((c) => c.kind !== "alias")
    .sort((a, b) => before(a.spans[0].start, b.spans[0].start))
    .map((item) => {
      const span = item.spans[0];
      const name = item.nameSpan ?? { start: span.start, end: span.start };
      return { name: item.text, kind: symbolKind(item.kind), range: range(span.start, span.end), selectionRange: range(name.start, name.end), children: documentSymbols(item.childItems) };
    });
}

// A tsserver completion entry as Go's CompletionItem (ls/completions.go createLSPCompletionItem):
// the optional member gets "?" on its label and its name as insert text.
function completionItem(e, info, at) {
  let label = e.name;
  let insertText = e.insertText;
  if (info.isMemberCompletion && (e.kindModifiers ?? "").split(",").includes("optional")) {
    insertText ??= e.name;
    label = e.name + "?";
  }
  const item = { label, kind: completionKind(e.kind), sortText: e.sortText };
  if (e.labelDetails) item.labelDetails = e.labelDetails;
  else if (e.source && e.hasAction) item.labelDetails = { description: e.sourceDisplay ? parts(e.sourceDisplay) : e.source };
  if (insertText !== undefined) item.insertText = insertText;
  item.data = { tsserver: at, name: e.name, ...(e.source ? { source: e.source } : {}), ...(e.data ? { entryData: e.data } : {}), ...(e.hasAction && e.source ? { autoImport: { moduleSpecifier: e.data?.moduleSpecifier ?? e.source } } : {}) };
  return item;
}
