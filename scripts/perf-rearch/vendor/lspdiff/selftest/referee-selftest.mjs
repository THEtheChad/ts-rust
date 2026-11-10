#!/usr/bin/env node
// Self-test of the LSP tier's JS referee (referee.mjs, tsserver.mjs, lspdiff.mjs --js).
//
//   node scripts/perf-rearch/vendor/lspdiff/selftest/referee-selftest.mjs [--unit]
//
// Part 1 (unit): the judge on hand-made answers: every rule of JS-AGREES, the residue,
// "no refereed point" and order guards, the lib anchoring, and the semantic token legend.
// Part 2 (fixture, skipped by --unit): lspdiff diff on a two-file project
// (~/.cache/perf-rearch/parity/scratch/referee-selftest/), Go's LSP server against
// selftest/proxy-lsp.mjs as "tsc-rs" (Go with some answers rewritten), with JS tsserver 6.0.3 as the
// referee. It checks:
//   - a hover rewritten to JS's union order (JS 6 prints "zero" | "one", Go "one" | "zero") is
//     JS-AGREES when hover is refereed (--js-kinds), and DIFF with the default kinds;
//   - a hover rewritten to a type nobody prints is DIFF, with JS's reason;
//   - a references answer that drops a location JS has is DIFF ("go-only ...: JS has it");
//   - a rewrite JS agrees with on run 1 only is DIFF rs-nondet (the confirming tsc-rs run);
//   - every answer the proxy leaves alone is MATCH, and --js-all writes the calibration;
//   - with the proxy as Go instead (and Go's server as tsc-rs): a Go run that crashes (exits at a
//     hover) and one that hangs (never answers a references request; LSPDIFF_REQUEST_TIMEOUT_MS 5 s)
//     make every answer from there GO-BROKEN, confirmed by one more tsc-rs run, and the answers
//     before stay MATCH.
// Exit 0 and "referee self-test OK", or exit 1 with the failed checks.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { GO_LIBS, JS_LIBS, judge, normalize, sameNormalized } from "../referee.mjs";
import { DEFAULT_TSSERVER, TOKEN_TYPES } from "../tsserver.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const failed = [];
const check = (name, ok, info = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok || !info ? "" : `: ${info}`}`);
  if (!ok) failed.push(name);
};

// ---- part 1: unit ----------------------------------------------------------------------------
const R = (l1, c1, l2, c2) => ({ start: { line: l1, character: c1 }, end: { line: l2, character: c2 } });
const loc = (uri, ...r) => ({ uri, range: R(...r) });
const ans = (result) => ({ result });
const N = (method, result, libDir) => normalize(method, ans(result), { libDir });
const refs = "textDocument/references";
const [a1, a2, a3] = [loc("file:///p/a.ts", 1, 0, 1, 3), loc("file:///p/a.ts", 2, 0, 2, 3), loc("file:///p/b.ts", 0, 0, 0, 3)];
const J = (m, g, r, j, libs) => judge(N(m, g), N(m, r), N(m, j, libs));

check("rs-only location that JS has: agrees", J(refs, [a1], [a1, a2], [a1, a2, a3]).agrees);
check("rs-only location that JS lacks: disagrees", !J(refs, [a1], [a1, a2], [a1]).agrees);
check("go-only location that JS lacks: agrees", J(refs, [a1, a3], [a1], [a1, a2]).agrees);
check("go-only location that JS has: disagrees", /JS has it/.test(J(refs, [a1, a3], [a1], [a1, a3]).reason));
check("points where tsc-rs equals Go need nothing from JS", J(refs, [a1, a3], [a1, a3, a2], [a2]).agrees);
const hover = (t) => ({ contents: { kind: "markdown", value: "```typescript\n" + t + "\n```\n" }, range: R(0, 0, 0, 1) });
check("hover display: JS equal to tsc-rs agrees", J("textDocument/hover", hover("const x: 1"), hover("const x: 2"), hover("const x: 2")).agrees);
check("hover display: JS equal to Go disagrees", !J("textDocument/hover", hover("const x: 1"), hover("const x: 2"), hover("const x: 1")).agrees);
const item = (label, data) => ({ label, kind: 6, sortText: "11", data });
const comp = (...items) => ({ isIncomplete: false, items });
check(
  "completion that differs only in data: not refereed",
  /outside the refereed fields/.test(J("textDocument/completion", comp(item("a", { x: 1 })), comp(item("a", { x: 2 })), comp(item("a", { x: 2 }))).reason),
);
check(
  "completion rest (itemDefaults) is not refereed",
  /outside the refereed fields/.test(J("textDocument/completion", { ...comp(item("a")), foo: 1 }, { ...comp(item("a")), foo: 2 }, comp(item("a"))).reason),
);
const hint = (label) => [{ position: { line: 0, character: 5 }, label: [{ value: label }], kind: 1, paddingLeft: true }];
check("inlay hints that differ in whitespace only: no referee (the JS view drops whitespace)", /lossy/.test(J("textDocument/inlayHint", hint(": unique symbol"), hint(": uniquesymbol"), hint(": unique symbol")).reason));
const diag = (code, line) => ({ range: R(line, 0, line, 1), severity: 1, code, source: "ts", message: `m${code}` });
const diags = (...d) => ({ kind: "full", items: d });
check("diagnostic order has no referee, even when JS has tsc-rs's order", /order has no JS referee/.test(J("textDocument/diagnostic", diags(diag(1, 0), diag(2, 1)), diags(diag(2, 1), diag(1, 0)), diags(diag(2, 1), diag(1, 0))).reason));
check(
  "an extra diagnostic JS has, with the shared ones reordered: still DIFF",
  /order has no JS referee/.test(J("textDocument/diagnostic", diags(diag(1, 0), diag(2, 1)), diags(diag(2, 1), diag(1, 0), diag(3, 2)), diags(diag(2, 1), diag(1, 0), diag(3, 2))).reason),
);
check("an extra diagnostic JS has, in Go's order: agrees", J("textDocument/diagnostic", diags(diag(1, 0)), diags(diag(1, 0), diag(3, 2)), diags(diag(1, 0), diag(3, 2))).agrees);
check("completion item order is free", J("textDocument/completion", comp(item("a"), item("b")), comp(item("b"), item("a"), item("c")), comp(item("c"))).agrees);
check("no normalizer: no referee", /no JS referee/.test(judge(null, null, null).reason));
check("no JS answer at the step: undecided", /no JS answer/.test(judge(N(refs, [a1]), N(refs, [a2]), null).reason));
// Lib anchoring: the line of `interface Console {` differs between Go's and JS's lib.dom.d.ts.
const lineOf = (dir, text) => readFileSync(join(dir, "lib.dom.d.ts"), "utf8").split("\n").findIndex((l) => l.startsWith(text));
const [gl, jl] = [lineOf(GO_LIBS, "interface Console {"), lineOf(JS_LIBS, "interface Console {")];
const lib = (l) => [loc("bundled:///libs/lib.dom.d.ts", l, 10, l, 17)];
check(`lib locations compare by line text (Go line ${gl}, JS line ${jl})`, gl !== jl && sameNormalized(N(refs, lib(gl)), N(refs, lib(jl), JS_LIBS)));
check("lib locations on other lines differ", !sameNormalized(N(refs, lib(gl)), N(refs, lib(gl + 1))));
// tsserver.mjs: semantic tokens in Go's legend (decoded back by the normalizer).
const tok = N("textDocument/semanticTokens/full", { data: [1, 2, 3, TOKEN_TYPES.indexOf("method"), 1] });
const tokRef = tok.sets.tokens.items[0].ref ?? tok.sets.tokens.items[0][0];
check("semantic tokens decode in Go's legend", tokRef === "1:2+3 method.declaration" || tokRef === JSON.stringify("1:2+3 method.declaration"), tokRef);

// ---- part 1b: negative controls (lane H3; reviews/2026-11-05-astra-h1h2.md). Each one is a false
// good of the referee at e0b29c61c: it must not be JS-AGREES.
// verdict: the judge's verdict, or a function giving it (an exception fails the check).
const NEG = (name, verdict, reasonRe) => {
  let v;
  try {
    v = typeof verdict === "function" ? verdict() : verdict;
  } catch (e) {
    return check(`negative control: ${name}`, false, `raised ${e}`);
  }
  check(`negative control: ${name}`, !v.agrees && (!reasonRe || reasonRe.test(v.reason)), JSON.stringify(v).slice(0, 300));
};
const failedJs = normalize(refs, { failed: "references: success false: Error processing request. Internal compiler failure" }, { libDir: JS_LIBS });
NEG("finding 1: a failed tsserver command is no evidence (go-only location)", judge(N(refs, [a1, a3]), N(refs, [a1]), failedJs), /JS failed/);
const diagS = (code, line, source) => ({ ...diag(code, line), source });
NEG("finding 2: diagnostic source changed beside an approved extra diagnostic",
  J("textDocument/diagnostic", diags(diagS(1, 0, "ts")), diags(diagS(1, 0, "xx"), diagS(3, 2, "ts")), diags(diagS(1, 0, "ts"), diagS(3, 2, "ts"))), /outside the refereed fields/);
const link = (o, uri, l) => ({ originSelectionRange: R(0, o, 0, o + 3), targetUri: uri, targetRange: R(l, 0, l + 1, 0), targetSelectionRange: R(l, 0, l, 3) });
const defs = "textDocument/definition";
NEG("finding 2: the origin of a second definition link changed beside an approved extra target",
  J(defs, [link(1, "file:///p/a.ts", 1), link(1, "file:///p/b.ts", 2)], [link(1, "file:///p/a.ts", 1), link(5, "file:///p/b.ts", 2), link(1, "file:///p/c.ts", 3)], [link(1, "file:///p/a.ts", 1), link(1, "file:///p/b.ts", 2), link(1, "file:///p/c.ts", 3)]));
NEG("finding 2: an error's code changed beside an approved location",
  judge(normalize(refs, { result: [a1, a3], error: { code: 1, message: "x" } }), normalize(refs, { result: [a1], error: { code: 2, message: "x" } }), normalize(refs, { result: [a1] }, { libDir: JS_LIBS })), /outside the refereed fields/);
const hoverDoc = (t, doc) => ({ contents: { kind: "markdown", value: "```typescript\n" + t + "\n```\n" + doc }, range: R(0, 0, 0, 1) });
NEG("finding 2: hover markup corrupted beside an approved display change",
  J("textDocument/hover", hoverDoc("const x: 1", "**bold** text"), hoverDoc("const x: 2", "*bold* text"), hoverDoc("const x: 2", "**bold** text")), /lossy/);
// Finding 3: lib.dom.d.ts has 235 copies of the generic addEventListener overload line (0-based 3733
// and 3812 among them): a location moved from one to the other must not look unchanged.
const libLoc = (l) => loc("bundled:///libs/lib.dom.d.ts", l, 4, l, 20);
const jsLine = (goLine) => {
  const go = readFileSync(join(GO_LIBS, "lib.dom.d.ts"), "utf8").split("\n");
  const js = readFileSync(join(JS_LIBS, "lib.dom.d.ts"), "utf8").split("\n");
  const before = go[goLine - 3];
  return js.findIndex((l, i) => l === go[goLine] && js[i - 3] === before && js[i - 1] === go[goLine - 1]);
};
const jl3733 = jsLine(3733);
NEG(`finding 3: a lib location moved between identical lines (Go 3733 -> 3812, JS ${jl3733}) beside an approved location`,
  judge(N(refs, [libLoc(3733), a1]), N(refs, [libLoc(3812), a1, a2]), N(refs, [libLoc(jl3733), a1, a2], JS_LIBS)));
check("finding 3: the two identical lib lines get different anchors", JSON.stringify(N(refs, [libLoc(3733)]).sets.locations.items) !== JSON.stringify(N(refs, [libLoc(3812)]).sets.locations.items));
check("finding 3: the same lib declaration still anchors equal in Go's and JS's copy", jl3733 > 0 && jl3733 !== 3733 && sameNormalized(N(refs, [libLoc(3733)]), N(refs, [libLoc(jl3733)], JS_LIBS)), jl3733);
const hashLoc = loc("file:///p/a%23b.ts", 0, 0, 0, 3);
const fragLoc = loc("file:///p/a#b.ts", 0, 0, 0, 3);
NEG("finding 3: %23 is not # (a location moved to another file beside an approved location)",
  judge(N(refs, [hashLoc, a1]), N(refs, [fragLoc, a1, a2]), N(refs, [hashLoc, a1, a2], JS_LIBS)));
NEG("finding 3: Go and tsc-rs are compared exactly (%40 against @ is a difference with no referee)",
  judge(N(refs, [loc("file:///p/node_modules/%40types/x.d.ts", 0, 0, 0, 1), a1]), N(refs, [loc("file:///p/node_modules/@types/x.d.ts", 0, 0, 0, 1), a1, a2]), N(refs, [loc("file:///p/node_modules/@types/x.d.ts", 0, 0, 0, 1), a1, a2], JS_LIBS)));
check("finding 3: %40 and @ are one path for JS", sameNormalized(N(refs, [loc("file:///p/node_modules/%40types/x.d.ts", 0, 0, 0, 1)]), N(refs, [loc("file:///p/node_modules/@types/x.d.ts", 0, 0, 0, 1)], JS_LIBS)));

// ---- part 2: fixture ---------------------------------------------------------------------------
if (!process.argv.includes("--unit")) {
  const root = join(homedir(), ".cache/perf-rearch/parity/scratch/referee-selftest");
  const out = join(root, "..", "referee-selftest-out");
  rmSync(root, { recursive: true, force: true });
  rmSync(out, { recursive: true, force: true });
  mkdirSync(join(root, "src"), { recursive: true });
  writeFileSync(join(root, "tsconfig.json"), JSON.stringify({ compilerOptions: { strict: true, target: "es2022", module: "nodenext", noEmit: true }, include: ["src"] }, null, 2) + "\n");
  writeFileSync(join(root, "package.json"), JSON.stringify({ name: "referee-selftest", type: "module" }) + "\n");
  writeFileSync(join(root, "src/a.ts"), ["export function pick(n: number) {", '  if (n > 1) return "zero";', '  return "one";', "}", "export const value = pick(2);", ""].join("\n"));
  writeFileSync(join(root, "src/b.ts"), ['import { pick, value } from "./a.js";', "export const again = pick(value.length);", "export const other = again;", ""].join("\n"));
  const go = join(homedir(), ".local/bin/tsgo-lsp-oracle-3e9236361fa9");
  const rules = [
    { method: "textDocument/hover", file: "src/a.ts", word: "pick", edit: "hover-replace", from: '"one" | "zero"', to: '"zero" | "one"' },
    { method: "textDocument/hover", file: "src/b.ts", word: "again", edit: "hover-replace", from: '"one" | "zero"', to: "banana" },
    { method: "textDocument/references", file: "src/a.ts", word: "value", edit: "drop-location", uri: "src/b.ts" },
    { method: "textDocument/hover", file: "src/a.ts", word: "value", edit: "hover-replace", from: '"one" | "zero"', to: '"zero" | "one"', runs: [1] },
  ];
  const proxy = join(HERE, "proxy-lsp.mjs");
  const lspdiff = (tag, extra, { asGo = false, rsProxy = false, proxyRules = rules, env = {}, jsServer = DEFAULT_TSSERVER } = {}) => {
    const state = join(out, `${tag}.runs`);
    mkdirSync(out, { recursive: true });
    // The proxy as a command (lspdiff splits --go and --rs at spaces): node, the script and its settings.
    const settings = join(out, `${tag}.proxy.json`);
    writeFileSync(settings, JSON.stringify({ go, rules: proxyRules, state }));
    const proxyCmd = `${process.execPath} ${proxy} ${settings}`;
    const [goSide, rsSide] = asGo ? [proxyCmd, rsProxy ? proxyCmd : go] : [go, proxyCmd];
    const stdout = execFileSync(
      process.execPath,
      [join(HERE, "../lspdiff.mjs"), "diff", "--project", root, "--file", "src/a.ts", "--file", "src/b.ts", "--positions", "20", "--go", goSide, "--rs", rsSide, "--out", join(out, tag), "--js", jsServer, ...extra],
      { env: { ...process.env, LSPDIFF_PROXY_GO: go, LSPDIFF_PROXY_RULES: JSON.stringify(proxyRules), LSPDIFF_PROXY_STATE: state, ...env }, encoding: "utf8", maxBuffer: 1 << 26 },
    );
    const dir = join(out, tag, "referee-selftest-diff");
    return { stdout, items: JSON.parse(readFileSync(join(dir, "items.json"), "utf8")), js: existsSync(join(dir, "js.json")) ? JSON.parse(readFileSync(join(dir, "js.json"), "utf8")) : null };
  };
  const find = (items, re) => items.filter((it) => re.test(it.step));
  const hovers = "textDocument/hover,textDocument/references";
  const r1 = lspdiff("hover-refereed", ["--js-kinds", hovers, "--js-all"]);
  const pick = find(r1.items, /^src\/a\.ts:(1:19|5:24) hover$/);
  check("a hover in JS's union order is JS-AGREES", pick.length > 0 && pick.every((it) => it.status === "JS-AGREES"), JSON.stringify(pick));
  const again = find(r1.items, /^src\/b\.ts:(2:16|3:24) hover$/);
  check("a hover nobody prints is DIFF with JS's reason", again.length > 0 && again.every((it) => it.status === "DIFF" && /display/.test(it.js?.reason)), JSON.stringify(again));
  const dropped = find(r1.items, /^src\/a\.ts:5:16 references$/);
  check("dropping a location JS has is DIFF", dropped.length > 0 && dropped.every((it) => it.status === "DIFF" && /go-only .*JS has it/.test(it.js?.reason)), JSON.stringify(dropped));
  const flip = find(r1.items, /^src\/a\.ts:5:16 hover$/);
  check("a rewrite of run 1 only is rs-nondet", flip.length > 0 && flip.every((it) => it.status === "DIFF" && /rs-nondet/.test(it.detail)), JSON.stringify(flip));
  check("the confirming tsc-rs run ran", /rs-confirm: \d+ answers/.test(r1.stdout));
  const touched = new Set([...pick, ...again, ...dropped, ...flip]);
  const others = r1.items.filter((it) => !touched.has(it) && it.status !== "MATCH");
  check("every answer the proxy leaves alone is MATCH", others.length === 0, JSON.stringify(others.slice(0, 5)));
  check("--js-all writes the calibration", !!r1.js?.calibration?.["textDocument/definition"]?.goEqual, JSON.stringify(r1.js?.calibration).slice(0, 300));
  const r2 = lspdiff("default-kinds", []);
  const pick2 = find(r2.items, /^src\/a\.ts:(1:19|5:24) hover$/);
  check("with the default kinds, the hover is DIFF (hover is not refereed)", pick2.length > 0 && pick2.every((it) => it.status === "DIFF"), JSON.stringify(pick2));
  const dropped2 = find(r2.items, /^src\/a\.ts:5:16 references$/);
  check("with the default kinds, the dropped reference is still DIFF", dropped2.length > 0 && dropped2.every((it) => it.status === "DIFF"), JSON.stringify(dropped2));
  // Go broken: the proxy is Go (runs 1 and 2 are Go's: --go-runs 2); Go's own server is tsc-rs.
  const goRules = [
    { method: "textDocument/hover", file: "src/b.ts", word: "again", edit: "crash", runs: [1] },
    { method: "textDocument/references", file: "src/a.ts", word: "value", edit: "hang", runs: [2] },
  ];
  const r3 = lspdiff("go-broken", [], { asGo: true, proxyRules: goRules, env: { LSPDIFF_REQUEST_TIMEOUT_MS: "5000" } });
  const at = (re) => r3.items.findIndex((it) => re.test(it.step));
  const [hang, crash] = [at(/^src\/a\.ts:5:16 references$/), at(/^src\/b\.ts:2:16 hover$/)];
  const before = r3.items.slice(0, hang);
  const after = r3.items.slice(hang).filter((it) => !it.method.startsWith("server/")); // answers, not the run-level items
  check("answers before Go's first crash or hang are MATCH", hang > 0 && before.every((it) => it.status === "MATCH"), JSON.stringify(before.filter((it) => it.status !== "MATCH").slice(0, 3)));
  check("a Go timeout makes the answers from there GO-BROKEN", after.length > 0 && after.every((it) => it.status === "GO-BROKEN" && /go2 timed out/.test(it.detail)), JSON.stringify(after.find((it) => it.status !== "GO-BROKEN" || !/go2 timed out/.test(it.detail))));
  // The run-level items (server/session, server/order: lane H4) judge whole runs, not answers.
  const answers3 = r3.items.slice(crash).filter((it) => !it.method.startsWith("server/"));
  check("a Go crash is named in the detail from the crashed request on", crash > hang && answers3.length > 0 && answers3.every((it) => /go crashed .*server exited \(3\)/.test(it.detail)), JSON.stringify(r3.items[crash]));
  check("the GO-BROKEN answers were confirmed by one more tsc-rs run", /rs-confirm: \d+ answers/.test(r3.stdout));

  // ---- part 2b: negative controls through lspdiff (lane H3) ----------------------------------
  // Finding 1: the reviewer's failing tsserver (success:false on every command) as the referee; a
  // references answer that drops a location: the failed JS answer must not count as "JS lacks it".
  const failing = join(HERE, "failing-tsserver.cjs");
  const lspdiffWith = (tag, extra, opts) => {
    try {
      return lspdiff(tag, extra, opts);
    } catch (e) {
      return { stdout: String(e.stdout ?? e), items: [], js: null, error: String(e.message).slice(0, 300) };
    }
  };
  const fjs = lspdiffWith("failing-js", ["--js-kinds", "textDocument/references"], { jsServer: failing, proxyRules: [rules[2]] });
  const fdrop = find(fjs.items, /^src\/a\.ts:5:16 references$/);
  check("negative control: finding 1: a failing tsserver gives no JS-AGREES (dropped reference)", fdrop.length > 0 && fdrop.every((it) => it.status === "DIFF" && /JS failed|is no evidence/.test(it.js?.reason ?? "")), JSON.stringify(fdrop).slice(0, 300) + (fjs.error ?? ""));
  // Finding 5: the confirming run is a complete tsc-rs run. Both sides are proxies (runs: go 1, go2
  // 2, rs 3, rs-confirm 4); Go's run 1 crashes late, so the later answers are GO-BROKEN and need a
  // confirming run. Run 4 changes an early MATCH answer, or crashes at the first hover.
  const lateCrash = { method: "textDocument/hover", file: "src/b.ts", word: "other", edit: "crash", runs: [1] };
  const both = { asGo: true, rsProxy: true };
  const c1 = lspdiffWith("confirm-change", [], { ...both, proxyRules: [lateCrash, { method: "textDocument/hover", file: "src/a.ts", word: "pick", edit: "hover-replace", from: "pick", to: "PICK", runs: [4] }] });
  const early = find(c1.items, /^src\/a\.ts:1:19 hover$/);
  check("negative control: finding 5: the confirming run changing a MATCH answer is rs-nondet", early.length > 0 && early.every((it) => it.status === "DIFF" && /rs-nondet: the confirming/.test(it.detail)), JSON.stringify(early).slice(0, 300) + (c1.error ?? ""));
  const c2 = lspdiffWith("confirm-crash", [], { ...both, proxyRules: [lateCrash, { method: "textDocument/hover", file: "src/a.ts", word: "pick", edit: "crash", runs: [4] }] });
  const server = c2.items.filter((it) => it.step === "rs-confirm server");
  const afterCrash = c2.items.filter((it) => it.status === "MATCH" && it.i > (c2.items.find((x) => /^src\/a\.ts:1:19 hover$/.test(x.step))?.i ?? -1));
  check("negative control: finding 5: a crash of the confirming run is a DIFF (its own item, and every answer after it)", server.length === 1 && server[0].status === "DIFF" && server[0].rsFailure === "crash" && afterCrash.length === 0, JSON.stringify(server) + ` ${afterCrash.length} MATCH after the crash` + (c2.error ?? ""));
  // Finding 11: a prepareRename that tsc-rs answers with null: Go is asked the rename, tsc-rs is not.
  // That is one difference; the answers after it stay aligned.
  const cond = lspdiffWith("conditional", [], { proxyRules: [{ method: "textDocument/prepareRename", file: "src/a.ts", word: "pick", edit: "null-result" }] });
  const renameIt = find(cond.items, /^src\/a\.ts:1:19 rename$/);
  const prepIt = find(cond.items, /^src\/a\.ts:1:19 prepareRename$/);
  const condOthers = cond.items.filter((it) => !renameIt.includes(it) && !prepIt.includes(it) && it.status !== "MATCH");
  check("negative control: finding 11: a request only Go was asked is one recorded difference", renameIt.length === 1 && renameIt[0].status === "DIFF" && /only Go was asked/.test(renameIt[0].detail ?? ""), JSON.stringify(renameIt) + (cond.error ?? ""));
  check("negative control: finding 11: the answers after it stay aligned (all MATCH)", prepIt.length === 1 && prepIt[0].status === "DIFF" && condOthers.length === 0, JSON.stringify(condOthers.slice(0, 3)));
}

console.log(failed.length ? `referee self-test FAILED: ${failed.length} checks` : "referee self-test OK");
process.exit(failed.length ? 1 : 0);
