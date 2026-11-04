#!/usr/bin/env node
// Drives a typing session in one file of a project against tsc-rs with the reuse verify mode on
// (GOPORT_VERIFY_REUSE in a `diagnostics` feature build, see
// crates/ts_goport/src/program/reuse_verify.rs): every keystroke is a
// didChange, then a completion and a diagnostic pull, so the server updates its program on
// each one and the verify mode checks each reuse against a full build in a child process.
//
//   node scripts/perf-rearch/a1/verify-typing.mjs --bin <tsgo> --project <dir> --edit <rel file>
//        --log <verify log> [--mode 1|all] [--text <typed>] [--open <rel file>...]
//
// Prints the verify log's OK/MISMATCH/ERROR counts; exit 1 on any MISMATCH or ERROR, or when no
// reuse was verified.
import { existsSync, readFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { LspClient, initializeParams } from "../vendor/lspdiff/client.mjs";
import { append, openFiles, removeTail } from "../vendor/lspdiff/session.mjs";

const { values: opt } = parseArgs({
  options: {
    bin: { type: "string" },
    project: { type: "string" },
    edit: { type: "string" },
    open: { type: "string", multiple: true, default: [] },
    log: { type: "string" },
    mode: { type: "string", default: "1" },
    text: { type: "string", default: "\nexport const TypedByVerify = () => <div className=\"a\">{[1, 2].map((n) => <span key={n}>{n}</span>)}</div>;\n" },
  },
});
if (!opt.bin || !opt.project || !opt.edit || !opt.log) {
  console.error("usage: verify-typing.mjs --bin <tsgo> --project <dir> --edit <rel file> --log <file>; see the header");
  process.exit(2);
}
const root = resolve(opt.project);
if (existsSync(opt.log)) rmSync(opt.log);
const client = new LspClient([opt.bin, "--lsp", "--stdio"], {
  cwd: root,
  env: { GOPORT_VERIFY_REUSE: opt.mode, GOPORT_VERIFY_REUSE_LOG: opt.log },
  stderr: undefined,
});
const init = await client.request("initialize", initializeParams(root));
if (init.error) throw new Error(JSON.stringify(init.error));
client.notify("initialized", {});
const docs = openFiles(client, [resolve(root, opt.edit), ...opt.open.map((f) => resolve(root, f))]);
for (const d of docs) await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
const d = docs[0];
for (const ch of opt.text) {
  const position = append(client, d, ch);
  if (ch !== "\n") await client.request("textDocument/completion", { textDocument: { uri: d.uri }, position, context: { triggerKind: 1 } });
  await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
}
// Undo in one change, and a diagnostic pull.
removeTail(client, d, opt.text.length);
await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
// An edit in a second open file, when there is one.
if (docs[1]) {
  append(client, docs[1], "\n");
  await client.request("textDocument/diagnostic", { textDocument: { uri: docs[1].uri } });
}
await client.shutdown();
const lines = existsSync(opt.log) ? readFileSync(opt.log, "utf8").trim().split("\n").filter(Boolean) : [];
const count = (kind) => lines.filter((l) => l.startsWith(`goport verify-reuse: ${kind}`)).length;
const [ok, mismatch, error] = ["OK", "MISMATCH", "ERROR"].map(count);
const ms = lines.map((l) => Number(/(\d+) ms\)/.exec(l)?.[1])).filter((n) => !Number.isNaN(n));
console.log(`${root} ${opt.edit}: ${opt.text.length + 2} changes; verify OK ${ok}, MISMATCH ${mismatch}, ERROR ${error}; verify ms median ${ms.sort((a, b) => a - b)[ms.length >> 1] ?? "-"}`);
for (const l of lines.filter((l) => !l.includes(": OK ")).slice(0, 5)) console.log(`  ${l.slice(0, 600)}`);
process.exit(mismatch || error || ok === 0 ? 1 : 0);
