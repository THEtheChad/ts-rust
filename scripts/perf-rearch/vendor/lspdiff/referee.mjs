// The JS-AGREES referee of the LSP tier (docs/perf-rearch/parity.md, "JS tsserver as the LSP
// referee"). normalize(method, answer) maps an LSP answer (Go's, tsc-rs's, or tsserver's after
// tsserver.mjs converted it) to a shared shape; judge(method, go, rs, js) says whether JS sides
// with tsc-rs on every point where tsc-rs differs from Go.
//
// The shared shape of an answer is { scalars, sets, rest }:
//   scalars  field -> value: one point each (hover display text, signature help indices, ...)
//   sets     field -> { order, items: [[key, residue]] }: list answers. key holds every refereed
//            field of an element (a location, a diagnostic's code, span and text, ...); residue the
//            element's fields JS cannot referee (completion data, inlay hint part locations, ...).
//   rest     everything of the answer outside scalars and sets that JS cannot referee
// JS-AGREES needs all of:
//   - rest equal on Go and tsc-rs, and the residues of every element both have equal (a difference
//     outside the refereed fields has no referee);
//   - per scalar that differs, JS's value equal to tsc-rs's;
//   - per set, each element Go has and tsc-rs lacks absent from JS (JS has it at most as often as
//     tsc-rs), and each element tsc-rs has and Go lacks present in JS (at least as often);
//   - per set whose order counts (order "fixed": every list but completion items, whose order
//     lspdiff ignores too), the elements Go and tsc-rs both have in the same order. Order has no JS
//     referee (tsserver.mjs assembles some lists itself, and the brief names pull-diagnostic order
//     as unrefereed), so a changed order stays a DIFF;
//   - at least one point (an answer that differs only where normalization looks away is not
//     refereed).
// Points where tsc-rs equals Go need nothing from JS.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { TOKEN_MODIFIERS, TOKEN_TYPES } from "./tsserver.mjs";

// The bundled libs: Go's (the pin's tsc/internal/bundled/libs; tsc-rs bundles the same files) and
// JS's (typescript/lib). They differ (lib.dom.d.ts alone in 1,470 lines), so a location in a lib
// is compared by the text of its lines, not by line numbers (anchorLibs).
export const GO_LIBS = join(homedir(), ".explore/repos/microsoft__TypeScript@3e9236361fa9/tsc/internal/bundled/libs");
export const JS_LIBS = join(homedir(), ".cache/perf-rearch/js-tsc/node_modules/typescript/lib");
const libCache = new Map();
function libLines(dir, name) {
  const key = `${dir}/${name}`;
  if (!libCache.has(key)) {
    try {
      libCache.set(key, readFileSync(key, "utf8").split("\n"));
    } catch {
      libCache.set(key, null);
    }
  }
  return libCache.get(key);
}

// A file uri without percent-encoding: Go encodes "@" (node_modules/%40types), Node does not.
const plainUri = (u) => {
  try {
    return u.startsWith("file://") ? decodeURIComponent(u) : u;
  } catch {
    return u;
  }
};

// In place: every uri without percent-encoding (also the keys of a WorkspaceEdit's changes), and
// every range in a bundled:///libs file with the text of its lines for line numbers
// ("«interface Console {»").
function anchorLibs(value, libDir) {
  if (!value || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    for (const v of value) anchorLibs(v, libDir);
    return value;
  }
  for (const k of ["uri", "targetUri"]) if (typeof value[k] === "string") value[k] = plainUri(value[k]);
  if (value.changes && typeof value.changes === "object" && !Array.isArray(value.changes)) {
    value.changes = Object.fromEntries(Object.entries(value.changes).map(([u, v]) => [plainUri(u), v]));
  }
  const uri = value.uri ?? value.targetUri;
  if (typeof uri === "string" && uri.startsWith("bundled:///libs/")) {
    const lines = libLines(libDir, uri.slice("bundled:///libs/".length));
    const anchor = (p) => {
      if (p && typeof p.line === "number") p.line = lines?.[p.line] !== undefined ? `«${lines[p.line].trim().slice(0, 120)}»` : `?${p.line}`;
    };
    for (const k of ["range", "targetRange", "targetSelectionRange"]) {
      if (value[k]) {
        anchor(value[k].start);
        anchor(value[k].end);
      }
    }
  }
  for (const v of Object.values(value)) anchorLibs(v, libDir);
  return value;
}

const J = (v) => JSON.stringify(v ?? null);
const r2s = (r) => (r ? `${r.start.line}:${r.start.character}-${r.end.line}:${r.end.character}` : "-");

// Hover and documentation markdown as plain words: links ([text](target) is text), the code fence,
// emphasis, inline code, the " — " of tags and all whitespace runs are dropped or collapsed.
export function plainDoc(md) {
  return String(md ?? "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/```\w*\n?/g, " ")
    .replace(/[*_`]/g, "")
    .replace(/ — /g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function markup(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(markup).join("\n");
  return v.value ?? "";
}

// Hover contents: the first ```typescript block is the display text, the rest documentation.
function splitHover(value) {
  const m = /^```typescript\n([\s\S]*?)\n```\n?([\s\S]*)$/.exec(value);
  return m ? { display: m[1], doc: plainDoc(m[2]) } : { display: null, doc: plainDoc(value) };
}

// order "fixed": Go's order of the shared elements must be kept; "free": the list is a set.
const set = (items, order = "fixed") => ({ order, items });
const without = (o, keys) => {
  if (!o || typeof o !== "object") return o;
  const c = { ...o };
  for (const k of keys) delete c[k];
  return Object.keys(c).length ? c : null;
};
const locKey = (l) => `${l.uri ?? l.targetUri} ${r2s(l.range ?? l.targetSelectionRange)}`;

function decodeTokens(data) {
  const out = [];
  let [line, ch] = [0, 0];
  for (let i = 0; i + 4 < data.length; i += 5) {
    line += data[i];
    ch = data[i] === 0 ? ch + data[i + 1] : data[i + 1];
    const mods = TOKEN_MODIFIERS.filter((_, b) => data[i + 4] & (1 << b));
    out.push(`${line}:${ch}+${data[i + 2]} ${TOKEN_TYPES[data[i + 3]] ?? data[i + 3]}${mods.length ? "." + mods.join(".") : ""}`);
  }
  return out;
}

// An inlay hint's label without whitespace: Go joins the parts of an interactive hint without the
// spaces between words ("uniquesymbol", "Record<string,number>"), JS keeps them.
const inlayLabel = (label) => (typeof label === "string" ? label : label.map((p) => p.value).join("")).replace(/\s+/g, "");

function flattenSymbols(list, path, out) {
  for (const s of list ?? []) {
    const p = path ? `${path}/${s.name}` : s.name;
    out.push([`${p} kind ${s.kind} ${r2s(s.range)} sel ${r2s(s.selectionRange)}`, J(without(s, ["name", "kind", "range", "selectionRange", "children"]))]);
    flattenSymbols(s.children, p, out);
  }
  return out;
}

function diagnosticKey(d) {
  return J({
    range: r2s(d.range),
    severity: d.severity,
    code: d.code,
    message: d.message,
    tags: d.tags ?? [],
    related: (d.relatedInformation ?? []).map((r) => `${locKey(r.location)} ${r.message}`),
  });
}

// method -> (answer.result) -> shared shape. A method that is not here has no JS referee.
export const NORMALIZERS = {
  "textDocument/diagnostic": (r) => ({
    scalars: {},
    sets: { items: set((r?.items ?? []).map((d) => [diagnosticKey(d), J(without(d, ["range", "severity", "code", "message", "tags", "relatedInformation", "source"]))])) },
    rest: J(without(r, ["items"])),
  }),
  "textDocument/hover": (r) => {
    const h = r ? splitHover(markup(r.contents)) : null;
    return { scalars: { display: h?.display ?? null, doc: h?.doc ?? null, range: r2s(r?.range) }, sets: {}, rest: J(without(r, ["contents", "range"])) };
  },
  "textDocument/completion": (r) => {
    const items = Array.isArray(r) ? r : r?.items ?? [];
    return {
      scalars: { isIncomplete: !!r?.isIncomplete },
      sets: {
        items: set(
          items.map((i) => [
            J({ label: i.label, kind: i.kind, sortText: i.sortText, insertText: i.insertText ?? null, source: i.labelDetails?.description ?? null }),
            J({ ...without(i, ["label", "kind", "sortText", "insertText", "labelDetails"]), labelDetail: i.labelDetails?.detail ?? null }),
          ]),
          "free",
        ),
      },
      rest: J(without(Array.isArray(r) ? null : r, ["items", "isIncomplete", "itemDefaults"])),
    };
  },
  "completionItem/resolve": (r) => ({
    scalars: { label: r?.label ?? null, detail: r?.detail ?? null, documentation: plainDoc(markup(r?.documentation)) },
    sets: { edits: set((r?.additionalTextEdits ?? []).map((e) => [`${r2s(e.range)} ${J(e.newText)}`, "null"])) },
    rest: "null",
  }),
  "textDocument/definition": (r) => {
    const list = r == null ? [] : Array.isArray(r) ? r : [r];
    return {
      scalars: { origin: r2s(list[0]?.originSelectionRange) },
      // targetRange (the declaration around the name) is not refereed: JS 6 gives the initializer's
      // span for a function-valued const, Go the declaration's.
      sets: { targets: set(list.map((l) => [locKey(l), J(l.targetUri ? r2s(l.targetRange) : null)])) },
      rest: "null",
    };
  },
  "textDocument/typeDefinition": (r) => locations(r),
  "textDocument/implementation": (r) => locations(r),
  "textDocument/references": (r) => locations(r),
  "textDocument/documentHighlight": (r) => ({ scalars: {}, sets: { highlights: set((r ?? []).map((h) => [`${r2s(h.range)} ${h.kind ?? 1}`, "null"])) }, rest: "null" }),
  "textDocument/signatureHelp": (r) => ({
    scalars: { activeSignature: r?.activeSignature ?? null, activeParameter: r?.activeParameter ?? null },
    sets: {
      signatures: set(
        (r?.signatures ?? []).map((s) => [
          J({ label: s.label, params: (s.parameters ?? []).map((p) => (Array.isArray(p.label) ? s.label.slice(...p.label) : p.label)), doc: plainDoc(markup(s.documentation)) }),
          J(without(s, ["label", "parameters", "documentation", "activeParameter"])),
        ]),
      ),
    },
    rest: "null",
  }),
  "textDocument/prepareRename": (r) => ({ scalars: { range: r2s(r?.range ?? r), placeholder: r?.placeholder ?? null }, sets: {}, rest: "null" }),
  "textDocument/rename": (r) => {
    const edits = [];
    for (const [uri, list] of Object.entries(r?.changes ?? {})) for (const e of list) edits.push([`${uri} ${r2s(e.range)} ${J(e.newText)}`, "null"]);
    for (const dc of r?.documentChanges ?? []) for (const e of dc.edits ?? []) edits.push([`${dc.textDocument.uri} ${r2s(e.range)} ${J(e.newText)}`, "null"]);
    return { scalars: { null: r == null }, sets: { edits: set(edits) }, rest: "null" };
  },
  "textDocument/inlayHint": (r) => ({
    scalars: {},
    sets: {
      hints: set(
        (r ?? []).map((h) => [
          J({ at: `${h.position.line}:${h.position.character}`, label: inlayLabel(h.label), kind: h.kind ?? null, l: !!h.paddingLeft, r: !!h.paddingRight }),
          J({ parts: typeof h.label === "string" ? null : h.label.map((p) => (p.location ? locKey(p.location) : null)), ...without(h, ["position", "label", "kind", "paddingLeft", "paddingRight"]) }),
        ]),
      ),
    },
    rest: "null",
  }),
  "textDocument/documentSymbol": (r) => ({ scalars: {}, sets: { symbols: set(flattenSymbols(r, "", [])) }, rest: "null" }),
  "textDocument/semanticTokens/full": (r) => ({ scalars: {}, sets: { tokens: set(decodeTokens(r?.data ?? []).map((t) => [t, "null"])) }, rest: J(without(r, ["data"])) }),
  "textDocument/foldingRange": (r) => ({
    scalars: {},
    sets: { ranges: set((r ?? []).map((f) => [`${f.startLine}:${f.startCharacter ?? "-"}-${f.endLine}:${f.endCharacter ?? "-"} ${f.kind ?? ""}`, J(without(f, ["startLine", "startCharacter", "endLine", "endCharacter", "kind"]))])) },
    rest: "null",
  }),
};

function locations(r) {
  const list = r == null ? [] : Array.isArray(r) ? r : [r];
  return { scalars: {}, sets: { locations: set(list.map((l) => [l.targetUri ? `${l.targetUri} ${r2s(l.targetSelectionRange)}` : locKey(l), "null"])) }, rest: "null" };
}

export const hasNormalizer = (method) => !!NORMALIZERS[method];

// An answer ({ result, error }) in the shared shape; an error is one scalar. root: the project root
// (masked, as lspdiff does); libDir: the bundled libs of the server that gave the answer (GO_LIBS
// for Go and tsc-rs, JS_LIBS for tsserver).
export function normalize(method, answer, { root, libDir = GO_LIBS } = {}) {
  const f = NORMALIZERS[method];
  if (!f) return null;
  let v = JSON.parse(J(answer?.result));
  if (root) v = JSON.parse(J(v).replaceAll(root, "<root>"));
  const n = f(anchorLibs(v, libDir) ?? null);
  n.scalars.error = answer?.error ? String(answer.error.message ?? J(answer.error)) : null;
  return n;
}

const count = (list) => {
  const m = new Map();
  for (const k of list) m.set(k, (m.get(k) ?? 0) + 1);
  return m;
};
const short = (s) => (s.length > 160 ? s.slice(0, 157) + "..." : s);

// The refereed part of a normalized answer: scalars and set keys (sorted: order is not refereed),
// without residues and rest. Two answers whose views are equal agree on every point JS could decide.
export function refereedView(n) {
  if (!n) return null;
  const sets = {};
  for (const [k, v] of Object.entries(n.sets)) sets[k] = v.items.map(([key]) => key).sort();
  return J({ scalars: n.scalars, sets });
}

// Whether two normalized answers agree on every refereed point (the calibration's question).
export function sameNormalized(a, b) {
  return refereedView(a) === refereedView(b);
}

// JS-AGREES on one answer. go, rs, js: normalized (normalize()). Returns { agrees, points, reason }:
// points are the differing points with what JS said ("rs-only <elem>: JS has it"); reason, when JS
// does not agree, the first point it failed or why the difference has no referee.
export function judge(go, rs, js) {
  if (!go || !rs) return { agrees: false, points: [], reason: "no JS referee for this kind" };
  if (!js) return { agrees: false, points: [], reason: "no JS answer at this step" };
  if (go.rest !== rs.rest) return { agrees: false, points: [], reason: `differs outside the refereed fields: ${short(go.rest)} vs ${short(rs.rest)}` };
  const points = [];
  let reason = null;
  const fail = (why) => {
    reason ??= why;
  };
  for (const k of new Set([...Object.keys(go.scalars), ...Object.keys(rs.scalars)])) {
    const [g, r, j] = [J(go.scalars[k]), J(rs.scalars[k]), J(js.scalars[k])];
    if (g === r) continue;
    points.push(`${k}: ${short(r)} (Go ${short(g)})`);
    if (j !== r) fail(`${k}: JS ${short(j)}, tsc-rs ${short(r)}, Go ${short(g)}`);
  }
  for (const k of new Set([...Object.keys(go.sets), ...Object.keys(rs.sets)])) {
    const gi = go.sets[k]?.items ?? [];
    const ri = rs.sets[k]?.items ?? [];
    const jk = count((js.sets[k]?.items ?? []).map(([key]) => key));
    const [gc, rc] = [count(gi.map(([key]) => key)), count(ri.map(([key]) => key))];
    for (const [key, n] of gc) {
      const m = rc.get(key) ?? 0;
      if (n > m) {
        points.push(`${k} go-only ${short(key)}`);
        if ((jk.get(key) ?? 0) > m) fail(`${k} go-only ${short(key)}: JS has it`);
      }
    }
    for (const [key, n] of rc) {
      const m = gc.get(key) ?? 0;
      if (n > m) {
        points.push(`${k} rs-only ${short(key)}`);
        if ((jk.get(key) ?? 0) < n) fail(`${k} rs-only ${short(key)}: JS lacks it`);
      }
    }
    // The residues of the elements both have: no referee for a difference there.
    const res = (items) => {
      const m = new Map();
      for (const [key, residue] of items) m.set(key, [...(m.get(key) ?? []), residue].sort());
      return m;
    };
    const [gr, rr] = [res(gi), res(ri)];
    for (const [key, list] of gr) {
      const other = rr.get(key);
      if (!other) continue;
      const n = Math.min(list.length, other.length);
      if (list.length === other.length ? J(list) !== J(other) : J(list.slice(0, n)) !== J(other.slice(0, n))) {
        return { agrees: false, points, reason: `${k} ${short(key)} differs outside the refereed fields` };
      }
    }
    if (go.sets[k]?.order === "fixed" || rs.sets[k]?.order === "fixed") {
      const shared = (items, other) => {
        const left = new Map(other);
        return items.map(([key]) => key).filter((key) => {
          const n = left.get(key) ?? 0;
          if (n > 0) left.set(key, n - 1);
          return n > 0;
        });
      };
      if (J(shared(gi, rc)) !== J(shared(ri, gc))) return { agrees: false, points, reason: `${k}: the order differs from Go's (order has no JS referee)` };
    }
  }
  if (!points.length) return { agrees: false, points, reason: "no refereed point differs (the difference is outside the normalized fields)" };
  return { agrees: !reason, points, reason };
}

// The request kinds JS referees: those whose calibration (docs/perf-rearch/parity.md, "JS tsserver
// as the LSP referee"; full LSP tier on head efef52257, 2026-11-05) showed JS's normalized answer
// equal to Go's on at least 90% of the answers where tsc-rs equals Go. The others have a normalizer
// (for the calibration and the report) but no JS-AGREES: hover 66.7%, signatureHelp 88.9%,
// typeDefinition 81.9%, completion 54.3%, completionItem/resolve 63.2%, inlayHint 55.6%,
// semanticTokens 69.4%, documentSymbol 63.9%, foldingRange 72.2%, and implementation (not asked by
// any session, so not calibrated).
export const REFEREED_KINDS = new Set([
  "textDocument/definition", // 217/218 99.5%
  "textDocument/diagnostic", // 133/142 93.7%
  "textDocument/documentHighlight", // 199/216 92.1%
  "textDocument/prepareRename", // 36/36 100%
  "textDocument/references", // 189/197 95.9%
  "textDocument/rename", // 24/25 96.0%
]);

// Pairs each answer of a Go/tsc-rs log with JS's answer at the same step: the n-th answer of a step
// name with JS's n-th answer of that name (JS can take another branch of a script, such as a
// prepareRename that JS refuses, so indices need not agree).
export function alignJs(goLog, jsLog) {
  const byStep = new Map();
  for (const e of jsLog) byStep.set(e.step, [...(byStep.get(e.step) ?? []), e]);
  const seen = new Map();
  return goLog.map((e) => {
    const n = seen.get(e.step) ?? 0;
    seen.set(e.step, n + 1);
    return byStep.get(e.step)?.[n];
  });
}

// The JS step of the LSP tier, on lspdiff's items ({ i, step, method, status, detail }), in place.
// logs: { go, rs, ... } (lspdiff's logs). runJs(): JS's log (from its cache or a tsserver run).
// rerunRs(): one more tsc-rs log, asked only when an item would become JS-AGREES and tsc-rs ran
// once (its determinism comes first); answerKey(entry, method): lspdiff's comparison key of an answer. all: run JS even without a DIFF to referee, and
// record JS's answer on every item (calibration, and the report on GO-UNSTABLE items).
// Returns { ran, calibration: { method: { goEqual, jsEqual, noJs } }, decided }.
export async function applyJsReferee({ items, logs, root, runJs, rerunRs, answerKey, all = false, kinds = REFEREED_KINDS } = {}) {
  kinds ??= REFEREED_KINDS;
  const candidates = items.filter((it) => it.status === "DIFF" && !it.detail && kinds.has(it.method));
  if (!(all ? items.some((it) => hasNormalizer(it.method)) : candidates.length)) return { ran: false, calibration: {}, decided: 0 };
  const jsLog = await runJs();
  const js = alignJs(logs.go, jsLog);
  const calibration = {};
  const verdicts = new Map();
  for (const it of items) {
    if (!hasNormalizer(it.method)) continue;
    // Without `all`, only the candidates (normalizing umami's MATCH answers takes 30 s).
    if (!all && !candidates.includes(it)) continue;
    const [g, r] = [normalize(it.method, logs.go[it.i], { root }), normalize(it.method, logs.rs[it.i], { root })];
    const j = js[it.i] ? normalize(it.method, js[it.i], { root, libDir: JS_LIBS }) : null;
    if (it.status === "MATCH") {
      const c = (calibration[it.method] ??= { goEqual: 0, jsEqual: 0, noJs: 0 });
      c.goEqual++;
      if (!j) c.noJs++;
      else if (sameNormalized(g, j)) c.jsEqual++;
      continue;
    }
    if (it.status === "GO-UNSTABLE") {
      // Report only (GO-BROKEN comes before JS-AGREES): which of Go's runs JS equals, and tsc-rs.
      const goRuns = Object.keys(logs).filter((k) => /^go\d*$/.test(k));
      const same = (k) => sameNormalized(normalize(it.method, logs[k][it.i], { root }), j);
      it.js = j
        ? { equalsRs: sameNormalized(r, j), equalsGoRuns: goRuns.filter(same), rsEqualsGoRuns: goRuns.filter((k) => sameNormalized(normalize(it.method, logs[k][it.i], { root }), r)) }
        : { reason: "no JS answer at this step" };
      continue;
    }
    const v = judge(g, r, j);
    verdicts.set(it, v);
    it.js = { agrees: v.agrees, refereed: kinds.has(it.method), ...(v.reason ? { reason: v.reason } : {}), points: v.points.slice(0, 20), ...(v.points.length > 20 ? { morePoints: v.points.length - 20 } : {}) };
  }
  let agreeing = candidates.filter((it) => verdicts.get(it)?.agrees);
  if (agreeing.length && rerunRs) {
    const again = await rerunRs();
    for (const it of agreeing) {
      if (answerKey(again[it.i], it.method) !== answerKey(logs.rs[it.i], it.method)) {
        it.detail = "rs-nondet: the run made to confirm JS-AGREES differs from run 1";
        delete it.js.agrees;
      }
    }
    agreeing = agreeing.filter((it) => !it.detail);
  }
  for (const it of agreeing) {
    it.status = "JS-AGREES";
    const n = it.js.points.length + (it.js.morePoints ?? 0);
    it.detail = `JS sides with tsc-rs on ${n} point${n === 1 ? "" : "s"}: ${it.js.points.slice(0, 3).join("; ")}`;
  }
  return { ran: true, calibration, decided: agreeing.length, jsAnswers: jsLog.length };
}
