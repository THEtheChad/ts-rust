#!/usr/bin/env python3
"""Lane B4: applies the lspdiff notification barrier to a scripts/perf-rearch tree (H8's harness).

usage: apply-barrier.py <scripts/perf-rearch dir>

Edits vendor/lspdiff/client.mjs (NotificationBarrier, LspClient.caughtUp), scenario.mjs
(Script.settle waits for it first) and selftest/h4-selftest.mjs (the probe client's caughtUp and
the "b4" checks). Each edit finds its anchor text and fails if it is missing (no partial edit).
lspdiff-barrier.patch is its output on H8's b214cfb3d.
"""
import sys
from pathlib import Path

CLIENT_CONSTS = '''
// The notifications whose handling LspClient.caughtUp waits for: the ones a scenario sends between
// its waits.
const BARRIER_METHODS = new Set(["textDocument/didOpen", "textDocument/didChange", "textDocument/didClose", "textDocument/didSave", "workspace/didChangeWatchedFiles", "workspace/didChangeConfiguration"]);
// The log line of a handled notification; a request's line has its id after the method:
// "handled method 'm' in 1.2ms", "error handling method 'm': e" (Go lsp/server.go
// handleRequestOrNotification, and tsc-rs's port of it).
const HANDLED_LOG = /^(?:handled|error handling) method '([^']+)'(?: in |: )/;

// Lane B4: which sent notifications of BARRIER_METHODS the server has handled, by its log. Both
// servers handle notifications one at a time, in order, on their dispatch loop, and log one such line
// when the handler returns. A notification has no answer, so a wait on the stream's quiet alone can
// end while the server still handles the last one; the next step's notifications then queue behind it
// and their background work runs back to back: ATA's results, each scheduling a refresh that a 500 ms
// debounce coalesces (watch-hono-f12's load: 10 to 12 refreshes on a starved CPU, Go and tsc-rs alike).
export class NotificationBarrier {
  constructor() {
    this.sent = new Map();
    this.handled = new Map();
  }

  // A notification the client sends.
  send(method) {
    if (BARRIER_METHODS.has(method)) this.sent.set(method, (this.sent.get(method) ?? 0) + 1);
  }

  // A window/logMessage text of the server.
  log(message) {
    const m = HANDLED_LOG.exec(message ?? "");
    if (m) this.handled.set(m[1], (this.handled.get(m[1]) ?? 0) + 1);
  }

  // The methods with sent notifications the server has not logged yet ("<method> <handled>/<sent>").
  behind() {
    return [...this.sent].filter(([m, n]) => (this.handled.get(m) ?? 0) < n).map(([m, n]) => `${m} ${this.handled.get(m) ?? 0}/${n}`);
  }
}'''

CAUGHT_UP = '''

  // Resolves once the server has handled every notification of BARRIER_METHODS sent so far
  // (NotificationBarrier), at most `maxMs`. A server that died or hangs is not waited for: the next
  // request reports its crash or timeout, as before (a tsc-rs failure, not a harness error).
  async caughtUp(maxMs = REQUEST_TIMEOUT_MS) {
    const start = performance.now();
    for (;;) {
      const waited = performance.now() - start;
      if (this.dead || waited >= maxMs || this.barrier.behind().length === 0) return waited;
      await new Promise((r) => setTimeout(r, 10));
    }
  }'''

SELFTEST_B4 = '''

// ---- lane B4: a scenario wait starts when the server has handled the notifications sent -----------
await part("b4", async () => {
  // Every wait first waits for the barrier, before any of its quiet or npm windows.
  let clock = 0;
  const probe = scriptProbe(() => "", () => (clock += 100), false);
  await probe.script.settle(300, "probe");
  check("b4: a scenario wait waits for the server to have handled its notifications before its windows", probe.caught.length === 1 && probe.caught[0] === 0 && probe.settles.length >= 1, `caught ${probe.caught}, settles ${probe.settles}`);
  // The barrier counts the notifications sent and the server's log lines of handled ones.
  const { NotificationBarrier } = await import(join(LSPDIFF, "client.mjs"));
  const b = new NotificationBarrier();
  b.send("textDocument/didOpen");
  b.send("textDocument/didOpen");
  b.send("$/cancelRequest"); // not waited for
  b.send("workspace/didChangeWatchedFiles");
  const first = b.behind().join("; ");
  b.log("handled method 'textDocument/didOpen' in 3.1ms");
  b.log("handled method 'textDocument/diagnostic' (4) in 1ms"); // a request's line
  b.log("handled method 'textDocument/didOpen' (7) in 1ms"); // with an id: not a notification
  b.log("Processed 1 file changes in 85µs");
  const second = b.behind().join("; ");
  b.log("handled method 'textDocument/didOpen' in 1.2s");
  b.log("error handling method 'workspace/didChangeWatchedFiles': boom");
  check("b4: the barrier is behind until each notification sent has its handled (or error) log line, requests' lines aside",
    first === "textDocument/didOpen 0/2; workspace/didChangeWatchedFiles 0/1" && second === "textDocument/didOpen 1/2; workspace/didChangeWatchedFiles 0/1" && b.behind().length === 0,
    JSON.stringify({ first, second, last: b.behind() }));
});'''


def edit(path, pairs):
    text = path.read_text()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f'apply-barrier: {path.name}: anchor found {text.count(old)} times: {old[:60]!r}')
        text = text.replace(old, new)
    return text


def main():
    root = Path(sys.argv[1]) / 'vendor/lspdiff'
    out = {}
    anchor = 'const REQUEST_TIMEOUT_MS = SETTINGS.LSPDIFF_REQUEST_TIMEOUT_MS;'
    notify = '  notify(method, params) {\n    this.#write({ jsonrpc: "2.0", method, params });\n  }'
    out['client.mjs'] = edit(root / 'client.mjs', [
        (anchor, anchor + CLIENT_CONSTS),
        ('    this.lastMessageAt = performance.now();\n    this.buf',
         '    this.lastMessageAt = performance.now();\n    this.barrier = new NotificationBarrier(); // caughtUp\n    this.buf'),
        ('    this.notifications.push({ method: msg.method, params: msg.params, at });',
         '    if (msg.method === "window/logMessage") this.barrier.log(msg.params?.message);\n'
         '    this.notifications.push({ method: msg.method, params: msg.params, at });'),
        (notify, notify.replace('  notify(method, params) {\n', '  notify(method, params) {\n    this.barrier.send(method);\n') + CAUGHT_UP),
    ])
    out['scenario.mjs'] = edit(root / 'scenario.mjs', [
        ('  async settle(ms, label) {\n    const start = performance.now();',
         '  async settle(ms, label) {\n'
         '    // First the server must have handled every notification sent (client.mjs caughtUp, lane B4): the\n'
         '    // windows below then start after its work for them has started.\n'
         '    await this.client.caughtUp();\n'
         '    const start = performance.now();'),
    ])
    nd2_end = '''  check("nd2: with ATA off, a scenario wait is the server's quiet window alone", off.reads() === 0 && quick < 1500, `reads ${off.reads()}, waited ${quick}`);\n});'''
    out['selftest/h4-selftest.mjs'] = edit(root / 'selftest/h4-selftest.mjs', [
        ('  script.client = { settle: async () => settles.push(now()), ',
         '  // caughtUp (lane B4): the windows that settle had opened when each barrier wait began.\n'
         '  const caught = [];\n'
         '  script.client = { settle: async () => settles.push(now()), caughtUp: async () => caught.push(settles.length), '),
        ('  return { script, settles, reads: () => reads };', '  return { script, settles, caught, reads: () => reads };'),
        (nd2_end, nd2_end + SELFTEST_B4),
    ])
    for name, text in out.items():
        (root / name).write_text(text)


if __name__ == '__main__':
    main()
