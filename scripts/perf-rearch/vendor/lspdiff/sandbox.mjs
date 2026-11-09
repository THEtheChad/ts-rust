// One language server process (Go, tsc-rs or JS tsserver) with every outcome the parity gate judges
// (lane H4): its exit code or signal, its stderr, what it did to its inputs, and its shutdown.
// client.mjs and tsserver.mjs spawn their servers with it; `result()` is the mandatory session
// result that lspdiff.mjs judges for every run.
//
// Hermetic environment (lane H5): every server process gets SERVER_ENV and nothing of lspdiff's own
// environment but what its caller passes (a scenario's ATA plumbing, a --rs-env setting). HOME is a
// private home, empty and new for every process (HOME_DIRS made in it): the XDG dirs (so ATA's typings
// location, os.UserCacheDir()/typescript/<version>, is in it) and TMPDIR are under it. PATH holds the
// system dirs only, so no npm of the user's is found (ATA's npm is the stand-in, ata-npm/, which
// scenario.mjs puts first). What a server and its npm write there is counted (`homeWrites`) and
// thrown away with the home.
//
// LSPDIFF_SANDBOX (set by parity_lsp.py): JSON { scratch, home, mounts: [{ lower, target }] }. Each
// server process then runs in its own user, mount and network namespace (no network: only a loopback
// that is down) with a fresh overlay per mount (lower: the input's read-only snapshot, upper: a new dir
// under scratch) at the input's own path, and its private home bound at `home`, the same path in every
// run (ATA's typings location shows in answers, so it must not differ between runs or servers). After
// the process ends, the upper dir of every mount must be empty: anything there is a file the server
// wrote, created, deleted (an overlay whiteout) or changed in its inputs, even one it put back.
// Without LSPDIFF_SANDBOX (lspdiff on its own) a server runs as it is, with its private home in a new
// temporary dir (so its path differs from run to run).
//
// stderr: the whole stream is hashed, and every line is matched against CRASH_RE (Go's panic,
// fatal error, goroutine trace and signal lines; Rust's panic and stack overflow; an abort). A
// server whose stderr has such a line is not clean, whatever else it did. The rest of stderr is not
// compared with Go's: both servers write logs there that differ in timing and wording.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SETTINGS } from "./settings.mjs";

const SANDBOX = process.env.LSPDIFF_SANDBOX ? JSON.parse(process.env.LSPDIFF_SANDBOX) : null;
if (SANDBOX && !(typeof SANDBOX.home === "string" && SANDBOX.home.startsWith("/"))) throw new Error("LSPDIFF_SANDBOX: no absolute `home`");
let started = 0;

// The system dirs a server's PATH holds (callers put their own dirs before them).
export const SERVER_PATH = "/usr/bin:/bin";
const HOME_DIRS = [".cache", ".config", ".local/share", ".local/state", "tmp"];
// The whole environment of a server process with its private home at `home` (callers add theirs).
export function serverEnv(home) {
  return {
    PATH: SERVER_PATH,
    HOME: home,
    XDG_CACHE_HOME: `${home}/.cache`,
    XDG_CONFIG_HOME: `${home}/.config`,
    XDG_DATA_HOME: `${home}/.local/share`,
    XDG_STATE_HOME: `${home}/.local/state`,
    TMPDIR: `${home}/tmp`,
    LANG: "C.UTF-8",
  };
}

export const CRASH_RE = /^(panic: |fatal error: |goroutine \d+ \[|SIG[A-Z]+: |\[signal |thread '.*' panicked at |.*\bpanicked at |.*has overflowed its stack|Aborted\b|.*core dumped)/;

// Every entry under dir, as relative paths ("" when dir is empty or absent).
function walk(dir, rel = "", out = []) {
  let names;
  try {
    names = readdirSync(join(dir, rel)).sort();
  } catch {
    return out;
  }
  for (const n of names) {
    const r = rel ? `${rel}/${n}` : n;
    out.push(r);
    if (lstatSync(join(dir, r)).isDirectory()) walk(dir, r, out);
  }
  return out;
}

export class ServerProcess {
  // cmd: [binary, ...args]; stderr: a stream that gets a copy of the server's stderr (--verbose).
  constructor(cmd, { cwd, env, stderr } = {}) {
    let argv = cmd;
    this.mounts = [];
    this.dir = SANDBOX ? join(SANDBOX.scratch, `${process.pid}-${++started}`) : mkdtempSync(join(tmpdir(), "lspdiff-server-"));
    if (SANDBOX) rmSync(this.dir, { recursive: true, force: true });
    this.home = join(this.dir, "home");
    for (const d of HOME_DIRS) mkdirSync(join(this.home, d), { recursive: true });
    let home = this.home;
    if (SANDBOX) {
      const pos = [];
      const script = [];
      SANDBOX.mounts.forEach((m, k) => {
        const upper = join(this.dir, String(k), "upper");
        const work = join(this.dir, String(k), "work");
        mkdirSync(upper, { recursive: true });
        mkdirSync(work, { recursive: true });
        this.mounts.push({ ...m, upper });
        const b = 4 * k;
        script.push(`mount -t overlay overlay -o "lowerdir=\${${b + 1}},upperdir=\${${b + 2}},workdir=\${${b + 3}}" "\${${b + 4}}"`);
        pos.push(m.lower, upper, work, m.target);
      });
      script.push(`mount --bind "\${${pos.length + 1}}" "\${${pos.length + 2}}"`);
      pos.push(this.home, SANDBOX.home);
      home = SANDBOX.home;
      script.push(`shift ${pos.length}`, 'exec "$@"');
      argv = ["unshare", "-rmn", "--", "sh", "-c", script.join(" && "), "sandbox", ...pos, ...cmd];
    }
    this.proc = spawn(argv[0], argv.slice(1), { cwd, env: { ...serverEnv(home), ...env }, stdio: ["pipe", "pipe", "pipe"] });
    this.hash = createHash("sha256");
    this.bytes = 0;
    this.tail = "";
    this.partial = "";
    this.crash = null;
    this.proc.stderr.on("data", (chunk) => {
      this.hash.update(chunk);
      this.bytes += chunk.length;
      const lines = (this.partial + chunk.toString("utf8")).split("\n");
      this.partial = lines.pop();
      for (const l of lines) this.#scan(l);
      this.tail = (this.tail + chunk.toString("utf8")).slice(-2000);
      stderr?.write(chunk);
    });
    this.exited = new Promise((resolve) => this.proc.on("exit", (code, signal) => resolve({ code, signal })));
    // close: the process ended and its stdio is drained (stderr is complete), unless a child of the
    // server still holds the pipes.
    this.closed = new Promise((resolve) =>
      this.proc.on("close", (code, signal) => {
        this.#scan(this.partial);
        this.partial = "";
        resolve({ code, signal });
      }),
    );
  }

  #scan(line) {
    if (!this.crash && CRASH_RE.test(line)) this.crash = line.slice(0, 300);
  }

  // What the process did to its inputs (after it ended): { modified: [paths], homeWrites: n } (the
  // entries it and its children made in its private home, which goes with this call).
  inputs() {
    const modified = [];
    for (const m of this.mounts) modified.push(...walk(m.upper).map((p) => `${m.target}/${p}`));
    const made = new Set(HOME_DIRS.flatMap((d) => d.split("/").map((_, k, a) => a.slice(0, k + 1).join("/"))));
    const homeWrites = walk(this.home).filter((p) => !made.has(p)).length;
    spawnSync("chmod", ["-R", "u+rwx", this.dir]); // overlayfs leaves work/work with mode 000
    rmSync(this.dir, { recursive: true, force: true });
    return { modified, homeWrites };
  }

  // The session result: every outcome of the process, and `clean` when none is a failure. shutdown:
  // "ok" or why the shutdown request failed; forced: the process did not exit by itself in time and
  // was killed; extra: more reasons from the caller.
  async result({ shutdown, forced, exitMs = null, extra = [] }) {
    const { code, signal } = await this.exited;
    // stderr is complete when its pipe closes. A child the server started (ATA's npm) can hold the
    // pipe open after the server exited: that is not the server's output, so the wait is bounded.
    let timer;
    const complete = await Promise.race([this.closed.then(() => true), new Promise((r) => (timer = setTimeout(() => r(false), 2000)))]);
    clearTimeout(timer);
    const inputs = this.inputs();
    const why = [];
    if (shutdown !== "ok") why.push(`shutdown: ${shutdown}`);
    if (forced) why.push(`it did not exit by itself in ${SETTINGS.LSPDIFF_SHUTDOWN_MS} ms after exit and was killed`);
    if (code !== 0 || signal) why.push(`exit ${signal ?? code}`);
    if (this.crash) why.push(`stderr: ${this.crash}`);
    if (inputs.modified.length) why.push(`it changed its inputs: ${inputs.modified.slice(0, 5).join(", ")}${inputs.modified.length > 5 ? ", ..." : ""}`);
    why.push(...extra);
    return {
      clean: why.length === 0,
      why,
      exit: { code, signal },
      shutdown,
      forced,
      exitMs,
      stderr: { bytes: this.bytes, sha: this.hash.digest("hex").slice(0, 16), crash: this.crash, complete },
      inputs,
    };
  }
}
