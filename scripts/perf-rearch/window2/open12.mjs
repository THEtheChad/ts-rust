#!/usr/bin/env node
// Window 2: the first request after 12 didOpens (hono's standard session, ND1). Spawns the server, initializes, opens 12
// files (pickFiles, as the parity session), then pulls diagnostics of each in turn. One JSON line:
// { first_ms, second_ms, all12_ms, init_ms, cpu_ms, maxrss_mb, items }. first_ms is the latency of the first
// textDocument/diagnostic after the didOpens, the number ND1 saw at 0.65-1.05 s (Go 0.12 s). ATA is off (the parity
// session's setting: its npm call is a network race), unless --ata.
//   node scripts/perf-rearch/window2/open12.mjs <server binary> <project dir> [--ata] [--files N]
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pickFiles } from "../vendor/lspdiff/project.mjs";
import { openFiles, startServer } from "../vendor/lspdiff/session.mjs";

const args = process.argv.slice(2);
const ata = args.includes("--ata");
if (ata) args.splice(args.indexOf("--ata"), 1);
const nAt = args.indexOf("--files");
const count = nAt >= 0 ? Number(args.splice(nAt, 2)[1]) : 12;
const [bin, root] = args;
if (!bin || !root) {
  console.error("usage: node scripts/perf-rearch/window2/open12.mjs <server binary> <project dir> [--ata] [--files N]");
  process.exit(2);
}
process.env.GOPORT_LAUNCH = "0"; // the measured process is the server itself, not a launcher
const files = pickFiles(resolve(root), count);
const t0 = performance.now();
const client = await startServer([bin, "--lsp", "--stdio"], resolve(root), { ata });
const init = performance.now() - t0;
const docs = openFiles(client, files);
const lat = [];
const t1 = performance.now();
let items = 0;
for (const d of docs) {
  const r = await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
  lat.push(r.ms);
  items += r.result?.items?.length ?? 0;
}
const all = performance.now() - t1;
const pid = client.proc.pid;
const stat = readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1].split(" ");
const ticks = Number(stat[11]) + Number(stat[12]);
const hwm = /VmHWM:\s+(\d+) kB/.exec(readFileSync(`/proc/${pid}/status`, "utf8"))?.[1];
await client.shutdown();
console.log(JSON.stringify({
  first_ms: lat[0], second_ms: lat[1], all12_ms: all, init_ms: init, cpu_ms: ticks * 10,
  maxrss_mb: hwm ? Number(hwm) / 1024 : null, files: docs.length, items,
}));
