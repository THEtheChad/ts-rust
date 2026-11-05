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
// --go-runs N (default 2) runs Go N times when it fills the cache. Answers are aligned across runs by
// request identity (the step name and its occurrence; session.mjs asks some requests only after
// some answers), and a request that only some runs were asked is an answer of its own ("not
// asked"). Each answer is judged in the order of the parity gate's referees
// (docs/perf-rearch/parity.md):
//   1. tsc-rs's determinism: a tsc-rs run that crashed or timed out, or a later run that differs
//      from the first (in its answer or in whether it was asked), makes a DIFF (detail "tsc-rs: ...",
//      "rs-nondet", "rs-schedule"; rsFailure "crash" or "timeout" for the first);
//   2. GO-BROKEN: a Go run crashed or timed out before the answer (client.mjs: a request without an
//      answer in LSPDIFF_REQUEST_TIMEOUT_MS ends the run), or Go's runs differ on it (the watch
//      scenarios use 4 runs: Go's background tasks race as concurrent goroutines). A Go crash is
//      cached ({run}.broken.json beside its log), a timeout is not;
//   3. JS-AGREES (--js, below);
//   4. MATCH when Go's answer equals tsc-rs's, else DIFF ("only Go was asked this request" or "only
//      tsc-rs was asked" where one side's script took another branch).
// With one plain tsc-rs run, an answer that would be GO-BROKEN or JS-AGREES needs one more tsc-rs
// run (rs-confirm). That run is a complete tsc-rs run: every answer of it (MATCH ones too) is
// compared with run 1, and an answer it lacks or changes, a request it adds, and its crash or
// timeout are DIFFs (rs-nondet).
// Lane H4: a request's identity is its step name, its method and its canonical params (keys sorted,
// the root masked), with its occurrence among equal ones; and every run must ask the requests it
// shares with the merged order in that order (a run that does not is a DIFF "<run> request order" for
// tsc-rs, GO-BROKEN for Go, and no evidence for JS). Every server run ends with a session result
// (sandbox.mjs: exit code or signal, stderr crash lines, inputs changed, shutdown), and the item
// "session end" judges them all: DIFF (rsFailure crash, or input when it changed its inputs) when a
// tsc-rs run's is not clean, GO-BROKEN when a Go run's is not; a JS run whose session is not clean is
// no evidence. A Go cache entry without its session results is incomplete and runs again.
// --go-stage <dir> / --js-stage <dir>: where new Go and JS logs are written (parity_lsp.py publishes
// them into --go-cache / --js-cache once the tier verified clean); without one, they are not kept.
// `node lspdiff.mjs info` prints the settings (settings.mjs) and the scenarios' ATA setup as JSON.
// --server-logs writes each scenario run's window/logMessage stream to <out>/<run>.server.log.
// --js <tsserver.js> adds the JS referee (referee.mjs, tsserver.mjs): when an answer is a DIFF of a
// refereed kind (and passed step 1), JS tsserver replays the same session once and an
// answer on which it sides with tsc-rs on every differing point is JS-AGREES. A tsserver answer that
// failed (tsserver.mjs: success:false, malformed, missing, a crash) is kept as failed and is never
// evidence; past a JS crash there is no JS answer. --js-cache <dir> keeps its log (js.jsonl, with the
// failed answers, and js.broken.json when the JS run crashed) there; --js-all runs JS on every session and records JS's view of every item
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
import { SCENARIOS, runScenario, scenarioInfo } from "./scenario.mjs";
import { applyJsReferee } from "./referee.mjs";
import { runSession } from "./session.mjs";
import { SETTINGS } from "./settings.mjs";

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
    "go-stage": { type: "string" },
    "js-stage": { type: "string" },
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
if (mode === "info") {
  console.log(JSON.stringify({ settings: SETTINGS, scenarios: scenarioInfo() }));
  process.exit(0);
}
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
writeFileSync(join(outDir, "settings.json"), JSON.stringify(SETTINGS));
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

// A value as canonical JSON: object keys sorted, the project root masked.
function canon(v) {
  const sort = (x) => (Array.isArray(x) ? x.map(sort) : x && typeof x === "object" ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, sort(x[k])])) : x);
  return JSON.stringify(sort(JSON.parse(JSON.stringify(v ?? null).replaceAll(root, "<root>"))));
}

// The identity of each request of a log (lane H4): its step name, method and canonical params, and how
// many requests with that identity came before it in the same log.
function requestKeys(log) {
  const seen = new Map();
  return log.map((e) => {
    const id = `${e.step}\u0000${e.method}\u0000${canon(e.params)}`;
    const n = seen.get(id) ?? 0;
    seen.set(id, n + 1);
    return `${n}\u0000${id}`;
  });
}

// Where a log asks the requests it shares with `order` in another relative order: the first such pair
// as text, or null.
function orderBreak(order, log) {
  const index = new Map(order.map((k, i) => [k, i]));
  let last = -1;
  let lastStep;
  for (const [i, k] of requestKeys(log).entries()) {
    const p = index.get(k);
    if (p === undefined) continue;
    if (p < last) return `it asked ${JSON.stringify(log[i].step)} after ${JSON.stringify(lastStep)}`;
    [last, lastStep] = [p, log[i].step];
  }
  return null;
}

// The request identities of all logs in one order: the first log's, with each request that only a
// later log has placed after the request that precedes it in that log.
function mergeOrder(logs) {
  const order = [];
  let index = new Map();
  for (const log of logs) {
    let cursor = -1;
    for (const k of requestKeys(log)) {
      if (index.has(k)) {
        cursor = index.get(k);
        continue;
      }
      order.splice(cursor + 1, 0, k);
      cursor++;
      index = new Map(order.map((x, i) => [x, i]));
    }
  }
  return order;
}

// A log as an array over `order`: element p is the log's answer to request order[p], or undefined.
function alignTo(order, log) {
  const keys = requestKeys(log);
  const byKey = new Map(keys.map((k, i) => [k, log[i]]));
  return order.map((k) => byKey.get(k));
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
  // { log, broken, session }: a crash or a timeout ends the run with the answers it gave; session is
  // the server's result (sandbox.mjs), mandatory for every run.
  const runLogged = async (name, cmd, env) => {
    const start = performance.now();
    let log;
    let fail;
    let session;
    try {
      ({ log, session } = await runOne(name, cmd, env));
    } catch (e) {
      if (!e.log) throw e;
      log = e.log;
      session = e.session ?? { clean: false, why: [`no session result (${String(e.message).slice(0, 200)})`] };
      fail = { kind: e.timeout ? "timeout" : "crash", reason: String(e.message).slice(0, 300), at: e.log.length };
    }
    console.log(`${name}${env ? ` (${JSON.stringify(env)})` : ""}: ${log.length} answers in ${((performance.now() - start) / 1000).toFixed(1)} s${fail ? ` (${fail.kind}: ${fail.reason})` : ""}${session.clean ? "" : `; session: ${session.why.join("; ")}`}`);
    return { log, broken: fail, session };
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
  const readSession = (dir, name) => JSON.parse(readFileSync(join(dir, `${name}.session.json`), "utf8"));
  // A cached run: its log and its session result (a log without one is incomplete: run it again).
  const cachedRun = (dir, name) => dir && existsSync(join(dir, `${name}.jsonl`)) && existsSync(join(dir, `${name}.session.json`));
  const saveRun = (dir, name, r) => {
    mkdirSync(dir, { recursive: true });
    if (r.broken) writeFileSync(join(dir, `${name}.broken.json`), JSON.stringify(r.broken));
    writeFileSync(join(dir, `${name}.session.json`), JSON.stringify(r.session));
    saveLog(dir, name, r.log);
  };
  // name -> the run's session result (sandbox.mjs).
  const sessions = {};
  for (const [name, cmd] of Object.entries(runs)) {
    const isGo = name.startsWith("go");
    const from = isGo && [opt["go-stage"], goCache].find((d) => cachedRun(d, name));
    if (from) {
      logs[name] = readLog(join(from, `${name}.jsonl`));
      broken[name] = readBroken(from, name);
      sessions[name] = readSession(from, name);
      console.log(`${name}: ${logs[name].length} answers from ${join(from, `${name}.jsonl`)}`);
      continue;
    }
    if (opt.reuse) {
      logs[name] = readLog(join(outDir, `${name}.jsonl`));
      broken[name] = readBroken(outDir, name);
      sessions[name] = readSession(outDir, name);
      continue;
    }
    const r = await runLogged(name, cmd, envs[name]);
    logs[name] = r.log;
    broken[name] = r.broken;
    sessions[name] = r.session;
    if (r.broken) writeFileSync(join(outDir, `${name}.broken.json`), JSON.stringify(r.broken));
    writeFileSync(join(outDir, `${name}.session.json`), JSON.stringify(r.session));
    writeFileSync(join(outDir, `${name}.jsonl`), logs[name].map((e) => JSON.stringify(e)).join("\n") + "\n");
    // A crash is Go's answer and is staged; a timeout depends on the load and is not.
    if (opt["go-stage"] && isGo && r.broken?.kind !== "timeout") saveRun(opt["go-stage"], name, r);
  }
  // Answers are aligned by request identity: the n-th request of a step name in one run is the n-th
  // of that name in every run (requestKeys). Some requests depend on earlier answers (session.mjs:
  // rename after prepareRename, quick fixes per diagnostic), so a run can lack one: that is an answer
  // of its own ("not asked"), compared like any other. The item order is Go's run 1, with the
  // requests only other runs made in their place (mergeOrder): where every run asks the same
  // requests, item i is answer i of every log, as before.
  const NOT_ASKED = '"<not asked>"';
  const answer = (e, method) => JSON.stringify(normalize(e && { step: e.step, result: e.result, error: e.error }, method));
  const goNames = Object.keys(logs).filter((k) => /^go\d*$/.test(k));
  const rsNames = Object.keys(logs).filter((k) => !goNames.includes(k));
  const order = mergeOrder([...goNames, ...rsNames].map((k) => logs[k]));
  const aligned = Object.fromEntries(Object.entries(logs).map(([k, log]) => [k, alignTo(order, log)]));
  // Every run asks the requests it shares with the others in the same relative order.
  const orderBroken = Object.fromEntries(Object.entries(logs).map(([k, log]) => [k, orderBreak(order, log)]).filter(([, v]) => v));
  // Where a run that crashed or timed out ended: after the merged position of its last answer.
  const endOf = (k, log = aligned[k]) => log.reduce((last, e, p) => (e ? p : last), -1);
  const ends = Object.fromEntries(Object.keys(logs).map((k) => [k, endOf(k)]));
  const ended = (k, p, b = broken[k], end = ends[k]) => !!b && p > end;
  const why = (k, b = broken[k]) => (b ? `${k} ${b.kind === "timeout" ? "timed out" : "crashed"} after ${b.at} answers (${b.reason})` : `${k} has no answer here`);
  const failureOf = (b) => (b ? (b.kind === "timeout" ? "timeout" : "crash") : undefined);
  // One run's answer at merged position p: its normalized answer, NOT_ASKED, or null when it ended.
  const at = (k, p, method) => (aligned[k][p] ? answer(aligned[k][p], method) : ended(k, p) ? null : NOT_ASKED);
  const mismatches = [];
  const unstable = [];
  const items = [];
  const n = order.length;
  for (let p = 0; p < n; p++) {
    const first = Object.values(aligned).map((l) => l[p]).find(Boolean);
    const { step, method } = first;
    const diff = (detail, extra = {}, more = {}) => {
      items.push({ i: p, step, method, status: "DIFF", ...(detail ? { detail } : {}), ...more });
      mismatches.push({ step, method, ...(detail ? { detail } : {}), ...extra });
    };
    // Step 1, tsc-rs's determinism: a tsc-rs run that crashed or timed out, or a later run that
    // differs from the first (also in whether it asked this request), is a DIFF whatever Go does.
    const rsEnded = rsNames.filter((k) => ended(k, p));
    if (rsEnded.length) {
      diff(rsEnded.includes("rs") ? `tsc-rs: ${why("rs")}` : `rs-nondet: ${rsEnded.map((k) => why(k)).join("; ")}`, {}, { rsFailure: failureOf(broken[rsEnded[0]]) });
      continue;
    }
    const r = at("rs", p, method);
    const nondet = [];
    for (let k = 2; k <= rsRuns; k++) if (at(`rs${k}`, p, method) !== r) nondet.push(`rs${k}`);
    const schedule = Object.keys(envs).filter((k) => at(k, p, method) !== r);
    if (nondet.length || schedule.length) {
      const detail = [
        nondet.length && `rs-nondet: run 1 differs from ${nondet.join(", ")}`,
        schedule.length && `rs-schedule: run 1 differs from ${schedule.map((k) => JSON.stringify(envs[k])).join(", ")}`,
      ].filter(Boolean).join("; ");
      diff(detail, { rs: JSON.parse(r), others: Object.fromEntries([...nondet, ...schedule].map((k) => [k, JSON.parse(at(k, p, method))])) });
      continue;
    }
    // Step 2, GO-BROKEN: a Go run crashed or timed out before this answer, or Go's runs differ (also
    // in whether they asked this request).
    const goAnswers = goNames.filter((k) => !ended(k, p)).map((k) => [k, at(k, p, method)]);
    const groups = new Map();
    for (const [k, a] of goAnswers) groups.set(a, [...(groups.get(a) ?? []), k]);
    const goEnded = goNames.filter((k) => ended(k, p));
    if (goEnded.length || groups.size > 1) {
      const detail = [goEnded.length && goEnded.map((k) => why(k)).join("; "), groups.size > 1 && `Go runs differ (${[...groups.values()].map((g) => g.join(",")).join(" | ")})`].filter(Boolean).join("; ");
      items.push({ i: p, step, method, status: "GO-BROKEN", detail });
      unstable.push({ step, method, detail, rs: JSON.parse(r), rsEqualsGoRuns: groups.get(r) ?? [], go: Object.fromEntries([...groups].map(([a, ks]) => [ks.join(","), JSON.parse(a)])) });
      continue;
    }
    const g = goAnswers[0][1];
    if (g === r) items.push({ i: p, step, method, status: "MATCH" });
    else if (r === NOT_ASKED) diff("only Go was asked this request (tsc-rs's earlier answers led the script elsewhere)", { go: JSON.parse(g) });
    else if (g === NOT_ASKED) diff("only tsc-rs was asked this request (Go's earlier answers led the script elsewhere)", { rs: JSON.parse(r) });
    else diff(undefined, { go: JSON.parse(g), rs: JSON.parse(r) });
  }
  // A tsc-rs run that ended early also gets an item of its own, so the crash is listed even where
  // Go ended at the same answer.
  for (const k of rsNames.filter((k) => broken[k])) {
    items.push({ i: n, step: `${k} server`, method: "server/exit", status: "DIFF", detail: `tsc-rs ${why(k)}`, rsFailure: failureOf(broken[k]) });
  }
  // Request order and session results (lane H4): one item each, after every answer.
  const orderItem = (names, status) => {
    const bad = names.filter((k) => orderBroken[k]);
    if (bad.length) items.push({ i: n, step: `${bad.join(",")} request order`, method: "server/order", status, detail: bad.map((k) => `${k}: ${orderBroken[k]}`).join("; ") });
  };
  orderItem(rsNames, "DIFF");
  orderItem(goNames, "GO-BROKEN");
  const sessionItem = (extra = {}) => {
    const all = { ...sessions, ...extra };
    const bad = (names) => names.filter((k) => all[k] && !all[k].clean);
    const rsBad = bad(Object.keys(all).filter((k) => !/^go\d*$/.test(k)));
    const goBad = bad(goNames);
    const why = (ks) => ks.map((k) => `${k}: ${all[k].why.join(", ")}`).join("; ");
    const it = items.find((x) => x.step === "session end") ?? items[items.push({ i: n, step: "session end", method: "server/session" }) - 1];
    delete it.rsFailure;
    if (rsBad.length) Object.assign(it, { status: "DIFF", detail: `tsc-rs ${why(rsBad)}${goBad.length ? `; Go ${why(goBad)}` : ""}`, rsFailure: rsBad.some((k) => all[k].inputs?.modified?.length) ? "input" : "crash" });
    else if (goBad.length) Object.assign(it, { status: "GO-BROKEN", detail: `Go ${why(goBad)}` });
    else Object.assign(it, { status: "MATCH", detail: undefined });
  };
  sessionItem();
  // Step 3, JS-AGREES (referee.mjs): JS's verdict on each DIFF of a refereed kind.
  let jsRef;
  if (opt.js) {
    const jsCache = opt["js-cache"];
    const runJs = async () => {
      let log;
      let jsBroken;
      let jsSession;
      const from = [opt["js-stage"], jsCache].find((d) => cachedRun(d, "js"));
      if (from) {
        log = readLog(join(from, "js.jsonl"));
        jsBroken = readBroken(from, "js");
        jsSession = readSession(from, "js");
        console.log(`js: ${log.length} answers from ${join(from, "js.jsonl")}`);
      } else if (opt.reuse && existsSync(join(outDir, "js.jsonl"))) {
        log = readLog(join(outDir, "js.jsonl"));
        jsBroken = readBroken(outDir, "js");
        jsSession = readSession(outDir, "js");
      } else {
        const res = await runLogged("js", { tsserver: opt.js });
        ({ log, session: jsSession } = res);
        jsBroken = res.broken;
        writeFileSync(join(outDir, "js.jsonl"), log.map((e) => JSON.stringify(e)).join("\n") + "\n");
        writeFileSync(join(outDir, "js.session.json"), JSON.stringify(jsSession));
        if (jsBroken) writeFileSync(join(outDir, "js.broken.json"), JSON.stringify(jsBroken));
        // A crash is JS's answer and is staged with its log; a timeout depends on the load and is not.
        if (opt["js-stage"] && jsBroken?.kind !== "timeout") saveRun(opt["js-stage"], "js", res);
      }
      // A JS run that broke the request order or whose session is not clean is no evidence at all.
      const unusable = orderBreak(order, log) ?? (jsSession.clean ? null : `its session: ${jsSession.why.join(", ")}`);
      if (unusable) return { answers: order.map(() => undefined), broken: `the JS run is no evidence: ${unusable}` };
      const answers = alignTo(order, log);
      const jsEnd = endOf("js", answers);
      // Past the point where the JS run ended there is no JS answer (never an empty one).
      return { answers: answers.map((e, p) => (jsBroken && p > jsEnd ? undefined : e)), broken: jsBroken ? why("js", jsBroken) : undefined };
    };
    const kinds = opt["js-kinds"] !== undefined ? new Set(opt["js-kinds"].split(",").filter(Boolean)) : undefined;
    jsRef = await applyJsReferee({ items, logs: aligned, root, runJs, all: opt["js-all"], kinds });
  }
  // tsc-rs's determinism comes before every referee status: with one plain tsc-rs run, an item that
  // would be GO-BROKEN or JS-AGREES needs one more tsc-rs run. That run is a complete tsc-rs run of
  // the session: every answer of it is compared with run 1, MATCH answers included; an answer it
  // lacks, one it adds, and its crash or timeout are DIFFs.
  const agreeing = new Set(jsRef?.agreeing ?? []);
  const pending = items.filter((it) => it.status === "GO-BROKEN" || agreeing.has(it));
  if (pending.length && rsRuns === 1 && !Object.keys(envs).length) {
    const confirm = await runLogged("rs-confirm", servers.rs);
    writeFileSync(join(outDir, "rs-confirm.jsonl"), confirm.log.map((e) => JSON.stringify(e)).join("\n") + "\n");
    writeFileSync(join(outDir, "rs-confirm.session.json"), JSON.stringify(confirm.session));
    sessionItem({ "rs-confirm": confirm.session });
    const confirmOrder = orderBreak(order, confirm.log);
    if (confirmOrder) items.push({ i: n, step: "rs-confirm request order", method: "server/order", status: "DIFF", detail: `rs-nondet: the confirming tsc-rs run ${confirmOrder}` });
    const c = alignTo(order, confirm.log);
    const cEnd = endOf("rs-confirm", c);
    const extra = confirm.log.length - c.filter(Boolean).length;
    for (const it of items) {
      if (it.i >= n) continue; // the run-level items (server, order, session)
      const p = it.i;
      const cAns = c[p] ? answer(c[p], it.method) : ended("rs-confirm", p, confirm.broken, cEnd) ? null : NOT_ASKED;
      if (cAns === at("rs", p, it.method)) continue;
      const what = cAns === null ? why("rs-confirm", confirm.broken) : cAns === NOT_ASKED ? "it was not asked this request" : "its answer differs from run 1";
      it.detail = `rs-nondet: the confirming tsc-rs run (made for the ${pending.length} GO-BROKEN or JS-AGREES items): ${what}${it.status === "MATCH" ? "" : ` (was ${it.status}${it.detail ? `: ${it.detail}` : ""})`}`;
      it.status = "DIFF";
      if (cAns === null) it.rsFailure = failureOf(confirm.broken);
      agreeing.delete(it);
    }
    if (extra > 0) items.push({ i: n, step: "rs-confirm extra answers", method: "server/answers", status: "DIFF", detail: `rs-nondet: the confirming tsc-rs run asked ${extra} requests that run 1 did not` });
    if (confirm.broken) items.push({ i: n, step: "rs-confirm server", method: "server/exit", status: "DIFF", detail: `tsc-rs ${why("rs-confirm", confirm.broken)}`, rsFailure: failureOf(confirm.broken) });
  }
  for (const it of agreeing) {
    const k = it.js.points.length + (it.js.morePoints ?? 0);
    it.status = "JS-AGREES";
    it.detail = `JS sides with tsc-rs on ${k} point${k === 1 ? "" : "s"}: ${it.js.points.slice(0, 3).join("; ")}`;
  }
  if (jsRef) {
    writeFileSync(join(outDir, "js.json"), JSON.stringify({ ...jsRef, agreeing: undefined, decided: agreeing.size }, null, 1));
    if (jsRef.ran) console.log(`js referee: ${agreeing.size} answers JS-AGREES`);
  }
  const byMethod = {};
  for (const it of items) {
    const row = (byMethod[it.method] ??= { equal: 0, differ: 0, goBroken: 0, jsAgrees: 0 });
    row[{ MATCH: "equal", DIFF: "differ", "GO-BROKEN": "goBroken", "JS-AGREES": "jsAgrees" }[it.status]]++;
  }
  const total = (k) => Object.values(byMethod).reduce((a, b) => a + b[k], 0);
  const [equal, differ, goBroken] = ["equal", "differ", "goBroken"].map(total);
  console.log(`\n${equal} of ${equal + differ} answers with a stable Go equal (${((100 * equal) / Math.max(equal + differ, 1)).toFixed(1)}%); ${goBroken} more where Go is broken (tsc-rs matches one Go run on ${unstable.filter((u) => u.rsEqualsGoRuns.length).length})`);
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
