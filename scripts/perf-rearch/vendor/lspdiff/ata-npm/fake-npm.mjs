// The stand-in npm of the watch scenarios with ATA on (scenario.mjs; the idea of
// scripts/perf-rearch/nd1/fake-npm; lane H5 made it the only npm a server can run): offline, every call
// takes the configured delay, and it installs from a store, so what ATA's typings are never depends on the
// registry, the network, a shared cache or npm's timing, for Go and tsc-rs alike.
//
// Its configuration is CONFIG, a file the harness writes and the sandbox binds read-only at that fixed
// path (lane H8 round 2: nothing of the environment, which the server controls): { store, files, log,
// delayMs, cache (the private home's XDG_CACHE_HOME: a call elsewhere is refused) }. The store (the fixture's, fixtures.mjs ATA_STORE, or the frozen typings store that
// scripts/perf-rearch/ata_typings.py builds; read-only for the server) is authenticated file by file:
// `files` maps every store path to its sha256 (the frozen store's manifest, ata-typings.json), and a file
// read from the store that is not listed or has other bytes fails the call. The store:
//   registry/            the types-registry package (index.json, package.json, ...)
//   node_modules/        every package of the store, as npm laid them out when it installed them
//   package-lock.json    that install's lock (lockfileVersion 3; its `packages` entries)
//   closure.json         { "@types/<x>": [the lock paths an install of @types/<x> lays out] }
// Calls, in the current directory (the server's typings location):
//   npm install --ignore-scripts types-registry@latest        copies registry/ to node_modules/types-registry/
//   npm install --ignore-scripts @types/<x>@<tag>... --save-dev   copies the closure of each package
// and records them in package.json (dependencies, devDependencies with --save-dev) and package-lock.json,
// as npm does (every map sorted; dirs 755, files 644). A package the store lacks fails the call with
// nothing installed (npm's E404 does too) and logs `missing`; a call whose directory is not under the
// server's private XDG_CACHE_HOME (sandbox.mjs) does nothing and logs `refused`; a failure of its own (a
// store file that fails its hash, a file it cannot write) logs `error`. scenario.mjs makes each a harness
// error. Each call appends two JSON lines to the configured log: { start, pid, args } and, when its work
// is done, { done, pid, args, code }: the scenario's barrier (it pairs them by pid and waits for the
// process to exit). The log's dir is writable by the server, so the log is a barrier and a report, not
// evidence: what was installed is in the sandbox's record of every write to the home (sandbox.mjs).
import { appendFileSync, chmodSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

export const CONFIG = "/run/lspdiff-ata/config.json";
const cfg = JSON.parse(readFileSync(CONFIG, "utf8"));
const args = process.argv.slice(2);
const log = (entry) => appendFileSync(cfg.log, JSON.stringify(entry) + "\n");
const done = (code, more = {}) => {
  log({ done: true, pid: process.pid, args, code, ...more });
  process.exit(code);
};
log({ start: true, pid: process.pid, args });
process.on("uncaughtException", (e) => done(1, { error: String(e?.message ?? e).slice(0, 300) }));
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(cfg.delayMs));
if (!process.cwd().startsWith(`${cfg.cache}/`)) done(1, { refused: `npm ${args.join(" ")} in ${process.cwd()}, not under the private XDG_CACHE_HOME ${cfg.cache}` });
if (args[0] !== "install") done(1, { refused: `npm ${args.join(" ")}: the stand-in only installs` });

// A store file's bytes, checked against the configured sha256 (else the call fails).
function storeFile(rel) {
  const data = readFileSync(join(cfg.store, rel));
  const want = Object.hasOwn(cfg.files, rel) ? cfg.files[rel] : null;
  if (want === null) throw new Error(`the store file ${rel} is not in its manifest`);
  if (createHash("sha256").update(data).digest("hex") !== want) throw new Error(`the store file ${rel} does not have its manifest's sha256`);
  return data;
}
const storeJson = (rel) => JSON.parse(storeFile(rel).toString("utf8"));
const own = (o, k) => (o && Object.hasOwn(o, k) ? o[k] : undefined);

const closure = storeJson("closure.json");
const storeLock = storeJson("package-lock.json");
const names = args.slice(1).filter((a) => !a.startsWith("--"));
const dev = args.includes("--save-dev");
const missing = [];
const wanted = []; // [name, version, [lock paths]]
for (const a of names) {
  if (a === "types-registry@latest") {
    wanted.push(["types-registry", storeJson("registry/package.json").version, null]);
    continue;
  }
  const m = /^(@types\/[^@]+)@[^@]+$/.exec(a);
  if (!m || !own(closure, m[1])) missing.push(a);
  else wanted.push([m[1], own(storeLock.packages, `node_modules/${m[1]}`).version, own(closure, m[1])]);
}
if (missing.length) done(1, { missing });

// Copies a package from the store as npm lays one out: dirs 755, files 644 (755 where the store's file is
// executable). Every file is checked against the manifest; the store has no symlinks or special files
// (ata_typings.py refuses them), and one here fails the call.
function install(rel, dst) {
  const st = lstatSync(join(cfg.store, rel));
  if (st.isDirectory()) {
    mkdirSync(dst, { recursive: true });
    chmodSync(dst, 0o755);
    for (const n of readdirSync(join(cfg.store, rel)).sort()) install(`${rel}/${n}`, join(dst, n));
    return;
  }
  if (!st.isFile()) throw new Error(`the store entry ${rel} is not a file or a dir`);
  writeFileSync(dst, storeFile(rel));
  chmodSync(dst, st.mode & 0o111 ? 0o755 : 0o644);
}

const readJson = (f, d) => (existsSync(f) ? JSON.parse(readFileSync(f, "utf8")) : d);
const pkg = readJson("package.json", {});
const lock = readJson("package-lock.json", { lockfileVersion: 3, requires: true, packages: {} });
for (const [name, version, paths] of wanted) {
  if (paths === null) {
    mkdirSync("node_modules", { recursive: true });
    install("registry", "node_modules/types-registry");
    lock.packages["node_modules/types-registry"] = { version };
  } else {
    for (const p of paths) {
      mkdirSync(join(p, ".."), { recursive: true });
      install(p, p);
      lock.packages[p] = own(storeLock.packages, p);
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
