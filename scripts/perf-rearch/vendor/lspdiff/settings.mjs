// Every setting from the environment that changes what an lspdiff session does or how it is judged
// (lane H4). No other lspdiff module reads an LSPDIFF_* or ATA_* setting (selftest/h4-selftest.mjs
// greps for it; the plumbing variables PLUMBING are not settings: the sandbox's mounts, keyed by their
// snapshots' hashes, and what scenario.mjs passes to the stand-in npm: its store, which parity_lsp.py
// keys by its content hash, its log, and values from SETTINGS), so these are
// all of them: parity_lsp.py puts SETTINGS in every Go and JS cache key (it asks `node lspdiff.mjs
// info`), and lspdiff writes them to <out>/settings.json, which parity_lsp.py checks against the key
// it used.
//   LSPDIFF_SETTLE_MS           a checkpoint's quiet window (scenario.mjs)
//   LSPDIFF_QUIET_MS            the quiet wait after a didOpen (scenario.mjs quiet())
//   LSPDIFF_REQUEST_TIMEOUT_MS  a request without an answer in this time ends the run (client.mjs, tsserver.mjs)
//   LSPDIFF_SHUTDOWN_MS         the time a server gets to answer shutdown, and then to exit by itself (it
//                               may wait for ATA's npm: both servers take up to about 5 s at a high load)
//   ATA_IDLE_MS                 ATA is idle when no npm call of it started or ended for this long (scenario.mjs
//                               npmIdle: the stand-in npm ata-npm/)
//   ATA_NPM_DELAY_MS            the stand-in npm's time per call (ata-npm/fake-npm.mjs)
//   ATA_DEADLINE_MS             an ATA wait longer than this fails the run (never a success)
//   LSPDIFF_PROGRESS_DEADLINE_MS  a work-done progress the server began and did not end in this time fails the
//                               run as a harness error (scenario.mjs progressEnded; never a success)
const num = (k, d) => {
  const v = process.env[k];
  if (v === undefined || v === "") return d;
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) throw new Error(`${k}=${v}: not a number of milliseconds`);
  return n;
};

export const PLUMBING = ["LSPDIFF_SANDBOX"];

export const SETTINGS = Object.freeze({
  LSPDIFF_SETTLE_MS: num("LSPDIFF_SETTLE_MS", 1000),
  LSPDIFF_QUIET_MS: num("LSPDIFF_QUIET_MS", 300),
  LSPDIFF_REQUEST_TIMEOUT_MS: num("LSPDIFF_REQUEST_TIMEOUT_MS", 300000),
  LSPDIFF_SHUTDOWN_MS: num("LSPDIFF_SHUTDOWN_MS", 30000),
  ATA_IDLE_MS: num("ATA_IDLE_MS", 1500),
  ATA_NPM_DELAY_MS: num("ATA_NPM_DELAY_MS", 500),
  ATA_DEADLINE_MS: num("ATA_DEADLINE_MS", 60000),
  LSPDIFF_PROGRESS_DEADLINE_MS: num("LSPDIFF_PROGRESS_DEADLINE_MS", 60000),
});
