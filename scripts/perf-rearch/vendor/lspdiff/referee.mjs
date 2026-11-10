// The JS-AGREES referee of the LSP tier (docs/perf-rearch/parity.md, "JS tsserver as the LSP
// referee"). normalize(method, answer) maps an LSP answer (Go's, tsc-rs's, or tsserver's after
// tsserver.mjs converted it) to a shared shape; judge(method, go, rs, js) says whether JS sides
// with tsc-rs on every point where tsc-rs differs from Go.
//
// Every part of a normalized answer has two views:
//   exact  the raw JSON of that part (the project root masked, nothing else changed). Go and tsc-rs
//          are compared on it: they bundle the same libs and write the same encodings, so any byte
//          that differs between them is a point.
//   ref    the same part with equivalent encodings mapped to one form, for comparing with JS only:
//          file URIs parsed structurally (fileURLToPath plus the query and fragment, so %40 and @
//          are one path, and %23 and # stay apart), and every position in a bundled lib file
//          anchored to its text (anchorLine: a window of lines that is unique in Go's and JS's copy
//          of that lib). A position whose anchor is ambiguous has no ref (null).
// The shape is { failed, scalars, sets, rest }:
//   failed   why tsserver did not give this answer (tsserver.mjs: success:false, a malformed or
//            missing body, a crash); a failed answer is no evidence for anything
//   scalars  field -> { exact, ref, lossy }: one point each
//   sets     field -> { order, lossy, items: [{ exact, ref, residue, refResidue }] }: list answers.
//            exact/ref: the element's refereed fields; residue: every other field of the element,
//            so key and residue together are the whole element
//   rest     the exact JSON of everything else in the answer (error code and data included)
// lossy marks a ref view that merges values that are not equivalent (hover and signature
// documentation as plain words, inlay hint labels without whitespace): such a field is good for the
// calibration, but a point on it has no referee.
// JS-AGREES needs all of:
//   - JS's answer present and not failed;
//   - rest equal on Go and tsc-rs, and the residues of every element both have equal;
//   - per scalar that differs: a ref that is neither lossy nor ambiguous, that differs between Go and
//     tsc-rs (else they differ only in encoding: no referee), and that JS's ref equals;
//   - per set: each element Go has and tsc-rs lacks absent from JS (JS has its ref at most as often
//     as tsc-rs), and each element tsc-rs has and Go lacks present in JS as a whole element: its key
//     and its residue both JS's for that element, at least as often (lane H4: never a residue Go
//     gives another element); no
//     ambiguous ref, and no ref that two distinct exact elements share (that merge would let one
//     stand for the other);
//   - per set whose order counts (order "fixed": every list but completion items, whose order
//     lspdiff ignores too), the elements Go and tsc-rs both have in the same order. Order has no JS
//     referee (tsserver.mjs assembles some lists itself, and the brief names pull-diagnostic order
//     as unrefereed), so a changed order stays a DIFF;
//   - at least one point.
// Points where tsc-rs equals Go need nothing from JS.
import { parseKeep } from "./json.mjs";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { TOKEN_MODIFIERS, TOKEN_TYPES } from "./tsserver.mjs";

// The bundled libs: Go's (the pin's tsc/internal/bundled/libs; tsc-rs bundles the same files) and
// JS's (typescript/lib). They differ (lib.dom.d.ts alone in 1,470 lines), so for comparing with JS a
// location in a lib is anchored to its text (anchorLine), not its line number.
export const GO_LIBS = join(homedir(), ".explore/repos/microsoft__TypeScript@3e9236361fa9/tsc/internal/bundled/libs");
export const JS_LIBS = join(homedir(), ".cache/perf-rearch/js-tsc/node_modules/typescript/lib");
const LIB_DIRS = { go: GO_LIBS, js: JS_LIBS };
const AMBIGUOUS = "\u0000ambiguous";
const libCache = new Map();
function libFile(side, name) {
  const key = `${side}/${name}`;
  if (!libCache.has(key)) {
    let lines = null;
    try {
      lines = readFileSync(join(LIB_DIRS[side], name), "utf8").split("\n");
    } catch {}
    const byText = new Map();
    for (const [i, l] of (lines ?? []).entries()) byText.set(l, [...(byText.get(l) ?? []), i]);
    libCache.set(key, lines && { lines, byText });
  }
  return libCache.get(key);
}

// The largest context window anchorLine tries (lines on each side).
const ANCHOR_MAX_K = 40;
const at = (f, i) => (i >= 0 && i < f.lines.length ? f.lines[i] : "\u0000eof");
// How often the 2k+1 lines around line `line` of `own` occur in f (candidates: lines with the same text).
function occurrences(own, line, k, f) {
  let n = 0;
  for (const c of f.byText.get(own.lines[line]) ?? []) {
    let same = true;
    for (let d = -k; d <= k && same; d++) same = at(f, c + d) === at(own, line + d);
    if (same) n++;
  }
  return n;
}

// The anchor of line `line` of a lib file as `side` ("go" or "js") bundles it: «text»#<sha of the
// smallest window of 2k+1 lines around it that occurs once in that side's copy and at most once in
// the other side's>. The same declaration in both copies gets the same anchor when its window is the
// same text (both sides find the same smallest k). AMBIGUOUS when no window up to ANCHOR_MAX_K is
// unique, or the lib or line is unknown.
export function anchorLine(side, name, line) {
  const own = libFile(side, name);
  const other = libFile(side === "go" ? "js" : "go", name);
  if (!own || !other || !Number.isInteger(line) || line < 0 || line >= own.lines.length) return AMBIGUOUS;
  for (let k = 0; k <= ANCHOR_MAX_K; k++) {
    if (occurrences(own, line, k, own) !== 1 || occurrences(own, line, k, other) > 1) continue;
    const window = [];
    for (let d = -k; d <= k; d++) window.push(at(own, line + d));
    const sha = createHash("sha256").update(JSON.stringify(window)).digest("hex").slice(0, 12);
    return `«${own.lines[line].trim().slice(0, 60)}»#${sha}`;
  }
  return AMBIGUOUS;
}

// A URI as a structural value: a file URI by its path (fileURLToPath: %-encoding decoded, %2F
// refused), query and fragment; any other URI as it is.
export function canonUri(u) {
  if (typeof u !== "string" || !u.startsWith("file:")) return u;
  try {
    const url = new URL(u);
    return `file:${JSON.stringify([fileURLToPath(url), url.search, url.hash])}`;
  } catch {
    return `invalid-uri:${u}`;
  }
}

// The ref view of a value (a deep copy): every uri canonUri'd (also a WorkspaceEdit's changes keys),
// and every line of a range in a bundled:///libs file anchored (anchorLine) on `side`'s copy.
function refValue(value, side) {
  if (!value || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((v) => refValue(v, side));
  // Object.fromEntries defines every key as an own property: a key named "__proto__" from an answer
  // stays a key (an assignment into a {} would set the prototype instead; lane H8 round 2).
  const out = Object.fromEntries(Object.entries(value).map(([k, v]) => [k, refValue(v, side)]));
  if (out.changes && typeof out.changes === "object" && !Array.isArray(out.changes)) {
    out.changes = Object.fromEntries(Object.entries(out.changes).map(([u, v]) => [canonUri(u), v]));
  }
  const uri = value.uri ?? value.targetUri;
  for (const k of ["uri", "targetUri"]) if (typeof out[k] === "string") out[k] = canonUri(out[k]);
  if (typeof uri === "string" && uri.startsWith("bundled:///libs/")) {
    const name = uri.slice("bundled:///libs/".length);
    for (const k of ["range", "targetRange", "targetSelectionRange"]) {
      for (const end of ["start", "end"]) {
        const p = out[k]?.[end];
        if (p && typeof p.line === "number") out[k] = { ...out[k], [end]: { ...p, line: anchorLine(side, name, p.line) } };
      }
    }
  }
  return out;
}

const J = (v) => JSON.stringify(v ?? null);
const pick = (o, keys) => (o && typeof o === "object" ? Object.fromEntries(keys.filter((k) => k in o).map((k) => [k, o[k]])) : o);
const omit = (o, keys) => {
  if (!o || typeof o !== "object" || Array.isArray(o)) return o === undefined ? null : { value: o };
  const c = { ...o };
  for (const k of keys) delete c[k];
  return Object.keys(c).length ? c : null;
};
// What kind of value an answer is, so null, [] and an object are never the same answer.
const form = (r) => (r === undefined ? "undefined" : r === null ? "null" : Array.isArray(r) ? "array" : typeof r);

// Hover and documentation markdown as plain words: links ([text](target) is text), the code fence,
// emphasis, inline code, the " — " of tags and all whitespace runs are dropped or collapsed. Lossy.
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

// The relative semantic-token data decoded to absolute tokens, losslessly: [line, char, length, type
// index, modifier bits] per token, and the trailing numbers that do not make a whole token.
function decodeTokens(data) {
  const out = [];
  let [line, ch] = [0, 0];
  let i = 0;
  for (; i + 4 < data.length; i += 5) {
    line += data[i];
    ch = data[i] === 0 ? ch + data[i + 1] : data[i + 1];
    out.push([line, ch, data[i + 2], data[i + 3], data[i + 4]]);
  }
  return { tokens: out, trailing: data.slice(i) };
}
// A decoded token in Go's legend (names), with any modifier bit outside the legend kept as a number.
function tokenName([line, ch, len, type, bits]) {
  const mods = TOKEN_MODIFIERS.filter((_, b) => bits & (1 << b));
  const unknown = bits & ~((1 << TOKEN_MODIFIERS.length) - 1);
  return `${line}:${ch}+${len} ${TOKEN_TYPES[type] ?? type}${mods.length ? "." + mods.join(".") : ""}${unknown ? `+${unknown}` : ""}`;
}

// A builder per method: (result, ref) -> { scalars: {k: value}, lossy: [scalar names], sets: {k: {
// order, lossy, items: [[key, residue]] }}, rest }. ref says which view is built: false the exact view
// (the raw values), true the view for comparing with JS (only where a field needs another form than
// refValue gives). Key and residue of an element are the whole element.
const S = (items, order = "fixed", lossy = false) => ({ order, lossy, items });

function locationItems(list) {
  return list.map((l) =>
    l && "targetUri" in l
      ? [{ uri: l.targetUri, range: l.targetSelectionRange }, omit(l, ["targetUri", "targetSelectionRange"])]
      : [pick(l, ["uri", "range"]), omit(l, ["uri", "range"])],
  );
}
const asList = (r) => (r == null ? [] : Array.isArray(r) ? r : [r]);

function flattenSymbols(list, path, out) {
  for (const s of list ?? []) {
    const p = [...path, s.name];
    out.push([{ path: p, kind: s.kind, range: s.range, selectionRange: s.selectionRange }, { ...omit(s, ["name", "kind", "range", "selectionRange", "children"]), children: form(s.children) }]);
    flattenSymbols(s.children, p, out);
  }
  return out;
}

// Hover contents: a MarkupContent whose value starts with a ```typescript fence is the display text
// (exact) and the rest of the value; anything else is one documentation value.
function hoverParts(contents) {
  if (contents && typeof contents === "object" && !Array.isArray(contents) && typeof contents.value === "string") {
    const m = /^```typescript\n([\s\S]*?)\n```([\s\S]*)$/.exec(contents.value);
    if (m) return { display: m[1], doc: m[2], shape: { ...omit(contents, ["value"]), fenced: true } };
    return { display: null, doc: contents.value, shape: { ...omit(contents, ["value"]), fenced: false } };
  }
  return { display: null, doc: J(contents), shape: { form: form(contents) } };
}

export const NORMALIZERS = {
  "textDocument/diagnostic": (r) => ({
    sets: { items: S((r?.items ?? []).map((d) => [pick(d, ["range", "severity", "code", "message", "tags", "relatedInformation"]), omit(d, ["range", "severity", "code", "message", "tags", "relatedInformation"])])) },
    rest: { form: form(r), ...omit(r, ["items"]), items: form(r?.items) },
  }),
  "textDocument/hover": (r, ref) => {
    const h = r ? hoverParts(r.contents) : null;
    return {
      scalars: { display: h?.display ?? null, doc: h ? (ref ? plainDoc(h.doc) : h.doc) : null, range: r?.range ?? null },
      lossy: ["doc"],
      rest: { form: form(r), ...omit(r, ["contents", "range"]), contents: h?.shape ?? null },
    };
  },
  "textDocument/completion": (r) => {
    const items = Array.isArray(r) ? r : r?.items ?? [];
    return {
      scalars: { isIncomplete: Array.isArray(r) ? null : r?.isIncomplete ?? null },
      sets: {
        items: S(
          items.map((i) => [
            { label: i.label, kind: i.kind, sortText: i.sortText, insertText: i.insertText, source: i.labelDetails?.description },
            { ...omit(i, ["label", "kind", "sortText", "insertText", "labelDetails"]), labelDetails: i.labelDetails === undefined ? "absent" : omit(i.labelDetails, ["description"]) },
          ]),
          "free",
        ),
      },
      rest: { form: form(r), ...(Array.isArray(r) ? {} : omit(r, ["items", "isIncomplete"])) },
    };
  },
  "completionItem/resolve": (r, ref) => ({
    scalars: { label: r?.label ?? null, detail: r?.detail ?? null, documentation: ref ? plainDoc(markup(r?.documentation)) : r?.documentation ?? null },
    lossy: ["documentation"],
    sets: { edits: S((r?.additionalTextEdits ?? []).map((e) => [pick(e, ["range", "newText"]), omit(e, ["range", "newText"])])) },
    rest: { form: form(r), ...omit(r, ["label", "detail", "documentation", "additionalTextEdits"]), edits: form(r?.additionalTextEdits) },
  }),
  "textDocument/definition": (r) => ({
    // The origin of every link is refereed with its target. targetRange (the declaration around the
    // name) is in the residue: JS 6 gives the initializer's span for a function-valued const, Go
    // the declaration's.
    sets: {
      targets: S(
        asList(r).map((l) =>
          l && "targetUri" in l
            ? [{ origin: l.originSelectionRange, uri: l.targetUri, range: l.targetSelectionRange }, omit(l, ["originSelectionRange", "targetUri", "targetSelectionRange"])]
            : [pick(l, ["uri", "range"]), omit(l, ["uri", "range"])],
        ),
      ),
    },
    rest: { form: form(r) },
  }),
  "textDocument/typeDefinition": (r) => ({ sets: { locations: S(locationItems(asList(r))) }, rest: { form: form(r) } }),
  "textDocument/implementation": (r) => ({ sets: { locations: S(locationItems(asList(r))) }, rest: { form: form(r) } }),
  "textDocument/references": (r) => ({ sets: { locations: S(locationItems(asList(r))) }, rest: { form: form(r) } }),
  "textDocument/documentHighlight": (r) => ({ sets: { highlights: S(asList(r).map((h) => [pick(h, ["range", "kind"]), omit(h, ["range", "kind"])])) }, rest: { form: form(r) } }),
  "textDocument/signatureHelp": (r, ref) => ({
    scalars: { activeSignature: r?.activeSignature ?? null, activeParameter: r?.activeParameter ?? null },
    sets: {
      signatures: S(
        (r?.signatures ?? []).map((s) => [
          {
            label: s.label,
            // A parameter label is a string or [start, end] offsets into the signature's label: two
            // encodings of the same text, one form for JS.
            params: (s.parameters ?? []).map((p) => (ref && Array.isArray(p.label) ? s.label.slice(...p.label) : p.label)),
            doc: ref ? plainDoc(markup(s.documentation)) : s.documentation ?? null,
          },
          { ...omit(s, ["label", "parameters", "documentation"]), parameters: (s.parameters ?? []).map((p) => omit(p, ["label"])) },
        ]),
        "fixed",
        true,
      ),
    },
    rest: { form: form(r), ...omit(r, ["signatures", "activeSignature", "activeParameter"]) },
  }),
  "textDocument/prepareRename": (r) => {
    const isRange = r && typeof r === "object" && "start" in r && "end" in r && !("range" in r);
    return {
      scalars: { range: isRange ? pick(r, ["start", "end"]) : r?.range ?? null, placeholder: r?.placeholder ?? null },
      rest: { form: form(r), isRange, ...(isRange ? omit(r, ["start", "end"]) : omit(r, ["range", "placeholder"])) },
    };
  },
  "textDocument/rename": (r) => {
    // The files of a WorkspaceEdit are in map order in Go (lspdiff sorts them too): normalize puts
    // them in uri order first (sortChanges), and edits in a file stay as given.
    const edits = [];
    for (const [uri, list] of Object.entries(r?.changes ?? {})) for (const e of list ?? []) edits.push([{ uri, ...pick(e, ["range", "newText"]) }, { via: "changes", ...omit(e, ["range", "newText"]) }]);
    for (const dc of r?.documentChanges ?? []) for (const e of dc.edits ?? []) edits.push([{ uri: dc.textDocument?.uri, ...pick(e, ["range", "newText"]) }, { via: "documentChanges", ...omit(e, ["range", "newText"]) }]);
    return {
      sets: { edits: S(edits) },
      rest: {
        form: form(r),
        ...omit(r, ["changes", "documentChanges"]),
        changes: r?.changes ? Object.fromEntries(Object.entries(r.changes).map(([u, l]) => [u, form(l)])) : form(r?.changes),
        documentChanges: Array.isArray(r?.documentChanges) ? r.documentChanges.map((dc) => ({ ...omit(dc, ["edits"]), edits: form(dc.edits) })) : form(r?.documentChanges),
      },
    };
  },
  "textDocument/inlayHint": (r, ref) => ({
    sets: {
      hints: S(
        asList(r).map((h) => [
          {
            at: h.position,
            // Go joins the parts of an interactive hint without the spaces between words
            // ("uniquesymbol", "Record<string,number>"), JS keeps them: the JS view drops whitespace.
            label: ref ? (typeof h.label === "string" ? h.label : (h.label ?? []).map((p) => p.value).join("")).replace(/\s+/g, "") : typeof h.label === "string" ? h.label : (h.label ?? []).map((p) => p.value),
            kind: h.kind,
            l: h.paddingLeft,
            r: h.paddingRight,
          },
          { ...omit(h, ["position", "label", "kind", "paddingLeft", "paddingRight"]), label: typeof h.label === "string" ? "string" : (h.label ?? []).map((p) => omit(p, ["value"])) },
        ]),
        "fixed",
        true,
      ),
    },
    rest: { form: form(r) },
  }),
  "textDocument/documentSymbol": (r) => ({ sets: { symbols: S(flattenSymbols(asList(r), [], [])) }, rest: { form: form(r) } }),
  "textDocument/semanticTokens/full": (r, ref) => {
    const { tokens, trailing } = decodeTokens(r?.data ?? []);
    return { sets: { tokens: S(tokens.map((t) => [ref ? tokenName(t) : t, null])) }, rest: { form: form(r), ...omit(r, ["data"]), trailing } };
  },
  "textDocument/foldingRange": (r) => ({
    sets: { ranges: S(asList(r).map((f) => [pick(f, ["startLine", "startCharacter", "endLine", "endCharacter", "kind"]), omit(f, ["startLine", "startCharacter", "endLine", "endCharacter", "kind"])])) },
    rest: { form: form(r) },
  }),
};

export const hasNormalizer = (method) => !!NORMALIZERS[method];

// A WorkspaceEdit's changes in raw uri order (Go writes the map in map order).
function sortChanges(v) {
  if (v && typeof v === "object" && !Array.isArray(v) && v.changes && typeof v.changes === "object" && !Array.isArray(v.changes)) {
    return { ...v, changes: Object.fromEntries(Object.entries(v.changes).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) };
  }
  return v;
}

const refJ = (v) => {
  const s = J(v);
  return s.includes(AMBIGUOUS.replace("\u0000", "\\u0000")) ? null : s;
};

// An answer ({ result, error, failed }) in the shared shape. root: the project root (masked, as
// lspdiff does); side: whose bundled libs the answer's lib positions are in ("go" for Go and tsc-rs,
// "js" for tsserver; libDir JS_LIBS means "js" too). An error is one scalar (its message) and its code
// and data are rest.
export function normalize(method, answer, { root, side, libDir } = {}) {
  const f = NORMALIZERS[method];
  if (!f) return null;
  side ??= libDir === JS_LIBS ? "js" : "go";
  if (answer?.failed) return { failed: String(answer.failed), scalars: {}, sets: {}, rest: null };
  // parseKeep (json.mjs): a number keeps its text, so 1e999 is never null here either (lane H8).
  const parse = answer?.rawNumbers ? parseKeep : JSON.parse;
  let v = parse(J(answer?.result));
  if (root) v = parse(J(v).replaceAll(root, "<root>"));
  v = sortChanges(v);
  // Both views come from the same value in the same element order (refValue keeps it), so element i
  // of one is element i of the other.
  const ex = f(v, false);
  const rf = f(refValue(v, side), true);
  const scalars = {};
  for (const k of Object.keys(ex.scalars ?? {})) scalars[k] = { exact: J(ex.scalars[k]), ref: refJ(rf.scalars[k]), lossy: (ex.lossy ?? []).includes(k) };
  const err = answer?.error;
  scalars.error = { exact: J(err ? String(err.message ?? J(err)) : null), ref: J(err ? String(err.message ?? J(err)) : null), lossy: false };
  const sets = {};
  for (const [k, set] of Object.entries(ex.sets ?? {})) {
    const other = rf.sets[k].items;
    sets[k] = { order: set.order, lossy: set.lossy, items: set.items.map(([key, residue], i) => ({ exact: J(key), ref: refJ(other[i][0]), residue: J(residue), refResidue: refJ(other[i][1]) })) };
  }
  return { failed: null, scalars, sets, rest: J({ ...ex.rest, error: err ? omit(err, ["message"]) : null }) };
}

const count = (list) => {
  const m = new Map();
  for (const k of list) m.set(k, (m.get(k) ?? 0) + 1);
  return m;
};
const short = (s) => (String(s).length > 160 ? String(s).slice(0, 157) + "..." : String(s));

// The refereed part of a normalized answer in the view for JS: scalars and set keys (sorted: order is
// not refereed), without residues and rest. Two answers whose views are equal agree on every point JS
// could decide (the calibration's question).
export function refereedView(n) {
  if (!n) return null;
  if (n.failed) return J({ failed: true });
  const sets = {};
  for (const [k, v] of Object.entries(n.sets)) sets[k] = v.items.map((it) => it.ref).sort();
  return J({ scalars: Object.fromEntries(Object.entries(n.scalars).map(([k, v]) => [k, v.ref])), sets });
}

export function sameNormalized(a, b) {
  return refereedView(a) === refereedView(b);
}

// JS-AGREES on one answer. go, rs, js: normalized (normalize()). Returns { agrees, points, reason }:
// points are the differing points with what JS said ("rs-only <elem>: JS has it"); reason, when JS
// does not agree, the first point it failed or why the difference has no referee.
export function judge(go, rs, js) {
  if (!go || !rs) return { agrees: false, points: [], reason: "no JS referee for this kind" };
  if (!js) return { agrees: false, points: [], reason: "no JS answer at this step" };
  if (js.failed) return { agrees: false, points: [], reason: `JS failed here (no evidence): ${short(js.failed)}` };
  if (go.rest !== rs.rest) return { agrees: false, points: [], reason: `differs outside the refereed fields: ${short(go.rest)} vs ${short(rs.rest)}` };
  const points = [];
  let reason = null;
  const fail = (why) => {
    reason ??= why;
  };
  for (const k of new Set([...Object.keys(go.scalars), ...Object.keys(rs.scalars)])) {
    const [g, r, j] = [go.scalars[k], rs.scalars[k], js.scalars[k]];
    if (g?.exact === r?.exact) continue;
    points.push(`${k}: ${short(r?.exact)} (Go ${short(g?.exact)})`);
    if (!g || !r) fail(`${k}: only one side has this field`);
    else if (g.lossy || r.lossy) fail(`${k}: its JS view is lossy (no referee for a difference here)`);
    else if (g.ref == null || r.ref == null) fail(`${k}: a lib position with no unique anchor (no referee)`);
    else if (g.ref === r.ref) fail(`${k}: tsc-rs and Go differ only in encoding (no referee)`);
    else if (j?.ref !== r.ref) fail(`${k}: JS ${short(j?.ref)}, tsc-rs ${short(r.ref)}, Go ${short(g.ref)}`);
  }
  for (const k of new Set([...Object.keys(go.sets), ...Object.keys(rs.sets)])) {
    const gi = go.sets[k]?.items ?? [];
    const ri = rs.sets[k]?.items ?? [];
    const ji = js.sets[k]?.items ?? [];
    const [gc, rc] = [count(gi.map((it) => it.exact)), count(ri.map((it) => it.exact))];
    const refOf = new Map([...gi, ...ri].map((it) => [it.exact, it.ref]));
    const exactsOfRef = new Map();
    for (const [e, rf] of refOf) exactsOfRef.set(rf, new Set([...(exactsOfRef.get(rf) ?? []), e]));
    const jk = count(ji.map((it) => it.ref));
    const rsRef = count(ri.map((it) => it.ref));
    const lossy = go.sets[k]?.lossy || rs.sets[k]?.lossy;
    const check = (e, side) => {
      const rf = refOf.get(e);
      if (lossy) return fail(`${k} ${side} ${short(e)}: its JS view is lossy (no referee for a difference here)`);
      if (rf == null) return fail(`${k} ${side} ${short(e)}: a lib position with no unique anchor (no referee)`);
      if (exactsOfRef.get(rf).size > 1) return fail(`${k} ${side} ${short(e)}: another element has the same JS view (they differ only in encoding: no referee)`);
      if (jk.has(null)) return fail(`${k} ${side} ${short(e)}: JS's answer has a lib position with no unique anchor`);
      return rf;
    };
    for (const [e, n] of gc) {
      if (n <= (rc.get(e) ?? 0)) continue;
      points.push(`${k} go-only ${short(e)}`);
      const rf = check(e, "go-only");
      if (typeof rf === "string" && (jk.get(rf) ?? 0) > (rsRef.get(rf) ?? 0)) fail(`${k} go-only ${short(e)}: JS has it`);
    }
    for (const [e, n] of rc) {
      if (n <= (gc.get(e) ?? 0)) continue;
      points.push(`${k} rs-only ${short(e)}`);
      const rf = check(e, "rs-only");
      if (typeof rf !== "string") continue;
      if ((jk.get(rf) ?? 0) < n) {
        fail(`${k} rs-only ${short(e)}: JS lacks it`);
        continue;
      }
      // The whole element needs JS's evidence (lane H4): its other fields must be JS's for that same
      // element, as often as tsc-rs has it. A field Go gives another element of the answer is no
      // evidence for this one (it would let a new target borrow an old target's range).
      const jsWhole = count(ji.filter((it) => it.ref === rf).map((it) => it.refResidue));
      for (const [res, m] of count(ri.filter((x) => x.exact === e).map((x) => x.refResidue))) {
        if (res == null || (jsWhole.get(res) ?? 0) < m) {
          fail(`${k} rs-only ${short(e)}: its other fields ${short(res)} are not JS's for this element (no evidence for the whole element)`);
        }
      }
    }
    // The residues of the elements both have: no referee for a difference there.
    const res = (items) => {
      const m = new Map();
      for (const it of items) m.set(it.exact, [...(m.get(it.exact) ?? []), it.residue].sort());
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
        return items.map((it) => it.exact).filter((key) => {
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

// The JS step of the LSP tier, on lspdiff's items ({ i, step, method, status, detail }): sets
// `it.js` (JS's verdict) and returns the items JS agrees with; lspdiff confirms tsc-rs's
// determinism and sets their status. logs: { go, go2, ..., rs, ... }, each aligned to the items by
// request identity (lspdiff.mjs: logs[run][it.i] is that run's answer to the item's request, or
// undefined when the run did not ask it). runJs(): { answers, broken }: JS's answers aligned the same
// way (from its cache or a tsserver run), and why the JS run ended early, if it did. all: run JS even
// without a DIFF to referee, and record JS's view of every item (calibration, and the report on
// GO-BROKEN items).
// Returns { ran, calibration: { method: { goEqual, jsEqual, noJs, jsFailed } }, agreeing }.
export async function applyJsReferee({ items, logs, root, runJs, all = false, kinds = REFEREED_KINDS } = {}) {
  kinds ??= REFEREED_KINDS;
  const candidates = items.filter((it) => it.status === "DIFF" && !it.detail && kinds.has(it.method));
  if (!(all ? items.some((it) => hasNormalizer(it.method)) : candidates.length)) return { ran: false, calibration: {}, agreeing: [] };
  const { answers: js, broken } = await runJs();
  const calibration = {};
  const agreeing = [];
  const jsOf = (it) => {
    const e = js[it.i];
    if (!e) return null;
    return normalize(it.method, e, { root, side: "js" });
  };
  for (const it of items) {
    if (!hasNormalizer(it.method) || !logs.rs[it.i]) continue;
    // Without `all`, only the candidates (normalizing umami's MATCH answers takes 30 s).
    if (!all && !candidates.includes(it)) continue;
    const j = jsOf(it);
    const r = normalize(it.method, logs.rs[it.i], { root });
    const noJs = broken ? `no JS answer at this step (the JS run ended early: ${broken})` : "no JS answer at this step";
    if (it.status === "GO-BROKEN") {
      // Report only (GO-BROKEN comes before JS-AGREES): which of Go's runs JS equals, and tsc-rs.
      const goRuns = Object.keys(logs).filter((k) => /^go\d*$/.test(k) && logs[k][it.i]);
      const sameAs = (n) => (k) => sameNormalized(normalize(it.method, logs[k][it.i], { root }), n);
      it.js = j && !j.failed ? { equalsRs: sameNormalized(r, j), equalsGoRuns: goRuns.filter(sameAs(j)), rsEqualsGoRuns: goRuns.filter(sameAs(r)) } : { reason: j?.failed ? `JS failed: ${j.failed}` : noJs };
      continue;
    }
    if (!logs.go[it.i]) continue;
    const g = normalize(it.method, logs.go[it.i], { root });
    if (it.status === "MATCH") {
      const c = (calibration[it.method] ??= { goEqual: 0, jsEqual: 0, noJs: 0, jsFailed: 0 });
      c.goEqual++;
      if (!j) c.noJs++;
      else if (j.failed) c.jsFailed++;
      else if (sameNormalized(g, j)) c.jsEqual++;
      continue;
    }
    if (it.status !== "DIFF") continue;
    const v = j ? judge(g, r, j) : { agrees: false, points: [], reason: noJs };
    it.js = { agrees: v.agrees, refereed: kinds.has(it.method), ...(v.reason ? { reason: v.reason } : {}), points: v.points.slice(0, 20), ...(v.points.length > 20 ? { morePoints: v.points.length - 20 } : {}) };
    if (v.agrees && candidates.includes(it)) agreeing.push(it);
  }
  return { ran: true, calibration, agreeing, jsAnswers: js.filter(Boolean).length, ...(broken ? { jsBroken: broken } : {}) };
}
