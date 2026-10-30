// The scripted differential session. Every request runs one at a time, so both servers
// see exactly the same sequence and Go picks its checkers deterministically.
import { LspClient, fileUri, initializeParams } from "./client.mjs";
import { endPosition, languageId, pickPositions, readText, rel } from "./project.mjs";

// Typed at the end of the first file, one character per didChange, with a completion after each.
export const TYPED = "\nconsole.log(Math.max(1, 2).toFixed(1));\n";

export async function startServer(cmd, root, { stderr } = {}) {
  const client = new LspClient(cmd, { cwd: root, stderr });
  const init = await client.request("initialize", initializeParams(root));
  if (init.error) throw new Error(`initialize failed: ${JSON.stringify(init.error)}`);
  client.notify("initialized", {});
  return client;
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

// Runs the session and returns [{ step, method, params, result, error }].
export async function runSession(cmd, root, files, { positions = 4, stderr } = {}) {
  const client = await startServer(cmd, root, { stderr });
  const log = [];
  const ask = async (step, method, params) => {
    const r = await client.request(method, params);
    log.push({ step, method, params, result: r.result, error: r.error, ms: r.ms });
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
  } finally {
    await client.shutdown();
  }
  return log;
}
