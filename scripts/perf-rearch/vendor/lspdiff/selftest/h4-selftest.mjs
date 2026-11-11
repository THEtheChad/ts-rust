// Negative controls of lane H4 (LSP side): Astra's H3 re-review (docs/perf-rearch/reviews/
// 2026-11-05-astra-h3.md), findings 5, 6, 9, 10 and 11, each run as the reviewer ran it, and lane ND2's
// scenario waits (the npm barrier of the scenarios with ATA on), and lane H5's hermetic servers (the
// stand-in npm and its store, the server environment and sandbox)
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
import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
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
  // Round 3, finding 2: stdout at EOF (the reviewer's eof/ probes) and stray output in front of a header.
  const garbage = lspdiff("eof-garbage", fake("rs-garbage", "", '  sys.stdout.write("debug: shutdown complete\\n"); sys.stdout.flush(); sys.exit(0)'));
  check("round 3: a stray line on stdout at EOF fails the session (probe eof rs-garbage)", sessionBad(garbage.items, (it) => it.status === "DIFF" && /no whole message/.test(it.detail)), JSON.stringify(bad(garbage.items)) + garbage.out.slice(-300));
  const short = lspdiff("eof-short", fake("rs-short-frame", "", '  sys.stdout.write("Content-Length: 100\\r\\n\\r\\n{\\"jsonrpc\\":"); sys.stdout.flush(); sys.exit(0)'));
  check("round 3: a truncated frame at EOF fails the session (probe eof rs-short-frame)", sessionBad(short.items, (it) => it.status === "DIFF" && /no whole message/.test(it.detail)), JSON.stringify(bad(short.items)));
  // Recheck 2026-11-05: a child that inherited stdout writes after the server exits (probe late-eof).
  const lateChild = lspdiff("eof-late", fake("rs-late-child", "import os", '  pid=os.fork()\n  if pid==0:\n   time.sleep(2.5); sys.stdout.write("debug: late child output\\n"); sys.stdout.flush(); os._exit(0)\n  sys.exit(0)'));
  check("recheck: stdout held open by a child past the exit fails the session (probe late-eof)", sessionBad(lateChild.items, (it) => it.status === "DIFF" && /EOF|no whole message/.test(it.detail)), JSON.stringify(bad(lateChild.items)) + lateChild.out.slice(-300));
  const stray = lspdiff("stray-header", fake("rs-stray", "sys.stdout.write('debug: starting\\n')", "  sys.exit(0)"));
  check("round 3: stray output in front of a header is a protocol error", bad(stray.items).some((it) => it.status === "DIFF" && /stray bytes|protocol/.test(it.detail ?? "")), JSON.stringify(bad(stray.items).slice(0, 2)));
  const ok = lspdiff("clean", fake("rs-ok", "", "  sys.exit(0)"));
  check("finding 9: control: a clean server run has no DIFF", ok.items.length > 0 && bad(ok.items).length === 0, JSON.stringify(bad(ok.items)) + ok.out.slice(-300));
  // A server that writes into its project (lane H4 sandbox): parity_lsp.py sets LSPDIFF_SANDBOX.
  const snap = join(ROOT, "snapshot");
  mkdirSync(snap, { recursive: true });
  writeFileSync(join(snap, "a.ts"), "export const a = 1;\n");
  const writer = fake("rs-writer", `open(${JSON.stringify(join(project, "a.ts"))},'w').write('tampered')`, "  sys.exit(0)");
  writeFileSync(join(project, "a.ts"), "export const a = 1;\n");
  mkdirSync(join(ROOT, "home"), { recursive: true });
  const sandbox = JSON.stringify({ scratch: join(ROOT, "sandbox"), home: join(ROOT, "home"), mounts: [{ lower: snap, target: project }] });
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

// ---- accumulated Go evidence (root's decision after H4; round 3: provenance, schema) ------------
await part("go variants", async () => {
  const root = join(ROOT, "variants");
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "a.ts"), "export const a=1;");
  const out = join(ROOT, "out-variants");
  const dir = join(out, "variants-diff");
  mkdirSync(dir, { recursive: true });
  const refs = (order) => ({ step: "a.ts:1:14 references", method: "textDocument/references", params: { position: { line: 0, character: 13 } }, result: order.map((line) => ({ uri: "file:///p/a.ts", range: range(line) })) });
  const hover = (text) => ({ step: "a.ts:1:14 hover", method: "textDocument/hover", params: { position: { line: 0, character: 13 } }, result: text === null ? null : { contents: text } });
  const clean = { clean: true, why: [], exit: { code: 0, signal: null }, shutdown: "ok", inputs: { modified: [], cacheWrites: 0 } };
  const writeRuns = (go, rs) => {
    for (const n of ["go", "go2", "rs", "rs2"]) {
      writeFileSync(join(dir, `${n}.jsonl`), (n.startsWith("go") ? go : rs).map((e) => JSON.stringify(e)).join("\n") + "\n");
      writeFileSync(join(dir, `${n}.session.json`), JSON.stringify(clean));
    }
  };
  writeRuns([hover(null), refs([1, 2])], [hover(null), refs([1, 2])]);
  const writePrior = (name, log, session) => {
    const f = join(ROOT, `${name}.jsonl`);
    writeFileSync(f, log.map((e) => JSON.stringify(e)).join("\n") + "\n");
    if (session) writeFileSync(join(ROOT, `${name}.session.json`), JSON.stringify(session));
    return f;
  };
  const prior = writePrior("prior-go", [hover(null), refs([2, 1])], clean); // an earlier verified fill: the other order
  const store = join(ROOT, "variants.json");
  const run = (material, extra, lspdiffDir = LSPDIFF) => {
    const r = spawnSync("node", [join(lspdiffDir, "lspdiff.mjs"), "diff", "--project", root, "--out", out, "--go-runs", "2", "--rs-runs", "2", "--reuse", "--go-variants-material", material, ...extra], { encoding: "utf8" });
    const f = join(dir, "items.json");
    const items = r.status === 0 && existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : [];
    if (existsSync(f)) rmSync(f);
    return { ref: items.find((it) => /references/.test(it.step)), hov: items.find((it) => /hover/.test(it.step)), out: r.stdout + r.stderr, status: r.status };
  };
  const M = JSON.stringify({ go: "sha-a", tree: "t1" });
  const first = run(M, ["--go-prior", prior, "--go-variants-out", store]);
  check("go variants: an answer an earlier verified fill of the same material gave makes the item GO-BROKEN", first.ref?.status === "GO-BROKEN" && /vary across fills/.test(first.ref.detail) && first.hov?.status === "MATCH", JSON.stringify(first.ref) + first.out.slice(-300));
  const again = run(M, ["--go-variants", store, "--go-variants-out", store]);
  check("go variants: the record is monotone (the earlier variant stays without its log)", again.ref?.status === "GO-BROKEN", JSON.stringify(again.ref) + again.out.slice(-300));
  const rec = existsSync(store) ? JSON.parse(readFileSync(store, "utf8")) : {};
  const provs = Object.values(rec.variants ?? {}).flatMap((byHash) => (Array.isArray(byHash) ? [] : Object.values(byHash).flat()));
  check("round 3: every variant keeps its provenance (fill, run, log)", Object.values(rec.variants ?? {}).some((byHash) => Object.keys(byHash).length === 2) && provs.length > 0 && provs.every((pv) => pv.fill && pv.run && pv.log) && provs.some((pv) => pv.log === prior), JSON.stringify(rec).slice(0, 400));
  const other = run(JSON.stringify({ go: "sha-b", tree: "t1" }), ["--go-variants", store]);
  check("go variants: negative control: a record of another binary is never merged", other.ref?.status === "MATCH" && /not merged/.test(other.out), JSON.stringify(other.ref) + other.out.slice(-200));
  const input = run(JSON.stringify({ go: "sha-a", tree: "t2" }), ["--go-variants", store]);
  check("go variants: negative control: a record of another input is never merged", input.ref?.status === "MATCH" && /not merged/.test(input.out), JSON.stringify(input.ref));

  // Finding 3 (round 3): the reviewer's invalid-input prior: tsc-rs's hover differs from Go's; a prior
  // log whose session changed its inputs must not turn that DIFF into GO-BROKEN.
  writeRuns([hover("go"), refs([1, 2])], [hover("tsc-rs"), refs([1, 2])]);
  const control = run(M, []);
  check("round 3: control: tsc-rs's other hover is a DIFF", control.hov?.status === "DIFF", JSON.stringify(control.hov) + control.out.slice(-300));
  const tainted = writePrior("prior-modified", [hover("tsc-rs"), refs([1, 2])], { ...clean, clean: false, why: ["it changed its inputs: /p/a.ts"], inputs: { modified: ["/p/a.ts"], cacheWrites: 0 } });
  const t = run(M, ["--go-prior", tainted]);
  check("round 3: a prior log whose session changed its inputs is never evidence (probe invalid-input-prior)", t.hov?.status === "DIFF" && /not used/.test(t.out), JSON.stringify(t.hov) + t.out.slice(-300));
  const unclean = writePrior("prior-unclean", [hover("tsc-rs"), refs([1, 2])], { ...clean, clean: false, why: ["exit 3"], exit: { code: 3, signal: null } });
  const u = run(M, ["--go-prior", unclean]);
  check("round 3: nor one whose session did not end clean", u.hov?.status === "DIFF", JSON.stringify(u.hov));
  const bare = writePrior("prior-bare", [hover("tsc-rs"), refs([1, 2])], null);
  const b = run(M, ["--go-prior", bare]);
  check("round 3: nor one without a session result", b.hov?.status === "DIFF", JSON.stringify(b.hov));
  // A Go run of this fill that changed its inputs: no Go evidence at all.
  writeFileSync(join(dir, "go2.session.json"), JSON.stringify({ ...clean, clean: false, why: ["it changed its inputs"], inputs: { modified: ["/p/a.ts"], cacheWrites: 0 } }));
  const g = run(M, []);
  check("round 3: a Go run of this fill that changed its inputs stops the session (no GO-BROKEN from it)", g.status !== 0 && /changed its inputs/.test(g.out), `${g.status} ${g.out.slice(-300)}`);
  writeFileSync(join(dir, "go2.session.json"), JSON.stringify(clean));

  // Finding 4: the reviewer's malformed records (a string where the hashes belong; the old shape).
  const k0 = Object.keys(rec.variants ?? { "0\u0000a\u0000b\u0000{}": 1 })[0];
  for (const [name, body] of [
    ["a hash string, not an object of hashes (probe hash-string-not-array)", { ...rec, variants: { [k0]: "6c9df7c0dcf5a295a673106e0f75d4ee" } }],
    ["the version-1 shape (probe poisoned-record)", { material: M, normalizer: rec.normalizer, variants: { [k0]: ["6c9df7c0dcf5a295", "a673106e0f75d4ee"] } }],
    ["a hash of the wrong size", { ...rec, variants: { [k0]: { abc: [{ fill: "f", run: "go", log: "l" }] } } }],
    ["a variant without provenance", { ...rec, variants: { [k0]: { "0123456789abcdef": [] } } }],
    ["an unknown key", { ...rec, extra: true }],
  ]) {
    const f = join(ROOT, "bad-record.json");
    writeFileSync(f, JSON.stringify(body));
    const r = run(M, ["--go-variants", f]);
    check(`round 3: a malformed record fails loudly: ${name}`, r.status !== 0 && /malformed Go variant record/.test(r.out), `${r.status} ${JSON.stringify(r.hov)} ${r.out.slice(-200)}`);
  }

  // Finding 5: the fingerprint covers the whole answer-to-hash transformation: an lspdiff whose
  // `answer` hashes another view of the answer must not read this record.
  const copy = join(ROOT, "lspdiff-copy");
  spawnSync("cp", ["-r", LSPDIFF, copy]);
  const src = readFileSync(join(copy, "lspdiff.mjs"), "utf8");
  writeFileSync(join(copy, "lspdiff.mjs"), src.replace("const answer = (e, method) => JSON.stringify(normalize(e && { step: e.step, result: e.result, error: e.error }, method, !!e?.rawNumbers));", "const answer = (e, method) => JSON.stringify(normalize(e && { result: e.result }, method, !!e?.rawNumbers));"));
  writeRuns([hover(null), refs([1, 2])], [hover(null), refs([1, 2])]);
  const changed = run(M, ["--go-variants", store], copy);
  check("round 3: a changed answer function does not read the record (normalizer identity)", changed.ref?.status === "MATCH" && /other normalizer/.test(changed.out), JSON.stringify(changed.ref) + changed.out.slice(-200));
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
  const probe = scriptProbe(() => '{"start":true,"pid":4242,"args":["install"]}\n', () => (clock += 20000));
  let clock = 0;
  const s = probe.script;
  let threw = null;
  try {
    await s.ataIdle("deadline");
  } catch (e) {
    threw = e;
  }
  check("finding 11: an ATA wait that reaches its deadline with npm still running fails the run (probe ata-probe)", !!threw && threw.timeout === true && s.log.length === 0, threw ? String(threw.message) : JSON.stringify(s.log));
});

// The Script waits of scenario.mjs (npmCalls, npmIdle, settle, ataIdle) in a context of their own:
// `npmLog(polls)` gives the npm log's text at each read, `now()` the clock (ms); `alive`: whether a
// logged npm process still runs (finding 11: always).
function scriptProbe(npmLog, now, alive = true) {
  const src = readFileSync(join(LSPDIFF, "scenario.mjs"), "utf8");
  const methods = ["npmCalls() {", "async npmIdle(label, since) {", "async settle(ms, label) {", "async ataIdle(label) {", "npmReport() {"].map((head) => {
    const at = src.indexOf(`\n  ${head}`);
    if (at < 0) throw new Error(`scenario.mjs has no ${head}`);
    return src.slice(at, src.indexOf("\n  }\n", at) + 4);
  });
  let reads = 0;
  const ctx = {
    process: {
      env: {},
      kill: () => {
        if (!alive) throw Object.assign(new Error("ESRCH"), { code: "ESRCH" });
        return true;
      },
    },
    performance: { now },
    existsSync: () => true,
    readFileSync: () => npmLog(reads++),
    SETTLE_MS: 0,
    ATA_DIR: "/run/lspdiff-ata",
    SETTINGS: { ATA_IDLE_MS: 1500, ATA_DEADLINE_MS: 60000 },
    setTimeout: (cb) => cb(),
    Error,
    Object,
    JSON,
  };
  const cls = vm.runInNewContext(`(class {${methods.join("")}})`, ctx);
  const script = new cls();
  const settles = [];
  script.ata = { log: "fake", reported: 0, seen: 0, changedAt: -Infinity };
  // The server's own liveness check (sandbox.mjs ServerProcess.alive: the pid as npm saw it) and home marks.
  script.client = { settle: async () => settles.push(now()), server: { alive: () => alive, inside: (p) => p } };
  script.log = [];
  return { script, settles, reads: () => reads };
}

// ---- lane ND2: the scenarios' waits cover ATA's npm calls ----------------------------------------
await part("nd2", async () => {
  // A call that starts on the 3rd read of the log (ATA was about to start it when the wait began) and
  // is done on the 20th: a wait ends only after the done line, and only after the server was quiet
  // again after it. 100 ms per clock read.
  let clock = 0;
  const start = '{"start":true,"pid":1}\n';
  const done = '{"done":true,"pid":1,"args":["install"],"code":0}\n';
  const late = scriptProbe((n) => (n < 2 ? "" : n < 19 ? start : start + done), () => (clock += 100), false);
  const ended = await late.script.settle(300, "probe");
  check("nd2: a scenario wait sees an npm call that starts after the wait began and lasts until it is done", late.reads() > 19 && late.settles.length >= 2 && late.settles.at(-1) > 1900, `reads ${late.reads()}, settles ${late.settles}, waited ${ended}`);
  // No npm call at all: the wait still lasts ATA_IDLE_MS from its start (a call ATA is about to make).
  clock = 0;
  const none = scriptProbe(() => "", () => (clock += 100), false);
  const waited = await none.script.settle(300, "probe");
  check("nd2: with ATA on, a scenario wait lasts ATA_IDLE_MS even when npm never runs", waited >= 1500, `waited ${waited}`);
  // ATA off (no npm log): the wait is the server's quiet window alone.
  clock = 0;
  const off = scriptProbe(() => "", () => (clock += 100), false);
  off.script.ata = undefined;
  const quick = await off.script.settle(300, "probe");
  check("nd2: with ATA off, a scenario wait is the server's quiet window alone", off.reads() === 0 && quick < 1500, `reads ${off.reads()}, waited ${quick}`);
});

// ---- lane H5: hermetic servers and ATA ------------------------------------------------------------
await part("h5", async () => {
  const { ATA_STORE, writeTree } = await import(join(LSPDIFF, "fixtures.mjs"));
  const dir = join(ROOT, "h5");
  const store = join(dir, "store");
  writeTree(ATA_STORE, store);
  const home = join(dir, "home");
  const typings = join(home, ".cache/typescript/7.1");
  mkdirSync(typings, { recursive: true });
  // The stand-in reads only its fixed config (lane H8 round 2): each call runs in a mount namespace with
  // the config, node and the log dir bound at /run/lspdiff-ata, as the sandbox binds them.
  const { createHash } = await import("node:crypto");
  const logDir = join(dir, "log");
  mkdirSync(logDir, { recursive: true });
  const log = join(logDir, "npm.log");
  const files = Object.fromEntries(Object.entries(ATA_STORE).map(([k, v]) => [k, createHash("sha256").update(v).digest("hex")]));
  const config = (over = {}) => {
    const f = join(dir, "config.json");
    writeFileSync(f, JSON.stringify({ store, files, log: "/run/lspdiff-ata/log/npm.log", delayMs: 0, cache: join(home, ".cache"), ...over }));
    return f;
  };
  const npm = (args, cwd = typings, { env = {}, cfg = config() } = {}) => {
    const bind = 'mount -t tmpfs tmpfs /run && mkdir -p /run/lspdiff-ata/log && touch /run/lspdiff-ata/config.json /run/lspdiff-ata/node /run/lspdiff-ata/fake-npm.mjs && mount --bind "$1" /run/lspdiff-ata/config.json && mount --bind "$2" /run/lspdiff-ata/node && mount --bind "$3" /run/lspdiff-ata/log && mount --bind "$4" /run/lspdiff-ata/fake-npm.mjs && cd "$5" && shift 5 && exec "$@"';
    const r = spawnSync("unshare", ["-rm", "sh", "-c", bind, "sh", cfg, process.execPath, logDir, join(LSPDIFF, "ata-npm/fake-npm.mjs"), cwd, join(LSPDIFF, "ata-npm/npm"), ...args], {
      env: { PATH: "/usr/bin:/bin", XDG_CACHE_HOME: join(home, ".cache"), ...env },
      encoding: "utf8",
    });
    const lines = existsSync(log) ? readFileSync(log, "utf8").trim().split("\n").map((l) => JSON.parse(l)) : [];
    return { code: r.status, last: lines.at(-1), stderr: r.stderr };
  };
  const reg = npm(["install", "--ignore-scripts", "types-registry@latest"]);
  const two = npm(["install", "--ignore-scripts", "@types/ata-fake-lib@latest", "@types/ata-fake-two@latest", "--save-dev", '--user-agent="typesInstaller/7.1"']);
  const pkg = JSON.parse(readFileSync(join(typings, "package.json"), "utf8"));
  check(
    "h5: the stand-in npm installs the registry and each package's closure from its store, and records them as npm does",
    reg.code === 0 && two.code === 0 && existsSync(join(typings, "node_modules/types-registry/index.json")) && existsSync(join(typings, "node_modules/@types/ata-fake-two/index.d.ts")) && pkg.devDependencies?.["@types/ata-fake-lib"] === "^1.0.0" && pkg.dependencies?.["types-registry"] === "^0.1.0",
    JSON.stringify({ reg, two, pkg }),
  );
  const miss = npm(["install", "--ignore-scripts", "@types/ata-fake-lib@latest", "@types/not-in-store@latest", "--save-dev"]);
  check("h5: a package the store lacks fails the call, is logged as missing, and nothing of the call is installed", miss.code === 1 && JSON.stringify(miss.last.missing) === '["@types/not-in-store@latest"]', JSON.stringify(miss));
  const out = npm(["install", "--ignore-scripts", "types-registry@latest"], dir);
  check("h5: a call outside the private XDG_CACHE_HOME is refused (the real typings cache, say)", out.code === 1 && /not under the private XDG_CACHE_HOME/.test(out.last.refused ?? ""), JSON.stringify(out));
  // Lane H8 round 2 (Astra's ata-unlogged): the environment is not the stand-in's config.
  const before = readFileSync(log, "utf8").split("\n").length;
  const env = npm(["install", "--ignore-scripts", "types-registry@latest"], typings, { env: { ATA_NPM_LOG: "/dev/null", ATA_STORE: "/nowhere", ATA_NPM_DELAY_MS: "0", XDG_CACHE_HOME: "/elsewhere" } });
  check("h8 r2: the stand-in ignores the environment: ATA_NPM_LOG, ATA_STORE and XDG_CACHE_HOME of the server change nothing (the call is logged, installs, and is not refused)",
    env.code === 0 && readFileSync(log, "utf8").split("\n").length === before + 2 && env.last.code === 0, JSON.stringify(env));
  const tampered = npm(["install", "--ignore-scripts", "types-registry@latest"], typings, { cfg: config({ files: { ...files, "registry/index.json": "0".repeat(64) } }) });
  check("h8 r2: a store file that does not have its configured sha256 fails the call, logged as error", tampered.code === 1 && /sha256/.test(tampered.last.error ?? ""), JSON.stringify(tampered));
  for (const [what, line] of [["missing", '{"done":true,"pid":1,"args":[],"code":1,"missing":["@types/x@latest"]}'], ["refused", '{"done":true,"pid":1,"args":[],"code":1,"refused":"outside"}'], ["error", '{"done":true,"pid":1,"args":[],"code":1,"error":"a store file"}']]) {
    const probe = scriptProbe(() => '{"start":true,"pid":1}\n' + line + "\n", () => 0, false);
    let threw = null;
    try {
      await probe.script.settle(300, "probe");
    } catch (e) {
      threw = e;
    }
    check(`h5: a ${what} npm call makes the scenario's wait a harness error (no server's answer: never GO-BROKEN)`, threw?.harness === true && !threw.timeout, threw ? threw.message : "no error");
  }
  // The server environment: nothing of lspdiff's own, a private home; with the sandbox, the home at its
  // fixed path, empty again afterwards, and no network.
  const fixed = join(dir, "fixed-home");
  mkdirSync(fixed);
  const probe = (sandbox) =>
    JSON.parse(
      execFileSync(process.execPath, ["--input-type=module", "-e", `
        const { ServerProcess } = await import(${JSON.stringify(join(LSPDIFF, "sandbox.mjs"))});
        const p = new ServerProcess(["sh", "-c", 'env | sort | cut -d= -f1 | tr "\\n" " "; echo; echo "$HOME"; ls -A "$HOME" | tr "\\n" " "; echo; tail -n +3 /proc/net/dev | wc -l; touch "$HOME/.cache/written"'], {});
        let out = ""; p.proc.stdout.on("data", (c) => (out += c));
        const r = await p.result({ shutdown: "ok", forced: false });
        console.log(JSON.stringify({ out: out.split("\\n"), r }));`], { env: { ...process.env, LEAK_ME: "1", ...(sandbox ? { LSPDIFF_SANDBOX: JSON.stringify({ scratch: join(dir, "scratch"), home: fixed, mounts: [] }) } : { LSPDIFF_SANDBOX: "" }) }, encoding: "utf8" }),
    );
  const plain = probe(false);
  const keys = plain.out[0].trim().split(" ");
  check("h5: a server gets none of lspdiff's environment, a private home and the system PATH only", !keys.includes("LEAK_ME") && !keys.includes("ATA_REAL_NPM") && keys.includes("XDG_CACHE_HOME") && plain.out[1] !== homedir() && plain.out[2].includes(".cache"), JSON.stringify(plain));
  const boxed = probe(true);
  check("h5: in the sandbox the private home is at its fixed path, the write is recorded and listed, the host's dir stays empty, and there is no network (loopback only)",
    boxed.out[1] === fixed && boxed.out[3].trim() === "1" && boxed.r.writes?.["home/.cache/written"]?.includes("create") && /^[0-9a-f]{16} /.test(boxed.r.storage?.home?.[".cache/written"] ?? "") && readdirSync(fixed).length === 0, JSON.stringify(boxed));
});

rmSync(ROOT, { recursive: true, force: true });
console.log(fails.length ? `H4 LSP SELFTEST FAIL: ${fails.join("; ")}` : "H4 LSP SELFTEST OK");
process.exit(fails.length ? 1 : 0);
