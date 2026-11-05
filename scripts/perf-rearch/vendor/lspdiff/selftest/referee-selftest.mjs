#!/usr/bin/env node
// Self-test of the LSP tier's JS referee (referee.mjs, tsserver.mjs, lspdiff.mjs --js).
//
//   node scripts/perf-rearch/vendor/lspdiff/selftest/referee-selftest.mjs [--unit]
//
// Part 1 (unit): the judge on hand-made answers: every rule of JS-AGREES, the residue and
// "no refereed point" guards, ordered sets, the lib anchoring, and tsserver.mjs's conversions.
// Part 2 (fixture, skipped by --unit): lspdiff diff on a two-file project
// (~/.cache/perf-rearch/parity/scratch/referee-selftest/), Go's LSP server against
// selftest/proxy-lsp.mjs as "tsc-rs" (Go with some answers rewritten), with JS tsserver 6.0.3 as the
// referee. It checks:
//   - a hover rewritten to JS's union order (JS 6 prints "zero" | "one", Go "one" | "zero") is
//     JS-AGREES when hover is refereed (--js-kinds), and DIFF with the default kinds;
//   - a hover rewritten to a type nobody prints is DIFF, with JS's reason;
//   - a references answer that drops a location JS has is DIFF ("go-only ...: JS has it");
//   - a rewrite JS agrees with on run 1 only is DIFF rs-nondet (the confirming tsc-rs run);
//   - every answer the proxy leaves alone is MATCH, and --js-all writes the calibration.
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
check("inlay hints that differ in whitespace only: no refereed point", /no refereed point/.test(J("textDocument/inlayHint", hint(": unique symbol"), hint(": uniquesymbol"), hint(": unique symbol")).reason));
const diag = (code, line) => ({ range: R(line, 0, line, 1), severity: 1, code, source: "ts", message: `m${code}` });
const diags = (...d) => ({ kind: "full", items: d });
check("diagnostic order: JS in tsc-rs's order agrees", J("textDocument/diagnostic", diags(diag(1, 0), diag(2, 1)), diags(diag(2, 1), diag(1, 0)), diags(diag(2, 1), diag(3, 2), diag(1, 0))).agrees);
check("diagnostic order: JS in Go's order disagrees", !J("textDocument/diagnostic", diags(diag(1, 0), diag(2, 1)), diags(diag(2, 1), diag(1, 0)), diags(diag(1, 0), diag(2, 1))).agrees);
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
check("semantic tokens decode in Go's legend", tok.sets.tokens.items[0][0] === "1:2+3 method.declaration", tok.sets.tokens.items[0][0]);

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
  const lspdiff = (tag, extra) => {
    const state = join(out, `${tag}.runs`);
    mkdirSync(out, { recursive: true });
    const stdout = execFileSync(
      process.execPath,
      [join(HERE, "../lspdiff.mjs"), "diff", "--project", root, "--file", "src/a.ts", "--file", "src/b.ts", "--positions", "20", "--go", go, "--rs", join(HERE, "proxy-lsp.mjs"), "--out", join(out, tag), "--js", DEFAULT_TSSERVER, ...extra],
      { env: { ...process.env, LSPDIFF_PROXY_GO: go, LSPDIFF_PROXY_RULES: JSON.stringify(rules), LSPDIFF_PROXY_STATE: state }, encoding: "utf8", maxBuffer: 1 << 26 },
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
}

console.log(failed.length ? `referee self-test FAILED: ${failed.length} checks` : "referee self-test OK");
process.exit(failed.length ? 1 : 0);
