//! Go `cmd/tsc/lsp.go`.

use crate::cmd::tsgo::prelude::*;

#[cfg(not(unix))]
use crate::cmd::tsgo::isprocessalive_other::{PROCESS_ALIVE_SUPPORTED, is_process_alive};
#[cfg(unix)]
use crate::cmd::tsgo::isprocessalive_unix::{PROCESS_ALIVE_SUPPORTED, is_process_alive};
use crate::cmd::tsgo::main::{ErrorHandling, must_getwd, new_flag_set, notify_context};
use crate::execute::tsc::compile::{Writer, spawn_process};
use crate::execute::tsc::stdio;
use crate::frontend::bundled;
use crate::frontend::tspath;
use crate::frontend::vfs::osvfs;
use crate::gostd::context::{self, CancelFunc};
use crate::gostd::errors;
use std::io::Write;
use std::sync::Arc;
use std::time::Duration;

// Go: cmd/tsc/lsp.go:21 runLSP (ts#64836)
pub fn run_lsp(args: &[String]) -> i32 {
    let mut flag = new_flag_set("lsp", ErrorHandling::ContinueOnError);
    let stdio = flag.bool("stdio", false, "use stdio for communication");
    let pprof_dir = flag.string(
        "pprofDir",
        "",
        "Generate pprof CPU/memory profiles to the given directory.",
    );
    let pipe = flag.string("pipe", "", "use named pipe for communication");
    let _ = pipe;
    let socket = flag.string("socket", "", "use socket for communication");
    let _ = socket;
    let client_process_id = flag.int(
        "clientProcessId",
        0,
        "use the given PID for the parent process watchdog",
    );
    if flag.parse(args).is_err() {
        return 2;
    }

    if !stdio.get() {
        let _ = writeln!(stdio::Stderr, "only stdio is supported");
        return 1;
    }

    // Go: profileSession := pprof.BeginProfiling(*pprofDir, os.Stderr); defer profileSession.Stop()
    // PORT: the session stops when it drops at the return of `run_lsp`,
    // after `stop`, as the Go defers run (last in, first out).
    let _profile_session = if pprof_dir.borrow().is_empty() {
        None
    } else {
        let _ = writeln!(
            stdio::Stderr,
            "pprof profiles will be written to: {}",
            pprof_dir.borrow()
        );
        let stderr: Writer = Rc::new(RefCell::new(stdio::Stderr));
        Some(crate::pprof::begin_profiling(&pprof_dir.borrow(), stderr))
    };

    // Loader v2 L1: the program loads read the OS through a per-load view
    // under the overlays and the snapshot caches (`vfs::loadview`).
    // PORT: Go passes `osvfs.FS()` (cmd/tsc/lsp.go); the view answers what
    // it answers.
    let fs = bundled::wrap_fs_exported(crate::frontend::vfs::loadview::os_view_fs());
    let default_library_path = bundled::lib_path_exported();
    // ts#64836: a rooted directory, or empty (ATA off) without a user
    // cache directory. Go passes it as is (cmd/tsc/lsp.go:60).
    let typings_location = get_global_typings_cache_location();
    // ts#64159: Go roots and normalizes the current directory
    // (cmd/tsc/lsp.go:48 RootedDirectoryPathFromAbsolute).
    let cwd = tspath::get_normalized_absolute_path(&must_getwd(), "");

    // Go: ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    let (ctx, stop) = notify_context(&context::background());

    let s = crate::lsp::new_server(crate::lsp::ServerOptions {
        // Go: os.Stdin, os.Stdout and os.Stderr (see `execute::tsc::stdio`).
        in_: crate::lsp::to_reader(Box::new(std::io::BufReader::new(stdio::Stdin))),
        out: crate::lsp::to_writer(Box::new(stdio::Stdout)),
        err: Box::new(stdio::Stderr),
        cwd,
        fs,
        default_library_path,
        typings_location,
        parse_cache: None,
        npm_install: Some(Arc::new(npm_install)),
        // Go: Spawn: spawnProcess (tsgo#4712; Go cmd/tsc/sys.go spawnProcess is
        // ported in `execute::tsc::compile`).
        spawn: Some(Rc::new(spawn_process)),
        progress_delay: Duration::from_millis(250),
        set_parent_process_id: new_parent_process_watchdog(&ctx, &stop, client_process_id.get()),
    });

    let result = s.run(&ctx);
    // Go: defer stop()
    stop();
    if let Err(err) = result {
        let _ = writeln!(stdio::Stderr, "{}", err.error());
        return 1;
    }
    0
}

// Go: cmd/tsc/lsp.go:81 newParentProcessWatchdog
// newParentProcessWatchdog returns a SetParentProcessID callback if the platform
// supports process-alive checking and no client process ID override was provided,
// or nil otherwise.
// Go `clientProcessID` and the pids are Go `int` (64 bits, see
// `FlagValue`); `is_process_alive` truncates to the 32-bit `pid_t` only at
// `kill`, as Go's syscall does.
pub fn new_parent_process_watchdog(
    ctx: &Context,
    stop: &CancelFunc,
    client_process_id: i64,
) -> Option<Box<dyn Fn(i64) + Send + Sync>> {
    if !PROCESS_ALIVE_SUPPORTED {
        return None;
    }
    if client_process_id > 0 {
        start_parent_process_watchdog(ctx, stop, client_process_id);
        return None;
    }
    let ctx = ctx.clone();
    let stop = stop.clone();
    Some(Box::new(move |parent_pid: i64| {
        start_parent_process_watchdog(&ctx, &stop, parent_pid);
    }))
}

// Go: cmd/tsc/lsp.go:97 startParentProcessWatchdog
// startParentProcessWatchdog starts a goroutine that monitors the parent process
// and cancels the context if the parent dies. This prevents orphaned language
// server processes when the editor crashes or is killed.
pub fn start_parent_process_watchdog(ctx: &Context, stop: &CancelFunc, parent_pid: i64) {
    if parent_pid <= 0 {
        return;
    }
    let ctx = ctx.clone();
    let stop = stop.clone();
    crate::core::GoThread::new()
        .name("lsp-watchdog".to_string())
        .spawn(move || {
            // Go: ticker := time.NewTicker(5 * time.Second); defer ticker.Stop()
            // PORT: the `select` on `ctx.Done()` and `ticker.C` is a 5 s wait
            // on `ctx.Done()`.
            let done = ctx.done();
            loop {
                let closed = match &done {
                    Some(done) => done.wait_timeout(Duration::from_secs(5)),
                    None => {
                        std::thread::sleep(Duration::from_secs(5));
                        false
                    }
                };
                if closed {
                    return;
                }
                if !is_process_alive(parent_pid) {
                    let _ = writeln!(
                        stdio::Stderr,
                        "Parent process {parent_pid} has exited, shutting down."
                    );
                    stop();
                    return;
                }
            }
        });
}

// Go: vfs/osvfs/os.go:231 GetGlobalTypingsCacheLocation (ts#64836)
// The rooted typings cache directory under the user cache directory, or ""
// when there is no user cache directory or it is not absolute.
// PORT: ported here; `vfs/osvfs` is an accepted compiler file. Go returns a
// `tspath.RootedDirectoryPath`; the port keeps string paths.
pub fn get_global_typings_cache_location() -> String {
    let Ok(cache_dir) = user_cache_dir() else {
        return String::new();
    };
    let Some(cache_path) = tspath::try_rooted_path_from_absolute(&cache_dir) else {
        return String::new();
    };

    let subdir = if cfg!(windows) {
        "Microsoft/TypeScript"
    } else {
        "typescript"
    };
    // Go: RootedDirectoryPathFromPath(cachePath).ResolveDirectory(subdir).ResolveDirectory(core.VersionMajorMinor())
    // PORT: `cache_path` is normalized and the names are plain, so the
    // combined path is normalized as Go's.
    tspath::combine_paths(&cache_path, &[subdir, crate::core::version_major_minor()])
}

// Go: os/file.go:504 UserCacheDir
// PORT: the Windows, darwin and Unix branches (not plan9).
fn user_cache_dir() -> Result<String, GoError> {
    let getenv = |key: &str| std::env::var(key).unwrap_or_default();
    let mut dir;

    if cfg!(windows) {
        dir = getenv("LocalAppData");
        if dir.is_empty() {
            return Err(errors::new("%LocalAppData% is not defined"));
        }
    } else if cfg!(any(target_os = "macos", target_os = "ios")) {
        dir = getenv("HOME");
        if dir.is_empty() {
            return Err(errors::new("$HOME is not defined"));
        }
        dir.push_str("/Library/Caches");
    } else {
        // Unix
        dir = getenv("XDG_CACHE_HOME");
        if dir.is_empty() {
            dir = getenv("HOME");
            if dir.is_empty() {
                return Err(errors::new("neither $XDG_CACHE_HOME nor $HOME are defined"));
            }
            dir.push_str("/.cache");
        } else if !dir.starts_with('/') {
            // Go: !filepathlite.IsAbs(dir)
            return Err(errors::new("path in $XDG_CACHE_HOME is relative"));
        }
    }

    Ok(dir)
}

// Go: cmd/tsc/lsp.go:61 the NpmInstall func of runLSP (ts#64544)
//     cmd := exec.CommandContext(ctx, "npm", args...)
//     cmd.Dir = cwd
//     return cmd.Output()
// Also the NpmInstall of lsp TestReplay (replay_test.go:62).
pub fn npm_install(ctx: &Context, cwd: &str, args: &[String]) -> (Vec<u8>, Option<GoError>) {
    command_context_output(ctx, "npm", cwd, args)
}

/// How often `command_context_output` looks for the end of the command
/// while it waits for ctx.
const COMMAND_WAIT_STEP: Duration = Duration::from_millis(20);

// Go `exec.CommandContext(ctx, name, args...)` with `cmd.Dir = cwd`, then
// `cmd.Output()`.
// PORT: Go error texts are approximated (ATA only logs them). `Output`
// reads stdin from the null device and keeps stdout and stderr (stderr only
// for the `ExitError`, which no caller reads, so it is read and dropped).
// `rlimit::spawn` gives the command the open-file limit as Go does. Go kills
// the command when ctx is done (`watchCtx`; `Cmd.Cancel` is `Process.Kill`).
// std cannot kill a child while another thread waits for it, so this thread
// looks for the end of the command every `COMMAND_WAIT_STEP` while it waits
// for ctx, and helper threads read the pipes.
fn command_context_output(
    ctx: &Context,
    name: &str,
    cwd: &str,
    args: &[String],
) -> (Vec<u8>, Option<GoError>) {
    use std::io::Read;
    use std::process::{Command, Stdio};

    // Go `Start`: a done ctx gives its error, and the command does not start.
    if let Some(err) = ctx.err() {
        return (Vec::new(), Some(err));
    }
    let mut cmd = Command::new(name);
    cmd.args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = match crate::gostd::rlimit::spawn(&mut cmd) {
        Ok(child) => child,
        Err(err) => return (Vec::new(), Some(errors::new(err.to_string()))),
    };
    let stdout = child.stdout.take().map(|mut pipe| {
        crate::core::GoThread::new()
            .name("npm-stdout".to_string())
            .spawn(move || {
                let mut output = Vec::new();
                let _ = pipe.read_to_end(&mut output);
                output
            })
    });
    let stderr = child.stderr.take().map(|mut pipe| {
        crate::core::GoThread::new()
            .name("npm-stderr".to_string())
            .spawn(move || {
                let _ = std::io::copy(&mut pipe, &mut std::io::sink());
            })
    });

    // Go `watchCtx`: a done ctx kills the command. `canceled` is a Cancel
    // that succeeded; then a clean exit gives `ctx.Err()`, as in Go.
    let mut canceled = false;
    let waited = match ctx.done() {
        None => child.wait(),
        Some(done) => loop {
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Ok(None) => {}
                Err(err) => break Err(err),
            }
            if done.wait_timeout(COMMAND_WAIT_STEP) {
                canceled = child.kill().is_ok();
                break child.wait();
            }
        },
    };
    // Go `Wait` waits for the pipe copies after the process.
    let output = stdout
        .and_then(|reader| reader.join().ok())
        .unwrap_or_default();
    if let Some(reader) = stderr {
        let _ = reader.join();
    }
    match waited {
        Ok(status) if status.success() => (output, if canceled { ctx.err() } else { None }),
        Ok(status) => {
            let message = match status.code() {
                Some(code) => format!("exit status {code}"),
                None => status.to_string(),
            };
            (output, Some(errors::new(message)))
        }
        Err(err) => (output, Some(errors::new(err.to_string()))),
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::time::Instant;

    // ts#64544 (cmd/tsc/lsp.go:61): `exec.CommandContext` kills the command
    // when ctx is done, so Session.Close does not wait for a running npm.
    #[test]
    fn command_context_output_kills_the_command_when_ctx_is_done() {
        let (ctx, cancel) = context::with_cancel(&context::background());
        let canceler = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(100));
            cancel();
        });
        let start = Instant::now();
        let (_, err) = command_context_output(&ctx, "sleep", "/", &["30".to_string()]);
        let elapsed = start.elapsed();
        canceler.join().expect("canceler");
        assert!(err.is_some(), "a killed command is an error");
        assert!(
            elapsed < Duration::from_secs(10),
            "the command ran {elapsed:?} after ctx was done"
        );
    }

    // Go `Cmd.Start` returns ctx.Err() for a done ctx and starts nothing;
    // with a live ctx `Output` gives stdout.
    #[test]
    fn command_context_output_with_a_done_ctx_does_not_start() {
        let (ctx, cancel) = context::with_cancel(&context::background());
        let args = ["-c".to_string(), "echo out; echo err >&2".to_string()];
        let (output, err) = command_context_output(&ctx, "sh", "/", &args);
        assert_eq!(
            (output.as_slice(), err.map(|e| e.error())),
            (&b"out\n"[..], None)
        );

        cancel();
        let (output, err) = command_context_output(&ctx, "sh", "/", &args);
        assert!(output.is_empty());
        assert_eq!(err.map(|e| e.error()), Some("context canceled".to_string()));
    }
}
