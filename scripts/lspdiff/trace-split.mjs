#!/usr/bin/env node
// Splits each request's latency in `lspdiff.mjs bench --trace` runs into the time the server
// spent on it and the time before it started (queue wait), from the server's own
// "handled method 'M' (id) in D" log lines, which both servers send as window/logMessage.
//
//   node scripts/lspdiff/trace-split.mjs <trace-*.json>... [--json FILE] [--per-request]
//
// Per request (all times are the client's, in ms; a log line's time is when the client read it):
//   latency   send -> answer
//   handled   the server's own time for it (Go: sync part on the dispatch loop + the async part on
//             its goroutine; tsc-rs: both parts, run back to back on the dispatch thread)
//   snapshot  the part of `handled` that was a snapshot clone ("Finished cloning snapshot ... in X",
//             given to the earliest-sent request pending when it was logged; both servers clone in
//             the sync part of the first request after an edit)
//   own       handled - snapshot
//   wait      latency - handled: queue wait plus transport
//   behind    for each other request whose handled interval overlaps this one's wait: the overlap,
//             by kind (on tsc-rs these ran on the dispatch thread while this one waited)
//   other     wait - sum(behind): no logged request ran (idle work, frees, transport, didChange)
//   preWire   first byte of the answer - send - handled: queue wait plus the server's marshalling of
//             the answer and anything ahead of it in the server's outgoing queue
//   read      answer complete and parsed - first byte: the pipe transfer and the client's JSON.parse
//   KB        the answer's size
// (preWire and read need traces taken with the client's wire times; a big answer such as a global
// completion list spends most of its "wait" there, not in the queue.)
// Table: medians per kind over all files given; "wait mean" and the behind/other columns are means
// (behind + other = wait mean). Completions are split by what was
// typed ("c", "console.", ...: the word or member access before the cursor), and the first
// completion of each session is its own row ("completion first"). Diagnostic pulls are split into
// the edited file's (sent first at each key) and the other open files'.
import { readFileSync, writeFileSync } from "node:fs";

const args = process.argv.slice(2);
const opt = (name) => {
  const i = args.indexOf(name);
  return i < 0 ? undefined : args.splice(i, 2)[1];
};
const flag = (name) => {
  const i = args.indexOf(name);
  return i >= 0 && args.splice(i, 1).length > 0;
};
const jsonOut = opt("--json");
const perRequest = flag("--per-request");
if (!args.length) {
  console.error("usage: trace-split.mjs <trace-*.json>... [--json FILE] [--per-request]");
  process.exit(2);
}

const UNIT = { ns: 1e-6, "µs": 1e-3, us: 1e-3, ms: 1, s: 1000 };
// Go's time.Duration and Rust's Duration Debug both print "<float><unit>"; Go can also print "1m2.5s".
function parseDuration(text) {
  let total = 0;
  for (const m of text.matchAll(/([\d.]+)(ns|µs|us|ms|s|m|h)/g)) total += Number(m[1]) * (m[2] === "m" ? 60000 : m[2] === "h" ? 3600000 : UNIT[m[2]]);
  return total;
}

const KIND = {
  "textDocument/completion": "completion",
  "textDocument/hover": "hover",
  "textDocument/diagnostic": "diagnostic",
  "textDocument/semanticTokens/full": "semanticTokens",
  "textDocument/inlayHint": "inlayHint",
};

const rows = [];
for (const file of args) {
  const t = JSON.parse(readFileSync(file, "utf8"));
  const handled = new Map(); // id -> { method, ms, at }
  const clones = []; // { at, ms }
  for (const l of t.logs) {
    const h = /^handled method '([^']+)' \((\d+)\) in (\S+)/.exec(l.message);
    if (h) handled.set(Number(h[2]), { method: h[1], ms: parseDuration(h[3]), at: l.at });
    const c = /Finished cloning snapshot \d+ into snapshot \d+ in (\S+)/.exec(l.message);
    if (c) clones.push({ at: l.at, ms: parseDuration(c[1]) });
  }
  // A handled line reaches the client after the request's answer, which the server marshals after
  // the handler ends; the answer's first byte is the closer bound for the handler's end.
  const firstByte = new Map(t.samples.filter((s) => s.firstByte !== undefined).map((s) => [s.id, s.firstByte]));
  const endOf = (id, h) => Math.min(h.at, firstByte.get(id) ?? h.at);
  const intervals = []; // handled intervals of every logged request in the measured part
  for (const [id, h] of handled) intervals.push({ id, kind: KIND[h.method] ?? h.method, from: endOf(id, h) - h.ms, to: endOf(id, h) });
  // Each snapshot clone belongs to the earliest-sent request still pending when its log line came:
  // both servers clone in the sync part of the first request after an edit, in arrival order.
  const cloneOf = new Map();
  for (const c of clones) {
    // Pending: sent, and its handled line not yet read (tsc-rs logs the clone that auto-imports
    // need in the request's async part, and the line comes right after the answer).
    const owner = t.samples.filter((s) => s.id !== undefined && s.start <= c.at && (handled.get(s.id)?.at ?? s.end) + 0.5 >= c.at).sort((a, b) => a.start - b.start)[0];
    if (owner) cloneOf.set(owner.id, (cloneOf.get(owner.id) ?? 0) + c.ms);
  }
  // The text typed so far in the round at each key, to name a completion by what was typed.
  const typedAt = (start) => {
    let k;
    for (const key of t.keys) if (key.at <= start + 0.01) k = key;
    if (!k) return "?";
    const line = t.keys.filter((x) => x.round === k.round && x.key <= k.key).map((x) => x.ch).join("").split("\n").pop();
    return /[\w$)]*\.?$/.exec(line)[0] || line.slice(-1);
  };
  // The bench sends the edited file's pull first among a key's diagnostic pulls (lowest id).
  const editedPull = new Set();
  for (const k of t.keys) {
    const pulls = t.samples.filter((s) => s.kind === "diagnostic" && s.id !== undefined && s.start >= k.at - 0.01 && s.start <= k.at + 5);
    if (pulls.length) editedPull.add(Math.min(...pulls.map((s) => s.id)));
  }
  let firstCompletion = true;
  for (const s of t.samples.slice().sort((a, b) => a.start - b.start)) {
    if (s.kind === "completion") {
      s.kind = firstCompletion ? "completion first" : `completion ${typedAt(s.start)}`;
      firstCompletion = false;
    }
    if (s.kind === "diagnostic") s.kind = editedPull.has(s.id) ? "diagnostic edited" : "diagnostic other";
    if (s.cancelled || s.error || s.id === undefined) continue;
    const h = handled.get(s.id);
    if (!h) continue;
    const latency = s.end - s.start;
    const from = endOf(s.id, h) - h.ms;
    const snapshot = cloneOf.get(s.id) ?? 0;
    const wait = Math.max(0, latency - h.ms);
    const behind = {};
    for (const iv of intervals) {
      if (iv.id === s.id) continue;
      const overlap = Math.min(iv.to, from) - Math.max(iv.from, s.start);
      if (overlap > 0) behind[iv.kind] = (behind[iv.kind] ?? 0) + overlap;
    }
    const behindTotal = Object.values(behind).reduce((a, b) => a + b, 0);
    const wire = s.firstByte === undefined ? {} : { preWire: Math.max(0, s.firstByte - s.start - h.ms), read: s.end - s.firstByte, KB: s.bytes / 1024 };
    rows.push({ file, id: s.id, kind: s.kind, latency, handled: h.ms, snapshot: Math.min(snapshot, h.ms), own: h.ms - Math.min(snapshot, h.ms), wait, behind, other: Math.max(0, wait - behindTotal), ...wire });
  }
}

const median = (a) => {
  if (!a.length) return NaN;
  const s = [...a].sort((x, y) => x - y);
  return s[(s.length - 1) >> 1];
};
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : NaN);
const f = (x) => (Number.isNaN(x) ? "-" : x.toFixed(1));
const kinds = ["completion first", "completion c", "completion M", "completion console.", "completion Math.", "completion 2).", "completion", "hover", "diagnostic edited", "diagnostic other", "semanticTokens", "inlayHint"];
for (const r of rows) if (!kinds.includes(r.kind)) kinds.push(r.kind);
const behindKinds = [...new Set(rows.flatMap((r) => Object.keys(r.behind)))].sort((a, b) => kinds.indexOf(a) - kinds.indexOf(b));
const table = [];
for (const kind of kinds) {
  const rs = rows.filter((r) => r.kind === kind);
  if (!rs.length) continue;
  const row = { kind, n: rs.length };
  for (const k of ["latency", "handled", "snapshot", "own", "wait", "other", "preWire", "read", "KB"]) {
    if (rs[0][k] === undefined) continue;
    row[k] = f(median(rs.map((r) => r[k])));
  }
  row["wait mean"] = f(mean(rs.map((r) => r.wait)));
  for (const b of behindKinds) row[`behind ${b}`] = f(mean(rs.map((r) => r.behind[b] ?? 0)));
  row["other mean"] = f(mean(rs.map((r) => r.other)));
  table.push(row);
}
console.table(table);
if (perRequest) for (const r of rows) console.log(JSON.stringify({ ...r, file: undefined }, (k, v) => (typeof v === "number" ? Number(v.toFixed(1)) : v)));
if (jsonOut) writeFileSync(jsonOut, JSON.stringify({ table, rows }, null, 1));
