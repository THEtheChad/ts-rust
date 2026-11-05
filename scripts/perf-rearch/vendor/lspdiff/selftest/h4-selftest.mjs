// Negative controls of lane H4 (LSP side): Astra's H3 re-review (docs/perf-rearch/reviews/
// 2026-11-05-astra-h3.md), findings 5, 6, 9, 10 and 11, each run as the reviewer ran it
// (~/.cache/perf-rearch/scratch/review-h3-astra/: js-probes.mjs, malformed-tsserver.cjs, late-crash/,
// alignment/, ata-probe.mjs), plus the session result's other outcomes (stderr, a server that does not
// exit, a server that writes into its project). The Python side is scripts/perf-rearch/parity_h4_test.py.
//
//   node scripts/perf-rearch/vendor/lspdiff/selftest/h4-selftest.mjs
//
// Prints one line per check and "H4 LSP SELFTEST OK" (exit 0) or "H4 LSP SELFTEST FAIL: ..." (exit 1).
// The checks also run against older lspdiff versions (copy this file into their selftest/): a missing
// module or function there is a FAIL of that check, not an error of the test. About 30 s.
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const LSPDIFF = join(HERE, "..");
const ROOT = join(homedir(), `.cache/perf-rearch/parity/scratch/h4-lsp-selftest-${process.pid}`);
const fails = [];
const check = (name, ok, got = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `: ${String(got).slice(0, 1200)}`}`);
  if (!ok) fails.push(name);
};
const part = async (name, f) => {
  try {
    await f();
  } catch (e) {
    check(`${name}: raised`, false, e?.stack ?? e);
  }
};
rmSync(ROOT, { recursive: true, force: true });
mkdirSync(ROOT, { recursive: true });
const range = (line, end = 1) => ({ start: { line, character: 0 }, end: { line, character: end } });

// ---- finding 5: an element's fields stay with the element -----------------------------------
await part("finding 5", async () => {
  const { normalize, judge } = await import(join(LSPDIFF, "referee.mjs"));
  const g = { originSelectionRange: range(0), targetUri: "file:///p/a.ts", targetSelectionRange: range(1), targetRange: range(1, 10) };
  const j = { ...g, targetUri: "file:///p/b.ts", targetSelectionRange: range(20), targetRange: range(20, 10) };
  const r = { ...j, targetRange: g.targetRange }; // the new target with the old target's range
  const N = (x) => normalize("textDocument/definition", { result: x });
  const v = judge(N([g]), N([r]), N([j]));
  check("finding 5: a definition target cannot borrow another target's unrefereed range (probe js-probes)", v.agrees === false, JSON.stringify(v));
  const ok = judge(N([g]), N([j]), N([j]));
  check("finding 5: control: the same change with JS's whole element is JS-AGREES", ok.agrees === true, JSON.stringify(ok));
});

// ---- finding 6: tsserver responses fit their command's schema --------------------------------
await part("finding 6", async () => {
  const { TsserverClient } = await import(join(LSPDIFF, "tsserver.mjs"));
  const { normalize, judge } = await import(join(LSPDIFF, "referee.mjs"));
  const fake = join(ROOT, "malformed-tsserver.cjs");
  writeFileSync(fake, `const rl=require('node:readline').createInterface({input:process.stdin});rl.on('line',l=>{const q=JSON.parse(l);if(q.command==='exit')process.exit(0);const b=JSON.stringify({type:'response',request_seq:q.seq,command:q.command,success:true,body:{}});process.stdout.write('Content-Length: '+Buffer.byteLength(b)+'\\r\\n\\r\\n'+b);});`);
  const c = new TsserverClient(fake, { cwd: ROOT });
  await c.request("initialize", {});
  const ans = await c.request("textDocument/references", { textDocument: { uri: "file:///p/a.ts" }, position: { line: 0, character: 0 }, context: { includeDeclaration: true } });
  const hover = await c.request("textDocument/hover", { textDocument: { uri: "file:///p/a.ts" }, position: { line: 0, character: 0 } });
  await c.shutdown();
  check("finding 6: success:true with body {} is a failed references answer (probe malformed-tsserver)", !!ans.failed && ans.result === undefined, JSON.stringify(ans));
  check("finding 6: and a failed hover", !!hover.failed, JSON.stringify(hover));
  const refN = (x) => normalize("textDocument/references", x);
  const loc = { uri: "file:///p/a.ts", range: range(0) };
  const v = judge(refN({ result: [loc] }), refN({ result: [] }), refN(ans));
  check("finding 6: the malformed answer approves no dropped reference", v.agrees === false, JSON.stringify(v));
});

// ---- finding 9: every outcome of a server run is judged --------------------------------------
const FAKE = (preamble, onExit) => `#!/usr/bin/env python3
import sys,json,time
${preamble}
while True:
 h=sys.stdin.buffer.readline()
 if not h: break
 n=int(h.decode().split(':')[1]);sys.stdin.buffer.readline();q=json.loads(sys.stdin.buffer.read(n))
 if q.get('method')=='exit':
${onExit}
 if 'id' not in q: continue
 m=q['method']
 result={'capabilities':{}} if m=='initialize' else {'kind':'full','items':[]} if m=='textDocument/diagnostic' else None if m in ('shutdown','textDocument/semanticTokens/full') else []
 b=json.dumps({'jsonrpc':'2.0','id':q['id'],'result':result}).encode();sys.stdout.buffer.write(('Content-Length: '+str(len(b))+'\\r\\n\\r\\n').encode()+b);sys.stdout.buffer.flush()
`;
const fake = (name, preamble, onExit) => {
  const p = join(ROOT, name);
  writeFileSync(p, FAKE(preamble, onExit));
  chmodSync(p, 0o755);
  return p;
};
const project = join(ROOT, "project");
mkdirSync(project, { recursive: true });
writeFileSync(join(project, "a.ts"), "export const a = 1;\n");
const goFake = fake("go", "", "  sys.exit(0)");
function lspdiff(name, rs, env = {}, extra = []) {
  const out = join(ROOT, `out-${name}`);
  const r = spawnSync("node", [join(LSPDIFF, "lspdiff.mjs"), "diff", "--project", project, "--file", "a.ts", "--positions", "1", "--go", goFake, "--rs", rs, "--out", out, "--go-runs", "2", ...extra], { encoding: "utf8", env: { ...process.env, LSPDIFF_SHUTDOWN_MS: "800", ...env } });
  const f = join(out, "project-diff", "items.json");
  return { items: existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : [], out: r.stdout + r.stderr };
}
const bad = (items) => items.filter((it) => it.status !== "MATCH");
// The run-level verdict on the servers' sessions (lane H4), and only that: every answer MATCHes.
const sessionBad = (items, test) => items.length > 1 && items.filter((it) => !it.method.startsWith("server/")).every((it) => it.status === "MATCH") && bad(items).some(test);
await part("finding 9", async () => {
  const late = lspdiff("late-crash", fake("rs-late", 'sys.stderr.write("panic: newly introduced late failure\\n")', "  sys.exit(70)"));
  check("finding 9: a panic and exit 70 after the last answer fail the session (probe late-crash)",
    sessionBad(late.items, (it) => it.status === "DIFF" && it.rsFailure === "crash"), JSON.stringify(bad(late.items)) + late.out.slice(-400));
  const panic0 = lspdiff("panic-exit-0", fake("rs-panic", "", '  sys.stderr.write("panic: at shutdown\\n"); sys.exit(0)'));
  check("finding 9: a panic line on stderr fails the session even with exit 0", sessionBad(panic0.items, (it) => it.status === "DIFF" && /panic: at shutdown/.test(it.detail)), JSON.stringify(bad(panic0.items)));
  const hang = lspdiff("no-exit", fake("rs-hang", "", "  time.sleep(30)"));
  check("finding 9: a server that does not exit after exit fails the session", sessionBad(hang.items, (it) => it.status === "DIFF" && /killed|SIGKILL/.test(it.detail)), JSON.stringify(bad(hang.items)));
  const ok = lspdiff("clean", fake("rs-ok", "", "  sys.exit(0)"));
  check("finding 9: control: a clean server run has no DIFF", ok.items.length > 0 && bad(ok.items).length === 0, JSON.stringify(bad(ok.items)) + ok.out.slice(-300));
  // A server that writes into its project (lane H4 sandbox): parity_lsp.py sets LSPDIFF_SANDBOX.
  const snap = join(ROOT, "snapshot");
  mkdirSync(snap, { recursive: true });
  writeFileSync(join(snap, "a.ts"), "export const a = 1;\n");
  const writer = fake("rs-writer", `open(${JSON.stringify(join(project, "a.ts"))},'w').write('tampered')`, "  sys.exit(0)");
  writeFileSync(join(project, "a.ts"), "export const a = 1;\n");
  const sandbox = JSON.stringify({ scratch: join(ROOT, "sandbox"), mounts: [{ lower: snap, target: project, writable: false }] });
  const w = lspdiff("writer", writer, { LSPDIFF_SANDBOX: sandbox });
  check("finding 9: a server that writes into its project fails its session (rsFailure input)",
    sessionBad(w.items, (it) => it.status === "DIFF" && it.rsFailure === "input"), JSON.stringify(bad(w.items)) + w.out.slice(-300));
  check("finding 9: and the project itself is untouched", readFileSync(join(project, "a.ts"), "utf8") === "export const a = 1;\n", readFileSync(join(project, "a.ts"), "utf8"));
});

// ---- finding 10: request identity is method, params and step ----------------------------------
await part("finding 10", async () => {
  const root = join(ROOT, "alignment");
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "a.ts"), "export const a=1;");
  const out = join(ROOT, "out-alignment");
  const dir = join(out, "alignment-diff");
  mkdirSync(dir, { recursive: true });
  const ca = (character, code) => ({ step: "a.ts:1 codeAction(diag)", method: "textDocument/codeAction", params: { range: { start: { line: 0, character } } }, context: { diagnostics: [{ code }] }, result: [] });
  const hover = { step: "a.ts:2 hover", method: "textDocument/hover", params: { position: { line: 1, character: 2 } }, result: null };
  const fix = (e) => ({ ...e, params: { ...e.params, context: e.context } });
  const go = [fix(ca(1, 2322)), hover];
  const rs = [hover, fix(ca(99, 9999))]; // the reviewer's probe: another request under the same label, in another order
  const clean = JSON.stringify({ clean: true, why: [], exit: { code: 0, signal: null }, shutdown: "ok" });
  for (const [n, log] of [["go", go], ["go2", go], ["rs", rs]]) {
    writeFileSync(join(dir, `${n}.jsonl`), log.map((e) => JSON.stringify(e)).join("\n") + "\n");
    writeFileSync(join(dir, `${n}.session.json`), clean);
  }
  spawnSync("node", [join(LSPDIFF, "lspdiff.mjs"), "diff", "--project", root, "--out", out, "--go-runs", "2", "--reuse"], { encoding: "utf8" });
  const items = JSON.parse(readFileSync(join(dir, "items.json"), "utf8"));
  check("finding 10: a request with other params under the same step label is not compared as the same request (probe alignment)",
    items.some((it) => it.status === "DIFF" && /only Go was asked/.test(it.detail ?? "")) && items.some((it) => it.status === "DIFF" && /only tsc-rs was asked/.test(it.detail ?? "")), JSON.stringify(items));
  const rs2 = [hover, fix(ca(1, 2322))];
  writeFileSync(join(dir, "rs.jsonl"), rs2.map((e) => JSON.stringify(e)).join("\n") + "\n");
  spawnSync("node", [join(LSPDIFF, "lspdiff.mjs"), "diff", "--project", root, "--out", out, "--go-runs", "2", "--reuse"], { encoding: "utf8" });
  const items2 = JSON.parse(readFileSync(join(dir, "items.json"), "utf8"));
  check("finding 10: the same requests in another order are a DIFF", items2.some((it) => it.status === "DIFF" && /order/.test(`${it.step} ${it.detail}`)), JSON.stringify(items2));
});

// ---- finding 11: the ATA barrier never succeeds on its deadline -------------------------------
await part("finding 11", async () => {
  const { PLUMBING } = await import(join(LSPDIFF, "settings.mjs"));
  const reads = [];
  for (const f of ["client.mjs", "fixtures.mjs", "lspdiff.mjs", "project.mjs", "referee.mjs", "sandbox.mjs", "scenario.mjs", "session.mjs", "tsserver.mjs", "ata-npm/fake-npm.mjs"]) {
    for (const m of readFileSync(join(LSPDIFF, f), "utf8").matchAll(/process\.env\.((?:LSPDIFF|ATA)_\w+)|process\.env\[["'`]((?:LSPDIFF|ATA)_\w+)/g)) reads.push(`${f}:${m[1] ?? m[2]}`);
  }
  const stray = reads.filter((r) => !PLUMBING.includes(r.split(":")[1]));
  check("finding 11: every LSPDIFF_*/ATA_* setting is read in settings.mjs only (so the cache key has them all)", stray.length === 0, stray.join(", "));
  const src = readFileSync(join(LSPDIFF, "scenario.mjs"), "utf8");
  const method = src.match(/  async ataIdle\(label\) \{[\s\S]*?\n  \}/)[0];
  let clock = 0;
  const env = { ATA_IDLE_MS: "1500" };
  const ctx = {
    process: { env, kill: () => true },
    performance: { now: () => (clock += 20000) },
    existsSync: () => true,
    readFileSync: () => '{"start":true,"pid":4242,"args":["install"]}\n',
    SETTLE_MS: 0,
    SETTINGS: { ATA_IDLE_MS: 1500, ATA_DEADLINE_MS: 60000 },
    setTimeout: (cb) => cb(),
    Error,
    Object,
    JSON,
  };
  const cls = vm.runInNewContext(`(class {${method}})`, ctx);
  const s = new cls();
  s.ata = { log: "fake", reported: 0 };
  s.client = { settle: async () => {} };
  s.log = [];
  let threw = null;
  try {
    await s.ataIdle("deadline");
  } catch (e) {
    threw = e;
  }
  check("finding 11: an ATA wait that reaches its deadline with npm still running fails the run (probe ata-probe)", !!threw && threw.timeout === true && s.log.length === 0, threw ? String(threw.message) : JSON.stringify(s.log));
});

rmSync(ROOT, { recursive: true, force: true });
console.log(fails.length ? `H4 LSP SELFTEST FAIL: ${fails.join("; ")}` : "H4 LSP SELFTEST OK");
process.exit(fails.length ? 1 : 0);
