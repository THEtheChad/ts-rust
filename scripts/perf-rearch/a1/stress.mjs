#!/usr/bin/env node
// Long-session stress of the language server's synthetic reuse (perf/first-principles A1): many
// `.tsx` keystrokes over several files, each followed by a completion that is cancelled at once,
// with hovers and diagnostic pulls, import edits that build the program again, and close/reopen
// cycles that release snapshots. The server writes a GOPORT_REUSE_STATS line after each program
// update (a `diagnostics` feature build only: --features diagnostics); this samples them and the
// server's RSS.
//
//   node scripts/perf-rearch/a1/stress.mjs --bin <tsgo> --project <dir> --edit <rel>... [--iterations 600]
//        [--sample 50] [--stats <file>] [--env NAME=VALUE...]
//
// Exit 1 when the server exits, a request fails other than by cancellation, or no reuse ran.
//
// What it shows: the lifetimes of reused programs, their synthetic specifier owners and their
// snapshots under releases and cancellations (with GOPORT_CHECK_VERSION_TABLES=1 in the diagnostics
// build, the table checks too; a debug build adds its debug checks), and memory over a long session. It does not compare
// answers: answer parity under cancellation is not established here (lspdiff runs one request at
// a time).
import { existsSync, readFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { LspClient, initializeParams } from "../vendor/lspdiff/client.mjs";
import { append, openFiles, removeTail } from "../vendor/lspdiff/session.mjs";
import { endPosition, languageId } from "../vendor/lspdiff/project.mjs";

const { values: opt } = parseArgs({
  options: {
    bin: { type: "string" },
    project: { type: "string" },
    edit: { type: "string", multiple: true, default: [] },
    iterations: { type: "string", default: "600" },
    sample: { type: "string", default: "50" },
    stats: { type: "string", default: "/dev/null" },
    env: { type: "string", multiple: true, default: [] },
  },
});
const root = resolve(opt.project);
if (existsSync(opt.stats)) rmSync(opt.stats);
const env = { GOPORT_REUSE_STATS: opt.stats, ...Object.fromEntries(opt.env.map((e) => e.split(/=(.*)/s).slice(0, 2))) };
const client = new LspClient([opt.bin, "--lsp", "--stdio"], { cwd: root, env });
let exited = false;
client.exited.then(() => (exited = true));
const init = await client.request("initialize", initializeParams(root));
if (init.error) throw new Error(JSON.stringify(init.error));
client.notify("initialized", {});
const docs = openFiles(client, opt.edit.map((f) => resolve(root, f)));
for (const d of docs) await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
const rss = () => Number(/VmRSS:\s+(\d+)/.exec(readFileSync(`/proc/${client.proc.pid}/status`, "utf8"))?.[1] ?? 0);
const stats = () => (existsSync(opt.stats) ? readFileSync(opt.stats, "utf8").trim().split("\n").filter(Boolean) : []);
const failures = [];
const check = (r, what) => {
  if (r.error && r.error.code !== -32800) failures.push(`${what}: ${JSON.stringify(r.error).slice(0, 200)}`);
};
const n = Number(opt.iterations);
const samples = [];
let importAdded = null;
for (let i = 0; i < n && !exited; i++) {
  const d = docs[i % docs.length];
  const position = append(client, d, i % 7 === 6 ? "\n" : "x");
  // A completion that the client cancels at once.
  const pending = client.request("textDocument/completion", { textDocument: { uri: d.uri }, position, context: { triggerKind: 1 } });
  client.cancel(pending.id);
  check(await pending, `completion ${i}`);
  if (i % 3 === 0) check(await client.request("textDocument/hover", { textDocument: { uri: d.uri }, position: { line: 0, character: 1 } }), `hover ${i}`);
  if (i % 5 === 0) check(await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } }), `diagnostic ${i}`);
  // An import edit at the top builds the program again; the next one takes it out.
  if (i % 25 === 24) {
    const t = docs[0];
    const text = 'import type {} from "react";\n';
    const change = importAdded
      ? { range: { start: { line: 0, character: 0 }, end: { line: 1, character: 0 } }, text: "" }
      : { range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } }, text };
    t.text = importAdded ? t.text.slice(text.length) : text + t.text;
    importAdded = !importAdded;
    t.version++;
    client.notify("textDocument/didChange", { textDocument: { uri: t.uri, version: t.version }, contentChanges: [change] });
    check(await client.request("textDocument/diagnostic", { textDocument: { uri: t.uri } }), `import diagnostic ${i}`);
  }
  // Close and reopen the last file: its snapshots and programs go.
  if (i % 40 === 39 && docs.length > 1) {
    const c = docs[docs.length - 1];
    client.notify("textDocument/didClose", { textDocument: { uri: c.uri } });
    check(await client.request("textDocument/hover", { textDocument: { uri: docs[0].uri }, position: { line: 0, character: 1 } }), `hover after close ${i}`);
    c.version++;
    client.notify("textDocument/didOpen", { textDocument: { uri: c.uri, languageId: languageId(c.path), version: c.version, text: c.text } });
    check(await client.request("textDocument/diagnostic", { textDocument: { uri: c.uri } }), `diagnostic after reopen ${i}`);
  }
  if ((i + 1) % Number(opt.sample) === 0) {
    const last = stats().at(-1) ?? "";
    samples.push({ i: i + 1, rssMiB: (rss() / 1024).toFixed(1), last });
  }
}
const lines = stats();
if (opt.stats !== "/dev/null" && lines.length === 0) {
  console.error(`stress.mjs: ${opt.bin} wrote no GOPORT_REUSE_STATS lines (not a diagnostics feature build?)`);
  process.exitCode = 1;
}
const count = (k) => lines.filter((l) => l.startsWith(`outcome=${k} `)).length;
const died = exited;
await client.shutdown();
console.log(`${root}: ${n} iterations over ${docs.length} files; updates: ReusedSynthetic ${count("ReusedSynthetic")}, Reused ${count("Reused")}, Rebuilt ${count("Rebuilt")}, New ${count("New")}`);
console.table(samples);
for (const f of failures.slice(0, 10)) console.log(`FAILED ${f}`);
if (died) console.log("FAILED the server exited during the session");
process.exit(died || failures.length || (count("ReusedSynthetic") === 0 && process.env.STRESS_ALLOW_NO_REUSE !== "1") ? 1 : 0);
