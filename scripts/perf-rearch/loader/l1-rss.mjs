// RSS of a language server after its first load (loader v2 L1: the per-load view is freed
// with the load). Opens one file, pulls its diagnostics, then reads /proc/<pid>/status
// (VmRSS, VmHWM) at each of the given delays after the diagnostics (jemalloc returns freed
// pages over seconds), and shuts the server down.
// usage: node l1-rss.mjs <server bin> <project dir> [--file REL] [--at ms,ms,...]
// Prints one JSON line: { ttfd_ms, samples: [{ at_ms, rss_kb, hwm_kb }] }.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const repo = new URL("../../lspdiff", import.meta.url).pathname;
const { pickFiles } = await import(`${repo}/project.mjs`);
const { openFiles, startServer } = await import(`${repo}/session.mjs`);
const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args.splice(i, 2)[1] : dflt;
};
const fileArg = opt("--file");
const ats = opt("--at", "1000,5000,12000").split(",").map(Number);
const [bin, root] = args;
const file = fileArg ? resolve(root, fileArg) : pickFiles(root, 6)[0];
process.env.GOPORT_LAUNCH = "0";
const t0 = performance.now();
const client = await startServer([bin, "--lsp", "--stdio"], root, { stderr: { write() {} } });
const [doc] = openFiles(client, [file]);
await client.request("textDocument/diagnostic", { textDocument: { uri: doc.uri } });
const ttfd = performance.now() - t0;
const kb = (status, key) => Number((status.match(new RegExp(`^${key}:\\s+(\\d+)`, "m")) || [0, 0])[1]);
const samples = [];
let waited = 0;
for (const at of ats) {
  await new Promise((r) => setTimeout(r, at - waited));
  waited = at;
  const status = readFileSync(`/proc/${client.proc.pid}/status`, "utf8");
  samples.push({ at_ms: at, rss_kb: kb(status, "VmRSS"), hwm_kb: kb(status, "VmHWM") });
}
await client.shutdown();
console.log(JSON.stringify({ ttfd_ms: ttfd, file, samples }));
