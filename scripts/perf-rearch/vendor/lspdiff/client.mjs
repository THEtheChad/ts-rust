// A minimal LSP client over stdio (JSON-RPC with Content-Length framing). No dependencies.
//
// The wire is read strictly (lanes H4 and H8), as the api tier reads its server
// (scripts/perf-rearch/parity_api_session.py Rpc): a header is only Content-Length (and Content-Type)
// lines, each at most MAX_HEADER_LINE bytes, at most MAX_HEADERS of them, with a Content-Length of at
// most 10 digits and MAX_FRAME bytes; a body is UTF-8 (no BOM) and one JSON text with no duplicate key,
// whose numbers keep their text (json.mjs parseStrict: `1e999` is not null); and every message is exactly
// a JSON-RPC 2.0 envelope (envelopeError): an answer to a request this client sent and that is still
// pending ({jsonrpc, id, result} or {jsonrpc, id, error: {code, message, data?}}), a server request
// ({jsonrpc, id, method, params?}) or a notification ({jsonrpc, method, params?}). Anything else breaks
// the protocol: the run ends there, a crash of the server (never an answer), and the session is not clean.
import { pathToFileURL } from "node:url";
import { parseStrict } from "./json.mjs";
import { ServerProcess } from "./sandbox.mjs";
import { SETTINGS } from "./settings.mjs";

// A request the server does not answer in this time ends the run: the server is killed and the
// request rejects with an error whose `timeout` is true (lspdiff.mjs: Go timed out, GO-BROKEN).
const REQUEST_TIMEOUT_MS = SETTINGS.LSPDIFF_REQUEST_TIMEOUT_MS;
// The notifications whose handling LspClient.caughtUp waits for: the ones a scenario sends between
// its waits.
const BARRIER_METHODS = new Set(["textDocument/didOpen", "textDocument/didChange", "textDocument/didClose", "textDocument/didSave", "workspace/didChangeWatchedFiles", "workspace/didChangeConfiguration"]);
// The log line of a handled notification; a request's line has its id after the method:
// "handled method 'm' in 1.2ms", "error handling method 'm': e" (Go lsp/server.go
// handleRequestOrNotification, and tsc-rs's port of it).
const HANDLED_LOG = /^(?:handled|error handling) method '([^']+)'(?: in |: )/;

// Lane B4: which sent notifications of BARRIER_METHODS the server has handled, by its log. Both
// servers handle notifications one at a time, in order, on their dispatch loop, and log one such line
// when the handler returns. A notification has no answer, so a wait on the stream's quiet alone can
// end while the server still handles the last one; the next step's notifications then queue behind it
// and their background work runs back to back: ATA's results, each scheduling a refresh that a 500 ms
// debounce coalesces (watch-hono-f12's load: 10 to 12 refreshes on a starved CPU, Go and tsc-rs alike).
export class NotificationBarrier {
  constructor() {
    this.sent = new Map();
    this.handled = new Map();
  }

  // A notification the client sends.
  send(method) {
    if (BARRIER_METHODS.has(method)) this.sent.set(method, (this.sent.get(method) ?? 0) + 1);
  }

  // A window/logMessage text of the server.
  log(message) {
    const m = HANDLED_LOG.exec(message ?? "");
    if (m) this.handled.set(m[1], (this.handled.get(m[1]) ?? 0) + 1);
  }

  // The methods with sent notifications the server has not logged yet ("<method> <handled>/<sent>").
  behind() {
    return [...this.sent].filter(([m, n]) => (this.handled.get(m) ?? 0) < n).map(([m, n]) => `${m} ${this.handled.get(m) ?? 0}/${n}`);
  }
}

// Lane H9: the work-done progress of a server, by token. The watch scenarios advertise window.workDoneProgress
// (initializeParams), so both servers report their background work through it (Go lsp/progress.go
// projectLoadingProgress, and tsc-rs's port lsp/progress.rs): ATA's "Installing types for '<project>'"
// (project/session.go triggerATAForUpdatedProjects, around InstallTypings) and a program's "Project '<name>'"
// (projectcollectionbuilder.go). Go keeps one token while its group of active operations is nonempty, and
// shows it only once the group has run 250 ms (cmd/tsc/lsp.go ProgressDelay): window/workDoneProgress/create,
// then $/progress begin, reports, and end. ATA's npm calls of one request are thus inside one begin/end pair,
// and Script.settle waits for it (lane B4's residual race: a first install spent more than ATA_IDLE_MS between
// its two npm calls and the npm-idle wait ended). Work shorter than 250 ms never shows: progress is a signal
// of work in flight, never proof that none is coming.
//
// The lifecycle Go keeps (progress.go run, at the pin) is enforced (round 2): every token is created once
// (tokenID++ makes a new one per group, :141), its create comes before its begin (:146, :186), it has one
// begin (:203, after `begun` is false), and reports and its end come only while it is begun (:166, :173, :210).
// A message that breaks it is a protocol failure (LspClient #broken: the run ends there, a crash of that
// server). A token created and not begun in LSPDIFF_PROGRESS_DEADLINE_MS is one too (Go sends the begin right
// after the create, in the same step).
export class ProgressBarrier {
  constructor() {
    this.tokens = new Map(); // token (as JSON) -> { state: "created" | "begun" | "ended", title, message }
  }

  // The params of a window/workDoneProgress/create request: null, or why it breaks the lifecycle.
  create(params) {
    const token = JSON.stringify(params?.token ?? null);
    if (this.tokens.has(token)) return `work-done progress token ${token} created again`;
    this.tokens.set(token, { state: "created" });
    return null;
  }

  // The params of a $/progress notification: null, or why it breaks the lifecycle.
  update(params) {
    const token = JSON.stringify(params?.token ?? null);
    const v = params?.value ?? {};
    const t = this.tokens.get(token);
    const kind = JSON.stringify(v.kind);
    if (!t) return `$/progress ${kind} on token ${token}, which was never created`;
    if (v.kind === "begin") {
      if (t.state !== "created") return `$/progress "begin" on token ${token}, which is ${t.state === "begun" ? "already begun" : "ended"}`;
      Object.assign(t, { state: "begun", title: v.title, message: v.message });
    } else if (v.kind === "report" || v.kind === "end") {
      if (t.state !== "begun") return `$/progress ${kind} on token ${token} with no active begin (${t.state})`;
      if (v.kind === "end") t.state = "ended";
      else if (v.message !== undefined) t.message = v.message;
    } else return `$/progress of kind ${kind} on token ${token}`;
    return null;
  }

  // The progress created or begun and not ended ("<token> <title>: <message>", or "<token> (created)").
  pending() {
    return [...this.tokens].filter(([, t]) => t.state !== "ended").map(([token, t]) => (t.state === "created" ? `${token} (created, not begun)` : `${token} ${t.title}${t.message ? `: ${t.message}` : ""}`));
  }
}

// The work-done progress messages (ProgressBarrier). Whether one shows, in which order and on which token number
// depends on how long the work took (umami's program load, near 250 ms, began a progress in 1 of 4 Go runs), so
// they are left out of every compared message list (scenario.mjs checkpoint "server messages", session.mjs
// "session messages") and kept in the checkpoint's log entry as `progress`, which is not compared. What Go fixes
// in each payload is checked instead (progressPayloadErrors), and every rule a message breaks joins the
// compared list.
export const PROGRESS_METHODS = new Set(["$/progress", "window/workDoneProgress/create"]);

const keysAre = (o, want) => !!o && typeof o === "object" && !Array.isArray(o) && Object.keys(o).sort().join(",") === [...want].sort().join(",");
// The token: fmt.Sprintf("tsgo-loading-%d", tokenID), tokenID from 1 (progress.go:141-142).
const TOKEN = /^tsgo-loading-[1-9][0-9]*$/;
// The two texts a progress message has: diagnostics Installing_types_for_0 (95203) and Project_0 (95204), English
// (the client sends no locale; progress.go localize). Their argument is a project's display name; which projects
// show a progress depends on the time their work took, so it is not checked against Go's run (a project Go loaded
// in under 250 ms has no Go progress to compare with: a false DIFF from timing).
const MESSAGE = /^(?:Installing types for|Project) '[^]*'$/;

// The rules a progress message breaks, of those Go fixes at the pin whatever the timing (lane H9 round 2; Go
// lsp/progress.go and lsproto's omitzero fields):
//   create   params exactly { token }, the token as above (serverProgressReporter.createWorkDoneProgress :46-50)
//   $/progress params exactly { token, value } (sendProgress :52-56)
//   begin    value exactly { kind, title, message }: title the localized "Loading" (diagnostics 95202, :202-206),
//            message one of the two texts (beginOrReport's text, a loading key)
//   report   value exactly { kind, message }, the message one of the two texts (:175-179, :210-213)
//   end      value exactly { kind } (:167-169)
export function progressPayloadErrors(method, params) {
  const out = [];
  const token = params?.token;
  if (method === "window/workDoneProgress/create") {
    if (!keysAre(params, ["token"])) out.push(`create params with keys ${Object.keys(params ?? {})}`);
  } else if (!keysAre(params, ["token", "value"])) out.push(`$/progress params with keys ${Object.keys(params ?? {})}`);
  if (typeof token !== "string" || !TOKEN.test(token)) out.push(`token ${JSON.stringify(token)}, not tsgo-loading-<n>`);
  if (method !== "$/progress") return out;
  const v = params?.value ?? {};
  const fields = { begin: ["kind", "title", "message"], report: ["kind", "message"], end: ["kind"] }[v.kind];
  if (!fields) return [...out, `value of kind ${JSON.stringify(v.kind)}`];
  if (!keysAre(v, fields)) out.push(`${v.kind} with fields ${Object.keys(v).sort()}, not ${[...fields].sort()}`);
  if (v.kind === "begin" && v.title !== "Loading") out.push(`begin title ${JSON.stringify(v.title)}, not "Loading"`);
  if (v.kind !== "end" && !(typeof v.message === "string" && MESSAGE.test(v.message))) out.push(`${v.kind} message ${JSON.stringify(v.message)}, not "Installing types for '{0}'" or "Project '{0}'"`);
  return out;
}

// The compared form of a list of progress events: the rules broken, each once, sorted ([] for a server that keeps
// them, whatever progress it showed).
export function progressFindings(events) {
  return [...new Set(events.flatMap((e) => progressPayloadErrors(e.method, e.params)))].sort().map((rule) => ({ progress: rule }));
}

const MAX_HEADER_LINE = 256;
const MAX_HEADERS = 4;
const MAX_FRAME = 256 << 20;
const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

const isObject = (v) => !!v && typeof v === "object" && !Array.isArray(v) && !("rawJSON" in v && Object.getPrototypeOf(v) === null);
const sameKeys = (o, want) => Object.keys(o).sort().join(",") === [...want].sort().join(",");

// Why msg is not a JSON-RPC 2.0 message this client may get, else null. pending: the ids of the
// requests that wait for an answer.
export function envelopeError(msg, pending) {
  if (!isObject(msg)) return "a message that is not an object";
  if (msg.jsonrpc !== "2.0") return `jsonrpc ${JSON.stringify(msg.jsonrpc)}`;
  if ("params" in msg && !(isObject(msg.params) || Array.isArray(msg.params))) return `params ${JSON.stringify(msg.params).slice(0, 80)}`;
  if ("method" in msg) {
    if (typeof msg.method !== "string") return `method ${JSON.stringify(msg.method)}`;
    if ("id" in msg) {
      if (!(Number.isInteger(msg.id) || typeof msg.id === "string")) return `a server request's id ${JSON.stringify(msg.id)}`;
      if (!sameKeys(msg, ["jsonrpc", "id", "method", ...("params" in msg ? ["params"] : [])])) return `a server request with keys ${Object.keys(msg)}`;
    } else if (!sameKeys(msg, ["jsonrpc", "method", ...("params" in msg ? ["params"] : [])])) return `a notification with keys ${Object.keys(msg)}`;
    return null;
  }
  if (!(sameKeys(msg, ["jsonrpc", "id", "result"]) || sameKeys(msg, ["jsonrpc", "id", "error"]))) return `an answer with keys ${Object.keys(msg)}`;
  if (!Number.isInteger(msg.id) || !pending.has(msg.id)) return `an answer to no pending request (id ${JSON.stringify(msg.id)})`;
  if ("error" in msg) {
    const e = msg.error;
    if (!isObject(e) || !Number.isInteger(e.code) || typeof e.message !== "string" || !sameKeys(e, ["code", "message", ...("data" in e ? ["data"] : [])])) return `an error object ${JSON.stringify(e).slice(0, 120)}`;
  }
  return null;
}

export class LspClient {
  // cmd: [binary, ...args]. Server-to-client requests get a fixed answer, see onServerRequest. binds:
  // more host paths the server sees (sandbox.mjs).
  constructor(cmd, { cwd, env, stderr, binds, plumbing } = {}) {
    this.server = new ServerProcess(cmd, { cwd, env, stderr, binds, plumbing });
    this.proc = this.server.proc;
    this.nextId = 1;
    this.pending = new Map(); // id -> { resolve, reject, method, start }
    this.notifications = []; // { method, params, at }
    // Every server-to-client request and notification, in arrival order: { kind, method, params, at }.
    // The watch scenarios (scenario.mjs) read registrations and publishDiagnostics from it.
    this.events = [];
    this.lastMessageAt = performance.now();
    this.barrier = new NotificationBarrier(); // caughtUp
    this.progress = new ProgressBarrier(); // scenario.mjs Script.progressEnded
    this.unbegun = new Map(); // a created progress token -> its timer: not begun in LSPDIFF_PROGRESS_DEADLINE_MS breaks the protocol
    this.buf = Buffer.alloc(0);
    this.exited = this.server.exited;
    this.dead = null; // why the server is gone, once it is
    this.malformed = null; // a message that broke the framing, the JSON or the envelope
    this.proc.stdout.on("data", (chunk) => this.#onData(chunk));
    // At EOF every byte must have been part of a whole message: a partial header or body, or any
    // stray output, fails the session (lane H4 round 3).
    this.stdoutEnded = new Promise((resolve) => this.proc.stdout.on("end", resolve));
    this.proc.stdin.on("error", () => {}); // writes after a crash: the exit handler reports it
    this.server.exited.then(({ code, signal }) => {
      this.dead ??= `server exited (${code ?? signal})`;
      const panic = /^panic: .*$/m.exec(this.server.tail)?.[0];
      for (const p of this.pending.values()) {
        clearTimeout(p.timer);
        p.reject(new Error(`${this.dead} during ${p.method}${panic ? `: ${panic}` : ""}`));
      }
      this.pending.clear();
      for (const timer of this.unbegun.values()) clearTimeout(timer);
    });
  }

  #onData(chunk) {
    if (this.malformed) return;
    this.buf = Buffer.concat([this.buf, chunk]);
    for (;;) {
      const headerEnd = this.buf.indexOf("\r\n\r\n");
      if (headerEnd < 0) {
        if (this.buf.length > MAX_HEADERS * (MAX_HEADER_LINE + 2)) return this.#broken(`a header longer than ${MAX_HEADERS} lines of ${MAX_HEADER_LINE} bytes: ${JSON.stringify(this.buf.subarray(0, 80).toString("latin1"))}`);
        return;
      }
      const header = this.buf.subarray(0, headerEnd).toString("latin1");
      const lines = header.split("\r\n");
      if (lines.length > MAX_HEADERS) return this.#broken(`more than ${MAX_HEADERS} header lines`);
      const fields = new Map();
      for (const l of lines) {
        // Every header line is a "Name: value" field (LSP base protocol), so stray output in front of a
        // header is a protocol error, not part of it.
        const m = /^(Content-Length|Content-Type): ?([^\r\n]*)$/i.exec(l);
        if (!m || l.length > MAX_HEADER_LINE || fields.has(m[1].toLowerCase())) return this.#broken(`a header with stray bytes: ${JSON.stringify(header.slice(0, 80))}`);
        fields.set(m[1].toLowerCase(), m[2]);
      }
      const len = fields.get("content-length");
      if (!/^[0-9]{1,10}$/.test(len ?? "")) return this.#broken(`a message without a Content-Length of at most 10 digits: ${JSON.stringify(header.slice(0, 80))}`);
      const length = Number(len);
      if (length > MAX_FRAME) return this.#broken(`a Content-Length of ${length} bytes, more than ${MAX_FRAME}`);
      if (this.buf.length < headerEnd + 4 + length) return;
      const bytes = this.buf.subarray(headerEnd + 4, headerEnd + 4 + length);
      this.buf = this.buf.subarray(headerEnd + 4 + length);
      let msg;
      let exact;
      try {
        ({ value: msg, exact } = parseStrict(UTF8.decode(bytes)));
      } catch (e) {
        return this.#broken(`a body that is not strict JSON (${e.message}): ${JSON.stringify(bytes.subarray(0, 80).toString("latin1"))}`);
      }
      const why = envelopeError(msg, this.pending);
      if (why) return this.#broken(`not a JSON-RPC 2.0 message: ${why}`);
      this.#onMessage(msg, !exact);
      if (this.malformed) return; // the message broke the protocol (a progress lifecycle): nothing after it counts
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
    for (const timer of this.unbegun.values()) clearTimeout(timer);
    this.server.kill();
  }

  // raw: the message holds a number kept as its text (json.mjs); what keeps a part of it marks it.
  #onMessage(msg, raw) {
    const at = performance.now();
    this.lastMessageAt = at;
    if (msg.id !== undefined && msg.method !== undefined) return this.#onServerRequest(msg, raw);
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      p.resolve({ id: msg.id, method: p.method, result: msg.result, error: msg.error, ms: at - p.start, ...(raw ? { rawNumbers: true } : {}) });
      return;
    }
    if (msg.method === "window/logMessage") this.barrier.log(msg.params?.message);
    if (msg.method === "$/progress") {
      const why = this.progress.update(msg.params);
      if (why) return this.#broken(why);
      if (msg.params?.value?.kind === "begin") clearTimeout(this.unbegun.get(JSON.stringify(msg.params.token)));
    }
    this.notifications.push({ method: msg.method, params: msg.params, at });
    this.events.push({ kind: "notification", method: msg.method, params: msg.params, at, ...(raw ? { raw } : {}) });
  }

  #onServerRequest(msg, raw) {
    if (msg.method === "window/workDoneProgress/create") {
      const why = this.progress.create(msg.params);
      if (why) return this.#broken(why);
      const token = JSON.stringify(msg.params?.token ?? null);
      const ms = SETTINGS.LSPDIFF_PROGRESS_DEADLINE_MS;
      const timer = setTimeout(() => this.#broken(`work-done progress token ${token} created and not begun in ${ms} ms`), ms);
      timer.unref?.();
      this.unbegun.set(token, timer);
    }
    // workspace/configuration is never asked: initialize does not advertise it.
    // registerCapability, unregisterCapability, window/workDoneProgress/create, refresh requests: accept
    // with null (the answer of a successful registration, and of a progress token created).
    this.events.push({ kind: "request", method: msg.method, params: msg.params, at: performance.now(), ...(raw ? { raw } : {}) });
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
        this.server.kill();
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, method, start, timer });
    });
    promise.id = id;
    this.#write({ jsonrpc: "2.0", id, method, params });
    return promise;
  }

  notify(method, params) {
    this.barrier.send(method);
    this.#write({ jsonrpc: "2.0", method, params });
  }

  // Resolves once the server has handled every notification of BARRIER_METHODS sent so far
  // (NotificationBarrier). Three outcomes (lane H9 round 2):
  //   caught up        resolves with the time waited;
  //   the server died  resolves as well: the next request reports its crash or timeout, as before (a tsc-rs
  //                    failure, not a harness error);
  //   `maxMs` passed   (LSPDIFF_REQUEST_TIMEOUT_MS) with notifications still unhandled: rejects as a harness error
  //                    naming what is behind (lspdiff exits 3: the session is ERROR), never a pass of the barrier.
  // A server that handles a notification and does not log it, or takes that long, cannot be paced, so its
  // session has no verdict.
  async caughtUp(maxMs = REQUEST_TIMEOUT_MS) {
    const start = performance.now();
    for (;;) {
      const waited = performance.now() - start;
      if (this.dead || this.barrier.behind().length === 0) return waited;
      if (waited >= maxMs) {
        throw Object.assign(new Error(`the server did not log the handling of every notification sent in ${maxMs} ms: ${this.barrier.behind().join("; ")} handled`), { harness: true });
      }
      await new Promise((r) => setTimeout(r, 10));
    }
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
      // LSP: the result of shutdown is null (lane H8 round 2: any other value is a broken shutdown).
      else if (r.result !== null) shutdown = `shutdown answered ${JSON.stringify(r.result).slice(0, 200)}, not null`;
      if (!this.dead) this.notify("exit", null);
    }
    this.proc.stdin.end();
    let killed = false;
    const asked = performance.now();
    const timer = setTimeout(() => {
      killed = this.proc.exitCode === null && this.proc.signalCode === null;
      if (killed) this.server.kill();
    }, ms);
    const { signal } = await this.server.exited;
    clearTimeout(timer);
    const forced = killed && signal === "SIGKILL"; // it was still running, and our kill ended it
    // stdout must really end (EOF), not just go quiet: a child that inherited it can still write.
    // An expired wait is no evidence of a clean end (Astra recheck, 2026-11-05).
    let eof;
    const ended = await Promise.race([this.stdoutEnded.then(() => true), new Promise((r) => (eof = setTimeout(() => r(false), 2000)))]);
    clearTimeout(eof);
    if (!ended) this.malformed ??= "stdout did not reach EOF within 2000 ms of the server's exit (a child process still holds it)";
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
// requests after file-system events. It also advertises window.workDoneProgress (lane H9), so both
// servers report their background work (ProgressBarrier).
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
      // The watch scenarios take work-done progress, as VS Code does: Script.settle waits for every
      // progress begun to end (ProgressBarrier). The standard sessions have no waits and keep it off.
      window: { workDoneProgress: watch },
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
