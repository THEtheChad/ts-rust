// ttfd with the server's own log lines (window/logMessage) and stderr, timestamped from spawn.
// usage: node ttfd-logs.mjs <server cmd...> -- <project dir> [--file REL]
// The server command may be a wrapper (samply). Prints one JSON line:
// { init_ms, diag_ms, ttfd_ms, cpu_ms, logs: [[ms, line]], stderr }.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const repo = new URL("../../lspdiff", import.meta.url).pathname;
const { pickFiles } = await import(`${repo}/project.mjs`);
const { openFiles, startServer } = await import(`${repo}/session.mjs`);
const args = process.argv.slice(2);
const sep = args.indexOf("--");
const cmd = args.slice(0, sep);
const rest = args.slice(sep + 1);
const fileAt = rest.indexOf("--file");
const fileArg = fileAt >= 0 ? rest.splice(fileAt, 2)[1] : undefined;
const root = rest[0];
const file = fileArg ? resolve(root, fileArg) : pickFiles(root, 6)[0];
let stderr = "";
const sink = { write: (c) => { stderr += `[${(performance.now() - t0).toFixed(1)}] ` + c.toString(); } };
process.env.GOPORT_LAUNCH = "0";
const t0 = performance.now();
const client = await startServer([...cmd, "--lsp", "--stdio"], root, { stderr: sink });
const init = performance.now() - t0;
const tOpen = performance.now() - t0;
const [doc] = openFiles(client, [file]);
const r = await client.request("textDocument/diagnostic", { textDocument: { uri: doc.uri } });
const ttfd = performance.now() - t0;
let cpu = null;
try {
  const stat = readFileSync(`/proc/${client.proc.pid}/stat`, "utf8").split(") ")[1].split(" ");
  cpu = (Number(stat[11]) + Number(stat[12])) * 10;
} catch {}
await client.shutdown();
const logs = client.notifications
  .filter((n) => n.method === "window/logMessage")
  .map((n) => [Number((n.at - t0).toFixed(1)), n.params.message.slice(0, 20000)]);
console.log(JSON.stringify({ init_ms: init, open_ms: tOpen, diag_ms: r.ms, ttfd_ms: ttfd, cpu_ms: cpu, items: r.result?.items?.length, file, logs, stderr: stderr.slice(0, 8000) }));
