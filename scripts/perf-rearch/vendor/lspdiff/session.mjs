// The scripted differential session. Every request runs one at a time, so both servers
// see exactly the same sequence and Go picks its checkers deterministically.
import { LspClient, PROGRESS_METHODS, fileUri, initializeParams } from "./client.mjs";
import { parseKeep } from "./json.mjs";
import { endPosition, languageId, pickPositions, readText, rel } from "./project.mjs";
import { TsserverClient } from "./tsserver.mjs";

// Typed at the end of the first file, one character per didChange (the diff session asks for
// a completion after each; bench.mjs types it at a fixed speed).
export const TYPED = "\nconsole.log(Math.max(1, 2).toFixed(1));\n";

// cmd: an LSP server's [binary, ...args], or { tsserver: <tsserver.js> } for JS tsserver driven over
// its own protocol (tsserver.mjs, the referee; its ATA is always off). binds: more host paths the server
// sees in the sandbox (sandbox.mjs). The initialize answer is the session's first log entry (lane H8:
// compared like every other answer), `client.initialize`; a run whose initialize fails (an error answer,
// or no answer) ends there, with that entry when there was an answer.
export async function startServer(cmd, root, { stderr, watch = false, ata = true, env, binds, plumbing } = {}) {
  const client = cmd.tsserver ? new TsserverClient(cmd.tsserver, { cwd: root, stderr, env, binds }) : new LspClient(cmd, { cwd: root, stderr, env, binds, plumbing });
  const params = initializeParams(root, { watch, ata });
  let init;
  try {
    init = await client.request("initialize", params);
  } catch (e) {
    throw Object.assign(e, { session: await client.shutdown() });
  }
  client.initialize = { step: "initialize", method: "initialize", params, result: init.result, error: init.error, ...(init.failed ? { failed: init.failed } : {}), ...(init.rawNumbers ? { rawNumbers: true } : {}), ms: init.ms };
  if (init.error) throw Object.assign(new Error(`initialize failed: ${JSON.stringify(init.error)}`), { initialize: client.initialize, session: await client.shutdown() });
  client.notify("initialized", {});
  return client;
}

// "<name> watcher <n>.<i>": n comes from a process-wide counter of watcher versions
// (project/watch.go watcherID), which counts every WatchedFiles version the process made, so it
// is masked in every item but "watch ids". Only a registration id is ever masked (lane H8 round 3: a
// replace over a whole message also changed diagnostic texts such as "bad watcher 1.").
export const maskId = (id) => (typeof id === "string" ? id.replace(/ watcher \d+\./, " watcher <n>.") : id);

// A server-to-client message's params with the registration ids masked: params.registrations[*].id of
// client/registerCapability and params.unregisterations[*].id (the LSP spelling; unregistrations too)
// of client/unregisterCapability, nothing else. A copy: the event keeps what the server sent.
export function maskRegistrations(method, params) {
  const field = { "client/registerCapability": ["registrations"], "client/unregisterCapability": ["unregisterations", "unregistrations"] }[method];
  if (!field || !params || typeof params !== "object" || Array.isArray(params)) return params;
  const out = { ...params };
  for (const f of field) if (Array.isArray(params[f])) out[f] = params[f].map((r) => (r && typeof r === "object" && !Array.isArray(r) && "id" in r ? { ...r, id: maskId(r.id) } : r));
  return out;
}

// Every server-to-client request and notification from event `from` through the end of the server's
// output (call it after client.shutdown(), which waits for EOF), in order, with payloads; window/logMessage
// (the server's log, with timings) and the work-done progress (client.mjs PROGRESS_METHODS: shown or not by
// the time the work took) are left out, and the registration ids are masked (maskRegistrations).
// The log's last entry, "session messages" (lane H8 round 2: a request or a notification a server sends
// while it answers shutdown, or in a session without checkpoints, is compared too).
export function messagesEntry(client, from = 0) {
  const events = client.events.slice(from).filter((e) => e.method !== "window/logMessage" && !PROGRESS_METHODS.has(e.method));
  const raw = events.some((e) => e.raw);
  const text = JSON.stringify(events.map((e) => ({ [e.kind]: e.method, params: maskRegistrations(e.method, e.params ?? null) })));
  return { step: "session messages", method: "server/messages", params: null, result: raw ? parseKeep(text) : JSON.parse(text), ...(raw ? { rawNumbers: true } : {}), ms: 0 };
}

export function openFiles(client, files) {
  const docs = files.map((path) => ({ path, uri: fileUri(path), text: readText(path), version: 1 }));
  for (const d of docs) {
    client.notify("textDocument/didOpen", {
      textDocument: { uri: d.uri, languageId: languageId(d.path), version: d.version, text: d.text },
    });
  }
  return docs;
}

// Inserts `text` at the end of the document (one didChange).
export function append(client, doc, text) {
  const at = endPosition(doc.text);
  doc.text += text;
  doc.version++;
  client.notify("textDocument/didChange", {
    textDocument: { uri: doc.uri, version: doc.version },
    contentChanges: [{ range: { start: at, end: at }, text }],
  });
  return endPosition(doc.text);
}

export function removeTail(client, doc, length) {
  const start = endPosition(doc.text.slice(0, doc.text.length - length));
  const end = endPosition(doc.text);
  doc.text = doc.text.slice(0, doc.text.length - length);
  doc.version++;
  client.notify("textDocument/didChange", {
    textDocument: { uri: doc.uri, version: doc.version },
    contentChanges: [{ range: { start, end }, text: "" }],
  });
}

// Runs the session and returns { log: [{ step, method, params, result, error, failed }], session }
// (the first entry: the initialize answer)
// (failed: JS tsserver gave no usable answer, tsserver.mjs; session: the server's result, every
// outcome of its process and its shutdown, sandbox.mjs). ATA is off: its typings would join an
// inferred project whenever its npm call returns (see initializeParams). When the server crashes or
// times out, the error carries the answers so far as `log` and the session as `session`.
// Two requests depend on earlier answers: rename (only after a prepareRename that gave a result) and
// the quick fixes (for the first 3 diagnostics of the file). Each server's log has the requests it was
// asked, so the logs need not have the same length; lspdiff.mjs aligns them by request identity (the
// step name and its occurrence), and a request that only one side was asked is a difference.
export async function runSession(cmd, root, files, { positions = 4, stderr, env } = {}) {
  const log = [];
  let client;
  try {
    client = await startServer(cmd, root, { stderr, env, ata: false });
  } catch (e) {
    throw Object.assign(e, { log: e.initialize ? [e.initialize] : log, session: e.session });
  }
  log.push(client.initialize);
  const finish = async () => {
    const session = await client.shutdown();
    log.push(messagesEntry(client));
    return session;
  };
  const ask = async (step, method, params) => {
    const r = await client.request(method, params);
    log.push({ step, method, params, result: r.result, error: r.error, ...(r.failed ? { failed: r.failed } : {}), ...(r.rawNumbers ? { rawNumbers: true } : {}), ms: r.ms });
    return r;
  };
  try {
    const docs = openFiles(client, files);
    for (const d of docs) {
      const name = rel(root, d.path);
      const td = { uri: d.uri };
      const diag = await ask(`${name} diagnostic`, "textDocument/diagnostic", { textDocument: td });
      await ask(`${name} semanticTokens`, "textDocument/semanticTokens/full", { textDocument: td });
      await ask(`${name} inlayHint`, "textDocument/inlayHint", { textDocument: td, range: { start: { line: 0, character: 0 }, end: endPosition(d.text) } });
      await ask(`${name} documentSymbol`, "textDocument/documentSymbol", { textDocument: td });
      await ask(`${name} foldingRange`, "textDocument/foldingRange", { textDocument: td });
      const { idents, members } = pickPositions(d.text, positions);
      for (const [i, position] of idents.entries()) {
        const at = `${name}:${position.line + 1}:${position.character + 1}`;
        await ask(`${at} hover`, "textDocument/hover", { textDocument: td, position });
        await ask(`${at} definition`, "textDocument/definition", { textDocument: td, position });
        await ask(`${at} typeDefinition`, "textDocument/typeDefinition", { textDocument: td, position });
        await ask(`${at} references`, "textDocument/references", { textDocument: td, position, context: { includeDeclaration: true } });
        await ask(`${at} documentHighlight`, "textDocument/documentHighlight", { textDocument: td, position });
        await ask(`${at} signatureHelp`, "textDocument/signatureHelp", { textDocument: td, position });
        if (i === 0) {
          const prep = await ask(`${at} prepareRename`, "textDocument/prepareRename", { textDocument: td, position });
          if (prep.result) await ask(`${at} rename`, "textDocument/rename", { textDocument: td, position, newName: "renamedByLspdiff" });
        }
        await ask(`${at} codeAction`, "textDocument/codeAction", {
          textDocument: td,
          range: { start: position, end: position },
          context: { diagnostics: [], triggerKind: 1 },
        });
      }
      for (const position of members) {
        const at = `${name}:${position.line + 1}:${position.character + 1}`;
        await ask(`${at} completion`, "textDocument/completion", { textDocument: td, position, context: { triggerKind: 2, triggerCharacter: "." } });
      }
      // Quick fixes for the first few errors, as an editor asks when the cursor is on them.
      for (const item of (diag.result?.items ?? []).slice(0, 3)) {
        await ask(`${name}:${item.range.start.line + 1} codeAction(diag)`, "textDocument/codeAction", {
          textDocument: td,
          range: item.range,
          context: { diagnostics: [item], only: ["quickfix"], triggerKind: 1 },
        });
      }
    }
    // Typing: one didChange per character, a completion after each, then diagnostics.
    const d = docs[0];
    const name = rel(root, d.path);
    for (let i = 0; i < TYPED.length; i++) {
      const position = append(client, d, TYPED[i]);
      if (TYPED[i] === "\n") continue;
      const context = TYPED[i] === "." ? { triggerKind: 2, triggerCharacter: "." } : { triggerKind: 1 };
      await ask(`${name} typed ${JSON.stringify(TYPED.slice(1, i + 1))} completion`, "textDocument/completion", { textDocument: { uri: d.uri }, position, context });
    }
    await ask(`${name} after typing diagnostic`, "textDocument/diagnostic", { textDocument: { uri: d.uri } });
    const hoverAt = { line: endPosition(d.text).line - 1, character: 9 };
    await ask(`${name} after typing hover`, "textDocument/hover", { textDocument: { uri: d.uri }, position: hoverAt });
    removeTail(client, d, TYPED.length);
    await ask(`${name} after undo diagnostic`, "textDocument/diagnostic", { textDocument: { uri: d.uri } });
  } catch (e) {
    throw Object.assign(e, { log, session: await finish() });
  }
  return { log, session: await finish() };
}
