#!/usr/bin/env python3
"""perf-rearch timing tool. Run it through bench.sh (`bench.sh help` prints the usage)."""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HOME = Path.home()
DEFAULT_PROJECTS = Path(os.environ.get("PR_PROJECTS", HOME / ".cache/perf-rearch/projects"))
GO_DEFAULT = HOME / ".local/bin/tsgo-oracle-3e9236361fa9"
GO_LSP_DEFAULT = HOME / ".local/bin/tsgo-lsp-oracle-3e9236361fa9"
MAX_LOAD = float(os.environ.get("MAX_LOAD", "1.5"))
REPEAT_LOAD = float(os.environ.get("REPEAT_LOAD", "3"))
OTHER_CORES = float(os.environ.get("OTHER_CORES", "1.0"))
# Lane P2: a run never starts in the first BOOT_SETTLE seconds after the VM booted. WSL shuts the VM down without
# warning; the first runs after a boot find every cache cold and the other agents restarting (W2's open12 outliers:
# 301, 623 and 536 ms, each 7-33 s after a boot, against 91-138 ms warm).
BOOT_SETTLE = float(os.environ.get("BOOT_SETTLE", "300"))
CLI_SCENARIOS = ["noemit", "emit", "emit-js", "emit-cjs", "single", "icount", "icount-emit", "icount-emit-js",
                 "icount-emit-cjs", "incr", "incr-edit", "build"]
LSP_SCENARIOS = ["concurrent", "probe", "ttfd", "open12"]

USAGE = """\
usage: scripts/perf-rearch/bench.sh <command> [options]

  cli    time CLI scenarios on projects:   bench.sh cli --bin rs=PATH --bin go=PATH [options]
  lsp    time LSP scenarios on umami:      bench.sh lsp --bin rs=PATH --bin go=PATH [options]
  table  merge result dirs into the gap table (JSON + markdown): bench.sh table DIR... [--ref go] [--out FILE.md]
  help   this text

Common options
  --bin LABEL=PATH   a binary to time (repeat). The label `go` (or --ref LABEL) is the reference side.
                     Without any --bin: rs=~/.cache/perf-rearch/bins/tsgo-main-91f3d021c-goport and the Go oracle.
  --out DIR          result dir (default ~/.cache/perf-rearch/runs/<date>-<cmd>); results.json and results.md go
                     there, plus one JSON line per measured run in runs.jsonl (a rerun skips runs already recorded).
  --runs N           measured runs per (scenario, project, binary), default 3. Each cell also gets 1 warm-up run.
  --ref LABEL        reference label for ratios (default go)

cli options
  --projects a,b     names from projects.tsv (default: all). Run setup-projects.sh first.
  --scenarios a,b    noemit, emit, single, incr, build (default: noemit,emit,single,incr)
                       noemit  -p CFG --noEmit --incremental false --composite false
                       emit    -p CFG with outDir/declarationDir/tsBuildInfoFile in a scratch dir (never the input)
                       single  noemit + --singleThreaded (tsc-rs labels only, unless --single-ref)
                       icount  single, ungated, compared on user-space instructions (Go: GOMAXPROCS=1); also
                               icount-emit, icount-emit-js, icount-emit-cjs (the emit scenarios, single-threaded)
                       incr    noemit + --incremental with build info in scratch: one cold build, then N warm
                               (no change) rebuilds; the warm ones are measured
                       incr-edit  noemit + --incremental, warm rebuild after ONE edit (window 2): per run, in a
                               scratch copy per label: restore, untimed cold build, edit, timed rebuild. Edits are the
                               ones of cleanup/walk_cost.sh (an exported const appended to a widely imported file:
                               query-core utils.ts, umami lib/types.ts, hono types.ts; project `synth85` is the
                               85-file project of tests/multi_program.rs, Shape.x number -> string). The cell's
                               output column compares the .tsbuildinfo too.
                       build   -b CFG on a scratch copy of the repo (node_modules linked): cold build (copy
                               reset before each run) and warm no-change rebuild, as build-cold and build-warm
  --single-ref       also run --singleThreaded for the reference label
  --timeout S        per-run timeout (default 900)

lsp options (project umami unless --project DIR)
  --scenarios a,b    concurrent, probe, ttfd (default all)
                       concurrent  scripts/lspdiff bench, concurrent mode, typing at --intervals (120,250,500 ms/key)
                       probe       scripts/lspdiff/probe-edit.mjs --edits 20 --cpu on a .tsx and a .ts file
                       ttfd        time to first diagnostics: spawn, initialize, open one file, pull diagnostics
  --scenarios open12  (window 2) first diagnostic request after 12 didOpens on --project (hono's parity checkout),
                     scripts/perf-rearch/window2/open12.mjs; the cell's wall is that request
  --intervals a,b    ms per key for concurrent (default 120,250,500)
  --edit-files a,b   files for probe (default umami's BoardCloneForm.tsx and useFunnelQuery.ts)

Method (docs/perf-rearch/brief.md): every run waits for the 1-minute load < MAX_LOAD (1.5). Labels are interleaved
run by run (the order rotates each run). Load is recorded before and after each run. A run is repeated (at most
twice) when the load after it is above REPEAT_LOAD (3) plus the load the run itself adds (its CPU/wall, decayed as
the kernel's 1-minute average). No run starts within BOOT_SETTLE (300) s of the VM's boot. Each CLI run records wall, user+sys CPU, max RSS (wait4 rusage;
GOPORT_LAUNCH is left at its default and the tool is a child
subreaper, so tsc-rs's worker is measured too, see run_measured), exit code, a hash of stdout+stderr, the CPU the rest of the machine used
during the run (/proc/stat), and for emit a hash of the emitted tree. The table reports median and spread
(min..max) and flags outputs that differ from the reference.
"""


# ---------------------------------------------------------------- helpers

def load1():
    return float(Path("/proc/loadavg").read_text().split()[0])


def machine_busy_s():
    """Busy CPU seconds of the whole machine (all cores) from /proc/stat."""
    f = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    vals = list(map(int, f))
    idle = vals[3] + vals[4]
    return (sum(vals[:8]) - idle) / os.sysconf("SC_CLK_TCK")


def uptime_s():
    return float(open("/proc/uptime").read().split()[0])


def wait_quiet(log):
    waited = 0
    while True:
        l = load1()
        if l < MAX_LOAD and uptime_s() >= BOOT_SETTLE:
            return l
        if waited % 300 == 0:
            log(f"  waiting for load < {MAX_LOAD} (now {l}) and {BOOT_SETTLE:.0f} s after boot (now {uptime_s():.0f})")
        time.sleep(10)
        waited += 10


def median(xs):
    return statistics.median(xs) if xs else None


def tree_hash(d):
    h = hashlib.sha256()
    if not Path(d).exists():
        return None
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(d)).encode())
            h.update(b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def parse_bins(specs, lsp=False):
    bins = []
    for s in specs or []:
        label, _, path = s.partition("=")
        if not path:
            raise SystemExit(f"--bin wants LABEL=PATH: {s}")
        bins.append((label, str(Path(path).expanduser().resolve())))
    if not bins:
        bins = [("rs", str(HOME / ".cache/perf-rearch/bins/tsgo-main-91f3d021c-goport")),
                ("go", str(GO_LSP_DEFAULT if lsp else GO_DEFAULT))]
    for _, p in bins:
        if not os.access(p, os.X_OK):
            raise SystemExit(f"not executable: {p}")
    return bins


def read_projects(root):
    out = {}
    for line in (HERE / "projects.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, url, sha, cfg, inst, *notes = line.split("\t")
        repo = root / name if url.startswith("local:") else root / url.rsplit("/", 1)[1]
        out[name] = {"name": name, "url": url, "sha": sha, "cfg": cfg, "repo": repo,
                     "notes": notes[0] if notes else ""}
    return out


class Recorder:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.out / "runs.jsonl"
        self.done = {}
        if self.jsonl.exists():
            for line in self.jsonl.read_text().splitlines():
                r = json.loads(line)
                self.done.setdefault(self.key(r), []).append(r)
        self.logf = open(self.out / "bench.log", "a")

    @staticmethod
    def key(r):
        return (r["scenario"], r["project"], r["label"], r.get("variant", ""))

    def count(self, scenario, project, label, variant=""):
        return len(self.done.get((scenario, project, label, variant), []))

    def add(self, r):
        self.done.setdefault(self.key(r), []).append(r)
        with open(self.jsonl, "a") as f:
            f.write(json.dumps(r) + "\n")

    def log(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        self.logf.write(line + "\n")
        self.logf.flush()


# ---------------------------------------------------------------- one measured process

def _subreaper():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER: orphaned descendants (tsc-rs's worker) come to us


class _PerfCounter:
    """User-space instructions of a process and its descendants (perf_event_open, excl_kernel, inherit,
    enable_on_exec; works at perf_event_paranoid=2), as scripts/perf-rearch/icount.py on perf/first-principles.
    Load-independent; with --singleThreaded (and GOMAXPROCS=1 for Go) the CPU-work measure."""

    def __init__(self, pid):
        import ctypes
        import struct
        self.struct = struct
        libc = ctypes.CDLL(None, use_errno=True)
        attr = bytearray(128)
        struct.pack_into("IIQ", attr, 0, 0, 128, 1)  # PERF_TYPE_HARDWARE, size, PERF_COUNT_HW_INSTRUCTIONS
        # disabled | inherit | exclude_kernel | exclude_hv | enable_on_exec
        struct.pack_into("Q", attr, 40, (1 << 0) | (1 << 1) | (1 << 5) | (1 << 6) | (1 << 12))
        self.fd = libc.syscall(298, (ctypes.c_char * 128).from_buffer(attr), pid, -1, -1, 0)

    def read(self):
        if self.fd < 0:
            return None
        v = self.struct.unpack("Q", os.read(self.fd, 8))[0]
        os.close(self.fd)
        return v


def run_measured(argv, cwd, timeout, env_extra=None):
    """Runs argv; returns wall (until the started process exits), user/sys CPU and max RSS of it and every
    descendant (wait4; this process is a child subreaper, so a worker that outlives its launcher is reaped
    here too), user-space instructions (perf counter, all threads and descendants), the exit code and a hash
    of stdout+stderr.

    tsc-rs's CLI starts a worker by default and its launcher exits as soon as the worker sends its exit code
    (before the worker's memory is unmapped), which is what a user waits for. PR_LAUNCH=0 sets
    GOPORT_LAUNCH=0 (no worker) instead. `drain` is the time until every descendant has exited."""
    import threading
    env = dict(os.environ)
    if os.environ.get("PR_LAUNCH") == "0":
        env["GOPORT_LAUNCH"] = "0"
    else:
        env.pop("GOPORT_LAUNCH", None)
    if env_extra:
        env.update(env_extra)
    exe = shutil.which(argv[0], path=env.get("PATH")) or argv[0]
    out_r, out_w = os.pipe()
    go_r, go_w = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: wait until the counter is attached, then exec
        try:
            os.close(out_r)
            os.close(go_w)
            os.read(go_r, 1)
            os.close(go_r)
            os.dup2(out_w, 1)
            os.dup2(out_w, 2)
            os.close(out_w)
            fd = os.open("/dev/null", os.O_RDONLY)
            os.dup2(fd, 0)
            os.chdir(cwd)
            os.execve(exe, argv, env)
        finally:
            os._exit(127)
    os.close(out_w)
    os.close(go_r)
    counter = _PerfCounter(pid)
    chunks = []

    def drain_pipe():
        while True:
            c = os.read(out_r, 1 << 16)
            if not c:
                break
            chunks.append(c)
    reader = threading.Thread(target=drain_pipe)
    reader.start()
    busy0 = machine_busy_s()
    t0 = time.perf_counter()
    os.write(go_w, b"x")
    os.close(go_w)
    killed = []
    killer = threading.Timer(timeout, lambda: (killed.append(1), os.kill(pid, 9)))
    killer.start()
    _, status, ru = os.wait4(pid, 0)
    wall = time.perf_counter() - t0
    killer.cancel()
    cpu_u, cpu_s, rss = ru.ru_utime, ru.ru_stime, ru.ru_maxrss
    while True:  # descendants that outlived the process
        try:
            _, _, r2 = os.wait4(-1, 0)
        except ChildProcessError:
            break
        cpu_u += r2.ru_utime
        cpu_s += r2.ru_stime
        rss = max(rss, r2.ru_maxrss)
    reader.join()
    os.close(out_r)
    drain = time.perf_counter() - t0
    busy1 = machine_busy_s()
    instr = counter.read()
    out = b"".join(chunks)
    rc = os.waitstatus_to_exitcode(status)
    cpu = cpu_u + cpu_s
    return {
        "wall": wall, "drain": drain, "user": cpu_u, "sys": cpu_s, "cpu": cpu, "instr": instr,
        "maxrss_mb": rss / 1024, "rc": rc, "timed_out": bool(killed),
        # The build scenario runs each label in its own scratch copy (build-<project>-<label>), and paths in
        # diagnostics name it: hash the output with the run's own directory masked, so equal output compares
        # equal across labels.
        "out_hash": hashlib.sha256(out.replace(str(cwd).encode(), b"<cwd>")).hexdigest()[:16],
        "out_lines": out.count(b"\n"), "other_cpu": max(0.0, (busy1 - busy0) - cpu),
        "launch": env.get("GOPORT_LAUNCH", "default"), "env": env_extra or {}, "_out": out,
    }


def gated_run(rec, desc, fn, ungated=False):
    """Load gate + repeat rule around fn() -> result dict with wall and cpu."""
    for attempt in range(3):
        before = load1() if ungated else wait_quiet(rec.log)
        r = fn()
        after = load1()
        wall, cpu = r.get("wall", 0), r.get("cpu", 0)
        decay = math.exp(-wall / 60.0)
        self_load = (cpu / wall if wall else 0) * (1 - decay)
        limit = REPEAT_LOAD + self_load
        other = r.get("other_cpu", 0) / wall if wall else 0
        r.update(uptime_s=round(uptime_s()), load_before=before, load_after=after, load_limit=round(limit, 2), attempt=attempt,
                 other_cores=round(other, 2))
        instr = f" instr {r['instr'] / 1e9:.3f}G" if r.get("instr") else ""
        rec.log(f"  {desc}: wall {wall:.3f}s cpu {cpu:.2f}s rss {r.get('maxrss_mb', 0) or 0:.0f}MB rc {r.get('rc')}"
                f"{instr} load {before}->{after} (limit {limit:.1f}) other cores {other:.2f}")
        if ungated or (after <= limit and other <= OTHER_CORES):
            return r
        rec.log("  load or other processes' CPU above the limit: repeating")
    r["load_flag"] = True
    return r


# ---------------------------------------------------------------- CLI scenarios

def show_config(go, proj):
    """Resolved compilerOptions via the Go oracle's --showConfig (reads only)."""
    p = subprocess.run([go, "--showConfig", "-p", proj["cfg"]], cwd=proj["repo"], capture_output=True, text=True)
    try:
        return json.loads(p.stdout).get("compilerOptions", {})
    except json.JSONDecodeError:
        return {}


def input_state(repo):
    p = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                       capture_output=True, text=True)
    return p.stdout


def scratch_copy(src, dst):
    """Copy the repo's tracked and untracked (not ignored) files to dst; link every node_modules dir."""
    if Path(dst).exists():
        shutil.rmtree(dst)
    Path(dst).mkdir(parents=True)
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=src,
                           capture_output=True, check=True).stdout
    # The inputs are read-only (brief, 2026-11-10); the copy must not be: runs write into it (emit next to sources
    # outside the common root, -b outputs, incr-edit's edit) and the next scratch_copy deletes it.
    subprocess.run(["rsync", "-a", "--chmod=u+w", "--files-from=-", "--from0", f"{src}/", f"{dst}/"], input=files,
                   check=True)
    # The top-level node_modules becomes a link to the original. A package's own node_modules (pnpm: links,
    # relative, into the top-level store and to the workspace's packages) is copied as it is, links kept, so
    # workspace packages resolve inside the copy and their sources stay under the copy's root.
    for nm in subprocess.run(["find", src, "-name", "node_modules", "-prune", "-type", "d"],
                             capture_output=True, text=True, check=True).stdout.split():
        rel = os.path.relpath(nm, src)
        link = Path(dst) / rel
        if link.exists() or link.is_symlink():
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        if rel == "node_modules":
            link.symlink_to(nm)
        else:
            subprocess.run(["cp", "-a", nm, str(link)], check=True)
            subprocess.run(["chmod", "-R", "u+w", str(link)], check=True)  # links are not followed


def clean_copy(repo, dst):
    """scratch_copy once (with a manifest of its files), then delete any file a run added."""
    dst = Path(dst)
    manifest = dst.parent / (dst.name + ".manifest")
    if not manifest.exists():
        scratch_copy(repo, str(dst))
        manifest.write_text("\n".join(sorted(walk_files(dst))))
        return
    keep = set(manifest.read_text().splitlines())
    for f in walk_files(dst):
        if f not in keep:
            (dst / f).unlink()


def walk_files(root):
    """Files under root, not following the node_modules links."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not os.path.islink(os.path.join(d, x)) and x != "node_modules"]
        out += [os.path.relpath(os.path.join(d, f), root) for f in files]
    return out


def cli(args):
    bins = parse_bins(args.bin)
    ref = args.ref
    projects = read_projects(Path(args.projects_root))
    names = args.projects.split(",") if args.projects else list(projects)
    scenarios = args.scenarios.split(",")
    for s in scenarios:
        if s not in CLI_SCENARIOS:
            raise SystemExit(f"unknown scenario {s}")
    out = Path(args.out or HOME / f".cache/perf-rearch/runs/{time.strftime('%Y%m%d-%H%M')}-cli")
    rec = Recorder(out)
    scratch = out / "scratch"
    if "synth85" in names:  # the C1 fixture, not a shared input: generated in the scratch dir
        write_synth85(scratch / "synth85-src", "x: number", "1")
        projects["synth85"] = {"repo": str(scratch / "synth85-src"), "cfg": "tsconfig.json"}
    go = dict(bins).get(ref, str(GO_DEFAULT))
    meta = {"bins": bins, "ref": ref, "runs": args.runs, "max_load": MAX_LOAD, "repeat_load": REPEAT_LOAD,
            "host": os.uname().nodename, "kernel": os.uname().release, "nproc": os.cpu_count(),
            "projects": {n: {k: str(v) for k, v in projects[n].items()} for n in names}}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    rec.log(f"cli: bins {bins} projects {names} scenarios {scenarios} runs {args.runs} -> {out}")
    for name in names:
        proj = projects[name]
        repo = Path(proj["repo"]).resolve()
        if not (repo / proj["cfg"]).exists():
            rec.log(f"{name}: missing {repo / proj['cfg']} (run setup-projects.sh); skipped")
            continue
        state0 = input_state(repo)
        opts = show_config(go, {**proj, "repo": repo})
        base = ["-p", proj["cfg"], "--pretty", "false"]
        for scen in scenarios:
            variants = []  # (variant name, labels, argv builder, pre-run hook, cwd)
            # --composite false: composite needs incremental build info, which would go into the input. A
            # composite config keeps its declaration setting (composite implies it; a declarationMap without
            # it is error TS5069, and the check would stop at the config).
            comp = ["--composite", "false"] + (["--declaration", "true"] if opts.get("composite") else [])
            noemit = base + ["--noEmit", "--incremental", "false"] + comp
            if scen == "noemit":
                variants.append(("noemit", [l for l, _ in bins], lambda b, lab: [b] + noemit, None, repo))
            elif scen == "single":
                labs = [l for l, _ in bins if l != ref or args.single_ref]
                variants.append(("single", labs, lambda b, lab: [b] + noemit + ["--singleThreaded"], None, repo))
            elif scen in ("emit", "emit-js", "emit-cjs", "icount-emit", "icount-emit-js", "icount-emit-cjs"):
                icount = scen.startswith("icount-")
                scen_emit = scen[len("icount-"):] if icount else scen
                # emit: the config's own outputs (JS, maps, d.ts as configured). emit-js: JS only (no maps,
                # no d.ts). emit-cjs: JS only, CommonJS ES2017 (the emit audit's three modes).
                extra = [] if scen_emit == "emit" else ["--sourceMap", "false", "--declaration", "false",
                                                   "--declarationMap", "false", "--emitDeclarationOnly", "false"]
                if scen_emit == "emit-cjs":
                    extra += ["--module", "commonjs", "--target", "es2017"]
                if icount:
                    extra += ["--singleThreaded"]

                src = scratch / f"src-{name}"

                def emit_argv(b, lab, scen=scen, extra=extra, src=src):
                    d = scratch / f"{scen}-{name}-{lab}"
                    # --rootDir: the copy's root. TypeScript 7 stops at TS5011 when outDir is set and the common
                    # source dir is not the config's dir (zod, ts-pattern, T3 Code), and at TS6059 when a
                    # configured rootDir does not hold every file (T3 server): both would skip the check.
                    a = [b] + base + ["--noEmit", "false", "--incremental", "false"] + comp + [
                                      "--outDir", str(d / "out"), "--rootDir", str(src)]
                    if scen_emit == "emit" and (opts.get("declaration") or opts.get("composite")
                                           or opts.get("declarationDir")):
                        a += ["--declarationDir", str(d / "decl")]
                    if opts.get("outFile"):
                        a += ["--outFile", str(d / "out.js")]
                    return a + extra

                # Emit runs in a scratch copy of the repo: a config whose files lie outside its common root
                # (T3 Code's web app includes ../../scripts/lib, TS5011) makes both compilers write JS and d.ts
                # next to those sources, outside --outDir. The copy loses those extra files before every run.

                def emit_pre(lab, scen=scen, src=src):
                    d = scratch / f"{scen}-{name}-{lab}"
                    shutil.rmtree(d, ignore_errors=True)
                    d.mkdir(parents=True)
                    clean_copy(str(repo), src)
                if scen_emit == "emit-cjs" and str(opts.get("moduleResolution", "")).lower() in ("node16", "nodenext"):
                    # --module commonjs with moduleResolution node16/nodenext is TS5110: the run would stop at
                    # the config.
                    rec.log(f"{name} {scen}: skipped (moduleResolution {opts.get('moduleResolution')}, TS5110)")
                    continue
                variants.append((scen, [l for l, _ in bins], emit_argv, emit_pre, src))
            elif scen == "icount":
                # Single-threaded user-space instructions: tsc-rs --singleThreaded, Go also GOMAXPROCS=1.
                variants.append(("icount", [l for l, _ in bins], lambda b, lab: [b] + noemit + ["--singleThreaded"],
                                 None, repo))
            elif scen == "incr":
                def incr_argv(b, lab):
                    return [b] + base + ["--noEmit", "--incremental"] + comp + [
                                         "--tsBuildInfoFile", str(scratch / f"incr-{name}-{lab}.tsbuildinfo")]
                variants.append(("incr-warm", [l for l, _ in bins], incr_argv, "incr", repo))
            elif scen == "incr-edit":
                INCR_COMP[name] = comp
                variants.append(("incr-edit", [l for l, _ in bins], None, "incr-edit", repo))
            elif scen == "build":
                variants.append(("build-cold", [l for l, _ in bins], None, "build-cold", repo))
                variants.append(("build-warm", [l for l, _ in bins], None, "build-warm", repo))
            for variant, labs, argv_fn, pre, cwd in variants:
                run_cell(rec, name, variant, [(l, b) for l, b in bins if l in labs], argv_fn, pre, cwd,
                         args, scratch, proj, repo)
                if input_state(repo) != state0:
                    rec.log(f"STOP: {name} input tree changed during {variant}")
                    raise SystemExit(1)
    summarize_dir(out, ref)


def label_env(args, lab, variant):
    env = {}
    for spec in args.env or []:
        l, _, kv = spec.partition(":")
        k, _, v = kv.partition("=")
        if l == lab:
            env[k] = v
    if variant.startswith("icount") and lab == args.ref:
        env["GOMAXPROCS"] = "1"
    return env



INCR_EDITS = {  # project -> (edited file, text appended), as cleanup/walk_cost.sh
    "query": ("packages/query-core/src/utils.ts", "\nexport const w2WalkProbe = 1;\n"),
    "umami": ("src/lib/types.ts", "\nexport const w2WalkProbe = 1;\n"),
    "hono": ("src/types.ts", "\nexport const w2WalkProbe = 1;\n"),
}


def write_synth85(root, member, x):
    """The 85-file project of tests/multi_program.rs (cleanup/walk_cost.sh `synth`); a.ts holds the edited member."""
    root = Path(root)
    (root / "src").mkdir(parents=True, exist_ok=True)

    def w(p, t):
        (root / p).write_text(t)
    w("tsconfig.json", '{"compilerOptions":{"incremental":true,"noEmit":true,"strict":true,"skipLibCheck":true,'
                       '"types":[]},"include":["src"]}')
    for i in range(40):
        w(f"src/b{i}.ts", f'import {{ make }} from "./a";\nexport const v{i} = make();\n')
        w(f"src/c{i}.ts", f'import {{ v{i} }} from "./b{i}";\nexport const n{i}: number = v{i}.x;\n')
    w("src/u.ts", 'export const u: number = "u";\n')
    w("src/g.ts", "export const g = 1;\ndeclare global {\n    var gv: number;\n}\n")
    w("src/ug.ts", "export const ug: number = gv;\n")
    w("src/d.d.ts", 'import { v0 } from "./b0";\nexport declare const d: typeof v0;\n')
    w("src/a.ts", f"export interface Shape {{ {member} }}\nexport const make = (): Shape => ({{ x: {x} }});\n")


_ie_orig = {}
INCR_COMP = {}  # project -> the --composite/--declaration flags of the incr scenario (set by cli)


def incr_edit_prep(name, lab, b, scratch, repo, cfg, args):
    """Untimed setup of one incr-edit run: the label's own copy restored, a cold incremental build, then the one
    edit. Returns (argv, cwd) of the timed rebuild. The copy and the build info sit under scratch/<label>/, so the
    paths inside the build info are the same for every label."""
    d = scratch / lab / f"ie-{name}"
    info = scratch / lab / f"ie-{name}.tsbuildinfo"
    if name == "synth85":
        edited = d / "src/a.ts"
        d.mkdir(parents=True, exist_ok=True)
        write_synth85(d, "x: number", "1")
        edit = lambda: write_synth85(d, "x: string", '"1"')
    else:
        rel, extra = INCR_EDITS[name]
        edited = d / rel
        if not d.exists():
            scratch_copy(str(repo), str(d))
            _ie_orig[(name, lab)] = edited.read_text()
        edited.write_text(_ie_orig[(name, lab)])
        edit = lambda: edited.write_text(_ie_orig[(name, lab)] + extra)
    comp = INCR_COMP.get(name, ["--composite", "false"])
    argv = [b, "-p", cfg, "--pretty", "false", "--noEmit", "--incremental"] + comp + ["--tsBuildInfoFile", str(info)]
    info.parent.mkdir(parents=True, exist_ok=True)
    info.unlink(missing_ok=True)
    subprocess.run(argv, cwd=d, capture_output=True, timeout=args.timeout)  # cold build (untimed)
    edit()
    return argv, d


def run_cell(rec, name, variant, bins, argv_fn, pre, cwd, args, scratch, proj, repo):
    runs = args.icount_runs if variant.startswith("icount") else args.runs
    need = {lab: runs - rec.count(variant, name, lab) for lab, _ in bins}
    if all(n <= 0 for n in need.values()):
        return
    rec.log(f"{name} {variant}: {', '.join(l for l, _ in bins)}")

    def prep(lab, b):
        """Returns (argv, cwd) for one run of lab, after the untimed setup of the scenario."""
        if pre == "incr":
            return argv_fn(b, lab), cwd
        if pre == "incr-edit":
            return incr_edit_prep(name, lab, b, scratch, repo, proj["cfg"], args)
        if pre in ("build-cold", "build-warm"):
            copy = scratch / f"build-{name}-{lab}"
            if pre == "build-cold" or not copy.exists():
                scratch_copy(str(repo), str(copy))
            return [b, "-b", proj["cfg"], "--pretty", "false"], copy
        if pre:
            pre(lab)
        return argv_fn(b, lab), cwd

    # Warm-up (and, for incr/build-warm, the cold build that the warm runs reuse); not recorded.
    first = {}
    for lab, b in bins:
        a, c = prep(lab, b)
        if pre in ("build-warm",):
            subprocess.run(a, cwd=c, capture_output=True,
                           timeout=args.timeout)
        if pre == "incr":
            Path(a[a.index("--tsBuildInfoFile") + 1]).unlink(missing_ok=True)
        if variant.startswith("icount"):
            break  # deterministic: no warm-up
        r = run_measured(a, c, args.timeout, label_env(args, lab, variant))
        first[lab] = r
        rec.log(f"  warm-up {lab}: wall {r['wall']:.3f}s rc {r['rc']} lines {r['out_lines']}")
    n = max(need.values())
    for i in range(n):
        order = bins[i % len(bins):] + bins[:i % len(bins)]
        for lab, b in order:
            if rec.count(variant, name, lab) >= runs:
                continue
            a, c = prep(lab, b)
            env = label_env(args, lab, variant)
            r = gated_run(rec, f"{variant} {name} {lab} run{i + 1}",
                          lambda: run_measured(a, c, args.timeout, env), ungated=variant.startswith("icount"))
            if variant.startswith("emit") or variant.startswith("icount-emit"):
                r["emit_hash"] = tree_hash(scratch / f"{variant}-{name}-{lab}")
            if variant == "incr-edit":
                ib = scratch / lab / f"ie-{name}.tsbuildinfo"
                r["emit_hash"] = hashlib.sha256(ib.read_bytes()).hexdigest()[:16] if ib.exists() else None
            out = r.pop("_out")
            if i == 0:
                (rec.out / "outputs").mkdir(exist_ok=True)
                (rec.out / "outputs" / f"{name}.{variant}.{lab}.txt").write_bytes(out[:2_000_000])
            rec.add({"scenario": variant, "project": name, "label": lab, "bin": b, "argv": a[1:],
                     "cwd": str(c), "run": i + 1, "t": time.time(), **r})


# ---------------------------------------------------------------- LSP scenarios

LSPDIFF = REPO / "scripts/lspdiff"
DEFAULT_EDITS = ["src/app/(main)/boards/BoardCloneForm.tsx", "src/components/hooks/queries/useFunnelQuery.ts"]


def lsp(args):
    bins = parse_bins(args.bin, lsp=True)
    ref = args.ref
    project = Path(args.project or DEFAULT_PROJECTS / "umami").resolve()
    out = Path(args.out or HOME / f".cache/perf-rearch/runs/{time.strftime('%Y%m%d-%H%M')}-lsp")
    rec = Recorder(out)
    scenarios = args.scenarios.split(",")
    meta = {"bins": bins, "ref": ref, "runs": args.runs, "project": str(project), "max_load": MAX_LOAD}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    pname = project.name
    for scen in scenarios:
        if scen == "ttfd":
            cells = [("ttfd", None)]
        elif scen == "probe":
            cells = [(f"probe-{Path(f).suffix[1:]}", f) for f in args.edit_files.split(",")]
        elif scen == "concurrent":
            cells = [(f"concurrent-{i}", i) for i in args.intervals.split(",")]
        elif scen == "open12":
            cells = [("open12", None)]
        else:
            raise SystemExit(f"unknown scenario {scen}")
        for variant, param in cells:
            for i in range(args.runs):
                order = bins[i % len(bins):] + bins[:i % len(bins)]
                for lab, b in order:
                    if rec.count(variant, pname, lab) > i:
                        continue
                    d = out / variant / f"{lab}-run{i + 1}"
                    d.mkdir(parents=True, exist_ok=True)
                    r = gated_run(rec, f"{variant} {lab} run{i + 1}",
                                  lambda: lsp_one(variant, param, b, lab, project, d, ref))
                    rec.add({"scenario": variant, "project": pname, "label": lab, "bin": b, "run": i + 1,
                             "t": time.time(), **r})
    summarize_dir(out, ref)


def node_run(argv, cwd=REPO, timeout=1800):
    t0 = time.perf_counter()
    p = subprocess.run(["node", *argv], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p, time.perf_counter() - t0


def lsp_one(variant, param, b, lab, project, d, ref):
    if variant == "ttfd":
        p, wall = node_run([str(HERE / "lsp-ttfd.mjs"), b, str(project)])
        (d / "stdout.txt").write_text(p.stdout + p.stderr)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        return {"wall": r["ttfd_ms"] / 1000, "cpu": r.get("cpu_ms", 0) / 1000, "maxrss_mb": r.get("maxrss_mb"),
                "metrics": r, "rc": p.returncode}
    if variant == "open12":
        p, wall = node_run([str(HERE / "window2/open12.mjs"), b, str(project)])
        (d / "stdout.txt").write_text(p.stdout + p.stderr)
        r = json.loads(p.stdout.strip().splitlines()[-1])
        # The cell's "wall" is the first request after the 12 didOpens.
        return {"wall": r["first_ms"] / 1000, "cpu": r.get("cpu_ms", 0) / 1000, "maxrss_mb": r.get("maxrss_mb"),
                "metrics": r, "rc": p.returncode}
    if variant.startswith("probe-"):
        logs = d / "server.log"
        p, wall = node_run([str(LSPDIFF / "probe-edit.mjs"), b, str(project), "--edits", "20", "--edit", param,
                            "--cpu", "--logs", str(logs)])
        (d / "stdout.txt").write_text(p.stdout + p.stderr)
        m = re.search(r"first request after an edit ([\d.]+) ms, second ([\d.-]+) ms, edited file's diagnostics "
                      r"([\d.-]+) ms", p.stdout)
        c = re.search(r"cpu per edit: total ([\d.]+) ms", p.stdout)
        upd = []
        if logs.exists():
            for line in logs.read_text().splitlines():
                u = re.search(r"Program update for .* completed in ([\d.]+)(ms|µs|us|s)\b", line)
                if u:
                    v = float(u.group(1))
                    upd.append(v / 1000 if u.group(2) in ("µs", "us") else v * 1000 if u.group(2) == "s" else v)
        first = float(m.group(1)) if m else None
        metrics = {"first_ms": first, "second_ms": tofloat(m.group(2)) if m else None,
                   "diag_ms": tofloat(m.group(3)) if m else None,
                   "cpu_per_edit_ms": float(c.group(1)) if c else None,
                   "program_update_ms": median(upd), "program_updates": len(upd)}
        # The cell's "wall" is the first request after an edit (it pays the snapshot update).
        return {"wall": (first or 0) / 1000, "cpu": (metrics["cpu_per_edit_ms"] or 0) / 1000, "metrics": metrics,
                "rc": p.returncode}
    if variant.startswith("concurrent-"):
        only = "go" if lab == ref else "rs"
        flag = "--go" if only == "go" else "--rs"
        p, wall = node_run([str(LSPDIFF / "lspdiff.mjs"), "bench", "--project", str(project), "--files", "6",
                            "--runs", "1", "--interval", param, "--pulls", "all", "--modes", "concurrent",
                            "--only", only, flag, b, "--out", str(d)])
        (d / "stdout.txt").write_text(p.stdout + p.stderr)
        samples = next(d.rglob("samples.json"), None)
        metrics = {}
        if samples:
            rows = json.loads(samples.read_text())
            by = {}
            for row in rows:
                for s in row.get("samples", []):
                    by.setdefault(s["kind"], []).append(s)
            for kind, ss in by.items():
                ms = sorted(s["ms"] for s in ss if not s.get("cancelled"))
                metrics[kind] = {"n": len(ms), "cancelled": sum(1 for s in ss if s.get("cancelled")),
                                 "p50": quantile(ms, 0.5), "p95": quantile(ms, 0.95)}
        # Cell "wall": completion p95 (what a typist waits for).
        p95 = metrics.get("completion", {}).get("p95")
        return {"wall": (p95 or 0) / 1000, "cpu": 0, "metrics": metrics, "rc": p.returncode, "session_s": wall}
    raise SystemExit(variant)


def tofloat(s):
    try:
        return float(s)
    except ValueError:
        return None


def quantile(xs, q):
    """scripts/lspdiff/bench.mjs's quantile (xs sorted)."""
    if not xs:
        return None
    return xs[max(0, min(len(xs) - 1, math.ceil(q * len(xs)) - 1))]


# ---------------------------------------------------------------- tables

def summarize_dir(out, ref):
    rows = [json.loads(l) for l in (Path(out) / "runs.jsonl").read_text().splitlines()] \
        if (Path(out) / "runs.jsonl").exists() else []
    cells = build_cells(rows, ref)
    (Path(out) / "results.json").write_text(json.dumps(cells, indent=1))
    md = markdown(cells, ref)
    (Path(out) / "results.md").write_text(md)
    print(md)


def build_cells(rows, ref):
    groups = {}
    for r in rows:
        groups.setdefault((r["scenario"], r["project"]), {}).setdefault(r["label"], []).append(r)
    cells = []
    for (scen, proj), labs in sorted(groups.items()):
        cell = {"scenario": scen, "project": proj, "sides": {}}
        for lab, rs in labs.items():
            walls = [r["wall"] for r in rs]
            side = {"n": len(rs), "wall": median(walls), "wall_min": min(walls), "wall_max": max(walls),
                    "cpu": median([r.get("cpu", 0) for r in rs]),
                    "instr": median([r["instr"] for r in rs if r.get("instr")]) if any(r.get("instr") for r in rs)
                    else None,
                    "instr_spread": (max(r["instr"] for r in rs if r.get("instr")) /
                                     min(r["instr"] for r in rs if r.get("instr")) - 1)
                    if any(r.get("instr") for r in rs) else None,
                    "maxrss_mb": median([r["maxrss_mb"] for r in rs if r.get("maxrss_mb") is not None]),
                    "rc": sorted({r.get("rc") for r in rs}), "out_hash": sorted({str(r.get("out_hash")) for r in rs}),
                    "emit_hash": sorted({str(r.get("emit_hash")) for r in rs if "emit_hash" in r}),
                    "load_flag": any(r.get("load_flag") for r in rs),
                    "load_before": [r.get("load_before") for r in rs], "load_after": [r.get("load_after") for r in rs]}
            if any("metrics" in r for r in rs):
                keys = {}
                for r in rs:
                    flat(r.get("metrics", {}), "", keys)
                side["metrics"] = {k: median([v for v in vs if v is not None]) if any(
                    v is not None for v in vs) else None for k, vs in keys.items()}
            cell["sides"][lab] = side
        if ref in cell["sides"]:
            # icount cells compare instructions; every other cell compares wall time.
            key = "instr" if scen.startswith("icount") else "wall"
            cell["metric"] = key
            rw = cell["sides"][ref][key]
            for lab, side in cell["sides"].items():
                if lab == ref:
                    continue
                side["ratio"] = side[key] / rw if rw and side[key] is not None else None
                side["slower"] = bool(rw) and side[key] is not None and side[key] > rw
                side["same_output"] = (side["out_hash"] == cell["sides"][ref]["out_hash"]
                                       and side["rc"] == cell["sides"][ref]["rc"]
                                       and side["emit_hash"] == cell["sides"][ref]["emit_hash"])
        cells.append(cell)
    return cells


def flat(m, prefix, out):
    for k, v in m.items():
        if isinstance(v, dict):
            flat(v, f"{prefix}{k}.", out)
        elif isinstance(v, (int, float)) or v is None:
            out.setdefault(prefix + k, []).append(v)


def fmt_s(v):
    if v is None:
        return "-"
    return f"{v * 1000:.0f} ms" if v < 1 else f"{v:.2f} s"


def markdown(cells, ref):
    lines = [f"| scenario | project | side | wall median (min..max) | CPU | max RSS | instructions | vs {ref} | output |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        for lab, s in sorted(c["sides"].items(), key=lambda kv: kv[0] != ref):
            ratio = s.get("ratio")
            mark = "" if ratio is None else (f"**{ratio:.2f}× slower**" if s.get("slower") else f"{ratio:.2f}×")
            same = "" if lab == ref or "same_output" not in s or s["out_hash"] == ["None"] else (
                "same" if s["same_output"] else "DIFFERS")
            flag = " (load)" if s["load_flag"] else ""
            rss = f"{s['maxrss_mb']:.0f} MB" if s.get("maxrss_mb") else "-"
            lines.append(f"| {c['scenario']} | {c['project']} | {lab} | {fmt_s(s['wall'])} "
                         f"({fmt_s(s['wall_min'])}..{fmt_s(s['wall_max'])}){flag} | {fmt_s(s['cpu'])} | {rss} | "
                         f"{(str(round(s['instr'] / 1e9, 2)) + 'G') if s.get('instr') else '-'} | "
                         f"{mark} | {same} |")
    return "\n".join(lines) + "\n"


def table(args):
    rows = []
    for d in args.dirs:
        f = Path(d) / "runs.jsonl"
        if not f.exists():
            print(f"skipped (no runs.jsonl): {d}", file=sys.stderr)
            continue
        rows += [json.loads(l) for l in f.read_text().splitlines()]
    cells = build_cells(rows, args.ref)
    md = markdown(cells, args.ref)
    if args.out:
        Path(args.out).write_text(md)
        Path(args.out).with_suffix(".json").write_text(json.dumps(cells, indent=1))
    print(md)


# ---------------------------------------------------------------- main

def main():
    _subreaper()
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
        print(USAGE)
        return
    ap = argparse.ArgumentParser(prog="bench.sh", add_help=False)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("cli", "lsp"):
        p = sub.add_parser(name, add_help=False)
        p.add_argument("--bin", action="append")
        p.add_argument("--out")
        p.add_argument("--runs", type=int, default=3)
        p.add_argument("--ref", default="go")
        p.add_argument("--timeout", type=int, default=900)
        if name == "cli":
            p.add_argument("--projects")
            p.add_argument("--projects-root", default=str(DEFAULT_PROJECTS))
            p.add_argument("--scenarios", default="noemit,emit,single,incr")
            p.add_argument("--single-ref", action="store_true")
            p.add_argument("--env", action="append", help="LABEL:VAR=VALUE, an environment variable for one label")
            p.add_argument("--icount-runs", type=int, default=1)
        else:
            p.add_argument("--project")
            p.add_argument("--scenarios", default="ttfd,probe,concurrent")
            p.add_argument("--intervals", default="120,250,500")
            p.add_argument("--edit-files", default=",".join(DEFAULT_EDITS))
    t = sub.add_parser("table", add_help=False)
    t.add_argument("dirs", nargs="+")
    t.add_argument("--ref", default="go")
    t.add_argument("--out")
    args = ap.parse_args()
    {"cli": cli, "lsp": lsp, "table": table}[args.cmd](args)


if __name__ == "__main__":
    main()
