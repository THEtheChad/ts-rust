#!/usr/bin/env node
// Differential LSP harness: runs the same scripted session against Go's language server and
// tsc-rs, and diffs the answers. See docs/lsp-concurrency.md.
//
// Vendored for the parity gate (scripts/perf-rearch/parity.sh) from perf/lsp-concurrency 931fad409,
// with two additions: --go-cache <dir> keeps Go's two sessions (go.jsonl, go2.jsonl) there and reuses
// them when present, so a gate run starts only tsc-rs; and items.json in the out dir lists every
// answer as {i, step, method, status: MATCH | DIFF | GO-BROKEN | JS-AGREES, detail}. The parity gate's later additions:
// --scenario <name> runs a watch scenario of scenario.mjs instead of the standard session (the
// client then advertises dynamic watch registration, and registrations and pushed diagnostics are
// compared at checkpoints), and --rs-runs N runs tsc-rs N times: an answer on which two tsc-rs runs
// differ is a DIFF with detail "rs-nondet", even when one of them matches Go; --rs-env K=V
// (repeatable) adds one more tsc-rs run with that environment (a worker schedule such as
// GOPORT_PARSE_THREADS=1): an answer on which it differs from the first run is "rs-schedule".
// --go-runs N (default 2) runs Go N times when it fills the cache. Each answer is judged in the
// order of the parity gate's referees (docs/perf-rearch/parity.md):
//   1. tsc-rs's determinism: a tsc-rs run that crashed or timed out, or a later run that differs
//      from the first, makes a DIFF (detail "tsc-rs: ...", "rs-nondet", "rs-schedule");
//   2. GO-BROKEN: a Go run crashed or timed out before the answer (client.mjs: a request without an
//      answer in LSPDIFF_REQUEST_TIMEOUT_MS ends the run), or Go's runs differ on it (the watch
//      scenarios use 4 runs: Go's background tasks race as concurrent goroutines). A Go crash is
//      cached ({run}.broken.json beside its log), a timeout is not;
//   3. JS-AGREES (--js, below);
//   4. MATCH when Go's answer equals tsc-rs's, else DIFF.
// With one plain tsc-rs run, an answer that would be GO-BROKEN or JS-AGREES needs one more tsc-rs
// run (rs-confirm) that gives it again, or it is a DIFF rs-nondet.
// --server-logs writes each scenario run's window/logMessage stream to <out>/<run>.server.log.
// --js <tsserver.js> adds the JS referee (referee.mjs, tsserver.mjs): when an answer is a DIFF of a
// refereed kind (and passed step 1), JS tsserver replays the same session once and an
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
  // Go runs --go-runs times (when it fills the cache): an answer on which Go disagrees with itself
  // is GO-BROKEN, not a mismatch (and so is one after a Go crash or timeout).
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
  // name -> { kind: "crash" | "timeout", reason, at }: the run ended (the server exited or a request
  // timed out) after `at` answers. session.mjs and scenario.mjs hand over the answers so far.
  const broken = {};
  const goCache = opt["go-cache"];
  // One server run of the session (or scenario): its log.
  const runOne = (name, cmd, env) =>
    scenario
      ? runScenario(cmd, scenario, root, { files, stderr, env, serverLog: opt["server-logs"] ? join(outDir, `${name}.server.log`) : undefined })
      : runSession(cmd, root, files, { positions: Number(opt.positions), stderr, env });
  // { log, broken }: a crash or a timeout ends the run with the answers it gave.
  const runLogged = async (name, cmd, env) => {
    const start = performance.now();
    let log;
    let fail;
    try {
      log = await runOne(name, cmd, env);
    } catch (e) {
      if (!e.log) throw e;
      log = e.log;
      fail = { kind: e.timeout ? "timeout" : "crash", reason: String(e.message).slice(0, 300), at: e.log.length };
    }
    console.log(`${name}${env ? ` (${JSON.stringify(env)})` : ""}: ${log.length} answers in ${((performance.now() - start) / 1000).toFixed(1)} s${fail ? ` (${fail.kind}: ${fail.reason})` : ""}`);
    return { log, broken: fail };
  };
  const saveLog = (dir, name, log) => {
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `${name}.jsonl.tmp`), log.map((e) => JSON.stringify(e)).join("\n") + "\n");
    renameSync(join(dir, `${name}.jsonl.tmp`), join(dir, `${name}.jsonl`));
  };
  const readLog = (f) => {
    const text = readFileSync(f, "utf8").trim();
    return text ? text.split("\n").map((l) => JSON.parse(l)) : [];
  };
  const readBroken = (dir, name) => (existsSync(join(dir, `${name}.broken.json`)) ? JSON.parse(readFileSync(join(dir, `${name}.broken.json`), "utf8")) : undefined);
  for (const [name, cmd] of Object.entries(runs)) {
    const isGo = name.startsWith("go");
    if (goCache && isGo && existsSync(join(goCache, `${name}.jsonl`))) {
      logs[name] = readLog(join(goCache, `${name}.jsonl`));
      broken[name] = readBroken(goCache, name);
      console.log(`${name}: ${logs[name].length} answers from ${join(goCache, `${name}.jsonl`)}`);
      continue;
    }
    if (opt.reuse) {
      logs[name] = readLog(join(outDir, `${name}.jsonl`));
      broken[name] = readBroken(outDir, name);
      continue;
    }
    const r = await runLogged(name, cmd, envs[name]);
    logs[name] = r.log;
    broken[name] = r.broken;
    if (r.broken) writeFileSync(join(outDir, `${name}.broken.json`), JSON.stringify(r.broken));
    writeFileSync(join(outDir, `${name}.jsonl`), logs[name].map((e) => JSON.stringify(e)).join("\n") + "\n");
    // A crash is Go's answer and is cached; a timeout depends on the load and is not.
    if (goCache && isGo && r.broken?.kind !== "timeout") {
      mkdirSync(goCache, { recursive: true });
      if (r.broken) writeFileSync(join(goCache, `${name}.broken.json`), JSON.stringify(r.broken));
      saveLog(goCache, name, logs[name]);
    }
  }
  const answer = (e, method) => JSON.stringify(normalize(e && { step: e.step, result: e.result, error: e.error }, method));
  const goNames = Object.keys(logs).filter((k) => /^go\d*$/.test(k));
  const rsNames = Object.keys(logs).filter((k) => !goNames.includes(k));
  const ended = (k, i) => i >= logs[k].length; // run k ended before answer i
  const why = (k) => (broken[k] ? `${k} ${broken[k].kind === "timeout" ? "timed out" : "crashed"} after ${broken[k].at} answers (${broken[k].reason})` : `${k} has no answer here`);
  const byMethod = {};
  const mismatches = [];
  const unstable = [];
  const items = [];
  const n = Math.max(...Object.values(logs).map((l) => l.length));
  for (let i = 0; i < n; i++) {
    const first = Object.values(logs).map((l) => l[i]).find(Boolean);
    const { step, method } = first;
    const row = (byMethod[method] ??= { equal: 0, differ: 0, goBroken: 0 });
    const diff = (detail, extra = {}) => {
      items.push({ i, step, method, status: "DIFF", ...(detail ? { detail } : {}) });
      mismatches.push({ step, method, ...(detail ? { detail } : {}), ...extra });
      row.differ++;
    };
    // Step 1, tsc-rs's determinism: a tsc-rs run that crashed or timed out, or a later run that
    // differs from the first, is a DIFF whatever Go does.
    const rsEnded = rsNames.filter((k) => ended(k, i));
    if (rsEnded.length) {
      diff(rsEnded.includes("rs") ? `tsc-rs: ${why("rs")}` : `rs-nondet: ${rsEnded.map(why).join("; ")}`);
      continue;
    }
    const r = answer(logs.rs[i], method);
    const nondet = [];
    for (let k = 2; k <= rsRuns; k++) if (answer(logs[`rs${k}`][i], method) !== r) nondet.push(`rs${k}`);
    const schedule = Object.keys(envs).filter((k) => answer(logs[k][i], method) !== r);
    if (nondet.length || schedule.length) {
      const detail = [
        nondet.length && `rs-nondet: run 1 differs from ${nondet.join(", ")}`,
        schedule.length && `rs-schedule: run 1 differs from ${schedule.map((k) => JSON.stringify(envs[k])).join(", ")}`,
      ].filter(Boolean).join("; ");
      diff(detail, { rs: JSON.parse(r), others: Object.fromEntries([...nondet, ...schedule].map((k) => [k, JSON.parse(answer(logs[k][i], method))])) });
      continue;
    }
    // Step 2, GO-BROKEN: a Go run crashed or timed out before this answer, or Go's runs differ.
    const goAnswers = goNames.filter((k) => !ended(k, i)).map((k) => [k, answer(logs[k][i], method)]);
    const groups = new Map();
    for (const [k, a] of goAnswers) groups.set(a, [...(groups.get(a) ?? []), k]);
    const goEnded = goNames.filter((k) => ended(k, i));
    if (goEnded.length || groups.size > 1) {
      const detail = [goEnded.length && goEnded.map(why).join("; "), groups.size > 1 && `Go runs differ (${[...groups.values()].map((g) => g.join(",")).join(" | ")})`].filter(Boolean).join("; ");
      items.push({ i, step, method, status: "GO-BROKEN", detail });
      row.goBroken++;
      unstable.push({ step, method, detail, rs: JSON.parse(r), rsEqualsGoRuns: groups.get(r) ?? [], go: Object.fromEntries([...groups].map(([a, ks]) => [ks.join(","), JSON.parse(a)])) });
      continue;
    }
    const g = goAnswers[0][1];
    if (g === r) {
      items.push({ i, step, method, status: "MATCH" });
      row.equal++;
    } else diff(undefined, { go: JSON.parse(g), rs: JSON.parse(r) });
  }
  // A tsc-rs run that ended early also gets an item of its own, so the crash is listed even where
  // Go ended at the same answer.
  for (const k of rsNames.filter((k) => broken[k])) {
    items.push({ i: n, step: `${k} server`, method: "server/exit", status: "DIFF", detail: `tsc-rs ${why(k)}` });
  }
  // Step 3, JS-AGREES (referee.mjs): JS's verdict on each DIFF of a refereed kind.
  let jsRef;
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
      const { log } = await runLogged("js", { tsserver: opt.js });
      writeFileSync(join(outDir, "js.jsonl"), log.map((e) => JSON.stringify(e)).join("\n") + "\n");
      if (jsCache) saveLog(jsCache, "js", log);
      return log;
    };
    const kinds = opt["js-kinds"] !== undefined ? new Set(opt["js-kinds"].split(",").filter(Boolean)) : undefined;
    jsRef = await applyJsReferee({ items, logs, root, runJs, all: opt["js-all"], kinds });
  }
  // tsc-rs's determinism comes before every referee status: with one plain tsc-rs run, an answer
  // that is GO-BROKEN or that JS agrees with needs one more tsc-rs run that gives it again.
  const agreeing = new Set(jsRef?.agreeing ?? []);
  const pending = items.filter((it) => it.status === "GO-BROKEN" || agreeing.has(it));
  if (pending.length && rsRuns === 1 && !Object.keys(envs).length) {
    const confirm = await runLogged("rs-confirm", servers.rs);
    for (const it of pending) {
      const same = it.i < confirm.log.length && answer(confirm.log[it.i], it.method) === answer(logs.rs[it.i], it.method);
      if (same) continue;
      if (it.status === "GO-BROKEN") move(it.method, "goBroken", "differ");
      it.detail = `rs-nondet: the run made to confirm ${it.status === "GO-BROKEN" ? "GO-BROKEN" : "JS-AGREES"} differs from run 1${it.status === "GO-BROKEN" ? ` (${it.detail})` : ""}`;
      it.status = "DIFF";
      agreeing.delete(it);
    }
  }
  for (const it of agreeing) {
    const k = it.js.points.length + (it.js.morePoints ?? 0);
    it.status = "JS-AGREES";
    it.detail = `JS sides with tsc-rs on ${k} point${k === 1 ? "" : "s"}: ${it.js.points.slice(0, 3).join("; ")}`;
    move(it.method, "differ", "jsAgrees");
  }
  if (jsRef) {
    writeFileSync(join(outDir, "js.json"), JSON.stringify({ ...jsRef, agreeing: undefined, decided: agreeing.size }, null, 1));
    if (jsRef.ran) console.log(`js referee: ${agreeing.size} answers JS-AGREES`);
  }
  function move(method, from, to) {
    byMethod[method][from]--;
    byMethod[method][to] = (byMethod[method][to] ?? 0) + 1;
  }
  const total = (k) => Object.values(byMethod).reduce((a, b) => a + b[k], 0);
  const [equal, differ, goBroken] = ["equal", "differ", "goBroken"].map(total);
  console.log(`\n${equal} of ${equal + differ} answers with a stable Go equal (${((100 * equal) / (equal + differ)).toFixed(1)}%); ${goBroken} more where Go is broken (tsc-rs matches one Go run on ${unstable.filter((u) => u.rsEqualsGoRuns.length).length})`);
  console.table(byMethod);
  for (const m of mismatches.slice(0, 20)) console.log(`differs: ${m.step}`);
  for (const m of unstable.slice(0, 10)) console.log(`Go-broken: ${m.step}`);
  writeFileSync(join(outDir, "items.json"), JSON.stringify(items, null, 0));
  writeFileSync(join(outDir, "mismatches.json"), JSON.stringify(mismatches, null, 1));
  writeFileSync(join(outDir, "go-broken.json"), JSON.stringify(unstable, null, 1));
  writeFileSync(join(outDir, "summary.json"), JSON.stringify({ project: root, files: files.map((f) => rel(root, f)), total: n, equal, differ, goBroken, byMethod }, null, 1));
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
