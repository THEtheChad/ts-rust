// A minimal LSP client over stdio (JSON-RPC with Content-Length framing). No dependencies.
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

export class LspClient {
  // cmd: [binary, ...args]. Server-to-client requests get a fixed answer, see onServerRequest.
  constructor(cmd, { cwd, env, stderr } = {}) {
    this.proc = spawn(cmd[0], cmd.slice(1), { cwd, env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
    this.nextId = 1;
    this.pending = new Map(); // id -> { resolve, reject, method, start }
    this.notifications = []; // { method, params, at }
    this.buf = Buffer.alloc(0);
    this.exited = new Promise((resolve) => this.proc.on("exit", (code, signal) => resolve({ code, signal })));
    this.proc.stdout.on("data", (chunk) => this.#onData(chunk));
    this.proc.stderr.on("data", (chunk) => stderr?.write(chunk));
    this.proc.on("exit", (code, signal) => {
      for (const p of this.pending.values()) p.reject(new Error(`server exited (${code ?? signal}) during ${p.method}`));
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
    if (msg.id !== undefined && msg.method) return this.#onServerRequest(msg);
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      p.resolve({ id: msg.id, method: p.method, result: msg.result, error: msg.error, ms: at - p.start });
      return;
    }
    this.notifications.push({ method: msg.method, params: msg.params, at });
  }

  #onServerRequest(msg) {
    // workspace/configuration is never asked: initialize does not advertise it.
    // registerCapability, workDoneProgress/create, refresh requests: accept with null.
    this.#write({ jsonrpc: "2.0", id: msg.id, result: null });
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
    const promise = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, method, start }));
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

  // killAfter: how long the server may take to exit after `exit` (a profiler that wraps it
  // writes its profile then).
  async shutdown({ killAfter = 5000 } = {}) {
    try {
      await Promise.race([this.request("shutdown", null), new Promise((r) => setTimeout(r, 5000))]);
      this.notify("exit", null);
      this.proc.stdin.end();
    } catch {}
    const timer = setTimeout(() => this.proc.kill("SIGKILL"), killAfter);
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

export function initializeParams(rootDir) {
  return {
    processId: null,
    rootUri: fileUri(rootDir),
    workspaceFolders: [{ uri: fileUri(rootDir), name: "root" }],
    initializationOptions: { userPreferences },
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
}
