#!/usr/bin/env node
// Time to first diagnostics after opening a project: spawn the server, initialize, open one file,
// pull its diagnostics. Prints one JSON line: { ttfd_ms, init_ms, diag_ms, cpu_ms, maxrss_mb, file, items }.
// cpu_ms and maxrss_mb are read from /proc/<pid> right after the answer (Linux).
//   node scripts/perf-rearch/lsp-ttfd.mjs <server binary> <project dir> [--file REL]
// The default file is the first of scripts/lspdiff's picked files (the one the edit benchmarks type in).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pickFiles } from "../lspdiff/project.mjs";
import { openFiles, startServer } from "../lspdiff/session.mjs";

const args = process.argv.slice(2);
const fileAt = args.indexOf("--file");
const fileArg = fileAt >= 0 ? args.splice(fileAt, 2)[1] : undefined;
const [bin, root] = args;
if (!bin || !root) {
  console.error("usage: node scripts/perf-rearch/lsp-ttfd.mjs <server binary> <project dir> [--file REL]");
  process.exit(2);
}
const file = fileArg ? resolve(root, fileArg) : pickFiles(root, 6)[0];
const t0 = performance.now();
process.env.GOPORT_LAUNCH = "0"; // the measured process is the server itself, not a launcher
const client = await startServer([bin, "--lsp", "--stdio"], root);
const init = performance.now() - t0;
const [doc] = openFiles(client, [file]);
const r = await client.request("textDocument/diagnostic", { textDocument: { uri: doc.uri } });
const ttfd = performance.now() - t0;
const pid = client.proc.pid;
const stat = readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1].split(" ");
const ticks = Number(stat[11]) + Number(stat[12]); // utime, stime (fields 14, 15)
const hwm = /VmHWM:\s+(\d+) kB/.exec(readFileSync(`/proc/${pid}/status`, "utf8"))?.[1];
await client.shutdown();
console.log(JSON.stringify({
  ttfd_ms: ttfd, init_ms: init, diag_ms: r.ms, cpu_ms: ticks * 10, maxrss_mb: hwm ? Number(hwm) / 1024 : null,
  file, items: r.result?.items?.length ?? null, error: r.error?.message,
}));
