// Concurrent-load benchmark. An editor session typing at a fixed speed in one open file:
// every keystroke sends a didChange, a completion, and re-pulls diagnostics for all open
// files plus semantic tokens and inlay hints for the edited one (what VS Code does with
// interFileDependencies), cancelling the previous keystroke's background pulls still in
// flight. Every third keystroke also hovers in another open file.
// Concurrent mode sends on a timer and never waits; one-at-a-time mode sends the same
// requests and awaits each answer before sending the next.
import { endPosition, pickPositions } from "./project.mjs";
import { TYPED, append, openFiles, removeTail, startServer } from "./session.mjs";

const ROUNDS = 2;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
    const other = docs[1] ?? docs[0];
    const hoverAt = pickPositions(other.text, 3).idents[1] ?? { line: 0, character: 0 };
    await client.request("textDocument/hover", { textDocument: { uri: other.uri }, position: hoverAt });

    let background = [];
    let key = 0;
    for (let round = 0; round < ROUNDS; round++) {
      for (let i = 0; i < TYPED.length; i++, key++) {
        const position = append(client, edited, TYPED[i]);
        const td = { uri: edited.uri };
        const requests = [];
        if (TYPED[i] !== "\n") {
          const context = TYPED[i] === "." ? { triggerKind: 2, triggerCharacter: "." } : { triggerKind: 1 };
          requests.push(() => track("completion", client.request("textDocument/completion", { textDocument: td, position, context })));
        }
        if (key % 3 === 0) {
          requests.push(() => track("hover", client.request("textDocument/hover", { textDocument: { uri: other.uri }, position: hoverAt })));
        }
        let pulls = [
          ...docs.map((d) => () => client.request("textDocument/diagnostic", { textDocument: { uri: d.uri } })),
          () => client.request("textDocument/semanticTokens/full", { textDocument: td }),
          () => client.request("textDocument/inlayHint", { textDocument: td, range: { start: { line: 0, character: 0 }, end: endPosition(edited.text) } }),
        ];
        let kinds = [...docs.map(() => "diagnostic"), "semanticTokens", "inlayHint"];
        if (which !== "all") {
          const keep = kinds.map((k) => which === "no-diagnostics" && k !== "diagnostic");
          pulls = pulls.filter((_, j) => keep[j]);
          kinds = kinds.filter((_, j) => keep[j]);
        }
        if (serial) {
          for (const send of requests) await send();
          for (const [j, send] of pulls.entries()) await track(kinds[j], send());
          continue;
        }
        for (const send of requests) send();
        // The editor cancels the previous keystroke's background pulls.
        for (const p of background) client.cancel(p.id);
        background = pulls.map((send, j) => {
          const p = send();
          track(kinds[j], p);
          return p;
        });
        await sleep(interval);
      }
      if (!serial) await Promise.all(background);
      removeTail(client, edited, TYPED.length);
    }
    // Let everything still in flight answer.
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
