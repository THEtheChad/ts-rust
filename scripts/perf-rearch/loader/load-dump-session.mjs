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
// Writes <out prefix>.dump (the load dump) and <out prefix>.json (server requests with
// their params, diagnostics). Server env passes through (GOPORT_FIRST_LOAD_AHEAD).
// S1A_FILE / S1A_FILE2: the file to open (relative to root / second root), else the first
// of lspdiff's pickFiles.
import { readFileSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, join, basename } from "node:path";
import { pathToFileURL } from "node:url";
const lsp = new URL("../../lspdiff/", import.meta.url).pathname;
const { pickFiles, languageId } = await import(`${lsp}/project.mjs`);
const { initializeParams } = await import(`${lsp}/client.mjs`);

const [bin, out, scenario, root, root2] = process.argv.slice(2);
const dump = `${out}.dump`;
if (existsSync(dump)) rmSync(dump);
const uri = (p) => pathToFileURL(p).href;

class Client {
  constructor(cmd, cwd, env) {
    this.proc = spawn(cmd[0], cmd.slice(1), { cwd, env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
    this.next = 1; this.pending = new Map(); this.buf = Buffer.alloc(0); this.serverRequests = []; this.notes = [];
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
      const msg = JSON.parse(this.buf.subarray(h + 4, h + 4 + len).toString("utf8"));
      this.buf = this.buf.subarray(h + 4 + len);
      if (msg.id !== undefined && msg.method) {
        this.serverRequests.push({ method: msg.method, params: msg.params });
        this.write({ jsonrpc: "2.0", id: msg.id, result: null });
      } else if (msg.id !== undefined) {
        const p = this.pending.get(msg.id); this.pending.delete(msg.id); p?.(msg);
      } else if (msg.method !== "window/logMessage") this.notes.push({ method: msg.method, params: msg.params });
    }
  }
  write(m) { const b = Buffer.from(JSON.stringify(m)); this.proc.stdin.write(`Content-Length: ${b.length}\r\n\r\n`); this.proc.stdin.write(b); }
  request(method, params) { const id = this.next++; const p = new Promise((r) => this.pending.set(id, r)); this.write({ jsonrpc: "2.0", id, method, params }); return p; }
  notify(method, params) { this.write({ jsonrpc: "2.0", method, params }); }
  open(path, text, version = 1) {
    this.notify("textDocument/didOpen", { textDocument: { uri: uri(path), languageId: languageId(path), version, text: text ?? readFileSync(path, "utf8") } });
  }
  async diag(path) { const r = await this.request("textDocument/diagnostic", { textDocument: { uri: uri(path) } }); return r.result?.items?.map((d) => [d.code, d.range.start.line, d.range.start.character, d.message]) ?? r.error; }
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
  const text = readFileSync(a, "utf8");
  c.notify("textDocument/didChange", { textDocument: { uri: uri(a), version: 2 }, contentChanges: [{ range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } }, text: 'import "./__s1a_edit_missing";\n' }] });
  diags.a2 = await c.diag(a);
} else throw new Error(`unknown scenario ${scenario}`);
await Promise.race([c.request("shutdown", null), new Promise((r) => setTimeout(r, 5000))]);
c.notify("exit", null); c.proc.stdin.end();
const t = setTimeout(() => c.proc.kill("SIGKILL"), 5000); await c.exited; clearTimeout(t);
writeFileSync(`${out}.json`, JSON.stringify({ scenario, root, root2, file: a, serverRequests: c.serverRequests, notifications: c.notes.map((n) => n.method), diags, stderr: c.stderr.slice(0, 4000) }, null, 1));
