#!/usr/bin/env node
// Splits the cost of one edit: after each one-character didChange, hover twice in another
// open file (the first hover pays the snapshot update, the second doesn't), then pull the
// edited file's diagnostics. Prints medians over the edits.
//   node scripts/lspdiff/probe-edit.mjs <server binary> <project dir> [options]
// Options, for profiling the edit step:
//   --edits N             number of edits (default 15)
//   --first-only          only the first hover after each edit (no second hover, no diagnostics)
//   --cpu-profile DIR     wrap the edit loop in custom/startCPUProfile and custom/stopCPUProfile
//                         (Go writes a pprof CPU profile to DIR; tsc-rs writes one with no samples)
//   --edit FILE           edit FILE (relative to the project) instead of the first picked file
//   --cpu                 print the server's CPU time per edit over the edit loop, per thread
//                         name (Linux /proc schedstat; the server must be the spawned process)
//   --logs FILE           write the server's window/logMessage lines sent during the edit loop
//                         to FILE (both servers log the snapshot clone, program update and
//                         request times)
//   --window FILE         write the edit loop's window to FILE: { start, end } in ms since the
//                         epoch, and { monoStart, monoEnd } in ms of CLOCK_MONOTONIC (samply's
//                         sample clock on Linux), to cut it out of an external profiler's samples; the
//                         server then gets 10 minutes to exit (a wrapping profiler writes its file)
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pickFiles, pickPositions } from "./project.mjs";
import { append, openFiles, startServer } from "./session.mjs";

const args = process.argv.slice(2);
const option = (name) => {
  const i = args.indexOf(name);
  return i < 0 ? undefined : args.splice(i, 2)[1];
};
const flag = (name) => {
  const i = args.indexOf(name);
  return i >= 0 && args.splice(i, 1).length > 0;
};
const edits = Number(option("--edits") ?? 15);
const cpuProfile = option("--cpu-profile");
const windowFile = option("--window");
const logsFile = option("--logs");
const editFile = option("--edit");
const firstOnly = flag("--first-only");
const cpu = flag("--cpu");
const [bin, root] = args;

const client = await startServer([bin, "--lsp", "--stdio"], root);
const picked = pickFiles(root, 6);
if (editFile) picked.splice(0, 1, resolve(root, editFile));
const docs = openFiles(client, picked);
for (const d of docs) await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
const other = docs[1] ?? docs[0];
const position = pickPositions(other.text, 3).idents[1];
const hover = () => client.request("textDocument/hover", { textDocument: { uri: other.uri }, position });
await hover();
const first = [];
const second = [];
const diagnostics = [];
if (cpuProfile) await client.request("custom/startCPUProfile", { dir: cpuProfile });
// Thread name -> on-CPU ms, summed over the server's threads (/proc/<pid>/task/*/schedstat).
const threadCpu = () => {
  const out = new Map();
  const dir = `/proc/${client.proc.pid}/task`;
  for (const tid of readdirSync(dir)) {
    try {
      const name = readFileSync(`${dir}/${tid}/comm`, "utf8").trim();
      const ns = Number(readFileSync(`${dir}/${tid}/schedstat`, "utf8").split(" ")[0]);
      out.set(name, (out.get(name) ?? 0) + ns / 1e6);
    } catch {} // the thread ended
  }
  return out;
};
const cpuBefore = cpu ? threadCpu() : undefined;
const start = Date.now();
const monoStart = Number(process.hrtime.bigint()) / 1e6;
const firstNotification = client.notifications.length;
for (let i = 0; i < edits; i++) {
  append(client, docs[0], i % 2 ? " " : "x");
  first.push((await hover()).ms);
  if (firstOnly) continue;
  second.push((await hover()).ms);
  diagnostics.push((await client.request("textDocument/diagnostic", { textDocument: { uri: docs[0].uri } })).ms);
}
const end = Date.now();
const cpuAfter = cpu ? threadCpu() : undefined;
const monoEnd = Number(process.hrtime.bigint()) / 1e6;
if (cpuProfile) {
  const r = await client.request("custom/stopCPUProfile", undefined);
  console.log(`cpu profile: ${JSON.stringify(r.result ?? r.error)}`);
}
if (logsFile) {
  const lines = client.notifications.slice(firstNotification).filter((n) => n.method === "window/logMessage");
  writeFileSync(logsFile, lines.map((n) => `${n.at.toFixed(1)}\t${n.params.message}`).join("\n") + "\n");
}
if (windowFile) writeFileSync(windowFile, JSON.stringify({ start, end, monoStart, monoEnd }));
if (cpu) {
  // A thread that ended during the loop is missing from cpuAfter; its time is not counted.
  const rows = [...cpuAfter].map(([name, ms]) => [name, (ms - (cpuBefore.get(name) ?? 0)) / edits]);
  const total = rows.reduce((sum, [, ms]) => sum + ms, 0);
  const shown = rows.filter(([, ms]) => ms >= 0.5).sort((a, b) => b[1] - a[1]);
  console.log(`cpu per edit: total ${total.toFixed(1)} ms; ${shown.map(([n, ms]) => `${n} ${ms.toFixed(1)}`).join(", ")}`);
}
const median = (a) => (a.length ? [...a].sort((x, y) => x - y)[a.length >> 1].toFixed(1) : "-");
console.log(`${bin}: first request after an edit ${median(first)} ms, second ${median(second)} ms, edited file's diagnostics ${median(diagnostics)} ms`);
await client.shutdown(windowFile ? { killAfter: 600_000 } : {});
