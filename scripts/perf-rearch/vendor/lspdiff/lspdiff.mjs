#!/usr/bin/env node
// Differential LSP harness: runs the same scripted session against Go's language server and
// tsc-rs, and diffs the answers. See docs/lsp-concurrency.md.
//
// Vendored for the parity gate (scripts/perf-rearch/parity.sh) from perf/lsp-concurrency 931fad409,
// with two additions: --go-cache <dir> keeps Go's two sessions (go.jsonl, go2.jsonl) there and reuses
// them when present, so a gate run starts only tsc-rs; and items.json in the out dir lists every
// answer as {i, step, method, status: MATCH | DIFF | GO-UNSTABLE}. The parity gate's later additions:
// --scenario <name> runs a watch scenario of scenario.mjs instead of the standard session (the
// client then advertises dynamic watch registration, and registrations and pushed diagnostics are
// compared at checkpoints), and --rs-runs N runs tsc-rs N times: an answer on which two tsc-rs runs
// differ is a DIFF with detail "rs-nondet", even when one of them matches Go; --rs-env K=V
// (repeatable) adds one more tsc-rs run with that environment (a worker schedule such as
// GOPORT_PARSE_THREADS=1): an answer on which it differs from the first run is "rs-schedule".
// --go-runs N (default 2) runs Go N times when it fills the cache: an answer on which any Go run
// differs from the first is GO-UNSTABLE (the watch scenarios use 4: Go's background tasks run as
// concurrent goroutines, so its registrations can race).
// --server-logs writes each scenario run's window/logMessage stream to <out>/<run>.server.log.
// --js <tsserver.js> adds the JS referee (referee.mjs, tsserver.mjs): when an answer is a DIFF of a
// refereed kind (and tsc-rs agreed with itself), JS tsserver replays the same session once and an
// answer on which it sides with tsc-rs on every differing point is JS-AGREES. --js-cache <dir> keeps
// its log (js.jsonl) there; --js-all runs JS on every session and records JS's view of every item
// and the calibration (js.json: per kind, how often JS equals Go where tsc-rs equals Go).
// --js-kinds m1,m2 replaces the refereed kinds (referee.mjs REFEREED_KINDS; the self-test uses it).
//
//   node scripts/lspdiff/lspdiff.mjs diff  --project <dir> [--files 6] [--positions 4] [--file <rel>...]
//                                          [--scenario <name>] [--rs-runs 1] [--rs-env K=V...] [--go-cache <dir>]
//   node scripts/lspdiff/lspdiff.mjs bench --project <dir> [--files 6] [--runs 3] [--interval 120]
//
// Options: --go <bin> (default ~/.local/bin/tsgo-lsp-oracle-3e9236361fa9), --rs <bin>
// (default $TSC_RS_BIN or target/release/tsgo), --out <dir> (default /tmp/lspdiff/out),
// --only go|rs (bench one server), --reuse (diff the answers saved by the last run),
// --pulls all|no-diagnostics|none (background pulls per keystroke), --modes concurrent,one-at-a-time. Each server is run on its own, never both at once.
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { runBench, summarize } from "./bench.mjs";
import { pickFiles, rel } from "./project.mjs";
import { SCENARIOS, runScenario } from "./scenario.mjs";
import { applyJsReferee } from "./referee.mjs";
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
    "go-cache": { type: "string" },
    scenario: { type: "string" },
    "rs-runs": { type: "string", default: "1" },
    "go-runs": { type: "string", default: "2" },
    "rs-env": { type: "string", multiple: true },
    "server-logs": { type: "boolean", default: false },
    js: { type: "string" },
    "js-cache": { type: "string" },
    "js-all": { type: "boolean", default: false },
    "js-kinds": { type: "string" },
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
const scenario = opt.scenario;
if (scenario && !SCENARIOS[scenario]) {
  console.error(`unknown scenario ${scenario}; known: ${Object.keys(SCENARIOS).join(", ")}`);
  process.exit(2);
}
// A fixture scenario writes its project into --project itself, so there is nothing to pick yet.
const files = scenario && SCENARIOS[scenario].fixture ? [] : pickFiles(root, Number(opt.files), opt.file);
const servers = { go: [opt.go, "--lsp", "--stdio"], rs: [opt.rs, "--lsp", "--stdio"] };
const stderr = opt.verbose ? process.stderr : undefined;
const outDir = join(opt.out, `${root.split("/").pop()}-${mode}${mode === "bench" && opt.pulls !== "all" ? `-${opt.pulls}` : ""}${scenario ? `-${scenario}` : ""}`);
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
  const goRuns = Number(opt["go-runs"]);
  const runs = { go: servers.go };
  for (let k = 2; k <= goRuns; k++) runs[`go${k}`] = servers.go;
  runs.rs = servers.rs;
  const rsRuns = Number(opt["rs-runs"]);
  for (let k = 2; k <= rsRuns; k++) runs[`rs${k}`] = servers.rs;
  // Extra tsc-rs runs, each with its own environment: name -> env.
  const envs = {};
  for (const [k, kv] of (opt["rs-env"] ?? []).entries()) {
    const eq = kv.indexOf("=");
    if (eq < 1) throw new Error(`--rs-env wants K=V, got ${kv}`);
    envs[`rs-env${k + 1}`] = { [kv.slice(0, eq)]: kv.slice(eq + 1) };
    runs[`rs-env${k + 1}`] = servers.rs;
  }
  const logs = {};
  const goCache = opt["go-cache"];
  // One server run of the session (or scenario): its log.
  const runOne = (name, cmd, env) =>
    scenario
      ? runScenario(cmd, scenario, root, { files, stderr, env, serverLog: opt["server-logs"] ? join(outDir, `${name}.server.log`) : undefined })
      : runSession(cmd, root, files, { positions: Number(opt.positions), stderr, env });
  const saveLog = (dir, name, log) => {
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `${name}.jsonl.tmp`), log.map((e) => JSON.stringify(e)).join("\n") + "\n");
    renameSync(join(dir, `${name}.jsonl.tmp`), join(dir, `${name}.jsonl`));
  };
  const readLog = (f) => readFileSync(f, "utf8").trim().split("\n").map((l) => JSON.parse(l));
  for (const [name, cmd] of Object.entries(runs)) {
    const isGo = name.startsWith("go");
    if (goCache && isGo) {
      const f = join(goCache, `${name}.jsonl`);
      if (existsSync(f)) {
        logs[name] = readFileSync(f, "utf8").trim().split("\n").map((l) => JSON.parse(l));
        console.log(`${name}: ${logs[name].length} answers from ${f}`);
        continue;
      }
    }
    if (opt.reuse) {
      logs[name] = readFileSync(join(outDir, `${name}.jsonl`), "utf8").trim().split("\n").map((l) => JSON.parse(l));
      continue;
    }
    const start = performance.now();
    const env = envs[name];
    logs[name] = await runOne(name, cmd, env);
    console.log(`${name}${env ? ` (${JSON.stringify(env)})` : ""}: ${logs[name].length} answers in ${((performance.now() - start) / 1000).toFixed(1)} s`);
    writeFileSync(join(outDir, `${name}.jsonl`), logs[name].map((e) => JSON.stringify(e)).join("\n") + "\n");
    if (goCache && isGo) saveLog(goCache, name, logs[name]);
  }
  const answer = (e, method) => JSON.stringify(normalize(e && { step: e.step, result: e.result, error: e.error }, method));
  const byMethod = {};
  const mismatches = [];
  const unstable = [];
  const items = [];
  const n = Math.max(...Object.values(logs).map((l) => l.length));
  for (let i = 0; i < n; i++) {
    const first = Object.values(logs).map((l) => l[i]).find(Boolean);
    const method = first.method;
    const row = (byMethod[method] ??= { equal: 0, differ: 0, goUnstable: 0 });
    const [g, r] = [logs.go[i], logs.rs[i]].map((e) => answer(e, method));
    // g2: the first Go run that differs from the first one (or the second run when all agree).
    let g2 = goRuns > 1 ? answer(logs.go2[i], method) : g;
    for (let k = 3; k <= goRuns && g2 === g; k++) g2 = answer(logs[`go${k}`][i], method);
    const entry = { step: first.step, method, go: JSON.parse(g), rs: JSON.parse(r) };
    // Later tsc-rs runs: one that differs from the first makes the answer rs-nondet.
    const nondet = [];
    for (let k = 2; k <= rsRuns; k++) if (answer(logs[`rs${k}`][i], method) !== r) nondet.push(`rs${k}`);
    const schedule = Object.keys(envs).filter((k) => answer(logs[k][i], method) !== r);
    if (nondet.length || schedule.length) {
      const detail = [
        nondet.length && `rs-nondet: run 1 differs from ${nondet.join(", ")}`,
        schedule.length && `rs-schedule: run 1 differs from ${schedule.map((k) => JSON.stringify(envs[k])).join(", ")}`,
      ].filter(Boolean).join("; ");
      items.push({ i, step: entry.step, method, status: "DIFF", detail });
      mismatches.push({ ...entry, detail, others: Object.fromEntries([...nondet, ...schedule].map((k) => [k, JSON.parse(answer(logs[k][i], method))])) });
      row.differ++;
      continue;
    }
    items.push({ i, step: entry.step, method, status: g !== g2 ? "GO-UNSTABLE" : g === r ? "MATCH" : "DIFF" });
    if (g !== g2) {
      row.goUnstable++;
      unstable.push({ ...entry, rsMatchesAGoRun: r === g || r === g2 });
    } else if (g === r) row.equal++;
    else {
      row.differ++;
      mismatches.push(entry);
    }
  }
  if (opt.js) {
    const jsCache = opt["js-cache"];
    const runJs = async () => {
      const cached = jsCache && join(jsCache, "js.jsonl");
      if (cached && existsSync(cached)) {
        const log = readLog(cached);
        console.log(`js: ${log.length} answers from ${cached}`);
        return log;
      }
      if (opt.reuse && existsSync(join(outDir, "js.jsonl"))) return readLog(join(outDir, "js.jsonl"));
      const start = performance.now();
      const log = await runOne("js", { tsserver: opt.js });
      console.log(`js: ${log.length} answers in ${((performance.now() - start) / 1000).toFixed(1)} s`);
      writeFileSync(join(outDir, "js.jsonl"), log.map((e) => JSON.stringify(e)).join("\n") + "\n");
      if (jsCache) saveLog(jsCache, "js", log);
      return log;
    };
    // tsc-rs's determinism comes before JS: with one plain tsc-rs run, an answer that would become
    // JS-AGREES needs one more run that gives it again.
    const rerunRs = rsRuns === 1 && !Object.keys(envs).length
      ? async () => {
          const start = performance.now();
          const log = await runOne("rs-confirm", servers.rs);
          console.log(`rs-confirm: ${log.length} answers in ${((performance.now() - start) / 1000).toFixed(1)} s`);
          return log;
        }
      : undefined;
    const kinds = opt["js-kinds"] !== undefined ? new Set(opt["js-kinds"].split(",").filter(Boolean)) : undefined;
    const ref = await applyJsReferee({ items, logs, root, runJs, rerunRs, answerKey: answer, all: opt["js-all"], kinds });
    for (const it of items) {
      if (it.status !== "JS-AGREES") continue;
      byMethod[it.method].differ--;
      byMethod[it.method].jsAgrees = (byMethod[it.method].jsAgrees ?? 0) + 1;
    }
    writeFileSync(join(outDir, "js.json"), JSON.stringify(ref, null, 1));
    if (ref.ran) console.log(`js referee: ${ref.decided} answers JS-AGREES`);
  }
  const total = (k) => Object.values(byMethod).reduce((a, b) => a + b[k], 0);
  const [equal, differ, goUnstable] = ["equal", "differ", "goUnstable"].map(total);
  console.log(`\n${equal} of ${equal + differ} Go-stable answers equal (${((100 * equal) / (equal + differ)).toFixed(1)}%); ${goUnstable} more where Go disagrees with itself (tsc-rs matches one Go run on ${unstable.filter((u) => u.rsMatchesAGoRun).length})`);
  console.table(byMethod);
  for (const m of mismatches.slice(0, 20)) console.log(`differs: ${m.step}`);
  for (const m of unstable.slice(0, 10)) console.log(`Go-unstable: ${m.step}`);
  writeFileSync(join(outDir, "items.json"), JSON.stringify(items, null, 0));
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
