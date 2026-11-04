//! Loader diagnostics for the perf work (perf/first-principles, stage S0):
//!
//! - `GOPORT_LOAD_TIMES=1`: one line per program load on stderr with the
//!   loader's phase times (`LoadTimes`).
//! - `GOPORT_LOAD_DUMP=<file>`: a canonical text dump of the observable
//!   effects of each program load, appended to `<file>`. Two loads with the
//!   same effects give the same text. Sections:
//!   - `load`: the queued runs of the serial loader in their order
//!     (`arrive`), the files in the order the loader loaded them (`load`),
//!     each path's task data (lowest depth, started subtasks, package id),
//!     each task (file name, loaded, started subtasks, lib, metadata, module
//!     and type reference resolutions, diagnostics counts, subtask names),
//!     the resolver's package.json cache entries, the parse-cache acquires in
//!     their order;
//!   - `host` (language server loads): the seen files with their names and
//!     the missing directories of the load's compiler host;
//!   - `snapshot` (language server snapshot builds): the snapshot's lookup
//!     cache (`file_exists`, `directory_exists`, `realpath`, entries), its
//!     cached files (name, hash, reload state, realpath path), its cached
//!     directories (children in their order) and its node_modules realpath
//!     aliases.
//!
//! Both are off unless the variable is set, and neither changes any output
//! of the program: the dump goes to its own file, the times to stderr.
// PORT: not in Go (perf diagnostics).

use crate::frontend::prelude::*;
use std::fmt::Write as _;
use std::sync::OnceLock;
use std::time::Instant;

/// The file of `GOPORT_LOAD_DUMP`, if set.
fn dump_file() -> Option<&'static str> {
    static FILE: OnceLock<Option<String>> = OnceLock::new();
    FILE.get_or_init(|| {
        std::env::var("GOPORT_LOAD_DUMP")
            .ok()
            .filter(|file| !file.is_empty())
    })
    .as_deref()
}

/// True when `GOPORT_LOAD_DUMP` names a file.
#[must_use]
pub fn dump_enabled() -> bool {
    dump_file().is_some()
}

/// True when `GOPORT_LOAD_TIMES` is set (not `0`).
#[must_use]
pub fn times_enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| {
        std::env::var("GOPORT_LOAD_TIMES").is_ok_and(|value| !value.is_empty() && value != "0")
    })
}

thread_local! {
    /// The `load` section of the load that runs on this thread.
    static LOAD: RefCell<Option<String>> = const { RefCell::new(None) };
    /// The load section of the last ended load on this thread, for the
    /// language server's hook (`take_last_load`).
    static LAST: RefCell<Option<String>> = const { RefCell::new(None) };
}

/// Starts the dump of a load on this thread (`process_all_program_files`).
pub fn begin_load() {
    if dump_enabled() {
        LOAD.with(|load| *load.borrow_mut() = Some(String::new()));
    }
}

/// Appends a line to the dump of the load that runs on this thread.
pub fn note(line: impl FnOnce(&mut String)) {
    LOAD.with(|load| {
        if let Some(text) = load.borrow_mut().as_mut() {
            line(text);
            text.push('\n');
        }
    });
}

/// True while a load on this thread dumps.
#[must_use]
pub fn loading() -> bool {
    LOAD.with(|load| load.borrow().is_some())
}

/// Ends the dump of the load on this thread with `tail` (the processed
/// state) and keeps it for the host (`take_last_load`). A CLI load (no
/// host takes it) writes it at once.
pub fn end_load(tail: impl FnOnce(&mut String), host_takes: bool) {
    let Some(mut text) = LOAD.with(|load| load.borrow_mut().take()) else {
        return;
    };
    tail(&mut text);
    if host_takes {
        LAST.with(|last| *last.borrow_mut() = Some(text));
    } else {
        write_section("load", &text);
    }
}

/// The load section of the last load on this thread, once.
#[must_use]
pub fn take_last_load() -> Option<String> {
    LAST.with(|last| last.borrow_mut().take())
}

/// Appends `== <kind> ==`, `text` and a blank line to the dump file.
pub fn write_section(kind: &str, text: &str) {
    use std::io::Write;
    let Some(file) = dump_file() else {
        return;
    };
    let mut out = String::with_capacity(text.len() + 32);
    let _ = writeln!(out, "== {kind} ==");
    out.push_str(text);
    out.push('\n');
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(file)
    {
        let _ = f.write_all(out.as_bytes());
    }
}

/// Appends `label key value` lines for `entries`, sorted by key.
pub fn sorted_lines<K: AsRef<str>, V: std::fmt::Debug>(
    out: &mut String,
    label: &str,
    entries: impl IntoIterator<Item = (K, V)>,
) {
    let mut lines: Vec<String> = entries
        .into_iter()
        .map(|(key, value)| format!("{label} {} {value:?}", key.as_ref()))
        .collect();
    lines.sort_unstable();
    for line in lines {
        out.push_str(&line);
        out.push('\n');
    }
}

/// The canonical text of a diagnostic: span, code, category, source,
/// message, arguments, flags, and the chain and related information in
/// full (the file is the dump's file; its node is left out).
#[must_use]
pub fn describe_diagnostic(diagnostic: &crate::core::Diagnostic) -> String {
    let nested = |list: &[crate::core::Diagnostic]| {
        list.iter()
            .map(describe_diagnostic)
            .collect::<Vec<_>>()
            .join(", ")
    };
    format!(
        "{{{}..{} TS{} {:?} source={:?} message={:?} text={:?} args={:?} unnecessary={} deprecated={} skipped_on_no_emit={} chain=[{}] related=[{}]}}",
        diagnostic.pos,
        diagnostic.end,
        diagnostic.code,
        diagnostic.category,
        diagnostic.source,
        diagnostic.message.key(),
        diagnostic.message_text,
        diagnostic.message_args,
        diagnostic.reports_unnecessary,
        diagnostic.reports_deprecated,
        diagnostic.skipped_on_no_emit,
        nested(&diagnostic.message_chain),
        nested(&diagnostic.related_information),
    )
}

/// The canonical text of a resolved module.
#[must_use]
pub fn describe_module(module: &crate::program::ResolvedModule) -> String {
    format!(
        "file={:?} path={:?} original={:?} ext={:?} ts_ext={} extra_ext={} pkg={:?} external={} custom={} alternate={:?} diags=[{}]",
        module.resolved_file_name,
        module.resolved_path.as_str(),
        module.original_path,
        module.extension,
        module.resolved_using_ts_extension,
        module.resolved_using_extra_extensions,
        module.package_id.string(),
        module.is_external_library_import,
        module.is_custom_resolution,
        module.alternate_result,
        module
            .resolution_diagnostics
            .iter()
            .map(describe_diagnostic)
            .collect::<Vec<_>>()
            .join(", "),
    )
}

/// The CPU of this thread so far (`getrusage(RUSAGE_THREAD)`): user and
/// system time in ms, and minor faults. Zero off Linux.
#[derive(Clone, Copy, Debug, Default)]
pub struct ThreadUsage {
    pub utime_ms: f64,
    pub stime_ms: f64,
    pub minflt: i64,
}

impl ThreadUsage {
    #[must_use]
    pub fn now() -> Self {
        #[cfg(target_os = "linux")]
        {
            use nix::sys::resource::{UsageWho, getrusage};
            if let Ok(usage) = getrusage(UsageWho::RUSAGE_THREAD) {
                let ms = |tv: nix::sys::time::TimeVal| {
                    tv.tv_sec() as f64 * 1e3 + tv.tv_usec() as f64 / 1e3
                };
                return ThreadUsage {
                    utime_ms: ms(usage.user_time()),
                    stime_ms: ms(usage.system_time()),
                    minflt: usage.minor_page_faults(),
                };
            }
        }
        ThreadUsage::default()
    }

    fn since(self, earlier: ThreadUsage) -> ThreadUsage {
        ThreadUsage {
            utime_ms: self.utime_ms - earlier.utime_ms,
            stime_ms: self.stime_ms - earlier.stime_ms,
            minflt: self.minflt - earlier.minflt,
        }
    }
}

/// `CLOCK_MONOTONIC` in ms (in-process timestamps that a tool can line up
/// with other processes on the machine). Zero off Linux.
#[must_use]
pub fn monotonic_ms() -> f64 {
    #[cfg(target_os = "linux")]
    {
        if let Ok(ts) = nix::time::clock_gettime(nix::time::ClockId::CLOCK_MONOTONIC) {
            return ts.tv_sec() as f64 * 1e3 + ts.tv_nsec() as f64 / 1e6;
        }
    }
    0.0
}

/// One phase of a load (`GOPORT_LOAD_TIMES`, loader v2 L0): its start and
/// end (`monotonic_ms`), and the thread's CPU and minor faults in it.
#[derive(Clone, Copy, Debug)]
struct PhaseRecord {
    name: &'static str,
    t0: f64,
    t1: f64,
    usage: ThreadUsage,
}

impl PhaseRecord {
    fn describe(&self) -> String {
        format!(
            "{}=[t0={:.3} t1={:.3} wall={:.3} utime={:.3} stime={:.3} minflt={}]",
            self.name,
            self.t0,
            self.t1,
            self.t1 - self.t0,
            self.usage.utime_ms,
            self.usage.stime_ms,
            self.usage.minflt
        )
    }
}

/// A phase outside `process_all_program_files` (publish, bind), which
/// writes its own `goport load-phase:` line when it ends.
pub struct PhaseTimer {
    name: &'static str,
    t0: f64,
    usage: ThreadUsage,
}

impl PhaseTimer {
    /// Starts phase `name`, or `None` when the times are off.
    #[must_use]
    pub fn start(name: &'static str) -> Option<Self> {
        times_enabled().then(|| PhaseTimer {
            name,
            t0: monotonic_ms(),
            usage: ThreadUsage::now(),
        })
    }
}

impl Drop for PhaseTimer {
    fn drop(&mut self) {
        use std::io::Write;
        let record = PhaseRecord {
            name: self.name,
            t0: self.t0,
            t1: monotonic_ms(),
            usage: ThreadUsage::now().since(self.usage),
        };
        let line = format!("goport load-phase: {}\n", record.describe());
        let _ = std::io::stderr().write_all(line.as_bytes());
    }
}

/// The loader's phase times of one load (`GOPORT_LOAD_TIMES`).
pub struct LoadTimes {
    start: Instant,
    last: Instant,
    phases: Vec<(&'static str, f64)>,
    /// Loader v2 L0: the timestamps and thread CPU of each phase.
    t_last: f64,
    usage_last: ThreadUsage,
    records: Vec<PhaseRecord>,
}

impl LoadTimes {
    /// Starts the timer of a load, or `None` when the times are off.
    #[must_use]
    pub fn start() -> Option<Self> {
        times_enabled().then(|| {
            let now = Instant::now();
            LoadTimes {
                start: now,
                last: now,
                phases: Vec::new(),
                t_last: monotonic_ms(),
                usage_last: ThreadUsage::now(),
                records: Vec::new(),
            }
        })
    }

    /// Ends the phase `name` now.
    pub fn mark(&mut self, name: &'static str) {
        let now = Instant::now();
        self.phases
            .push((name, now.duration_since(self.last).as_secs_f64() * 1e3));
        self.last = now;
        let t = monotonic_ms();
        let usage = ThreadUsage::now();
        // The loader's `parse` phase is its run of the task queue.
        let name = if name == "parse" { "run_queue" } else { name };
        self.records.push(PhaseRecord {
            name,
            t0: self.t_last,
            t1: t,
            usage: usage.since(self.usage_last),
        });
        self.t_last = t;
        self.usage_last = usage;
    }

    /// Writes the line of the load, with `extra` after the phases.
    pub fn report(self, files: usize, extra: &str) {
        use std::io::Write;
        let mut line = format!(
            "goport load-times: files={files} total={:.2}ms",
            self.start.elapsed().as_secs_f64() * 1e3
        );
        for (name, ms) in &self.phases {
            let _ = write!(line, " {name}={ms:.2}ms");
        }
        if !extra.is_empty() {
            line.push(' ');
            line.push_str(extra);
        }
        line.push('\n');
        // Loader v2 L0: the timestamps and thread CPU of each phase.
        line.push_str("goport load-phases:");
        for record in &self.records {
            line.push(' ');
            line.push_str(&record.describe());
        }
        line.push('\n');
        let _ = std::io::stderr().write_all(line.as_bytes());
    }
}
