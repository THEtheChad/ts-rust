#!/usr/bin/env node
// Splits the cost of one edit: after each one-character didChange, hover twice in another
// open file (the first hover pays the snapshot update, the second doesn't), then pull the
// edited file's diagnostics. Prints medians over 15 edits.
//   node scripts/lspdiff/probe-edit.mjs <server binary> <project dir>
import { pickFiles, pickPositions } from "./project.mjs";
import { append, openFiles, startServer } from "./session.mjs";

const [bin, root] = process.argv.slice(2);
const client = await startServer([bin, "--lsp", "--stdio"], root);
const docs = openFiles(client, pickFiles(root, 6));
for (const d of docs) await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
const other = docs[1] ?? docs[0];
const position = pickPositions(other.text, 3).idents[1];
const hover = () => client.request("textDocument/hover", { textDocument: { uri: other.uri }, position });
const first = [];
const second = [];
const diagnostics = [];
for (let i = 0; i < 15; i++) {
  append(client, docs[0], i % 2 ? " " : "x");
  first.push((await hover()).ms);
  second.push((await hover()).ms);
  diagnostics.push((await client.request("textDocument/diagnostic", { textDocument: { uri: docs[0].uri } })).ms);
}
const median = (a) => [...a].sort((x, y) => x - y)[a.length >> 1].toFixed(1);
console.log(`${bin}: first request after an edit ${median(first)} ms, second ${median(second)} ms, edited file's diagnostics ${median(diagnostics)} ms`);
await client.shutdown();
