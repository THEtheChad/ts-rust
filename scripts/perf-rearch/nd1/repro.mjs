#!/usr/bin/env node
// ND1: repro of lsp/hono-f12-p6/0057 (rs-schedule): the inlayHint answer of
// runtime-tests/deno/ssg.test.tsx in the hono standard session. Findings: docs/perf-rearch/parity.md, "ATA".
//
//   node scripts/perf-rearch/nd1/repro.mjs --bin <tsgo> [--mode full|session|open12|open1] [--runs 8]
//        [--jobs 1] [--env K=V ...] [--npm-delay S] [--probe] [--logs DIR [--logs-all]] [--verbose] [--full]
//
// Each run starts a server, opens files and asks for the inlay hints of ssg.test.tsx. It prints the
// label of the parameter hint at 50:19 (console.error's first parameter): "...data:" (lib.dom.d.ts,
// Go's cached answer) or "message:" (@types/node console.d.ts, once ATA's typings are in the
// inferred project), and the sha256 (12 hex) of the whole answer. The last line is
// "SUMMARY <label>=<count> ...". Modes:
//   full     the whole standard session (session.mjs runSession, 6 positions), as the parity gate runs it
//   session  the 12 files opened, then diagnostic, semanticTokens and inlayHint of each file up to
//            ssg.test.tsx (no position requests)
//   open12   the 12 files opened, then the inlayHint of ssg.test.tsx at once
//   open1    only ssg.test.tsx opened
// Options:
//   --jobs N        N servers side by side (the gate runs 4 sessions at a time; load widens the race)
//   --env K=V       server environment, for example GOPORT_PARSE_THREADS=1 (the gate's third run)
//   --npm-delay S   fake-npm/npm first on the servers' PATH: ATA's npm call takes S seconds, offline
//   --probe         also ask for the signature help at the console.error call (the overloads)
//   --logs DIR      keep the servers' window/logMessage streams: short modes every run
//                   (DIR/run-<n>.log), full mode the runs whose label is not "...data:"
//                   (DIR/full-<n>.log; --logs-all: every run)
//   --verbose       trace log verbosity (custom/setLogVerbosity): the logs list each project's files
//   --items I,J     full mode: also print the sha of the answers at session indexes I, J (the
//                   NNNN of lsp/hono-f12-p6/NNNN; the references items 178,232,335,595,635 share
//                   the cause) as raw sha / sha with the entries sorted / entry count
//   --ata           full mode: ATA on, as the session ran before ND1 (runSession turns it off)
//   --full          print each whole answer as JSON
//
// Before the fix, `--mode full --ata --npm-delay 0` gives "message:" on Go and tsc-rs alike, and
// `--npm-delay 5` gives "...data:" on both; real npm (about 0.5 s) gives either.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { LspClient } from "../vendor/lspdiff/client.mjs";
import { endPosition, pickFiles } from "../vendor/lspdiff/project.mjs";
import { openFiles, runSession, startServer } from "../vendor/lspdiff/session.mjs";

const { values: opt } = parseArgs({
  options: {
    bin: { type: "string" },
    runs: { type: "string", default: "8" },
    env: { type: "string", multiple: true },
    mode: { type: "string", default: "session" },
    project: { type: "string", default: join(process.env.HOME, ".cache/perf-rearch/parity-projects/repos/honojs__hono") },
    full: { type: "boolean", default: false },
    jobs: { type: "string", default: "1" },
    probe: { type: "boolean", default: false },
    logs: { type: "string" },
    verbose: { type: "boolean", default: false },
    "logs-all": { type: "boolean", default: false },
    "npm-delay": { type: "string" },
    ata: { type: "boolean", default: false },
    items: { type: "string", default: "" },
  },
});
if (!opt.bin) {
  console.error("usage: repro.mjs --bin <tsgo> [--mode full|session|open12|open1] [--runs N] [--jobs N] [--env K=V] [--npm-delay S] [--probe] [--logs DIR [--logs-all]] [--verbose] [--ata] [--full]; see the header");
  process.exit(2);
}
const root = opt.project;
const env = Object.fromEntries((opt.env ?? []).map((kv) => kv.split(/=(.*)/s).slice(0, 2)));
if (opt["npm-delay"] !== undefined) {
  env.PATH = `${join(import.meta.dirname, "fake-npm")}:${process.env.PATH}`;
  env.ND1_NPM_DELAY = opt["npm-delay"];
}
const target = join(root, "runtime-tests/deno/ssg.test.tsx");
const files = opt.mode === "open1" ? [target] : pickFiles(root, 12);
if (!files.includes(target)) throw new Error("ssg.test.tsx is not among the picked files");

const label = (h) => (Array.isArray(h.label) ? h.label.map((p) => p.value).join("") : h.label);
// Full mode hooks LspClient, since runSession owns its client: --ata takes ATA's off switch out
// of initialize, --verbose follows initialized, and right after the ssg.test.tsx inlay hints
// --probe asks the same server for the signature help at the console.error call.
const probes = new WeakMap(); // inlay hint result -> probe text
const clients = new WeakMap(); // inlay hint result -> its client
if (opt.mode === "full") {
  const { request, notify } = LspClient.prototype;
  LspClient.prototype.notify = function (method, params) {
    notify.call(this, method, params);
    if (method === "initialized" && opt.verbose) notify.call(this, "custom/setLogVerbosity", { verbosity: 1 });
  };
  LspClient.prototype.request = function (method, params) {
    if (method === "initialize" && opt.ata) delete params.initializationOptions.userPreferences.unstable.disableAutomaticTypeAcquisition;
    const r = request.call(this, method, params);
    if (method !== "textDocument/inlayHint" || !params.textDocument.uri.endsWith("/ssg.test.tsx")) return r;
    return r.then(async (answer) => {
      if (!answer.result) return answer;
      clients.set(answer.result, this);
      if (opt.probe) {
        const sh = await request.call(this, "textDocument/signatureHelp", { textDocument: params.textDocument, position: { line: 49, character: 19 } });
        probes.set(answer.result, `sigs ${JSON.stringify((sh.result?.signatures ?? []).map((x) => x.label))}`);
      }
      return answer;
    });
  };
}

function writeLog(client, name) {
  mkdirSync(opt.logs, { recursive: true });
  const lines = client.notifications.filter((n) => n.method === "window/logMessage").map((n) => n.params.message);
  writeFileSync(join(opt.logs, name), lines.join("\n") + "\n");
}

const sha = (value) => createHash("sha256").update(JSON.stringify(value ?? null)).digest("hex").slice(0, 12);
// The sha of a list answer with its entries sorted (Go's references order varies run to run).
const sortedSha = (list) => (Array.isArray(list) ? sha(list.map((e) => JSON.stringify(e)).sort()) : sha(list));
const itemIndexes = opt.items ? opt.items.split(",").map(Number) : [];
const itemText = new WeakMap(); // inlay hint result -> " items i:sha/count ..."

async function fullSession() {
  const log = await runSession([opt.bin, "--lsp", "--stdio"], root, files, { positions: 6, env });
  const result = log.find((e) => e.step === "runtime-tests/deno/ssg.test.tsx inlayHint").result;
  if (itemIndexes.length && result) {
    itemText.set(result, ` items ${itemIndexes.map((i) => `${i}:${sha(log[i].result)}/${sortedSha(log[i].result)}/${log[i].result?.length ?? "-"}`).join(" ")}`);
  }
  return result;
}

async function shortSession(run) {
  const client = await startServer([opt.bin, "--lsp", "--stdio"], root, { env });
  if (opt.verbose) client.notify("custom/setLogVerbosity", { verbosity: 1 });
  try {
    const docs = openFiles(client, files);
    const doc = docs.find((d) => d.path === target);
    if (opt.mode === "session") {
      for (const d of docs.slice(0, docs.indexOf(doc) + 1)) {
        await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
        await client.request("textDocument/semanticTokens/full", { textDocument: { uri: d.uri } });
        if (d !== doc) await client.request("textDocument/inlayHint", { textDocument: { uri: d.uri }, range: { start: { line: 0, character: 0 }, end: endPosition(d.text) } });
      }
    }
    const r = await client.request("textDocument/inlayHint", {
      textDocument: { uri: doc.uri },
      range: { start: { line: 0, character: 0 }, end: endPosition(doc.text) },
    });
    if (opt.probe) {
      const sh = await client.request("textDocument/signatureHelp", { textDocument: { uri: doc.uri }, position: { line: 49, character: 19 } });
      const sigs = (sh.result?.signatures ?? []).map((x) => x.label);
      console.log(`probe: active ${sh.result?.activeSignature} of ${JSON.stringify(sigs)}`);
    }
    return r.result;
  } finally {
    await client.shutdown();
    if (opt.logs) writeLog(client, `run-${run}.log`);
  }
}

const counts = {};
let next = 1;
async function worker() {
  for (let run = next++; run <= Number(opt.runs); run = next++) {
    const result = await (opt.mode === "full" ? fullSession() : shortSession(run));
    const hint = (result ?? []).find((h) => h.position.line === 49 && h.position.character === 18);
    const answer = hint ? label(hint) : "<none>";
    if (opt.full) console.log(JSON.stringify(result));
    const client = opt.mode === "full" && result && clients.get(result);
    if (opt.logs && client && (answer !== "...data:" || opt["logs-all"])) writeLog(client, `full-${run}.log`);
    counts[answer] = (counts[answer] ?? 0) + 1;
    console.log(`run ${run}: ${answer} answer ${sha(result)}${(result && itemText.get(result)) ?? ""}${result && probes.has(result) ? ` ${probes.get(result)}` : ""}`);
  }
}
await Promise.all(Array.from({ length: Number(opt.jobs) }, worker));
console.log(`SUMMARY ${Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(" ")}`);
