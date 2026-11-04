#!/usr/bin/env node
// Differential LSP harness: runs the same scripted session against Go's language server and
// tsc-rs, and diffs the answers. See docs/lsp-concurrency.md.
//
//   node scripts/lspdiff/lspdiff.mjs diff  --project <dir> [--files 6] [--positions 4] [--file <rel>...]
//   node scripts/lspdiff/lspdiff.mjs bench --project <dir> [--files 6] [--runs 3] [--interval 120]
//
// Options: --go <bin> (default ~/.local/bin/tsgo-lsp-oracle-3e9236361fa9), --rs <bin>
// (default $TSC_RS_BIN or target/release/tsgo), --out <dir> (default /tmp/lspdiff/out),
// --only go|rs (bench one server), --reuse (diff the answers saved by the last run),
// --pulls all|no-diagnostics|none (background pulls per keystroke), --modes concurrent,one-at-a-time. Each server is run on its own, never both at once.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { runBench, summarize } from "./bench.mjs";
import { pickFiles, rel } from "./project.mjs";
import { runSession } from "./session.mjs";

const { positionals, values: opt } = parseArgs({
  allowPositionals: true,
  options: {
    project: { type: "string" },
    files: { type: "string", default: "6" },
    file: { type: "string", multiple: true },
    positions: { type: "string", default: "4" },
    runs: { type: "string", default: "3" },
    interval: { type: "string", default: "120" },
    go: { type: "string", default: join(homedir(), ".local/bin/tsgo-lsp-oracle-3e9236361fa9") },
    rs: { type: "string", default: process.env.TSC_RS_BIN ?? resolve("target/release/tsgo") },
    out: { type: "string", default: "/tmp/lspdiff/out" },
    only: { type: "string" },
    reuse: { type: "boolean", default: false },
    pulls: { type: "string", default: "all" },
    modes: { type: "string", default: "concurrent,one-at-a-time" },
    verbose: { type: "boolean", default: false },
  },
});
const mode = positionals[0];
if (!["diff", "bench"].includes(mode) || !opt.project) {
  console.error("usage: lspdiff.mjs diff|bench --project <dir> [options]; see the header of this file");
  process.exit(2);
}
const root = resolve(opt.project);
const files = pickFiles(root, Number(opt.files), opt.file);
const servers = { go: [opt.go, "--lsp", "--stdio"], rs: [opt.rs, "--lsp", "--stdio"] };
const stderr = opt.verbose ? process.stderr : undefined;
const outDir = join(opt.out, `${root.split("/").pop()}-${mode}${mode === "bench" && opt.pulls !== "all" ? `-${opt.pulls}` : ""}`);
mkdirSync(outDir, { recursive: true });
console.log(`project ${root}\nfiles: ${files.map((f) => rel(root, f)).join(", ")}`);

// Only what must differ between two runs: timing, the project root, and orders Go itself
// does not keep stable from run to run: completion items with equal sort keys and the files
// of a WorkspaceEdit come out in map order. Editors sort completions by sortText anyway.
function normalize(value, method) {
  const v = JSON.parse(JSON.stringify(value ?? null).replaceAll(root, "<root>"));
  const byJson = (list) => list.map((i) => [JSON.stringify(i), i]).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([, i]) => i);
  if (method === "textDocument/completion" && Array.isArray(v?.result?.items)) v.result.items = byJson(v.result.items);
  if (v?.result?.changes) v.result.changes = Object.fromEntries(Object.entries(v.result.changes).sort(([a], [b]) => (a < b ? -1 : 1)));
  return v;
}

if (mode === "diff") {
  // Go runs twice: an answer on which Go disagrees with itself is "Go-unstable", not a mismatch.
  const runs = { go: servers.go, go2: servers.go, rs: servers.rs };
  const logs = {};
  for (const [name, cmd] of Object.entries(runs)) {
    if (opt.reuse) {
      logs[name] = readFileSync(join(outDir, `${name}.jsonl`), "utf8").trim().split("\n").map((l) => JSON.parse(l));
      continue;
    }
    const start = performance.now();
    logs[name] = await runSession(cmd, root, files, { positions: Number(opt.positions), stderr });
    console.log(`${name}: ${logs[name].length} answers in ${((performance.now() - start) / 1000).toFixed(1)} s`);
    writeFileSync(join(outDir, `${name}.jsonl`), logs[name].map((e) => JSON.stringify(e)).join("\n") + "\n");
  }
  const answer = (e, method) => JSON.stringify(normalize(e && { step: e.step, result: e.result, error: e.error }, method));
  const byMethod = {};
  const mismatches = [];
  const unstable = [];
  const n = Math.max(logs.go.length, logs.rs.length);
  for (let i = 0; i < n; i++) {
    const method = (logs.go[i] ?? logs.rs[i]).method;
    const row = (byMethod[method] ??= { equal: 0, differ: 0, goUnstable: 0 });
    const [g, g2, r] = [logs.go[i], logs.go2[i], logs.rs[i]].map((e) => answer(e, method));
    const entry = { step: (logs.go[i] ?? logs.rs[i]).step, method, go: JSON.parse(g), rs: JSON.parse(r) };
    if (g !== g2) {
      row.goUnstable++;
      unstable.push({ ...entry, rsMatchesAGoRun: r === g || r === g2 });
    } else if (g === r) row.equal++;
    else {
      row.differ++;
      mismatches.push(entry);
    }
  }
  const total = (k) => Object.values(byMethod).reduce((a, b) => a + b[k], 0);
  const [equal, differ, goUnstable] = ["equal", "differ", "goUnstable"].map(total);
  console.log(`\n${equal} of ${equal + differ} Go-stable answers equal (${((100 * equal) / (equal + differ)).toFixed(1)}%); ${goUnstable} more where Go disagrees with itself (tsc-rs matches one Go run on ${unstable.filter((u) => u.rsMatchesAGoRun).length})`);
  console.table(byMethod);
  for (const m of mismatches.slice(0, 20)) console.log(`differs: ${m.step}`);
  for (const m of unstable.slice(0, 10)) console.log(`Go-unstable: ${m.step}`);
  writeFileSync(join(outDir, "mismatches.json"), JSON.stringify(mismatches, null, 1));
  writeFileSync(join(outDir, "go-unstable.json"), JSON.stringify(unstable, null, 1));
  writeFileSync(join(outDir, "summary.json"), JSON.stringify({ project: root, files: files.map((f) => rel(root, f)), total: n, equal, differ, goUnstable, byMethod }, null, 1));
  console.log(`details in ${outDir}`);
} else {
  const results = [];
  for (const [name, cmd] of Object.entries(servers)) {
    if (opt.only && opt.only !== name) continue;
    for (const serial of opt.modes.split(",").map((m) => m === "one-at-a-time")) {
      for (let run = 0; run < Number(opt.runs); run++) {
        const samples = await runBench(cmd, root, files, { serial, interval: Number(opt.interval), pulls: opt.pulls, stderr });
        results.push({ server: name, mode: serial ? "one-at-a-time" : "concurrent", run, samples });
        console.log(`${name} ${serial ? "one-at-a-time" : "concurrent"} run ${run + 1}: ${samples.length} samples`);
      }
    }
  }
  writeFileSync(join(outDir, "samples.json"), JSON.stringify(results));
  const table = summarize(results);
  console.table(table);
  writeFileSync(join(outDir, "summary.json"), JSON.stringify(table, null, 1));
  console.log(`details in ${outDir}`);
}
