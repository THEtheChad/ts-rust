// One first load for the observation-to-watch window (l1-window.py): starts the server with
// client-side file watching (it answers registerCapability), opens one file, pulls its
// diagnostics, waits for the watch registration that follows the load, and shuts down. The
// server writes the observation log (GOPORT_LOAD_OBS_LOG, from the environment).
// usage: node l1-window-run.mjs <server bin> <project dir> [--file REL] [--wait ms]
import { resolve } from "node:path";
const repo = new URL("../vendor/lspdiff", import.meta.url).pathname;
const { pickFiles } = await import(`${repo}/project.mjs`);
const { openFiles, startServer } = await import(`${repo}/session.mjs`);
const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args.splice(i, 2)[1] : dflt;
};
const fileArg = opt("--file");
const wait = Number(opt("--wait", "3000"));
const [bin, root] = args;
const file = fileArg ? resolve(root, fileArg) : pickFiles(root, 6)[0];
process.env.GOPORT_LAUNCH = "0";
const client = await startServer([bin, "--lsp", "--stdio"], root, { watch: true, stderr: { write() {} } });
const [doc] = openFiles(client, [file]);
await client.request("textDocument/diagnostic", { textDocument: { uri: doc.uri } });
await new Promise((r) => setTimeout(r, wait));
await client.shutdown();
console.log(JSON.stringify({ file }));
