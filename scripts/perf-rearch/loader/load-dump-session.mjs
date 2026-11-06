// S1a proof driver: runs one LSP session with GOPORT_LOAD_DUMP and records every
// server-to-client request (client/registerCapability, unregisterCapability, ...).
// usage: node load-dump-session.mjs <server bin> <out prefix> <scenario> <root> [second root]
//   scenarios:
//     open      open one file, pull diagnostics (a first load)
//     second    open, then a file of another project (second root, or a nested
//               project of the same root), pull diagnostics of both
//     unsaved   open an unsaved buffer (a file absent on disk that imports the first
//               file), then the first file, pull diagnostics of both
//     pkgjson   open the root package.json as an edited overlay ("type" flipped), then
//               the first file, pull diagnostics
//     edit      open, pull, then add an import to the file (a second load, resolve ahead)
//     autoimport open, pull, ask for completions at the end of the file after typing an
//               identifier prefix, then completionItem/resolve the first auto-import item
//     cancel    open, then a diagnostic request and a completion request that are
//               cancelled at once, then pull diagnostics
//     fsmut     on a scratch copy of the root (<out dir>/scratch-fsmut, node_modules linked;
//               the root is not changed): open, pull, then
//               create a file the open file imports, delete another one, send
//               didChangeWatchedFiles for both and pull again
// Writes <out prefix>.dump (the load dump), <out prefix>.stream (the whole message
// stream in order, see below; the proof compares it) and <out prefix>.json (a summary:
// server requests, notifications other than window/logMessage, responses, diagnostics).
// Server env passes through (GOPORT_RESOLVE_AHEAD=0 and GOPORT_LOAD_TIMES in a
// `diagnostics` build).
// S1A_FILE / S1A_FILE2: the file to open (relative to root / second root), else the first
// of lspdiff's pickFiles.
import { readFileSync, writeFileSync, rmSync, existsSync, cpSync, mkdirSync, unlinkSync, readdirSync, symlinkSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, join, basename } from "node:path";
import { pathToFileURL } from "node:url";
const lsp = new URL("../../lspdiff/", import.meta.url).pathname;
const { pickFiles, languageId } = await import(`${lsp}/project.mjs`);
const { initializeParams } = await import(`${lsp}/client.mjs`);

const [bin, out, scenario, rootArg, root2] = process.argv.slice(2);
// fsmut runs on a scratch copy (node_modules linked), so the project input is not changed.
let root = rootArg;
if (scenario === "fsmut") {
  // One fixed path for every run of the scenario, so the paths in the output agree.
  root = join(dirname(out), "scratch-fsmut");
  rmSync(root, { recursive: true, force: true });
  mkdirSync(root, { recursive: true });
  for (const name of readdirSync(rootArg)) {
    if (name === ".git") continue;
    if (name === "node_modules") symlinkSync(join(rootArg, name), join(root, name));
    else cpSync(join(rootArg, name), join(root, name), { recursive: true, verbatimSymlinks: true });
  }
}
const dump = `${out}.dump`;
if (existsSync(dump)) rmSync(dump);
const uri = (p) => pathToFileURL(p).href;

// The only normalization of the message stream: in a window/logMessage
// notification's message text, durations ("in 12.3ms", "1.2s") and clock times
// ("[07:12:31.123]") vary from run to run. Every other byte is kept.
const DURATION = /\d+(?:\.\d+)?(?:ns|µs|us|ms|s|m)\b/g;
const CLOCK = /\b\d\d:\d\d:\d\d(?:\.\d+)?\b/g;
function normalizeLog(raw) {
  if (!raw.includes('"window/logMessage"')) return raw;
  const msg = JSON.parse(raw);
  if (msg.method !== "window/logMessage" || typeof msg.params?.message !== "string") return raw;
  msg.params.message = msg.params.message.replace(CLOCK, "<clock>").replace(DURATION, "<duration>");
  return JSON.stringify(msg);
}

class Client {
  constructor(cmd, cwd, env) {
    this.proc = spawn(cmd[0], cmd.slice(1), { cwd, env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
    this.next = 1; this.pending = new Map(); this.buf = Buffer.alloc(0); this.serverRequests = []; this.notes = [];
    this.responses = []; this.methods = new Map();
    // Every message in both directions, in order, as its raw JSON text.
    this.stream = [];
    this.stderr = "";
    this.proc.stdout.on("data", (c) => this.onData(c));
    this.proc.stderr.on("data", (c) => { this.stderr += c; });
    this.exited = new Promise((r) => this.proc.on("exit", r));
  }
  onData(c) {
    this.buf = Buffer.concat([this.buf, c]);
    for (;;) {
      const h = this.buf.indexOf("\r\n\r\n"); if (h < 0) return;
      const len = Number(/Content-Length: *(\d+)/i.exec(this.buf.subarray(0, h).toString())[1]);
      if (this.buf.length < h + 4 + len) return;
      const raw = this.buf.subarray(h + 4, h + 4 + len).toString("utf8");
      this.stream.push(["s2c", raw]);
      const msg = JSON.parse(raw);
      this.buf = this.buf.subarray(h + 4 + len);
      if (msg.id !== undefined && msg.method) {
        this.serverRequests.push({ method: msg.method, params: msg.params });
        this.write({ jsonrpc: "2.0", id: msg.id, result: null });
      } else if (msg.id !== undefined) {
        const p = this.pending.get(msg.id); this.pending.delete(msg.id);
        this.responses.push({ id: msg.id, method: this.methods.get(msg.id), result: msg.result, error: msg.error });
        p?.(msg);
      } else if (msg.method !== "window/logMessage") this.notes.push({ method: msg.method, params: msg.params });
    }
  }
  write(m) { const text = JSON.stringify(m); this.stream.push(["c2s", text]); const b = Buffer.from(text); this.proc.stdin.write(`Content-Length: ${b.length}\r\n\r\n`); this.proc.stdin.write(b); }
  request(method, params) { const id = this.next++; this.methods.set(id, method); const p = new Promise((r) => this.pending.set(id, r)); this.write({ jsonrpc: "2.0", id, method, params }); p.id = id; return p; }
  notify(method, params) { this.write({ jsonrpc: "2.0", method, params }); }
  open(path, text, version = 1) {
    this.notify("textDocument/didOpen", { textDocument: { uri: uri(path), languageId: languageId(path), version, text: text ?? readFileSync(path, "utf8") } });
  }
  async diag(path) { const r = await this.request("textDocument/diagnostic", { textDocument: { uri: uri(path) } }); return r.result ?? r.error; }
}

const c = new Client([bin, "--lsp", "--stdio"], root, { GOPORT_LOAD_DUMP: dump, GOPORT_LAUNCH: "0" });
// File watching on (dynamic registration), so the server registers its watchers
// (client/registerCapability workspace/didChangeWatchedFiles), which come from the
// load's seen files and missing directories.
const params = initializeParams(root);
params.capabilities.workspace.didChangeWatchedFiles = { dynamicRegistration: true, relativePatternSupport: true };
const init = await c.request("initialize", params);
if (init.error) throw new Error(JSON.stringify(init.error));
c.notify("initialized", {});
const [a] = process.env.S1A_FILE ? [join(root, process.env.S1A_FILE)] : pickFiles(root, 6);
const diags = {};
// S1A_SETTLE_MS: wait this long before each scenario's later step (the completion of
// autoimport, the edit of edit), so background work of the first load (the auto-import
// registry) settles: timing then does not decide what a later snapshot holds.
const settle = () => new Promise((r) => setTimeout(r, Number(process.env.S1A_SETTLE_MS ?? 0)));
if (scenario === "open") {
  c.open(a); diags.a = await c.diag(a);
} else if (scenario === "second") {
  c.open(a); diags.a = await c.diag(a);
  const [b] = process.env.S1A_FILE2 ? [join(root2, process.env.S1A_FILE2)] : pickFiles(root2, 6);
  c.open(b); diags.b = await c.diag(b);
} else if (scenario === "unsaved") {
  const u = join(dirname(a), "__s1a_unsaved.ts");
  const spec = "./" + basename(a).replace(/\.(tsx|ts|mts|cts)$/, "");
  c.open(u, `import * as s1a from "${spec}";\nimport "./__s1a_missing";\nexport const x = s1a;\n`);
  diags.u = await c.diag(u);
  c.open(a); diags.a = await c.diag(a);
} else if (scenario === "pkgjson") {
  const pj = join(root, "package.json");
  const json = JSON.parse(readFileSync(pj, "utf8"));
  json.type = json.type === "module" ? "commonjs" : "module";
  c.notify("textDocument/didOpen", { textDocument: { uri: uri(pj), languageId: "json", version: 1, text: JSON.stringify(json, null, 2) } });
  c.open(a); diags.a = await c.diag(a);
} else if (scenario === "edit") {
  c.open(a); diags.a = await c.diag(a);
  await settle();
  const text = readFileSync(a, "utf8");
  c.notify("textDocument/didChange", { textDocument: { uri: uri(a), version: 2 }, contentChanges: [{ range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } }, text: 'import "./__s1a_edit_missing";\n' }] });
  diags.a2 = await c.diag(a);
} else if (scenario === "autoimport") {
  c.open(a); diags.a = await c.diag(a);
  await settle();
  const text = readFileSync(a, "utf8") + "\nconst s1aProbe = use";
  c.notify("textDocument/didChange", { textDocument: { uri: uri(a), version: 2 }, contentChanges: [{ text }] });
  const lines = text.split("\n");
  const position = { line: lines.length - 1, character: lines[lines.length - 1].length };
  const r = await c.request("textDocument/completion", { textDocument: { uri: uri(a) }, position, context: { triggerKind: 1 } });
  const items = r.result?.items ?? r.result ?? [];
  const autoImport = items.find((i) => i.data?.autoImport || i.source || i.labelDetails?.description) ?? items[0];
  diags.completionCount = items.length;
  if (autoImport) diags.resolved = (await c.request("completionItem/resolve", autoImport)).result;
} else if (scenario === "cancel") {
  c.open(a);
  const d = c.request("textDocument/diagnostic", { textDocument: { uri: uri(a) } });
  c.notify("$/cancelRequest", { id: d.id });
  const comp = c.request("textDocument/completion", { textDocument: { uri: uri(a) }, position: { line: 0, character: 0 } });
  c.notify("$/cancelRequest", { id: comp.id });
  await d; await comp;
  diags.a = await c.diag(a);
} else if (scenario === "fsmut") {
  c.open(a); diags.a = await c.diag(a);
  await settle();
  const dir = dirname(a);
  const created = join(dir, "__s1a_created.ts");
  writeFileSync(created, "export const s1aCreated = 1;\n");
  const text = `import { s1aCreated } from "./__s1a_created";\n` + readFileSync(a, "utf8");
  c.notify("textDocument/didChange", { textDocument: { uri: uri(a), version: 2 }, contentChanges: [{ text }] });
  const others = readdirSync(dir).filter((n) => /\.tsx?$/.test(n) && join(dir, n) !== a && !n.startsWith("__s1a"));
  const deleted = others.length ? join(dir, others[0]) : null;
  if (deleted) unlinkSync(deleted);
  c.notify("workspace/didChangeWatchedFiles", { changes: [{ uri: uri(created), type: 1 }, ...(deleted ? [{ uri: uri(deleted), type: 3 }] : [])] });
  diags.a2 = await c.diag(a);
} else throw new Error(`unknown scenario ${scenario}`);
await Promise.race([c.request("shutdown", null), new Promise((r) => setTimeout(r, 5000))]);
// LSP `exit` has no params (with `params: null` the server logs an InvalidParams error).
c.write({ jsonrpc: "2.0", method: "exit" }); c.proc.stdin.end();
const t = setTimeout(() => c.proc.kill("SIGKILL"), 5000); await c.exited; clearTimeout(t);
writeFileSync(`${out}.json`, JSON.stringify({ scenario, root: rootArg, root2, file: a.replace(root, rootArg), serverRequests: c.serverRequests, notifications: c.notes, responses: c.responses, diags, stderr: c.stderr.slice(0, 4000) }, null, 1));
// <out>.stream: one line per message, in the order the client sent or got it:
// "c2s <raw JSON>" or "s2c <raw JSON>", byte for byte as on the wire, except the
// documented normalization (normalizeLog): in the message text of window/logMessage
// only, durations and clock times.
writeFileSync(`${out}.stream`, c.stream.map(([dir, raw]) => `${dir} ${normalizeLog(raw)}`).join("\n") + "\n");
if (scenario === "fsmut") rmSync(root, { recursive: true, force: true });
