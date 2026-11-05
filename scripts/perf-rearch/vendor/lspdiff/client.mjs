// A minimal LSP client over stdio (JSON-RPC with Content-Length framing). No dependencies.
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

// A request the server does not answer in this time ends the run: the server is killed and the
// request rejects with an error whose `timeout` is true (lspdiff.mjs: Go timed out, GO-BROKEN).
const REQUEST_TIMEOUT_MS = Number(process.env.LSPDIFF_REQUEST_TIMEOUT_MS ?? 300000);

export class LspClient {
  // cmd: [binary, ...args]. Server-to-client requests get a fixed answer, see onServerRequest.
  constructor(cmd, { cwd, env, stderr } = {}) {
    this.proc = spawn(cmd[0], cmd.slice(1), { cwd, env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
    this.nextId = 1;
    this.pending = new Map(); // id -> { resolve, reject, method, start }
    this.notifications = []; // { method, params, at }
    // Every server-to-client request and notification, in arrival order: { kind, method, params, at }.
    // The watch scenarios (scenario.mjs) read registrations and publishDiagnostics from it.
    this.events = [];
    this.lastMessageAt = performance.now();
    this.buf = Buffer.alloc(0);
    this.exited = new Promise((resolve) => this.proc.on("exit", (code, signal) => resolve({ code, signal })));
    this.dead = null; // why the server is gone, once it is
    this.stderrTail = "";
    this.proc.stdout.on("data", (chunk) => this.#onData(chunk));
    this.proc.stderr.on("data", (chunk) => {
      this.stderrTail = (this.stderrTail + chunk.toString("utf8")).slice(-2000);
      stderr?.write(chunk);
    });
    this.proc.stdin.on("error", () => {}); // writes after a crash: the exit handler reports it
    this.proc.on("exit", (code, signal) => {
      this.dead ??= `server exited (${code ?? signal})`;
      const panic = /^panic: .*$/m.exec(this.stderrTail)?.[0];
      for (const p of this.pending.values()) p.reject(new Error(`${this.dead} during ${p.method}${panic ? `: ${panic}` : ""}`));
      this.pending.clear();
    });
  }

  #onData(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    for (;;) {
      const headerEnd = this.buf.indexOf("\r\n\r\n");
      if (headerEnd < 0) return;
      const header = this.buf.subarray(0, headerEnd).toString("ascii");
      const length = Number(/Content-Length: *(\d+)/i.exec(header)?.[1]);
      if (this.buf.length < headerEnd + 4 + length) return;
      const body = this.buf.subarray(headerEnd + 4, headerEnd + 4 + length).toString("utf8");
      this.buf = this.buf.subarray(headerEnd + 4 + length);
      this.#onMessage(JSON.parse(body));
    }
  }

  #onMessage(msg) {
    const at = performance.now();
    this.lastMessageAt = at;
    if (msg.id !== undefined && msg.method) return this.#onServerRequest(msg);
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      p.resolve({ id: msg.id, method: p.method, result: msg.result, error: msg.error, ms: at - p.start });
      return;
    }
    this.notifications.push({ method: msg.method, params: msg.params, at });
    this.events.push({ kind: "notification", method: msg.method, params: msg.params, at });
  }

  #onServerRequest(msg) {
    // workspace/configuration is never asked: initialize does not advertise it.
    // registerCapability, unregisterCapability, workDoneProgress/create, refresh requests: accept with
    // null (the answer of a successful registration).
    this.events.push({ kind: "request", method: msg.method, params: msg.params, at: performance.now() });
    this.#write({ jsonrpc: "2.0", id: msg.id, result: null });
  }

  // Resolves once no server message has arrived for `quietMs` and no request is pending (at most
  // `maxMs`). Watch registrations and pushed diagnostics come from the servers' background queues
  // after an answer, so a checkpoint waits for them with this.
  async settle(quietMs = 1000, maxMs = 30000) {
    const start = performance.now();
    for (;;) {
      const now = performance.now();
      if ((this.pending.size === 0 && now - this.lastMessageAt >= quietMs) || now - start >= maxMs) return now - start;
      await new Promise((r) => setTimeout(r, 50));
    }
  }

  #write(msg) {
    const body = Buffer.from(JSON.stringify(msg), "utf8");
    this.proc.stdin.write(`Content-Length: ${body.length}\r\n\r\n`);
    this.proc.stdin.write(body);
  }

  // Sends a request; the promise resolves with { id, method, result, error, ms }.
  request(method, params) {
    const id = this.nextId++;
    const start = performance.now();
    if (this.dead) return Promise.reject(new Error(`${this.dead} before ${method}`));
    const promise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.dead = `server timed out (no answer to ${method} in ${REQUEST_TIMEOUT_MS} ms)`;
        reject(Object.assign(new Error(this.dead), { timeout: true }));
        this.proc.kill("SIGKILL");
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, method, start, timer });
    });
    promise.id = id;
    this.#write({ jsonrpc: "2.0", id, method, params });
    return promise;
  }

  notify(method, params) {
    this.#write({ jsonrpc: "2.0", method, params });
  }

  cancel(id) {
    this.notify("$/cancelRequest", { id });
  }

  async shutdown() {
    try {
      await Promise.race([this.request("shutdown", null), new Promise((r) => setTimeout(r, 5000))]);
      this.notify("exit", null);
    } catch {}
    const timer = setTimeout(() => this.proc.kill("SIGKILL"), 5000);
    await this.exited;
    clearTimeout(timer);
  }
}

export const fileUri = (path) => pathToFileURL(path).href;

// Inlay hints and auto-import completions are off by default; turn them on so those
// requests have something to compare. Passed as initializationOptions, so neither server
// needs a workspace/configuration round trip.
export const userPreferences = {
  unstable: {
    includeInlayParameterNameHints: "all",
    includeInlayParameterNameHintsWhenArgumentMatchesName: true,
    includeInlayFunctionParameterTypeHints: true,
    includeInlayVariableTypeHints: true,
    includeInlayPropertyDeclarationTypeHints: true,
    includeInlayFunctionLikeReturnTypeHints: true,
    includeInlayEnumMemberValueHints: true,
    includeCompletionsForModuleExports: true,
    includeCompletionsWithInsertText: true,
  },
};

// watch: advertise dynamic registration of workspace/didChangeWatchedFiles with relative pattern
// support, and refresh support for diagnostics, semantic tokens, inlay hints and code lenses, as
// VS Code does. Both servers then register their watch globs with the client
// (client/registerCapability) instead of logging "file watching: disabled", and send refresh
// requests after file-system events.
//
// ata: false turns automatic type acquisition off (disableAutomaticTypeAcquisition). With it on,
// both servers start ATA for an inferred project in the background, and its first step runs
// `npm install types-registry@latest` (network, about 0.5 s). Its typings join the inferred project
// at the first request after npm returns, so whether an answer sees them depends on npm's timing.
// That made lsp/hono-f12-p6/0057 vary on Go and on tsc-rs alike (docs/perf-rearch/parity.md, "ATA").
export function initializeParams(rootDir, { watch = false, ata = true } = {}) {
  const prefs = ata ? userPreferences : { ...userPreferences, unstable: { ...userPreferences.unstable, disableAutomaticTypeAcquisition: true } };
  const params = {
    processId: null,
    rootUri: fileUri(rootDir),
    workspaceFolders: [{ uri: fileUri(rootDir), name: "root" }],
    initializationOptions: { userPreferences: prefs },
    capabilities: {
      general: { positionEncodings: ["utf-16"] },
      workspace: { workspaceFolders: true, applyEdit: true, workspaceEdit: { documentChanges: true } },
      textDocument: {
        synchronization: { didSave: true, dynamicRegistration: false },
        diagnostic: { dynamicRegistration: false, relatedDocumentSupport: false },
        publishDiagnostics: { relatedInformation: true, tagSupport: { valueSet: [1, 2] } },
        completion: {
          completionItem: {
            snippetSupport: true,
            commitCharactersSupport: true,
            documentationFormat: ["markdown", "plaintext"],
            labelDetailsSupport: true,
            insertReplaceSupport: true,
            resolveSupport: { properties: ["documentation", "detail", "additionalTextEdits"] },
          },
          completionList: { itemDefaults: ["commitCharacters", "editRange", "insertTextFormat", "data"] },
        },
        hover: { contentFormat: ["markdown", "plaintext"] },
        definition: { linkSupport: true },
        references: {},
        rename: { prepareSupport: true },
        signatureHelp: { signatureInformation: { documentationFormat: ["markdown", "plaintext"], parameterInformation: { labelOffsetSupport: true } } },
        documentSymbol: { hierarchicalDocumentSymbolSupport: true },
        codeAction: {
          codeActionLiteralSupport: { codeActionKind: { valueSet: ["", "quickfix", "refactor", "source", "source.organizeImports"] } },
          resolveSupport: { properties: ["edit"] },
          dataSupport: true,
        },
        semanticTokens: {
          requests: { full: true, range: true },
          tokenTypes: ["namespace", "type", "class", "enum", "interface", "struct", "typeParameter", "parameter", "variable", "property", "enumMember", "event", "function", "method", "macro", "keyword", "modifier", "comment", "string", "number", "regexp", "operator", "decorator"],
          tokenModifiers: ["declaration", "definition", "readonly", "static", "deprecated", "abstract", "async", "modification", "documentation", "defaultLibrary"],
          formats: ["relative"],
          multilineTokenSupport: false,
          overlappingTokenSupport: false,
        },
        inlayHint: { resolveSupport: { properties: [] } },
      },
      window: { workDoneProgress: false },
    },
  };
  if (watch) {
    Object.assign(params.capabilities.workspace, {
      didChangeWatchedFiles: { dynamicRegistration: true, relativePatternSupport: true },
      diagnostics: { refreshSupport: true },
      semanticTokens: { refreshSupport: true },
      inlayHint: { refreshSupport: true },
      codeLens: { refreshSupport: true },
    });
  }
  return params;
}
