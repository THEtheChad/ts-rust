#!/usr/bin/env node
// Compares the textDocument/publishDiagnostics stream of Go's language server and tsc-rs over
// a typing sequence in one file (perf/first-principles A1: a `.tsx` edit that tsc-rs serves by
// reusing the program, where Go builds a new one, must publish what Go publishes).
//
//   node scripts/perf-rearch/a1/pubdiag-stream.mjs --project <dir> --edit <rel file>
//        [--open <rel file>...] [--go <bin>] [--rs <bin>] [--text <typed>] [--settle <ms>]
//
// Each server runs on its own. The session opens the edited file and the --open files, pulls
// the diagnostics of each, then types --text one character per didChange; after each
// character it pulls the edited file's diagnostics and waits --settle ms (default 300) for
// background publishes. It ends with a hover and a last wait. The stream is every
// publishDiagnostics notification in order (uri, version, diagnostics), with the project root
// replaced by <root>. Exit 0 when the two streams are equal, 1 when they differ.
import { writeFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { append, openFiles, startServer } from "../../lspdiff/session.mjs";

const { values: opt } = parseArgs({
  options: {
    project: { type: "string" },
    edit: { type: "string" },
    open: { type: "string", multiple: true, default: [] },
    go: { type: "string", default: join(homedir(), ".local/bin/tsgo-lsp-oracle-3e9236361fa9") },
    rs: { type: "string", default: process.env.TSC_RS_BIN ?? resolve("target/release/tsgo") },
    text: { type: "string", default: "\nexport const typedByPubdiag = <div>{1 + }</div>;\n" },
    settle: { type: "string", default: "300" },
    out: { type: "string", default: join(homedir(), ".cache/perf-rearch/a1/pubdiag") },
  },
});
if (!opt.project || !opt.edit) {
  console.error("usage: pubdiag-stream.mjs --project <dir> --edit <rel file> [options]; see the header");
  process.exit(2);
}
const root = resolve(opt.project);
const settle = Number(opt.settle);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function stream(bin) {
  const client = await startServer([bin, "--lsp", "--stdio"], root);
  const docs = openFiles(client, [resolve(root, opt.edit), ...opt.open.map((f) => resolve(root, f))]);
  for (const d of docs) await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
  await sleep(settle);
  const pulls = [];
  for (const ch of opt.text) {
    append(client, docs[0], ch);
    const r = await client.request("textDocument/diagnostic", { textDocument: { uri: docs[0].uri } });
    pulls.push(JSON.stringify(r.result ?? r.error ?? null).replaceAll(root, "<root>"));
    await sleep(settle);
  }
  await client.request("textDocument/hover", { textDocument: { uri: docs[0].uri }, position: { line: 0, character: 1 } });
  await sleep(settle * 3);
  const published = client.notifications
    .filter((n) => n.method === "textDocument/publishDiagnostics")
    .map((n) => JSON.stringify({ uri: n.params.uri, version: n.params.version, diagnostics: n.params.diagnostics }).replaceAll(root, "<root>"));
  await client.shutdown();
  return { published, pulls };
}

mkdirSync(opt.out, { recursive: true });
const go = await stream(opt.go);
const rs = await stream(opt.rs);
const name = `${root.split("/").pop()}-${opt.edit.replaceAll(/[^A-Za-z0-9.]/g, "_")}`;
writeFileSync(join(opt.out, `${name}.go.json`), JSON.stringify(go, null, 1));
writeFileSync(join(opt.out, `${name}.rs.json`), JSON.stringify(rs, null, 1));
const firstDiff = (a, b) => {
  for (let i = 0; i < Math.max(a.length, b.length); i++) if (a[i] !== b[i]) return i;
  return -1;
};
const pubDiff = firstDiff(go.published, rs.published);
const pullDiff = firstDiff(go.pulls, rs.pulls);
const uris = (s) => [...new Set(s.map((p) => JSON.parse(p).uri))].join(", ");
console.log(`project ${root}, edited ${opt.edit}, ${opt.text.length} keystrokes`);
console.log(`go: ${go.published.length} publishes (${uris(go.published)}); rs: ${rs.published.length} publishes`);
console.log(`publishDiagnostics stream: ${pubDiff < 0 ? "EQUAL" : `DIFFERS at #${pubDiff}:\n  go ${go.published[pubDiff]?.slice(0, 400)}\n  rs ${rs.published[pubDiff]?.slice(0, 400)}`}`);
console.log(`per-keystroke diagnostic pulls: ${pullDiff < 0 ? "EQUAL" : `DIFFER at keystroke ${pullDiff}`}`);
console.log(`streams in ${opt.out}/${name}.{go,rs}.json`);
process.exit(pubDiff < 0 && pullDiff < 0 ? 0 : 1);
