#!/usr/bin/env node
// A stand-in "tsc-rs" for the referee self-test: an LSP server that forwards everything to the Go
// LSP server (LSPDIFF_PROXY_GO, with this script's arguments) and rewrites some of its answers. LSPDIFF_PROXY_RULES is a JSON list of
//   { method, file, word, edit, runs }
// file: the end of the document's path; word: the identifier at the request's position; edit: what
// to do with the answer (below); runs: the server runs (1, 2, ...) that apply it (default all).
// The run number counts up in LSPDIFF_PROXY_STATE (a file), so a rule can make runs differ.
//   hover-replace  { from, to }: replace text in the hover's markdown
//   drop-location  { uri }: drop the locations whose uri ends with uri
//   crash          the server exits (code 3) when the request arrives
//   hang           the request is never answered (the client's timeout ends the run)
//   null-result    the answer's result is null
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The settings come from a JSON file named as the first argument ({ go, rules, state }: a server gets
// none of lspdiff's environment since lane H5), or from the LSPDIFF_PROXY_* variables.
const fromFile = process.argv[2]?.endsWith(".json") ? JSON.parse(readFileSync(process.argv[2], "utf8")) : null;
const go = fromFile ? fromFile.go : process.env.LSPDIFF_PROXY_GO;
const goArgs = process.argv.slice(fromFile ? 3 : 2);
const rules = fromFile ? fromFile.rules : JSON.parse(process.env.LSPDIFF_PROXY_RULES ?? "[]");
const state = fromFile ? fromFile.state : process.env.LSPDIFF_PROXY_STATE;
const run = state ? (existsSync(state) ? Number(readFileSync(state, "utf8")) : 0) + 1 : 1;
if (state) writeFileSync(state, String(run));
const server = spawn(go, goArgs, { stdio: ["pipe", "pipe", "inherit"] });
const docs = new Map(); // uri -> text
const asked = new Map(); // id -> { method, params }

function framer(onMessage) {
  let buf = Buffer.alloc(0);
  return (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    for (;;) {
      const end = buf.indexOf("\r\n\r\n");
      if (end < 0) return;
      const len = Number(/Content-Length: *(\d+)/i.exec(buf.subarray(0, end).toString("ascii"))[1]);
      if (buf.length < end + 4 + len) return;
      const msg = JSON.parse(buf.subarray(end + 4, end + 4 + len).toString("utf8"));
      buf = buf.subarray(end + 4 + len);
      onMessage(msg);
    }
  };
}
const write = (stream, msg) => {
  const body = Buffer.from(JSON.stringify(msg), "utf8");
  stream.write(`Content-Length: ${body.length}\r\n\r\n`);
  stream.write(body);
};

function wordAt(uri, p) {
  const line = (docs.get(uri) ?? "").split("\n")[p.line] ?? "";
  let [s, e] = [p.character, p.character];
  while (s > 0 && /[\w$]/.test(line[s - 1])) s--;
  while (e < line.length && /[\w$]/.test(line[e])) e++;
  return line.slice(s, e);
}

// The rules that apply to a request ({ method, params }).
function matching(req) {
  if (!req?.params?.position) return [];
  const uri = req.params.textDocument.uri;
  const word = wordAt(uri, req.params.position);
  return rules.filter((r) => r.method === req.method && fileURLToPath(uri).endsWith(r.file) && r.word === word && (!r.runs || r.runs.includes(run)));
}

function rewrite(msg) {
  const req = asked.get(msg.id);
  asked.delete(msg.id);
  for (const r of matching(req)) {
    if (r.edit === "hover-replace" && msg.result?.contents) msg.result.contents.value = msg.result.contents.value.replace(r.from, r.to);
    if (r.edit === "drop-location" && Array.isArray(msg.result)) msg.result = msg.result.filter((l) => !(l.uri ?? l.targetUri).endsWith(r.uri));
    if (r.edit === "null-result") msg.result = null;
  }
  return msg;
}

process.stdin.on("data", framer((msg) => {
  if (msg.method === "textDocument/didOpen") docs.set(msg.params.textDocument.uri, msg.params.textDocument.text);
  if (msg.id !== undefined && msg.method) {
    const req = { method: msg.method, params: msg.params };
    const edits = matching(req).map((r) => r.edit);
    if (edits.includes("crash")) process.exit(3);
    if (edits.includes("hang")) return;
    asked.set(msg.id, req);
  }
  write(server.stdin, msg);
}));
process.stdin.on("end", () => server.stdin.end());
server.stdout.on("data", framer((msg) => write(process.stdout, msg.id !== undefined && !msg.method ? rewrite(msg) : msg)));
server.on("exit", (code) => process.exit(code ?? 0));
