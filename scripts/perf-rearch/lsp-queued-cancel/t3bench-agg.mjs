#!/usr/bin/env node
// Aggregates t3bench.sh runs: per cell, kind and side, answered n, cancelled, p50 and p95 latency (ms,
// pooled over runs) and the spread of per-run medians. Diagnostic pulls are split into the edited
// file's and the other files' (from the trace's request ids: the edited file's pull is the one sent
// every key).   node t3bench-agg.mjs <out dir> [--md]
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
const out = process.argv[2];
const q = (s, p) => (s.length ? s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)] : undefined);
const f = (x) => (x === undefined ? "-" : x >= 1000 ? `${(x / 1000).toFixed(2)} s` : x.toFixed(1));
const rows = [];
for (const cell of readdirSync(out).filter((d) => !d.includes(".") && existsSync(join(out, d)) && readdirSync(join(out, d)).length)) {
  const groups = new Map();
  for (const run of readdirSync(join(out, cell)).filter((d) => existsSync(join(out, cell, d, "done")))) {
    const side = run.replace(/-r\d+$/, "");
    const [{ samples }] = JSON.parse(readFileSync(join(out, cell, run, "samples.json"), "utf8"));
    const trace = readdirSync(join(out, cell, run)).find((n) => n.startsWith("trace-"));
    // The edited file's diagnostic pulls: with --trace each sample has its send time; per keystroke
    // the edited pull is the first diagnostic sent after the key.
    let editedIds = new Set();
    if (trace) {
      const t = JSON.parse(readFileSync(join(out, cell, run, trace), "utf8"));
      const diags = t.samples.filter((s) => s.kind === "diagnostic").sort((a, b) => a.start - b.start);
      for (const k of t.keys) {
        const d = diags.find((s) => s.start >= k.at);
        if (d) editedIds.add(d.id);
      }
      for (const s of t.samples) if (s.kind === "diagnostic") s.kind = editedIds.has(s.id) ? "diagnostic edited" : "diagnostic other";
      samples.splice(0, samples.length, ...t.samples);
    }
    const perRun = new Map();
    for (const s of samples) {
      const key = `${s.kind}\t${side}`;
      if (!groups.has(key)) groups.set(key, { cell, kind: s.kind, side, ms: [], cancelled: 0, errors: 0, medians: [] });
      const g = groups.get(key);
      if (s.cancelled) g.cancelled++;
      else if (s.error) g.errors++;
      else {
        g.ms.push(s.ms);
        if (!perRun.has(key)) perRun.set(key, []);
        perRun.get(key).push(s.ms);
      }
    }
    for (const [key, ms] of perRun) groups.get(key).medians.push(q(ms.sort((a, b) => a - b), 0.5));
  }
  for (const g of groups.values()) {
    g.ms.sort((a, b) => a - b);
    g.medians.sort((a, b) => a - b);
    rows.push({ cell: g.cell, kind: g.kind, side: g.side, n: g.ms.length, cancelled: g.cancelled, errors: g.errors, p50: q(g.ms, 0.5), p95: q(g.ms, 0.95), runMedians: g.medians });
  }
}
const order = ["completion", "hover", "diagnostic edited", "diagnostic other", "diagnostic", "semanticTokens", "inlayHint"];
rows.sort((a, b) => a.cell.localeCompare(b.cell) || order.indexOf(a.kind) - order.indexOf(b.kind) || a.side.localeCompare(b.side));
if (process.argv.includes("--md")) {
  console.log("| cell | kind | side | answered | cancelled | p50 ms | p95 ms | run medians |\n|---|---|---|---|---|---|---|---|");
  for (const r of rows) console.log(`| ${r.cell} | ${r.kind} | ${r.side} | ${r.n} | ${r.cancelled}${r.errors ? ` (+${r.errors} err)` : ""} | ${f(r.p50)} | ${f(r.p95)} | ${r.runMedians.map(f).join(", ")} |`);
} else console.log(JSON.stringify(rows, null, 1));
