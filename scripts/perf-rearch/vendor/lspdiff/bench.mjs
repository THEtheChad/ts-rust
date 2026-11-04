// Concurrent-load benchmark: an editor session typing at a fixed speed (--interval ms per
// key) at the end of the first open file, sending what VS Code sends:
// - every key: didChange, and a diagnostics pull for the edited file;
// - a completion where VS Code asks for one: after "." and at the start of a word (it
//   filters the list itself while the user types the rest of the word);
// - every DEBOUNCE ms of typing: diagnostics pulls for the other open files, and semantic
//   tokens and inlay hints for the edited file;
// - every HOVER_EVERY keys: a hover in another open file (the mouse).
// A new request of a kind cancels the previous one of that kind still in flight, as the
// editor does. Concurrent mode sends on the timer and never waits; one-at-a-time mode sends
// the same requests and awaits each answer before sending the next.
import { endPosition, pickPositions } from "./project.mjs";
import { TYPED, append, openFiles, removeTail, startServer } from "./session.mjs";

const ROUNDS = 4;
const DEBOUNCE = 500;
const HOVER_EVERY = 8;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isIdent = (ch) => /[\w$]/.test(ch ?? "");

// pulls: "all", "no-diagnostics" (what completions would wait for if diagnostics ran off the
// dispatch thread) or "none" (completions and hovers alone).
export async function runBench(cmd, root, files, { serial, interval, pulls: which = "all", stderr }) {
  const client = await startServer(cmd, root, { stderr });
  const samples = [];
  const track = (kind, promise) =>
    promise.then((r) => {
      samples.push({ kind, ms: r.ms, cancelled: r.error?.code === -32800, error: r.error && r.error.code !== -32800 ? r.error.message : undefined });
      return r;
    });
  try {
    const docs = openFiles(client, files);
    // Warm-up, not measured: load the project and check every open file once.
    for (const d of docs) await client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } });
    const edited = docs[0];
    const td = { uri: edited.uri };
    const other = docs[1] ?? docs[0];
    const hoverAt = pickPositions(other.text, 3).idents[1] ?? { line: 0, character: 0 };
    await client.request("textDocument/hover", { textDocument: { uri: other.uri }, position: hoverAt });

    const inFlight = new Map(); // kind or uri -> pending request, for editor-style cancellation
    const debounceKeys = Math.max(1, Math.round(DEBOUNCE / interval));
    let key = 0;
    for (let round = 0; round < ROUNDS; round++) {
      for (let i = 0; i < TYPED.length; i++, key++) {
        const ch = TYPED[i];
        const position = append(client, edited, ch);
        const sends = []; // [slot, kind, () => promise]
        if (ch === ".") sends.push(["completion", "completion", () => client.request("textDocument/completion", { textDocument: td, position, context: { triggerKind: 2, triggerCharacter: "." } })]);
        else if (/[A-Za-z_$]/.test(ch) && !isIdent(TYPED[i - 1]) && TYPED[i - 1] !== ".") {
          sends.push(["completion", "completion", () => client.request("textDocument/completion", { textDocument: td, position, context: { triggerKind: 1 } })]);
        }
        if (key % HOVER_EVERY === 0) sends.push(["hover", "hover", () => client.request("textDocument/hover", { textDocument: { uri: other.uri }, position: hoverAt })]);
        if (which === "all") sends.push([edited.uri, "diagnostic", () => client.request("textDocument/diagnostic", { textDocument: td })]);
        if (key % debounceKeys === 0 && which !== "none") {
          if (which === "all") for (const d of docs.slice(1)) sends.push([d.uri, "diagnostic", () => client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } })]);
          sends.push(["semanticTokens", "semanticTokens", () => client.request("textDocument/semanticTokens/full", { textDocument: td })]);
          sends.push(["inlayHint", "inlayHint", () => client.request("textDocument/inlayHint", { textDocument: td, range: { start: { line: 0, character: 0 }, end: endPosition(edited.text) } })]);
        }
        if (serial) {
          for (const [, kind, send] of sends) await track(kind, send());
          continue;
        }
        for (const [slot, kind, send] of sends) {
          const previous = inFlight.get(slot);
          if (previous) client.cancel(previous.id);
          const p = send();
          inFlight.set(slot, p);
          track(kind, p).then(() => inFlight.get(slot) === p && inFlight.delete(slot));
        }
        await sleep(interval);
      }
      // A pause after the line, then delete it for the next round.
      while (client.pending.size) await sleep(20);
      removeTail(client, edited, TYPED.length);
    }
    while (client.pending.size) await sleep(20);
  } finally {
    await client.shutdown();
  }
  return samples;
}

const quantile = (sorted, q) => sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)];

export function summarize(results) {
  const groups = new Map();
  for (const { server, mode, samples } of results) {
    for (const s of samples) {
      const key = `${server} ${mode} ${s.kind}`;
      if (!groups.has(key)) groups.set(key, { server, mode, kind: s.kind, ms: [], cancelled: 0 });
      const g = groups.get(key);
      if (s.cancelled) g.cancelled++;
      else g.ms.push(s.ms);
    }
  }
  const order = ["completion", "hover", "diagnostic", "semanticTokens", "inlayHint"];
  return [...groups.values()]
    .sort((a, b) => a.server.localeCompare(b.server) || a.mode.localeCompare(b.mode) || order.indexOf(a.kind) - order.indexOf(b.kind))
    .map((g) => {
      const ms = g.ms.sort((a, b) => a - b);
      const f = (x) => (x === undefined ? "-" : x.toFixed(1));
      return { server: g.server, mode: g.mode, kind: g.kind, n: ms.length, cancelled: g.cancelled, p50: f(quantile(ms, 0.5)), p95: f(quantile(ms, 0.95)), max: f(ms.at(-1)) };
    });
}
