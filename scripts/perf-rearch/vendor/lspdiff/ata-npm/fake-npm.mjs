// The stand-in npm of the watch scenarios with ATA on (scenario.mjs; the idea of
// scripts/perf-rearch/nd1/fake-npm; lane H5 made it the only npm a server can run): offline, every call
// takes ATA_NPM_DELAY_MS, and it installs from a store, so what ATA's typings are never depends on the
// registry, the network, a shared cache or npm's timing, for Go and tsc-rs alike.
//
// The store ($ATA_STORE: the fixture's, fixtures.mjs ATA_STORE, or the frozen typings store that
// scripts/perf-rearch/ata_typings.py builds):
//   registry/            the types-registry package (index.json, package.json, ...)
//   node_modules/        every package of the store, as npm laid them out when it installed them
//   package-lock.json    that install's lock (lockfileVersion 3; its `packages` entries)
//   closure.json         { "@types/<x>": [the lock paths an install of @types/<x> lays out] }
// Calls, in the current directory (the server's typings location):
//   npm install --ignore-scripts types-registry@latest        copies registry/ to node_modules/types-registry/
//   npm install --ignore-scripts @types/<x>@<tag>... --save-dev   copies the closure of each package
// and records them in package.json (dependencies, devDependencies with --save-dev) and package-lock.json,
// as npm does. A package the store lacks fails the call with nothing installed (npm's E404 does too)
// and logs `missing`; a call whose directory is not under the server's private XDG_CACHE_HOME
// (sandbox.mjs) does nothing and logs `refused`. scenario.mjs makes either a harness error: the
// input would not be the frozen one. Each call appends two JSON lines to $ATA_NPM_LOG: { start, pid,
// args } and, when its work is done, { done, pid, args, code } (the scenario's barrier pairs them by
// pid and waits for the process to exit).
import { appendFileSync, cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2);
const log = (entry) => appendFileSync(process.env.ATA_NPM_LOG, JSON.stringify(entry) + "\n");
const done = (code, more = {}) => {
  log({ done: true, pid: process.pid, args, code, ...more });
  process.exit(code);
};
log({ start: true, pid: process.pid, args });
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(process.env.ATA_NPM_DELAY_MS)); // set by scenario.mjs from settings.mjs
const store = process.env.ATA_STORE;
const cache = process.env.XDG_CACHE_HOME;
if (!cache || !process.cwd().startsWith(`${cache}/`)) done(1, { refused: `npm ${args.join(" ")} in ${process.cwd()}, not under the private XDG_CACHE_HOME ${cache}` });
if (args[0] !== "install") done(1, { refused: `npm ${args.join(" ")}: the stand-in only installs` });

const readJson = (f, d) => (existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : d);
const closure = readJson(join(store, "closure.json"));
const storeLock = readJson(join(store, "package-lock.json"));
const names = args.slice(1).filter((a) => !a.startsWith("--"));
const dev = args.includes("--save-dev");
const missing = [];
const wanted = []; // [name, version, [lock paths]]
for (const a of names) {
  if (a === "types-registry@latest") {
    wanted.push(["types-registry", readJson(join(store, "registry/package.json")).version, null]);
    continue;
  }
  const m = /^(@types\/[^@]+)@[^@]+$/.exec(a);
  if (!m || !closure[m[1]]) missing.push(a);
  else wanted.push([m[1], storeLock.packages[`node_modules/${m[1]}`].version, closure[m[1]]]);
}
if (missing.length) done(1, { missing });

const pkg = readJson("package.json", {});
const lock = readJson("package-lock.json", { lockfileVersion: 3, requires: true, packages: {} });
for (const [name, version, paths] of wanted) {
  if (paths === null) {
    cpSync(join(store, "registry"), "node_modules/types-registry", { recursive: true });
    lock.packages["node_modules/types-registry"] = { version };
  } else {
    for (const p of paths) {
      mkdirSync(join(p, ".."), { recursive: true });
      cpSync(join(store, p), p, { recursive: true });
      lock.packages[p] = storeLock.packages[p];
    }
  }
  const field = dev && paths !== null ? "devDependencies" : "dependencies";
  pkg[field] = { ...pkg[field], [name]: `^${version}` };
}
// Every map sorted, as npm writes them: the files then do not depend on the order of ATA's calls (Go
// builds an install's list from a map), and the server's home, which lspdiff compares, neither (lane H8).
const sorted = (o) => o && Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
for (const field of ["dependencies", "devDependencies"]) if (pkg[field]) pkg[field] = sorted(pkg[field]);
lock.packages[""] = { ...(pkg.dependencies && { dependencies: pkg.dependencies }), ...(pkg.devDependencies && { devDependencies: pkg.devDependencies }) };
lock.packages = sorted(lock.packages);
writeFileSync("package.json", JSON.stringify(pkg));
writeFileSync("package-lock.json", JSON.stringify(lock, null, 2) + "\n");
done(0);
