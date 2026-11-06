// The logging shim of the user's npm (scenario.mjs runScenario, the scenarios with ATA on the user's
// typings cache): it runs the real npm (ATA_REAL_NPM, the npm on lspdiff's PATH) with the same
// arguments, directory, input and output, with this dir taken off PATH. Like the stand-in npm
// (ata-npm/fake-npm.mjs) a call writes two JSON lines to $ATA_NPM_LOG: { start, pid } (written by
// ./npm before node starts) and, when the real npm has exited, { done, pid, args, code } (here). The
// scenario's waits pair them by pid (Script.npmIdle): ATA's npm is the one part of a server's work
// that the LSP stream does not show (lane ND2). Both lines carry `at` (ms since the epoch), for
// diagnosis only.
import { spawnSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { delimiter, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const log = (entry) => appendFileSync(process.env.ATA_NPM_LOG, JSON.stringify(entry) + "\n");
const self = dirname(fileURLToPath(import.meta.url));
const PATH = (process.env.PATH ?? "").split(delimiter).filter((d) => d !== self).join(delimiter);
const r = spawnSync(process.env.ATA_REAL_NPM, args, { stdio: "inherit", env: { ...process.env, PATH } });
const code = r.error ? 127 : (r.status ?? 1);
log({ done: true, pid: process.pid, args, code, at: Date.now() });
process.exit(code);
