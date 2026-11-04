// Watch scenarios: scripted sessions in which the client advertises dynamic registration of
// workspace/didChangeWatchedFiles (relativePatternSupport, as VS Code does), so both servers
// register their watch globs, and the session's own steps (first loads, overlays, file-system
// events) are compared together with those registrations.
//
// Besides the answers, each checkpoint adds three entries to the log, compared like answers:
//   "<cp> watch events"     method watch/events: every client/registerCapability and
//                           client/unregisterCapability since the last checkpoint, in order, as
//                           { op, id, method, watchers | registerOptions }; any other
//                           server-to-client request as { op: "request", method }
//   "<cp> watch active"     method watch/active: the registered didChangeWatchedFiles watchers
//                           after the checkpoint (each { globPattern, kind }), sorted, ids dropped:
//                           the order-free view, still compared when Go's event order is unstable
//   "<cp> publishDiagnostics" method publishDiagnostics: the pushed diagnostics since the last
//                           checkpoint, stable-sorted by uri
// A checkpoint first waits until the server has been quiet for LSPDIFF_SETTLE_MS (default 1000)
// with no request pending: registrations come from the servers' background queues after an answer.
// Registration ids are kept as the servers send them ("<name> watcher <n>.<i>", n from a
// process-wide counter): both counters are deterministic for one scenario (checked on Go, see
// docs/perf-rearch/parity.md), so they are compared too.
//
// Scenarios (SCENARIOS below; `--scenario <name>` in lspdiff.mjs):
//   watch           a real project (--project, --files): open the files, diagnostics for each,
//                   one member completion (auto-import registry), the standard session's typing
//                   and undo (no completion per keystroke)
//   fsevents        fixture watch-small: create a missing import, delete an import, edit the
//                   package.json "imports", edit a dependency's package.json, break and fix the
//                   tsconfig, create a file in a new dir under the include glob; each on disk plus
//                   its workspace/didChangeWatchedFiles event
//   unsaved         ts-pattern: the first load starts from an open file that is not on disk
//   pkgjson-overlay fixture watch-small: package.json files open with edited text before the first
//                   load, then edited and closed
//   second-project  query: a second tsconfig project's first load after the first project's
//                   load and completions filled the session's caches
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileUri } from "./client.mjs";
import { writeFixture } from "./fixtures.mjs";
import { endPosition, offsetToPosition, pickPositions, rel } from "./project.mjs";
import { TYPED, startServer } from "./session.mjs";

const SETTLE_MS = Number(process.env.LSPDIFF_SETTLE_MS ?? 1000);
const json = (v) => JSON.stringify(v, null, 2) + "\n";
const langOf = (path) => (path.endsWith(".json") ? "json" : /x$/.test(path) ? "typescriptreact" : "typescript");

class Script {
  constructor(client, root) {
    this.client = client;
    this.root = root;
    this.log = [];
    this.docs = new Map(); // rel -> { uri, text, version }
    this.seen = 0; // client.events already reported
    this.active = new Map(); // didChangeWatchedFiles registration id -> watchers
    this.registered = new Map(); // other registrations: id -> { method, registerOptions }
    this.ids = []; // every registration id, raw, in order
  }

  abs(r) {
    return join(this.root, r);
  }

  async ask(step, method, params) {
    const r = await this.client.request(method, params);
    this.log.push({ step, method, params, result: r.result, error: r.error, ms: r.ms });
    return r;
  }

  open(r, text) {
    const doc = { uri: fileUri(this.abs(r)), text: text ?? readFileSync(this.abs(r), "utf8"), version: 1 };
    this.docs.set(r, doc);
    this.client.notify("textDocument/didOpen", { textDocument: { uri: doc.uri, languageId: langOf(r), version: 1, text: doc.text } });
    return doc;
  }

  // Replaces the whole text of an open document.
  change(r, text) {
    const doc = this.docs.get(r);
    doc.text = text;
    doc.version++;
    this.client.notify("textDocument/didChange", { textDocument: { uri: doc.uri, version: doc.version }, contentChanges: [{ text }] });
  }

  close(r) {
    this.client.notify("textDocument/didClose", { textDocument: { uri: this.docs.get(r).uri } });
    this.docs.delete(r);
  }

  text(r) {
    return this.docs.get(r)?.text ?? readFileSync(this.abs(r), "utf8");
  }

  // The position of `needle` in r (+ delta characters); throws if absent, so a scenario never
  // silently asks somewhere else.
  pos(r, needle, delta = 0) {
    const t = this.text(r);
    const i = t.indexOf(needle);
    if (i < 0) throw new Error(`${r}: ${JSON.stringify(needle)} not found`);
    return offsetToPosition(t, i + delta);
  }

  td(r) {
    return { uri: fileUri(this.abs(r)) };
  }

  diag(r, label = "") {
    return this.ask(`${r}${label} diagnostic`, "textDocument/diagnostic", { textDocument: this.td(r) });
  }

  at(method, short, r, needle, delta, extra = {}) {
    const position = this.pos(r, needle, delta);
    return this.ask(`${r}:${position.line + 1}:${position.character + 1} ${short}`, method, { textDocument: this.td(r), position, ...extra });
  }

  hover(r, needle, delta = 0) {
    return this.at("textDocument/hover", "hover", r, needle, delta);
  }

  definition(r, needle, delta = 0) {
    return this.at("textDocument/definition", "definition", r, needle, delta);
  }

  references(r, needle, delta = 0) {
    return this.at("textDocument/references", "references", r, needle, delta, { context: { includeDeclaration: true } });
  }

  // A completion just after the "." that ends `needle`.
  completion(r, needle) {
    return this.at("textDocument/completion", "completion", r, needle, needle.length, { context: { triggerKind: 2, triggerCharacter: "." } });
  }

  // Writes a file on disk and tells the server (Created when it did not exist, else Changed).
  fsWrite(r, text) {
    const path = this.abs(r);
    const type = existsSync(path) ? 2 : 1;
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text);
    this.client.notify("workspace/didChangeWatchedFiles", { changes: [{ uri: fileUri(path), type }] });
  }

  fsDelete(r) {
    rmSync(this.abs(r));
    this.client.notify("workspace/didChangeWatchedFiles", { changes: [{ uri: fileUri(this.abs(r)), type: 3 }] });
  }

  // Auto-import completion and completionItem/resolve: a completion with triggerKind 1 at
  // `position`, then a resolve of up to `count` auto-import items (items whose data names a module
  // to import from), the first by label. Each server picks from its own list; when the lists
  // differ, the completion is a DIFF already.
  async autoImports(r, position, count = 3) {
    const at = `${r}:${position.line + 1}:${position.character + 1}`;
    const c = await this.ask(`${at} auto-import completion`, "textDocument/completion", { textDocument: this.td(r), position, context: { triggerKind: 1 } });
    const items = (Array.isArray(c.result) ? c.result : c.result?.items ?? []).filter((i) => i.data?.autoImport ?? i.data?.AutoImport);
    items.sort((x, y) => (x.label < y.label ? -1 : x.label > y.label ? 1 : JSON.stringify(x) < JSON.stringify(y) ? -1 : 1));
    for (const item of items.slice(0, count)) await this.ask(`${at} resolve ${item.label}`, "completionItem/resolve", item);
    return items.length;
  }

  async autoImportsAt(r, needle, delta = needle.length) {
    return this.autoImports(r, this.pos(r, needle, delta));
  }

  // Opens r and asks diagnostics, then closes it.
  async openClose(r, text) {
    this.open(r, text);
    await this.diag(r, " (open)");
    this.close(r);
  }

  // Renames on disk: one didChangeWatchedFiles with the delete and the create, as VS Code sends it.
  fsRename(from, to) {
    const [a, b] = [this.abs(from), this.abs(to)];
    mkdirSync(dirname(b), { recursive: true });
    renameSync(a, b);
    this.client.notify("workspace/didChangeWatchedFiles", { changes: [{ uri: fileUri(a), type: 3 }, { uri: fileUri(b), type: 1 }] });
  }

  // Waits until the server is quiet (no checkpoint): between didOpens, so that each snapshot's
  // background task (watch updates) ends before the next snapshot starts. Go runs those tasks as
  // concurrent goroutines, and back-to-back snapshots make its registrations race.
  quiet(ms = 300) {
    return this.client.settle(ms);
  }

  async checkpoint(name) {
    const waited = await this.client.settle(SETTLE_MS);
    const fresh = this.client.events.slice(this.seen);
    this.seen = this.client.events.length;
    const events = [];
    const pushed = [];
    const other = [];
    const before = new Set(this.activeKeys());
    for (const e of fresh) {
      if (e.kind === "notification") {
        if (e.method === "textDocument/publishDiagnostics") pushed.push(e.params);
        // window/logMessage is the server's log (timings, snapshot dumps): not compared.
        else if (e.method !== "window/logMessage") other.push({ notification: e.method, params: e.params });
        continue;
      }
      if (e.method === "client/registerCapability") {
        for (const reg of e.params?.registrations ?? []) {
          this.ids.push(reg.id);
          if (reg.method === "workspace/didChangeWatchedFiles") {
            this.active.set(reg.id, reg.registerOptions?.watchers);
            events.push({ op: "register", id: maskId(reg.id), watchers: reg.registerOptions?.watchers });
          } else {
            this.registered.set(reg.id, { method: reg.method, registerOptions: reg.registerOptions });
            events.push({ op: "register", id: maskId(reg.id), method: reg.method, registerOptions: reg.registerOptions });
          }
        }
      } else if (e.method === "client/unregisterCapability") {
        for (const u of e.params?.unregisterations ?? e.params?.unregistrations ?? []) {
          this.active.delete(u.id);
          this.registered.delete(u.id);
          events.push({ op: "unregister", id: maskId(u.id), method: u.method });
        }
      } else other.push({ request: e.method, params: e.params });
    }
    const after = this.activeKeys();
    const delta = { added: after.filter((k) => !before.has(k)), removed: [...before].filter((k) => !after.includes(k)).sort() };
    pushed.sort((a, b) => (a.uri < b.uri ? -1 : a.uri > b.uri ? 1 : 0));
    const entry = (what, method, result, ms = 0) => this.log.push({ step: `${name} ${what}`, method, result, ms });
    entry("watch events", "watch/events", events, waited);
    entry("watch delta", "watch/delta", { added: delta.added.map(JSON.parse), removed: delta.removed.map(JSON.parse) });
    entry("watch active", "watch/active", after.map(JSON.parse));
    entry("publishDiagnostics", "publishDiagnostics", pushed);
    entry("server messages", "server/messages", other);
  }

  // Every registration (watchers and other methods) as a sorted list of JSON keys, ids masked.
  activeKeys() {
    const keys = [...this.active].map(([id, watchers]) => JSON.stringify({ id: maskId(id), watchers }));
    for (const [id, reg] of this.registered) keys.push(JSON.stringify({ id: maskId(id), ...reg }));
    return keys.sort();
  }
}

// "<name> watcher <n>.<i>": n comes from a process-wide counter of watcher versions
// (project/watch.go watcherID), which counts every WatchedFiles version the process made, so it
// is masked in every item but "watch ids".
const maskId = (id) => id.replace(/ watcher \d+\./, " watcher <n>.");

const SMALL_A = "src/a.ts";

export const SCENARIOS = {
  watch: {
    async run(s, { files }) {
      const rels = files.map((f) => rel(s.root, f));
      for (const r of rels) {
        s.open(r);
        await s.quiet();
      }
      for (const r of rels) await s.diag(r);
      await s.checkpoint("load");
      const first = rels[0];
      const member = pickPositions(s.text(first), 1).members[0];
      if (member) {
        await s.ask(`${first}:${member.line + 1}:${member.character + 1} completion`, "textDocument/completion", {
          textDocument: s.td(first),
          position: member,
          context: { triggerKind: 2, triggerCharacter: "." },
        });
      }
      // Auto-imports on a new last line of the first file: every exported name of the program
      // and its dependencies, then a resolve of three of them.
      const doc = s.docs.get(first);
      const original = doc.text;
      s.change(first, original + "\n");
      await s.autoImports(first, endPosition(doc.text));
      await s.checkpoint("completion");
      s.change(first, original);
      for (const ch of TYPED) {
        const at = endPosition(doc.text);
        doc.text += ch;
        doc.version++;
        s.client.notify("textDocument/didChange", { textDocument: { uri: doc.uri, version: doc.version }, contentChanges: [{ range: { start: at, end: at }, text: ch }] });
      }
      await s.diag(first, " after typing");
      await s.checkpoint("typed");
      s.change(first, original);
      await s.diag(first, " after undo");
      await s.checkpoint("undo");
      s.close(rels[rels.length - 1]);
      await s.diag(first, " after close");
      await s.checkpoint("close");
    },
  },

  fsevents: {
    fixture: "watch-small",
    async run(s) {
      const a = SMALL_A;
      s.open(a);
      await s.quiet();
      await s.diag(a);
      await s.hover(a, "total");
      await s.checkpoint("load");
      s.open("src/uses.ts");
      await s.autoImportsAt("src/uses.ts", "hel");
      await s.checkpoint("auto-import");
      s.fsWrite("src/c.ts", "export const c = 3;\n");
      await s.diag(a, " after create c.ts");
      await s.hover(a, "c, fake");
      await s.checkpoint("create-c");
      s.fsDelete("src/b.ts");
      await s.diag(a, " after delete b.ts");
      await s.hover(a, "b, c");
      await s.checkpoint("delete-b");
      s.fsRename("src/c.ts", "src/c2.ts");
      await s.diag(a, " after rename c.ts");
      await s.hover(a, "c, fake");
      await s.checkpoint("rename-c");
      s.fsWrite("package.json", json({ name: "watch-small", version: "1.0.0", type: "module", imports: { "#internal/*": "./src/internal/*2.js" } }));
      await s.diag(a, " after package.json");
      await s.hover(a, "helper, linked }");
      await s.checkpoint("edit-package-json");
      s.fsWrite("node_modules/fake-pkg/package.json", json({ name: "fake-pkg", version: "1.0.0", types: "./other.d.ts" }));
      await s.diag(a, " after fake-pkg package.json");
      await s.hover(a, "fake, helper");
      await s.checkpoint("edit-dep-package-json");
      // node_modules/linked-pkg is a symlink to packages/linked-pkg: the event names the realpath.
      s.fsWrite("packages/linked-pkg/index.d.ts", "export declare const linked: string;\n");
      await s.diag(a, " after linked-pkg realpath edit");
      await s.hover(a, "linked }");
      await s.checkpoint("edit-symlinked-realpath");
      const tsconfig = JSON.parse(readFileSync(s.abs("tsconfig.json"), "utf8"));
      s.fsWrite("tsconfig.json", json({ ...tsconfig, compilerOptions: { ...tsconfig.compilerOptions, bogusOption: true } }));
      await s.diag(a, " after bad tsconfig");
      await s.checkpoint("break-tsconfig");
      s.fsWrite("tsconfig.json", json(tsconfig));
      await s.diag(a, " after fixed tsconfig");
      await s.checkpoint("fix-tsconfig");
      s.fsWrite("src/sub/d.ts", 'export const d: string = 1;\nexport { b } from "../b.js";\n');
      await s.diag(a, " after create sub/d.ts");
      await s.openClose("src/sub/d.ts");
      await s.diag(a, " after open and close of sub/d.ts");
      await s.checkpoint("create-dir");
      // An overlay that is not on disk, then created on disk while open, then closed.
      s.open("src/ghost.ts", 'import { total } from "./a.js";\nexport const ghost: string = total;\n');
      await s.diag("src/ghost.ts");
      await s.checkpoint("open-unsaved");
      s.fsWrite("src/ghost.ts", 'import { total } from "./a.js";\nexport const ghost: number = total;\n');
      await s.diag("src/ghost.ts", " after create on disk");
      await s.checkpoint("create-open-file");
      s.close("src/ghost.ts");
      await s.diag(a, " after close of ghost.ts");
      await s.checkpoint("close-created");
    },
  },

  unsaved: {
    project: "ts-pattern",
    async run(s) {
      const u = "src/lspdiff-unsaved.ts";
      if (existsSync(s.abs(u))) throw new Error(`${u} exists on disk`);
      const text = [
        'import { match, P } from "./index";',
        "export const r = match(1 as number).with(P.number, (n) => n + 1).otherwise(() => 0);",
        "const bad: string = r;",
        "P.",
        "",
      ].join("\n");
      s.open(u, text);
      await s.quiet();
      await s.diag(u);
      await s.hover(u, "r = match", 0);
      await s.definition(u, "match(1");
      await s.completion(u, "\nP.");
      await s.checkpoint("load");
      s.open("src/index.ts");
      await s.diag("src/index.ts");
      await s.references("src/index.ts", "match");
      await s.checkpoint("open-on-disk");
      s.change(u, text.replace("const bad: string = r;", "const good: number = r;"));
      await s.diag(u, " after edit");
      await s.references("src/index.ts", "match");
      await s.checkpoint("edit-unsaved");
      s.close(u);
      await s.diag("src/index.ts", " after close");
      await s.checkpoint("close-unsaved");
    },
  },

  "pkgjson-overlay": {
    fixture: "watch-small",
    async run(s) {
      const a = SMALL_A;
      const pkg = JSON.parse(readFileSync(s.abs("package.json"), "utf8"));
      const dep = JSON.parse(readFileSync(s.abs("node_modules/fake-pkg/package.json"), "utf8"));
      s.open("package.json", json({ ...pkg, imports: { "#internal/*": "./src/internal/*2.js" } }));
      await s.quiet();
      s.open("node_modules/fake-pkg/package.json", json({ ...dep, types: "./other.d.ts" }));
      await s.quiet();
      s.open(a);
      await s.quiet();
      await s.diag(a);
      await s.hover(a, "helper, linked }");
      await s.hover(a, "fake, helper");
      await s.checkpoint("load");
      s.change("package.json", json(pkg));
      await s.diag(a, " after overlay edit");
      await s.hover(a, "helper, linked }");
      await s.checkpoint("edit-overlay");
      s.close("node_modules/fake-pkg/package.json");
      await s.diag(a, " after closing the dependency overlay");
      await s.hover(a, "fake, helper");
      await s.checkpoint("close-dep-overlay");
      s.close("package.json");
      await s.diag(a, " after closing the overlay");
      await s.checkpoint("close-overlay");
    },
  },

  "second-project": {
    project: "query",
    async run(s) {
      const core = "packages/query-core/src/queryObserver.ts";
      const react = "packages/react-query/src/useBaseQuery.ts";
      s.open(core);
      await s.quiet();
      await s.diag(core);
      await s.hover(core, "class QueryObserver", 6);
      await s.completion(core, "this.");
      await s.checkpoint("first-project");
      s.open(react);
      await s.quiet();
      await s.diag(react);
      await s.hover(react, "QueryObserver");
      await s.definition(react, "QueryObserver");
      await s.references(react, "useBaseQuery");
      await s.completion(react, "observer.");
      await s.checkpoint("second-project");
      // Auto-imports in the second project after its first load: a new last line, then resolve.
      const original = s.text(react);
      s.change(react, original + "\nuseQ");
      await s.autoImports(react, endPosition(s.text(react)));
      s.change(react, original);
      await s.checkpoint("auto-import");
    },
  },
};

// The same steps with the workspace folder at <root>/src: package.json, node_modules and the
// tsconfig are then outside the workspace, so Go registers watchers for the directories the loader
// looked in there instead of covering them with "<root>/**/*".
SCENARIOS["fsevents-narrow"] = { ...SCENARIOS.fsevents, workspace: "src" };
SCENARIOS["watch-narrow"] = { ...SCENARIOS.watch, workspace: "src" };

// Runs a scenario on one server and returns its log ([{ step, method, params, result, error }]).
// A fixture scenario first writes its fixture into `root`.
// serverLog: a file that gets the server's window/logMessage stream (lspdiff --server-logs).
export async function runScenario(cmd, name, root, { files = [], stderr, env, serverLog } = {}) {
  const scenario = SCENARIOS[name];
  if (!scenario) throw new Error(`unknown scenario ${name}`);
  if (scenario.fixture) writeFixture(scenario.fixture, root);
  const client = await startServer(cmd, scenario.workspace ? join(root, scenario.workspace) : root, { stderr, watch: true, env });
  const s = new Script(client, root);
  try {
    await scenario.run(s, { files });
    await s.checkpoint("end");
    // The raw ids, sorted: the only item where the watcher counter is not masked.
    s.log.push({ step: "end watch ids", method: "watch/ids", result: [...new Set(s.ids)].sort(), ms: 0 });
  } finally {
    await client.shutdown();
    if (serverLog) {
      const lines = client.events.filter((e) => e.method === "window/logMessage").map((e) => `[${(e.at / 1000).toFixed(3)}] ${e.params?.message}`);
      writeFileSync(serverLog, lines.join("\n") + "\n");
    }
    if (scenario.fixture) rmSync(root, { recursive: true, force: true });
  }
  return s.log;
}
