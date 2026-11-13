#!/usr/bin/env node
// Cancel probe (docs/perf-rearch/experiments/lsp-queued-cancel.md): what each server answers, per
// request kind, to a request that the client cancels while it waits or runs.
//
//   node scripts/lspdiff/probe-cancel.mjs --server name=<binary> [--server ...] --project <dir>
//        --file <edited file> [--repeat 3] [--out result.json]
//
// Scenarios (each request kind, --repeat times):
// - held-sync: the server's dispatch loop is held in the sync part of `initialized` (the client
//   delays its answer to workspace/configuration) while the client sends didOpen, one request of
//   each kind and a cancel for each. Go's dispatch loop is busy, so the requests wait in its
//   queue and Go drops the cancels (lsp/server.go:991). Runs on a one-file fixture in --scratch.
// - in-sync: didChange of the edited file, the request, and its cancel 2 ms later. The request's
//   sync part updates the snapshot, so the cancel comes while the server runs it.
// - behind-<D>: didChange, a diagnostic pull for the edited file (not cancelled), the request, and
//   its cancel D ms later (30 and 300). Go runs the request beside the pull once the pull's sync
//   part ends; the fork queues it behind the whole pull.
// - after: the request, its answer, then a cancel for it and one for an unknown id; then a hover,
//   which must answer its result.
// Answers: "cancelled" (-32800), "result", or "error <code>".
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pickPositions } from "./project.mjs";
import { fileUri, initializeParams } from "./client.mjs";
import { append, openFiles, startServer } from "./session.mjs";

const args = process.argv.slice(2);
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`);
  return i < 0 ? def : args[i + 1];
};
const servers = args.flatMap((a, i) => (a === "--server" ? [args[i + 1].split("=")] : [])).map(([name, bin]) => ({ name, bin }));
const project = resolve(opt("project"));
const file = resolve(project, opt("file"));
const repeat = Number(opt("repeat", 3));
const scratch = resolve(opt("scratch", join(process.env.HOME, ".cache/perf-rearch/lsp-queued-cancel/probe-fixture")));
const out = opt("out");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const KINDS = ["hover", "documentSymbol", "references", "rename", "workspaceSymbol", "completion", "diagnostic", "semanticTokens", "definition", "inlayHint"];
function request(kind, uri, position, text) {
  const td = { textDocument: { uri } };
  const at = { textDocument: { uri }, position };
  switch (kind) {
    case "hover": return ["textDocument/hover", at];
    case "documentSymbol": return ["textDocument/documentSymbol", td];
    case "references": return ["textDocument/references", { ...at, context: { includeDeclaration: true } }];
    case "rename": return ["textDocument/rename", { ...at, newName: "renamedByProbe" }];
    case "workspaceSymbol": return ["workspace/symbol", { query: "Chat" }];
    case "completion": return ["textDocument/completion", { ...at, context: { triggerKind: 1 } }];
    case "diagnostic": return ["textDocument/diagnostic", td];
    case "semanticTokens": return ["textDocument/semanticTokens/full", td];
    case "definition": return ["textDocument/definition", at];
    case "inlayHint": {
      const lines = text.split("\n").length;
      return ["textDocument/inlayHint", { ...td, range: { start: { line: 0, character: 0 }, end: { line: lines, character: 0 } } }];
    }
  }
}
const verdict = (r) => (!r.error ? "result" : r.error.code === -32800 ? "cancelled" : `error ${r.error.code}`);

// held-sync, on a raw connection: the shared client answers workspace/configuration at once.
async function heldSync(bin) {
  mkdirSync(scratch, { recursive: true });
  const text = "export const a = 1;\nexport function f(x: number) { return x + a; }\nf(a);\n";
  writeFileSync(join(scratch, "tsconfig.json"), "{}\n");
  writeFileSync(join(scratch, "a.ts"), text);
  const uri = fileUri(join(scratch, "a.ts"));
  const p = spawn(bin, ["--lsp", "--stdio"], { cwd: scratch, stdio: ["pipe", "pipe", "ignore"] });
  const send = (m) => {
    const s = Buffer.from(JSON.stringify({ jsonrpc: "2.0", ...m }));
    p.stdin.write(`Content-Length: ${s.length}\r\n\r\n`);
    p.stdin.write(s);
  };
  const waiting = new Map();
  const answers = {};
  let buf = Buffer.alloc(0);
  const done = new Promise((resolveDone) => {
    p.stdout.on("data", (d) => {
      buf = Buffer.concat([buf, d]);
      for (;;) {
        const h = buf.indexOf("\r\n\r\n");
        if (h < 0) return;
        const len = Number(/Content-Length: *(\d+)/i.exec(buf.subarray(0, h).toString())[1]);
        if (buf.length < h + 4 + len) return;
        const msg = JSON.parse(buf.subarray(h + 4, h + 4 + len).toString());
        buf = buf.subarray(h + 4 + len);
        if (msg.method === "workspace/configuration") {
          send({ method: "textDocument/didOpen", params: { textDocument: { uri, languageId: "typescript", version: 1, text } } });
          const ids = [];
          for (const kind of KINDS) {
            const id = `${kind}`;
            const [method, params] = request(kind, uri, { line: 2, character: 0 }, text);
            waiting.set(id, kind);
            send({ id, method, params });
            ids.push(id);
          }
          for (const id of ids) send({ method: "$/cancelRequest", params: { id } });
          setTimeout(() => send({ id: msg.id, result: msg.params.items.map(() => null) }), 300);
        } else if (msg.method && msg.id !== undefined) {
          send({ id: msg.id, result: null });
        } else if (msg.id !== undefined && !msg.method) {
          if (msg.id === "init") {
            send({ method: "initialized", params: {} });
            continue;
          }
          if (msg.id === "shutdown") {
            send({ method: "exit" });
            p.stdin.end();
            resolveDone();
            continue;
          }
          const kind = waiting.get(msg.id);
          if (kind) {
            answers[kind] = verdict(msg);
            waiting.delete(msg.id);
            if (!waiting.size) send({ id: "shutdown", method: "shutdown" });
          }
        }
      }
    });
  });
  const init = initializeParams(scratch);
  init.capabilities.workspace.configuration = true;
  send({ id: "init", method: "initialize", params: init });
  const timer = setTimeout(() => p.kill("SIGKILL"), 120000);
  await done;
  clearTimeout(timer);
  return answers;
}

async function onProject(bin) {
  const client = await startServer([bin, "--lsp", "--stdio"], project);
  const res = {};
  const note = (scenario, kind, v) => ((res[scenario] ??= {})[kind] ??= []).push(v);
  try {
    const [doc] = openFiles(client, [file]);
    await client.request("textDocument/diagnostic", { textDocument: { uri: doc.uri } });
    const position = pickPositions(doc.text, 3).idents[1] ?? { line: 0, character: 0 };
    for (let n = 0; n < repeat; n++) {
      for (const kind of KINDS) {
        // in-sync
        append(client, doc, "\n");
        let [method, params] = request(kind, doc.uri, position, doc.text);
        let r = client.request(method, params);
        await sleep(2);
        client.cancel(r.id);
        note("in-sync", kind, verdict(await r));
        // behind-D
        for (const delay of [30, 300]) {
          append(client, doc, "\n");
          const pull = client.request("textDocument/diagnostic", { textDocument: { uri: doc.uri } });
          [method, params] = request(kind, doc.uri, position, doc.text);
          r = client.request(method, params);
          await sleep(delay);
          client.cancel(r.id);
          note(`behind-${delay}`, kind, verdict(await r));
          await pull;
        }
        // after
        [method, params] = request(kind, doc.uri, position, doc.text);
        r = client.request(method, params);
        const answered = await r;
        client.cancel(r.id);
        client.cancel(999999);
        const next = await client.request("textDocument/hover", { textDocument: { uri: doc.uri }, position });
        note("after", kind, `${verdict(answered)}; next ${verdict(next)}`);
      }
    }
  } finally {
    await client.shutdown({ killAfter: 30000 });
  }
  return res;
}

const results = {};
for (const { name, bin } of servers) {
  const t = performance.now();
  results[name] = { "held-sync": await heldSync(bin), ...(await onProject(bin)) };
  console.error(`${name}: ${((performance.now() - t) / 1000).toFixed(1)} s`);
}
if (out) writeFileSync(out, JSON.stringify(results, null, 2));
// Table: scenario, kind, then per server the answers (counted).
const count = (list) => {
  if (!Array.isArray(list)) return list ?? "-";
  const c = {};
  for (const v of list) c[v] = (c[v] ?? 0) + 1;
  return Object.entries(c).map(([v, n]) => `${v} x${n}`).join(", ");
};
const names = servers.map((s) => s.name);
console.log(`| scenario | kind | ${names.join(" | ")} |`);
console.log(`|---|---|${names.map(() => "---|").join("")}`);
for (const scenario of ["held-sync", "in-sync", "behind-30", "behind-300", "after"]) {
  for (const kind of KINDS) console.log(`| ${scenario} | ${kind} | ${names.map((n) => count(results[n][scenario]?.[kind])).join(" | ")} |`);
}
