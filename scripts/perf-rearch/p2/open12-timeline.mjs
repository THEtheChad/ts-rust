#!/usr/bin/env node
// P2: the timeline of hono's open12 session (window2/open12.mjs) from the client's side. Same session: spawn,
// initialize (ATA off), 12 didOpens (pickFiles), then textDocument/diagnostic of each file in turn. Writes one JSON
// object: every server-to-client message (window/logMessage text, registerCapability, publishDiagnostics, answers)
// with its arrival time in ms after the first didOpen, plus first_ms as open12.mjs measures it.
//   node scripts/perf-rearch/p2/open12-timeline.mjs <server binary> <project dir> [--files N] [--out FILE]
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pickFiles } from "../vendor/lspdiff/project.mjs";
import { openFiles, startServer } from "../vendor/lspdiff/session.mjs";

const args = process.argv.slice(2);
const take = (flag) => {
  const at = args.indexOf(flag);
  return at >= 0 ? args.splice(at, 2)[1] : undefined;
};
const count = Number(take("--files") ?? 12);
const out = take("--out");
const [bin, root] = args;
if (!bin || !root) {
  console.error("usage: node scripts/perf-rearch/p2/open12-timeline.mjs <server binary> <project dir> [--files N] [--out FILE]");
  process.exit(2);
}
process.env.GOPORT_LAUNCH = "0";
const files = pickFiles(resolve(root), count);
const t0 = performance.now();
const client = await startServer([bin, "--lsp", "--stdio"], resolve(root), { ata: false, stderr: process.stderr });
const initMs = performance.now() - t0;
const firstEvent = client.events.length;
const t1 = performance.now();
const docs = openFiles(client, files);
const sent = performance.now() - t1;
const answers = [];
for (const d of docs) {
  const start = performance.now() - t1;
  const r = await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
  answers.push({ file: d.path.slice(resolve(root).length + 1), start, end: start + r.ms, ms: r.ms, items: r.result?.items?.length ?? 0 });
}
await client.settle(300, 3000);
const events = client.events.slice(firstEvent).map((e) => ({
  at: +(e.at - t1).toFixed(2),
  kind: e.kind,
  method: e.method,
  ...(e.method === "window/logMessage" ? { text: e.params?.message?.slice(0, 400) } : {}),
  ...(e.method === "client/registerCapability" ? { ids: e.params?.registrations?.map((r) => r.id) } : {}),
  ...(e.method === "textDocument/publishDiagnostics" ? { uri: e.params?.uri?.slice(-60) } : {}),
}));
const pid = client.proc.pid;
const stat = readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1].split(" ");
await client.shutdown();
const result = {
  bin, init_ms: initMs, sent_ms: sent, first_ms: answers[0].ms, cpu_ms: (Number(stat[11]) + Number(stat[12])) * 10,
  files: docs.map((d) => d.path.slice(resolve(root).length + 1)), answers, events,
};
if (out) writeFileSync(out, JSON.stringify(result, null, 1));
console.log(JSON.stringify({ first_ms: result.first_ms, init_ms: initMs, cpu_ms: result.cpu_ms, events: events.length }));
