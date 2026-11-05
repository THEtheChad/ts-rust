// A minimal LSP client over stdio (JSON-RPC with Content-Length framing). No dependencies.
import { pathToFileURL } from "node:url";
import { ServerProcess } from "./sandbox.mjs";
import { SETTINGS } from "./settings.mjs";

// A request the server does not answer in this time ends the run: the server is killed and the
// request rejects with an error whose `timeout` is true (lspdiff.mjs: Go timed out, GO-BROKEN).
const REQUEST_TIMEOUT_MS = SETTINGS.LSPDIFF_REQUEST_TIMEOUT_MS;

export class LspClient {
  // cmd: [binary, ...args]. Server-to-client requests get a fixed answer, see onServerRequest.
  constructor(cmd, { cwd, env, stderr } = {}) {
    this.server = new ServerProcess(cmd, { cwd, env, stderr });
    this.proc = this.server.proc;
    this.nextId = 1;
    this.pending = new Map(); // id -> { resolve, reject, method, start }
    this.notifications = []; // { method, params, at }
    // Every server-to-client request and notification, in arrival order: { kind, method, params, at }.
    // The watch scenarios (scenario.mjs) read registrations and publishDiagnostics from it.
    this.events = [];
    this.lastMessageAt = performance.now();
    this.buf = Buffer.alloc(0);
    this.exited = this.server.exited;
    this.dead = null; // why the server is gone, once it is
    this.malformed = null; // a message that broke the framing or the JSON
    this.proc.stdout.on("data", (chunk) => this.#onData(chunk));
    // At EOF every byte must have been part of a whole message: a partial header or body, or any
    // stray output, fails the session (lane H4 round 3).
    this.stdoutEnded = new Promise((resolve) => this.proc.stdout.on("end", resolve));
    this.proc.stdin.on("error", () => {}); // writes after a crash: the exit handler reports it
    this.proc.on("exit", (code, signal) => {
      this.dead ??= `server exited (${code ?? signal})`;
      const panic = /^panic: .*$/m.exec(this.server.tail)?.[0];
      for (const p of this.pending.values()) p.reject(new Error(`${this.dead} during ${p.method}${panic ? `: ${panic}` : ""}`));
      this.pending.clear();
    });
  }

  #onData(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    for (;;) {
      const headerEnd = this.buf.indexOf("\r\n\r\n");
      if (headerEnd < 0) return;
      const header = this.buf.subarray(0, headerEnd).toString("latin1");
      // Every header line is a "Name: value" field (LSP base protocol), so stray output in front of a
      // header is a protocol error, not part of it.
      const lines = header.split("\r\n");
      if (!lines.every((l) => /^[A-Za-z][A-Za-z0-9-]*: ?[^\r\n]*$/.test(l))) return this.#broken(`a header with stray bytes: ${JSON.stringify(header.slice(0, 80))}`);
      const length = Number(/^Content-Length: *(\d+)$/im.exec(header)?.[1]);
      if (!Number.isInteger(length)) return this.#broken(`a message without a Content-Length: ${header.slice(0, 80)}`);
      if (this.buf.length < headerEnd + 4 + length) return;
      const body = this.buf.subarray(headerEnd + 4, headerEnd + 4 + length).toString("utf8");
      this.buf = this.buf.subarray(headerEnd + 4 + length);
      let msg;
      try {
        msg = JSON.parse(body);
      } catch {
        return this.#broken(`malformed JSON: ${body.slice(0, 80)}`);
      }
      this.#onMessage(msg);
    }
  }

  // A server that breaks the protocol: the run ends there (a crash of the server, never an answer).
  #broken(why) {
    this.malformed ??= why;
    this.dead ??= `server broke the protocol (${why})`;
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(new Error(`${this.dead} during ${p.method}`));
    }
    this.pending.clear();
    this.proc.kill("SIGKILL");
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

  // Ends the session and returns its result (sandbox.mjs ServerProcess.result): the shutdown request
  // must be answered without an error in LSPDIFF_SHUTDOWN_MS, and after `exit` the server must exit
  // by itself with code 0 in that time; its stderr must have no crash line, and it must not have
  // changed its inputs. Every outcome is in the result, none is swallowed.
  async shutdown() {
    const ms = SETTINGS.LSPDIFF_SHUTDOWN_MS;
    let shutdown = "ok";
    if (this.dead) shutdown = `not asked: ${this.dead}`;
    else {
      let timer;
      const r = await Promise.race([
        // LSP shutdown has no params: Go (and tsc-rs) answer `params: null` with InvalidParams.
        this.request("shutdown").then((x) => x, (e) => ({ failed: String(e.message) })),
        new Promise((res) => (timer = setTimeout(() => res({ failed: `no answer to shutdown in ${ms} ms` }), ms))),
      ]);
      clearTimeout(timer);
      if (r.failed) shutdown = r.failed;
      else if (r.error) shutdown = `shutdown answered an error: ${JSON.stringify(r.error).slice(0, 200)}`;
      if (!this.dead) this.notify("exit", null);
    }
    this.proc.stdin.end();
    let killed = false;
    const asked = performance.now();
    const timer = setTimeout(() => {
      killed = this.proc.exitCode === null && this.proc.signalCode === null;
      if (killed) this.proc.kill("SIGKILL");
    }, ms);
    const { signal } = await this.server.exited;
    clearTimeout(timer);
    const forced = killed && signal === "SIGKILL"; // it was still running, and our kill ended it
    let eof;
    await Promise.race([this.stdoutEnded, new Promise((r) => (eof = setTimeout(r, 2000)))]);
    clearTimeout(eof);
    if (this.buf.length) this.malformed ??= `stdout ended with ${this.buf.length} bytes that are no whole message: ${JSON.stringify(this.buf.subarray(0, 80).toString("latin1"))}`;
    const exitMs = Math.round(performance.now() - asked);
    return this.server.result({ shutdown, forced, exitMs, extra: this.malformed ? [`protocol: ${this.malformed}`] : [] });
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
