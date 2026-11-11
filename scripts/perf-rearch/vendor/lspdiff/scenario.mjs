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
//   "<cp> server messages"  method server/messages: every other server request and notification but the
//                           log (window/logMessage) and the work-done progress (client.mjs PROGRESS_METHODS,
//                           kept in the entry's `progress`, not compared), in order; with ATA on, then its npm calls;
//                           then each payload rule of Go's progress a message broke ({ progress: <rule> })
// A checkpoint first waits (Script.settle) until the server has handled every notification sent (lane B4),
// every work-done progress it began has ended (lane H9), and it has been quiet for LSPDIFF_SETTLE_MS (default
// 1000) with no request pending: registrations come from the servers' background queues after an answer.
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
//   ata-overlay     fixture ata-small with automatic type acquisition on, a private typings cache
//                   and a stand-in npm with a fixed delay (ata-npm/): the inferred project's first
//                   load, its refresh when ATA's typings arrive, an overlay whose import makes ATA
//                   install more typings, and the watchers when the overlay and the file close
// ATA: the scenarios that set `ata: false` turn it off (pkgjson-overlay); every other one runs it
// hermetically (lane H5): the typings location is in the server's private home (sandbox.mjs), empty at
// the start of every server run, and ATA's npm is the stand-in ata-npm/ (offline; ATA_NPM_DELAY_MS per
// call), which installs from a store: ata-overlay's is the fixture's (fixtures.mjs ATA_STORE,
// `typings: "fixture"`), every other scenario's the frozen typings store (`typings: "frozen"`, lspdiff
// --ata-store: types-registry and the @types packages the scenarios acquire, built once by
// scripts/perf-rearch/ata_typings.py; parity_lsp.py keys the Go cache by its content hash). A call for a
// package the store lacks, or in another directory than the private typings location, fails the
// whole run (a harness error, never a server's answer). Every wait of a scenario with ATA on (quiet,
// checkpoint) also waits until ATA's npm is idle (Script.settle), so an answer never depends on how
// long npm took.
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, readlinkSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { PROGRESS_METHODS, fileUri, progressFindings } from "./client.mjs";
import { ATA_STORE, FIXTURES, writeFixture, writeTree } from "./fixtures.mjs";
import { endPosition, offsetToPosition, pickPositions, rel } from "./project.mjs";
import { TYPED, maskId, messagesEntry, startServer } from "./session.mjs";
import { SANDBOXED, SERVER_PATH, serverHome } from "./sandbox.mjs";
import { createHash } from "node:crypto";

// The fixed place of the stand-in npm's config, node and log in a server's sandbox (ata-npm/).
const ATA_DIR = "/run/lspdiff-ata";
import { SETTINGS } from "./settings.mjs";

const SETTLE_MS = SETTINGS.LSPDIFF_SETTLE_MS;
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
    this.expected = null; // a fixture scenario: rel -> text or { symlink }, what the dir must hold
  }

  abs(r) {
    return join(this.root, r);
  }

  async ask(step, method, params) {
    const r = await this.client.request(method, params);
    this.log.push({ step, method, params, result: r.result, error: r.error, ...(r.failed ? { failed: r.failed } : {}), ...(r.rawNumbers ? { rawNumbers: true } : {}), ms: r.ms });
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
    this.expected?.set(r, text);
    this.client.notify("workspace/didChangeWatchedFiles", { changes: [{ uri: fileUri(path), type }] });
  }

  fsDelete(r) {
    rmSync(this.abs(r));
    this.expected?.delete(r);
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
    if (this.expected) {
      this.expected.set(to, this.expected.get(from));
      this.expected.delete(from);
    }
    this.client.notify("workspace/didChangeWatchedFiles", { changes: [{ uri: fileUri(a), type: 3 }, { uri: fileUri(b), type: 1 }] });
  }

  // ATA's npm calls so far, from the stand-in npm's log (ata-npm/fake-npm.mjs): every call writes a
  // start line and, when its work is done, a done line with the same pid. The log is on the server's
  // recorded storage (the plumbing dir), read through the sandbox's init (sandbox.mjs inside()).
  npmCalls() {
    const file = this.client.server.inside(`${ATA_DIR}/log/npm.log`);
    return file && existsSync(file) ? readFileSync(file, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)) : [];
  }

  // Waits until ATA's npm is idle: every started call is done and its process has exited, and the log
  // has not changed for ATA_IDLE_MS, counted from `since` too (settle passes the start of its wait
  // first, so that a call ATA is about to start is seen, then -Infinity). Returns whether it waited
  // (false: npm was idle at once). A wait that reaches ATA_DEADLINE_MS fails the run (a timeout of
  // that server: for tsc-rs a DIFF, for Go GO-BROKEN), never a success.
  async npmIdle(label, since) {
    // The pid in the log is npm's own, in the server's PID namespace (sandbox.mjs alive).
    const alive = (pid) => this.client.server.alive(pid);
    const start = performance.now();
    for (let polls = 0; ; polls++) {
      const log = this.npmCalls();
      // A call the store cannot answer (a package it lacks) or made outside the private typings
      // location: the input is not the frozen one, so the run is a harness error, never an answer.
      const bad = log.find((e) => e.missing || e.refused || e.error);
      if (bad) throw Object.assign(new Error(`ATA's npm at ${label}: ${bad.missing ? `the typings store lacks ${bad.missing.join(", ")}` : bad.refused ? `refused: ${bad.refused}` : `failed: ${bad.error}`}`), { harness: true });
      if (log.length !== this.ata.seen) [this.ata.seen, this.ata.changedAt] = [log.length, performance.now()];
      const done = new Set(log.filter((e) => e.done).map((e) => e.pid));
      const busy = log.some((e) => e.start && (!done.has(e.pid) || alive(e.pid)));
      const now = performance.now();
      if (!busy && now - Math.max(since, this.ata.changedAt) >= SETTINGS.ATA_IDLE_MS) return polls > 0;
      if (now - start > SETTINGS.ATA_DEADLINE_MS) {
        const open = log.filter((e) => e.start && !done.has(e.pid)).map((e) => (e.args ? e.args.join(" ") : `pid ${e.pid}`));
        throw Object.assign(new Error(`ATA did not finish in ${SETTINGS.ATA_DEADLINE_MS} ms at ${label} (npm still running: ${open.join("; ") || "a process that has not exited"})`), { timeout: true });
      }
      await new Promise((r) => setTimeout(r, 50));
    }
  }

  // Waits until every work-done progress the server began has ended (client.mjs ProgressBarrier, lane
  // H9): ATA's install between its npm calls, a program's load. A progress that does not end within
  // LSPDIFF_PROGRESS_DEADLINE_MS fails the run as a harness error (lspdiff exits 3: the session is
  // ERROR), never a success; a server that died is not waited for (its next request reports it).
  async progressEnded(label) {
    const start = performance.now();
    for (;;) {
      const open = this.client.progress.pending();
      if (!open.length || this.client.dead) return;
      if (performance.now() - start > SETTINGS.LSPDIFF_PROGRESS_DEADLINE_MS) {
        throw Object.assign(new Error(`a work-done progress did not end in ${SETTINGS.LSPDIFF_PROGRESS_DEADLINE_MS} ms at ${label}: ${open.join("; ")}`), { harness: true });
      }
      await new Promise((r) => setTimeout(r, 20));
    }
  }

  // The scenario's waits (quiet, checkpoint, ataIdle), in this order:
  // 1. the server has handled every notification sent (client.mjs caughtUp, lane B4), so the windows
  //    below start after its work for them has started;
  // 2. every work-done progress it began has ended (progressEnded, lane H9);
  // 3. it has been quiet for `ms` with no request pending, and no progress began meanwhile (else 2 again);
  // 4. with ATA on, ATA's npm is idle (npmIdle) and was for ATA_IDLE_MS since the wait began; after an npm
  //    wait it all runs again from 2, which takes the diagnostics refresh that ATA's result schedules.
  // The LSP stream shows no ATA work that has not yet run 250 ms (Go's progress delay), and none of an ATA
  // request that runs no npm call and ends sooner, though its result still schedules a refresh 500 ms
  // later. On ts-pattern the inferred project's `npm install types-registry@latest` starts up to about 0.6 s
  // after the didOpen at load 90, and its result reaches the next request's snapshot. A wait on the stream
  // alone ended before or during that call at random (lane ND2), so the next answers and refreshes depended
  // on npm's time and the host's load, for Go and tsc-rs alike: hence the ATA_IDLE_MS floor of step 4.
  async settle(ms, label) {
    await this.client.caughtUp();
    const start = performance.now();
    for (let since = start; ; since = -Infinity) {
      do {
        await this.progressEnded(label);
        await this.client.settle(ms);
      } while (this.client.progress.pending().length && !this.client.dead);
      if (!this.ata || !(await this.npmIdle(label, since))) break;
    }
    return performance.now() - start;
  }

  // ata-overlay: waits until ATA is idle (settle, the barrier of lane H4: the stand-in npm's own log)
  // and logs the npm calls since the last wait as an item ("<label> ata npm", method ata/npm: each
  // call's arguments and exit code), compared like an answer. The scenario waits after every step, so
  // no request is ever sent while ATA's npm call runs: a request in that window starts a second
  // install of the same typings and makes the next answers depend on which install a snapshot sees
  // (Go and tsc-rs alike; the race ND1 found with the real npm).
  async ataIdle(label) {
    await this.settle(SETTLE_MS, label);
    this.log.push({ step: `${label} ata npm`, method: "ata/npm", result: this.npmReport(), ms: 0 });
  }

  // The npm calls done since the last report, for an `ata npm` item: each call's arguments (its package
  // names sorted: Go builds an install's list from a map, so their order varies from run to run, and
  // npm's result does not depend on it) and exit code, the calls sorted.
  npmReport() {
    const done = this.npmCalls().filter((e) => e.done);
    const calls = done.slice(this.ata.reported).map((e) => ({ args: [...e.args.filter((a) => a.startsWith("--")), ...e.args.filter((a) => !a.startsWith("--")).sort()], code: e.code }));
    this.ata.reported = done.length;
    return calls.sort((x, y) => (JSON.stringify(x) < JSON.stringify(y) ? -1 : 1));
  }

  // Waits until the server is quiet (no checkpoint): between didOpens, so that each snapshot's
  // background task (watch updates) ends before the next snapshot starts. Go runs those tasks as
  // concurrent goroutines, and back-to-back snapshots make its registrations race.
  quiet(ms = SETTINGS.LSPDIFF_QUIET_MS) {
    return this.settle(ms, "quiet");
  }

  async checkpoint(name) {
    const waited = await this.settle(SETTLE_MS, `checkpoint ${name}`);
    const fresh = this.client.events.slice(this.seen);
    this.seen = this.client.events.length;
    const events = [];
    const pushed = [];
    const other = [];
    const progress = []; // not compared (client.mjs PROGRESS_METHODS); kept in the log, and its payload rules checked
    const before = new Set(this.activeKeys());
    for (const e of fresh) {
      if (PROGRESS_METHODS.has(e.method)) {
        progress.push({ [e.kind]: e.method, params: e.params });
        continue;
      }
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
    // An event that held a number kept as its text (client.mjs) marks every entry of the checkpoint.
    const raw = fresh.some((e) => e.raw);
    const entry = (what, method, result, ms = 0, more = {}) => this.log.push({ step: `${name} ${what}`, method, result, ...(raw ? { rawNumbers: true } : {}), ms, ...more });
    entry("watch events", "watch/events", events, waited);
    entry("watch delta", "watch/delta", { added: delta.added.map(JSON.parse), removed: delta.removed.map(JSON.parse) });
    entry("watch active", "watch/active", after.map(JSON.parse));
    entry("publishDiagnostics", "publishDiagnostics", pushed);
    // With ATA on, the npm calls ATA made since the last report end the list (lane H5: each call's
    // arguments and exit code, sorted), so which typings each server's ATA acquired is compared too
    // (ata-overlay reports them at every ataIdle as items of their own). They are part of this item, not
    // one of their own, so the items of a scenario keep their indices.
    if (this.ata) other.push(...this.npmReport().map((call) => ({ ata: call })));
    // Every payload rule of Go's progress that a message broke (client.mjs progressFindings; lane H9 round 2):
    // none for a server that keeps them, whether or not it showed a progress.
    other.push(...progressFindings(fresh.filter((e) => PROGRESS_METHODS.has(e.method))));
    entry("server messages", "server/messages", other, 0, progress.length ? { progress } : {});
  }

  // Every registration (watchers and other methods) as a sorted list of JSON keys, ids masked.
  activeKeys() {
    const keys = [...this.active].map(([id, watchers]) => JSON.stringify({ id: maskId(id), watchers }));
    for (const [id, reg] of this.registered) keys.push(JSON.stringify({ id: maskId(id), ...reg }));
    return keys.sort();
  }
}

// maskId: session.mjs (the registration ids' watcher counter, masked in every item but "watch ids").

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
    // ATA off: its npm call raced the overlay steps (Go and tsc-rs alike; lane H2), and this
    // scenario tests package.json overlays, not type acquisition.
    ata: false,
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

SCENARIOS["ata-overlay"] = {
  fixture: "ata-small",
  // ATA on, against the private typings cache (in the server's private home, fresh for every server
  // run) and the stand-in npm with the fixture's store: the same for Go and tsc-rs, and the same on
  // every run. After every step the script waits until ATA is idle (ataIdle), so each answer sees
  // ATA's typings exactly when the steps before it made ATA install them.
  typings: "fixture",
  async run(s) {
    const app = "src/app.js";
    const ghost = "src/ghost.js";
    s.open(app);
    await s.ataIdle("open app.js:");
    await s.diag(app);
    await s.ataIdle("diagnostic:");
    await s.hover(app, "pad(");
    await s.ataIdle("hover:");
    await s.diag(app, " again");
    await s.ataIdle("diagnostic again:");
    await s.checkpoint("load");
    s.open(ghost, 'import { twice } from "ata-fake-two";\n\nexport const g = twice(2);\n');
    await s.ataIdle("open overlay:");
    await s.diag(ghost);
    await s.ataIdle("overlay diagnostic:");
    await s.hover(ghost, "twice(");
    await s.ataIdle("overlay hover:");
    await s.diag(ghost, " again");
    await s.ataIdle("overlay diagnostic again:");
    await s.checkpoint("open-overlay");
    s.close(ghost);
    await s.ataIdle("close overlay:");
    await s.diag(app, " after closing the overlay");
    await s.ataIdle("diagnostic after close:");
    await s.checkpoint("close-overlay");
    s.close(app);
    await s.ataIdle("close app.js:");
    await s.checkpoint("close-all");
  },
};

// The same steps with the workspace folder at <root>/src: package.json, node_modules and the
// tsconfig are then outside the workspace, so Go registers watchers for the directories the loader
// looked in there instead of covering them with "<root>/**/*".
SCENARIOS["fsevents-narrow"] = { ...SCENARIOS.fsevents, workspace: "src" };
SCENARIOS["watch-narrow"] = { ...SCENARIOS.watch, workspace: "src" };

// What a fixture dir holds that the script did not put there (lane H4): a file or symlink the server
// created, changed or deleted. [] when the dir is exactly `expected`.
function fixtureChanges(root, expected) {
  const found = new Map();
  const walk = (rel) => {
    for (const n of readdirSync(join(root, rel)).sort()) {
      const r = rel ? `${rel}/${n}` : n;
      const st = lstatSync(join(root, r));
      if (st.isSymbolicLink()) found.set(r, { symlink: readlinkSync(join(root, r)) });
      else if (st.isDirectory()) walk(r);
      else found.set(r, readFileSync(join(root, r), "utf8"));
    }
  };
  walk("");
  const out = [];
  for (const [r, v] of found) {
    if (!expected.has(r)) out.push(`created ${r}`);
    else if (JSON.stringify(v) !== JSON.stringify(expected.get(r))) out.push(`changed ${r}`);
  }
  for (const r of expected.keys()) if (!found.has(r)) out.push(`deleted ${r}`);
  return out;
}

// Runs a scenario on one server and returns { log: [{ step, method, params, result, error }],
// session } (the log's first entry: the initialize answer) (session: the server's result, sandbox.mjs; for a fixture scenario it also fails when the
// fixture dir is not what the script made it). A fixture scenario first writes its fixture into
// `root`. When the server crashes or times out, the error carries the answers so far as `log` and the
// session as `session`.
// serverLog: a file that gets the server's window/logMessage stream (lspdiff --server-logs).
// ataStore: the frozen typings store (lspdiff --ata-store), for the scenarios with `typings: "frozen"`.
export async function runScenario(cmd, name, root, { files = [], stderr, env, serverLog, ataStore, ataManifest } = {}) {
  const scenario = SCENARIOS[name];
  if (!scenario) throw new Error(`unknown scenario ${name}`);
  if (scenario.fixture) writeFixture(scenario.fixture, root);
  // ATA on: the stand-in npm first on PATH, with its store (the fixture's, written beside its log, or
  // the frozen one) and its log in a new temporary dir. The typings location is in the server's
  // private home (sandbox.mjs), new for every server run.
  let ata;
  const here = dirname(fileURLToPath(import.meta.url));
  // What the server sees besides its project (sandbox.mjs binds): a fixture dir (read-only: the script
  // writes it, the server only reads it), and with ATA on the stand-in npm, node, its store and its config;
  // its log is in the storage's plumbing dir, at ATA_DIR/log (every write to it recorded: lane H8 round 3).
  const binds = scenario.fixture ? [{ src: root }] : [];
  if (scenario.ata ?? true) {
    // ATA_DIR (lane H8 round 2): the stand-in npm's config, node and log dir at fixed paths, nothing in the
    // environment (the server controls that); the store authenticated file by file (its sha256s).
    if (!SANDBOXED) throw new Error(`scenario ${name} runs ATA: it needs the sandbox (LSPDIFF_SANDBOX)`);
    const typings = scenario.typings ?? "frozen";
    if (typings === "frozen" && !(ataStore && ataManifest)) throw new Error(`scenario ${name} runs ATA on the frozen typings store: pass --ata-store and --ata-manifest`);
    const dir = mkdtempSync(join(tmpdir(), "lspdiff-ata-"));
    if (typings === "fixture") writeTree(Object.fromEntries(Object.entries(ATA_STORE).map(([k, v]) => [`store/${k}`, v])), dir);
    ata = { dir };
    const store = typings === "fixture" ? join(dir, "store") : ataStore;
    const files = typings === "fixture"
      ? Object.fromEntries(Object.entries(ATA_STORE).map(([k, v]) => [k, createHash("sha256").update(v).digest("hex")]))
      : JSON.parse(readFileSync(ataManifest, "utf8")).files;
    writeFileSync(join(dir, "config.json"), JSON.stringify({ store, files, log: `${ATA_DIR}/log/npm.log`, delayMs: SETTINGS.ATA_NPM_DELAY_MS, cache: `${serverHome()}/.cache` }));
    binds.push({ src: join(here, "ata-npm") }, { src: store }, { src: join(dir, "config.json"), at: `${ATA_DIR}/config.json` },
      { src: process.execPath, at: `${ATA_DIR}/node` }, { src: join(here, "ata-npm/fake-npm.mjs"), at: `${ATA_DIR}/fake-npm.mjs` });
    env = { ...env, PATH: `${join(here, "ata-npm")}:${SERVER_PATH}` };
  }
  if (ata) Object.assign(ata, { reported: 0, seen: 0, changedAt: -Infinity });
  let client;
  try {
    client = await startServer(cmd, scenario.workspace ? join(root, scenario.workspace) : root, { stderr, watch: true, env, ata: scenario.ata ?? true, binds, ...(ata ? { plumbing: `${ATA_DIR}/log` } : {}) });
  } catch (e) {
    if (scenario.fixture) rmSync(root, { recursive: true, force: true });
    if (ata) rmSync(ata.dir, { recursive: true, force: true });
    throw Object.assign(e, { log: e.initialize ? [e.initialize] : [], session: e.session });
  }
  const s = new Script(client, root);
  s.ata = ata;
  s.log.push(client.initialize);
  if (scenario.fixture) s.expected = new Map(Object.entries(FIXTURES[scenario.fixture]));
  let failure;
  try {
    await scenario.run(s, { files });
    await s.checkpoint("end");
    // The raw ids, sorted: the only item where the watcher counter is not masked.
    s.log.push({ step: "end watch ids", method: "watch/ids", result: [...new Set(s.ids)].sort(), ms: 0 });
  } catch (e) {
    failure = e;
  }
  const session = await client.shutdown();
  s.log.push(messagesEntry(client, s.seen)); // what came after the last checkpoint, through EOF
  if (s.expected) {
    const changes = fixtureChanges(root, s.expected);
    if (changes.length) Object.assign(session, { clean: false, why: [...session.why, `it changed the fixture: ${changes.slice(0, 5).join(", ")}`] });
  }
  if (serverLog) {
    const lines = client.events.filter((e) => e.method === "window/logMessage").map((e) => `[${(e.at / 1000).toFixed(3)}] ${e.params?.message}`);
    writeFileSync(serverLog, lines.join("\n") + "\n");
  }
  if (scenario.fixture) rmSync(root, { recursive: true, force: true });
  if (ata) rmSync(ata.dir, { recursive: true, force: true });
  if (failure) throw Object.assign(failure, { log: s.log, session });
  return { log: s.log, session };
}

// What lspdiff and parity_lsp.py need to know of a scenario: whether it writes a fixture, whether ATA
// is on, and which store ATA's npm installs from ("fixture": ATA_STORE; "frozen": --ata-store).
export function scenarioInfo() {
  return Object.fromEntries(Object.entries(SCENARIOS).map(([k, v]) => [k, { fixture: v.fixture ?? null, ata: v.ata ?? true, typings: v.ata === false ? null : (v.typings ?? "frozen") }]));
}
