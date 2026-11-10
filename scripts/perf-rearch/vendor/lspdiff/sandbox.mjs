// One language server process (Go, tsc-rs or JS tsserver) with every outcome the parity gate judges
// (lane H4): its exit code or signal, its stderr, what it did to its inputs and its private home, and its
// shutdown. client.mjs and tsserver.mjs spawn their servers with it; `result()` is the mandatory session
// result that lspdiff.mjs judges for every run.
//
// Hermetic environment (lane H5): every server process gets SERVER_ENV and nothing of lspdiff's own
// environment but what its caller passes (a scenario's ATA plumbing, a --rs-env setting). HOME is a
// private home, new for every process with only its skeleton (HOME_DIRS, each dir mode 755): the XDG dirs
// (so ATA's typings location, os.UserCacheDir()/typescript/<version>, is in it) and TMPDIR are under it.
// PATH holds the system dirs only, so no npm of the user's is found (ATA's npm is the stand-in, ata-npm/,
// which scenario.mjs puts first).
//
// The home is an output (lane H8): `mark(at)` lists it (listHome: every entry with its type, mode and
// content hash) and keeps each listing that differs from the one before, with where in the session it was
// taken; the session result's `home` is that history and the final listing, which lspdiff compares with
// Go's like an answer (item "session home"). The scripts mark the home after every answer when ATA is off
// and at every wait (quiet, checkpoint) with ATA on, where ATA is idle, so a file a server writes and
// removes again between two answers is seen too (with ATA on: between two waits).
//
// LSPDIFF_SANDBOX (set by parity_lsp.py): JSON { scratch, home, mounts: [{ lower, target }] }. Each
// server process then runs under the sandbox's init, which the api tier uses too
// (scripts/perf-rearch/parity_sandbox_init.py; lane H8): PID 1 of its own user, mount, network and PID
// namespace, the host's state dirs (/home, /tmp, /run, ...) covered by empty tmpfs dirs (so the user's
// typings cache, npm's cache and config do not exist for it, not even for stat), the whole tree read-only
// but its storage: a fresh overlay per mount (lower: the input's read-only snapshot, upper: a new dir under
// scratch) at the input's own path, and its private home bound at `home`, the same path in every run
// (ATA's typings location shows in answers, so it must not differ between runs or servers). What else it
// needs is bound back read-only at its own path: the files its command names (the binary, node, a
// script), and the caller's `binds` (a fixture dir, the stand-in npm and its store; a `rw` bind, the npm
// log's dir, writable). The server itself has no privilege (a nested user namespace that owns nothing, no
// capability, uid 1000, no_new_privs), no fd but 0, 1 and 2, and no network. When it ends, every process
// left in its namespace is a survivor, a failure, and is killed. The init reports a setup or exec failure
// (a mount, the namespace, a file that is not executable) on a pipe of its own: `infra` in the result,
// the harness's failure and never a server's answer (lspdiff stops: the session is an ERROR and nothing is
// cached). After the process ends, the upper dir of every mount must be empty: anything there is a file
// the server wrote, created, deleted (an overlay whiteout) or changed in its inputs, even one it put back.
// Without LSPDIFF_SANDBOX (lspdiff on its own) a server runs as it is, with its private home in a new
// temporary dir (so its path differs from run to run).
//
// stderr: the whole stream is hashed, and every line is matched against CRASH_RE (Go's panic,
// fatal error, goroutine trace and signal lines; Rust's panic and stack overflow; an abort). A
// server whose stderr has such a line is not clean, whatever else it did. The rest of stderr is not
// compared with Go's: both servers write logs there that differ in timing and wording. Both output
// streams must reach EOF once the process ended (with the sandbox, the init's end closes them).
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, readlinkSync, realpathSync, rmSync, statSync } from "node:fs";
import { constants, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { SETTINGS } from "./settings.mjs";

const SANDBOX = process.env.LSPDIFF_SANDBOX ? JSON.parse(process.env.LSPDIFF_SANDBOX) : null;
if (SANDBOX && !(typeof SANDBOX.home === "string" && SANDBOX.home.startsWith("/"))) throw new Error("LSPDIFF_SANDBOX: no absolute `home`");
// The sandbox's PID 1 (shared with the api tier; parity_lsp.py keys its source with lspdiff's).
export const SANDBOX_INIT = join(dirname(fileURLToPath(import.meta.url)), "../../parity_sandbox_init.py");
const FIXED_MTIME = 978307200; // 2001-01-01: every dir the init makes
// How long both output streams (and the init's report) may take to end after the process exited.
const EOF_MS = 10000;
let started = 0;

// The system dirs a server's PATH holds (callers put their own dirs before them).
export const SERVER_PATH = "/usr/bin:/bin";
const HOME_DIRS = [".cache", ".config", ".local/share", ".local/state", "tmp"];
const SKELETON = [...new Set(HOME_DIRS.flatMap((d) => d.split("/").map((_, k, a) => a.slice(0, k + 1).join("/"))))].sort();
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

const mode = (st) => (st.mode & 0o7777).toString(8);
const sha16 = (data) => createHash("sha256").update(data).digest("hex").slice(0, 16);

// A dir as { relative path: what it is }, as the api tier lists its storage (parity_api_session.py
// _walk): "." is the dir itself ("dir <mode>", or what it is instead), then every entry, none skipped:
// "<sha256 16> <mode>" a file, "dir <mode>", "link <target>", "special <mode>", and "unlistable <code>"
// or "unreadable <code>" where a listing or a read failed. An absent dir is {}.
export function listHome(top) {
  const out = {};
  let st;
  try {
    st = lstatSync(top);
  } catch {
    return out;
  }
  out["."] = st.isDirectory() ? `dir ${mode(st)}` : `not a dir: ${st.mode.toString(8)}`;
  const visit = (rel) => {
    let names;
    try {
      names = readdirSync(join(top, rel)).sort();
    } catch (e) {
      out[rel || "."] += `; unlistable ${e.code}`;
      return;
    }
    for (const n of names) {
      const r = rel ? `${rel}/${n}` : n;
      const p = join(top, r);
      const s = lstatSync(p);
      if (s.isSymbolicLink()) out[r] = `link ${readlinkSync(p)}`;
      else if (s.isDirectory()) {
        out[r] = `dir ${mode(s)}`;
        visit(r);
      } else if (!s.isFile()) out[r] = `special ${s.mode.toString(8)}`;
      else {
        try {
          out[r] = `${sha16(readFileSync(p))} ${mode(s)}`;
        } catch (e) {
          out[r] = `unreadable ${e.code}`;
        }
      }
    }
  };
  if (st.isDirectory()) visit("");
  return out;
}

// A wait status (waitpid) as { code, signal }.
function waitStatus(status) {
  const sig = status & 0x7f;
  if (sig === 0) return { code: (status >> 8) & 0xff, signal: null };
  return { code: null, signal: Object.keys(constants.signals).find((k) => constants.signals[k] === sig) ?? `signal ${sig}` };
}

// A command word as the init execs it: a path as it is, a bare name looked up in SERVER_PATH.
function resolve(word) {
  if (word.includes("/")) return word;
  for (const d of SERVER_PATH.split(":")) if (existsSync(join(d, word))) return join(d, word);
  return word;
}

const isFile = (p) => {
  try {
    return statSync(p).isFile();
  } catch {
    return false;
  }
};

export class ServerProcess {
  // cmd: [binary, ...args]; stderr: a stream that gets a copy of the server's stderr (--verbose);
  // binds: [{ src, rw }] host paths the server needs besides its command's files, each seen at its own
  // path in the sandbox (read-only unless rw).
  constructor(cmd, { cwd, env, stderr, binds = [] } = {}) {
    this.mounts = [];
    this.dir = SANDBOX ? join(SANDBOX.scratch, `${process.pid}-${++started}`) : mkdtempSync(join(tmpdir(), "lspdiff-server-"));
    if (SANDBOX) rmSync(this.dir, { recursive: true, force: true });
    this.home = join(this.dir, "home");
    for (const d of ["", ...SKELETON]) {
      mkdirSync(join(this.home, d), { recursive: true });
      chmodSync(join(this.home, d), 0o755);
    }
    this.history = [];
    this.killed = false;
    this.spawnError = null;
    this.report = "";
    let home = this.home;
    let argv = cmd;
    let stdio = ["pipe", "pipe", "pipe"];
    if (SANDBOX) {
      const mounts = [{ bind: this.home, at: SANDBOX.home, rw: true }];
      SANDBOX.mounts.forEach((m, k) => {
        const base = join(this.dir, String(k));
        mkdirSync(join(base, "upper"), { recursive: true });
        mkdirSync(join(base, "ovl"), { recursive: true });
        this.mounts.push({ ...m, upper: join(base, "upper") });
        mounts.push({ overlay: m.lower, base, at: m.target });
      });
      for (const b of binds.filter((b) => b.rw)) mounts.push({ bind: b.src, at: b.src, rw: true });
      // Every word of the command that names a file is exec'd or read by its realpath, bound back.
      const words = cmd.map((w, i) => (i === 0 ? resolve(w) : w)).map((w) => (isFile(w) ? realpathSync(w) : w));
      const expose = [...words.filter(isFile), ...binds.filter((b) => !b.rw).map((b) => b.src)];
      const spec = { report: 3, mounts, expose, cwd: cwd ?? "/", mtime: FIXED_MTIME, cmd: words };
      home = SANDBOX.home;
      argv = ["/usr/bin/python3", "-I", SANDBOX_INIT, "launch", JSON.stringify(spec)];
      stdio = [...stdio, "pipe"];
      cwd = "/";
    }
    this.proc = spawn(argv[0], argv.slice(1), { cwd, env: { ...serverEnv(home), ...env }, stdio });
    this.proc.on("error", (e) => (this.spawnError ??= `${argv[0]}: ${e.message}`));
    this.reportDone = SANDBOX
      ? new Promise((resolve) => {
          this.proc.stdio[3].on("data", (c) => (this.report += c));
          this.proc.stdio[3].on("close", resolve);
          this.proc.stdio[3].on("error", resolve);
        })
      : Promise.resolve();
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
    // exit: the process ended (with the sandbox: unshare, after the init); a spawn failure ends it too.
    this.exited = new Promise((resolve) => {
      this.proc.on("exit", (code, signal) => resolve({ code, signal }));
      this.proc.on("error", () => resolve({ code: null, signal: null }));
    });
    // close: the process ended and every one of its streams reached EOF.
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

  // Ends the process (and with the sandbox every process of its namespace: killing unshare kills the
  // init, PID 1). A run lspdiff ended itself has no report from the init, and that is no harness failure.
  kill() {
    this.killed = true;
    this.proc.kill("SIGKILL");
  }

  // Lists the home and keeps the listing when it differs from the last one kept; `at` says where in the
  // session (the scripts' sync points, see the top comment).
  mark(at) {
    const state = sha16(JSON.stringify(listHome(this.home)));
    if (this.history.at(-1)?.state !== state) this.history.push({ at, state });
  }

  // The pids, in the server's PID namespace, of every process there that has not ended (the init's
  // descendants, read from the host's /proc: NSpid's last field). Without the sandbox, null.
  namespacePids() {
    if (!SANDBOX) return null;
    const children = (pid) => {
      try {
        return readdirSync(`/proc/${pid}/task`).flatMap((t) => readFileSync(`/proc/${pid}/task/${t}/children`, "utf8").trim().split(/\s+/).filter(Boolean));
      } catch {
        return [];
      }
    };
    const out = new Set();
    const todo = children(this.proc.pid); // the init
    while (todo.length) {
      const pid = todo.pop();
      try {
        const status = readFileSync(`/proc/${pid}/status`, "utf8");
        if (!/^State:\s+Z/m.test(status)) out.add(Number(/^NSpid:.*\s(\d+)\s*$/m.exec(status)?.[1]));
      } catch {
        continue;
      }
      todo.push(...children(pid));
    }
    return out;
  }

  // Whether the process with this pid (the server's own pid, as its children see it) has not ended.
  alive(pid) {
    const ns = this.namespacePids();
    if (ns) return ns.has(pid);
    try {
      process.kill(pid, 0);
      return true;
    } catch (e) {
      return e.code === "EPERM";
    }
  }

  // What the process did to its inputs and its home (after it ended): { modified: [paths], homeWrites: n }
  // (the entries it and its children made in its private home), and the home's record (see the top
  // comment); the dirs go with this call.
  inputs() {
    const modified = [];
    for (const m of this.mounts) modified.push(...walk(m.upper).map((p) => `${m.target}/${p}`));
    const final = listHome(this.home);
    this.mark("end");
    const homeWrites = Object.keys(final).filter((p) => p !== "." && !SKELETON.includes(p)).length;
    spawnSync("chmod", ["-R", "u+rwx", this.dir]); // overlayfs leaves ovl/work with mode 000
    rmSync(this.dir, { recursive: true, force: true });
    return { modified, homeWrites, home: { history: this.history, final } };
  }

  // The init's report: { status, survivors, reaped }, { setup }, or null (none).
  #report() {
    if (!SANDBOX) return null;
    try {
      return this.report ? JSON.parse(this.report) : null;
    } catch {
      return { setup: `an unreadable report from the sandbox's init: ${this.report.slice(0, 200)}` };
    }
  }

  // The session result: every outcome of the process, and `clean` when none is a failure. shutdown:
  // "ok" or why the shutdown request failed; forced: the process did not exit by itself in time and
  // was killed; extra: more reasons from the caller. infra: the server could not be started (the
  // harness's failure: lspdiff stops).
  async result({ shutdown, forced, exitMs = null, extra = [] }) {
    let { code, signal } = await this.exited;
    let timer;
    const complete = await Promise.race([Promise.all([this.closed, this.reportDone]).then(() => true), new Promise((r) => (timer = setTimeout(() => r(false), EOF_MS)))]);
    clearTimeout(timer);
    const report = this.#report();
    let infra = this.spawnError ?? report?.setup ?? null;
    if (SANDBOX && !report && !this.killed && !infra) infra = `no report from the sandbox's init (exit ${code ?? signal}): ${this.tail.slice(-300)}`;
    if (report?.status !== undefined) ({ code, signal } = waitStatus(report.status));
    const { home, ...inputs } = this.inputs();
    const why = [];
    if (infra) why.push(`the sandbox: ${infra}`);
    if (shutdown !== "ok") why.push(`shutdown: ${shutdown}`);
    if (forced) why.push(`it did not exit by itself in ${SETTINGS.LSPDIFF_SHUTDOWN_MS} ms after exit and was killed`);
    if (code !== 0 || signal) why.push(`exit ${signal ?? code}`);
    if (this.crash) why.push(`stderr: ${this.crash}`);
    if (!complete) why.push(`its output was still held open ${EOF_MS} ms after it ended`);
    if (report?.survivors?.length || report?.reaped) why.push(`processes survived the server: ${JSON.stringify((report.survivors ?? []).slice(0, 5))}, ${report.reaped} killed after it`);
    if (inputs.modified.length) why.push(`it changed its inputs: ${inputs.modified.slice(0, 5).join(", ")}${inputs.modified.length > 5 ? ", ..." : ""}`);
    why.push(...extra);
    return {
      clean: why.length === 0,
      why,
      ...(infra ? { infra } : {}),
      exit: { code, signal },
      shutdown,
      forced,
      exitMs,
      stderr: { bytes: this.bytes, sha: this.hash.digest("hex").slice(0, 16), crash: this.crash, complete },
      survivors: report?.survivors ?? [],
      reaped: report?.reaped ?? 0,
      inputs,
      home,
    };
  }
}
