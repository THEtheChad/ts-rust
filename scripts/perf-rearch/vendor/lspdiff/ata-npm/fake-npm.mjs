// The stand-in npm of the ata-overlay watch scenario (scenario.mjs; the idea of
// scripts/perf-rearch/nd1/fake-npm): offline, and every call takes ATA_NPM_DELAY_MS, so whether an
// answer sees ATA's typings never depends on npm's or the network's timing, for Go and tsc-rs alike.
//   npm install --ignore-scripts types-registry@latest     writes node_modules/types-registry/index.json
//                                                          from $ATA_STORE/registry.json
//   npm install --ignore-scripts @types/<x>@<tag>... ...   copies $ATA_STORE/types/<x>/ to
//                                                          node_modules/@types/<x>/ for each package
// in the current directory (the server's typings location). Each call appends two JSON lines to
// $ATA_NPM_LOG: { start, pid, args } and, when its work is done, { done, pid, args, code } (the
// scenario's barrier pairs them by pid and waits for the process to exit).
import { appendFileSync, cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2);
const log = (entry) => process.env.ATA_NPM_LOG && appendFileSync(process.env.ATA_NPM_LOG, JSON.stringify(entry) + "\n");
log({ start: true, pid: process.pid, args });
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(process.env.ATA_NPM_DELAY_MS)); // set by scenario.mjs from settings.mjs
const store = process.env.ATA_STORE;
let code = 0;
if (args[0] === "install") {
  for (const a of args.slice(1).filter((x) => !x.startsWith("--"))) {
    if (a === "types-registry@latest") {
      mkdirSync("node_modules/types-registry", { recursive: true });
      writeFileSync("node_modules/types-registry/index.json", readFileSync(join(store, "registry.json")));
      writeFileSync("node_modules/types-registry/package.json", JSON.stringify({ name: "types-registry", version: "0.1.0" }) + "\n");
      continue;
    }
    const m = /^@types\/([^@]+)@/.exec(a);
    if (m && existsSync(join(store, "types", m[1]))) cpSync(join(store, "types", m[1]), join("node_modules/@types", m[1]), { recursive: true });
    else code = 1;
  }
} else code = 1;
log({ done: true, pid: process.pid, args, code });
process.exit(code);
