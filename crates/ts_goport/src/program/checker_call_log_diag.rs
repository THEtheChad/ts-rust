//! The checker call log (`GOPORT_CHECKER_CALL_LOG`): a `diagnostics`
//! feature build only. Without the feature none of it is compiled, and its
//! call sites are cfg'd out (program.rs, emitter/program_emit.rs,
//! bin/tsgo.rs).
//!
//! PORT: not in Go (perf, debug). `GOPORT_CHECKER_CALL_LOG=<file>` logs the
//! sequence of work on each checker: one line per checker job, and lines
//! for the checks, global diagnostics reads and emits inside the jobs. Each
//! line has the checker's type, symbol and instantiation counts and the
//! number of checker lends (`with_checker_at`, which every emit resolver
//! call takes) on its thread so far. Two runs that make the same checker
//! calls in the same order on each checker give the same file; the early
//! emit of plain `tsc -p` (`program_emit::start_plain_emit`) must not change
//! it. `write_checker_call_log` writes it, sorted by checker, at the end of
//! the run. With the log off, each site is one relaxed load and one
//! predictable branch.

use super::{Checker, Node, WORKER_CHECKER, source_file_file_name, worker_index};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Mutex, OnceLock, PoisonError};

/// 0 before `GOPORT_CHECKER_CALL_LOG` is read, 1 off, 2 on.
static CHECKER_CALL_LOG_STATE: AtomicU8 = AtomicU8::new(0);

/// The lines of the call log: (checker index, line number on that checker,
/// text).
static CHECKER_CALL_LOG: Mutex<Vec<(usize, u64, String)>> = Mutex::new(Vec::new());

thread_local! {
    /// Checker lends on this thread (`with_checker_at`), for the call log.
    static CHECKER_CALL_LOG_LENDS: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
    /// Lines that this thread added to the call log.
    static CHECKER_CALL_LOG_SEQ: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
}

/// True when `GOPORT_CHECKER_CALL_LOG` names a file.
#[inline]
pub fn checker_call_log_enabled() -> bool {
    // One relaxed load and one predictable branch once the variable is read.
    match CHECKER_CALL_LOG_STATE.load(Ordering::Relaxed) {
        1 => false,
        2 => true,
        _ => {
            let on = checker_call_log_path().is_some();
            CHECKER_CALL_LOG_STATE.store(if on { 2 } else { 1 }, Ordering::Relaxed);
            on
        }
    }
}

fn checker_call_log_path() -> Option<&'static str> {
    static PATH: OnceLock<Option<String>> = OnceLock::new();
    PATH.get_or_init(|| {
        std::env::var("GOPORT_CHECKER_CALL_LOG")
            .ok()
            .filter(|path| !path.is_empty())
    })
    .as_deref()
}

/// Counts a checker lend on this thread (`with_checker_at`), when the log
/// is on.
#[inline]
pub fn checker_call_log_lend() {
    if checker_call_log_enabled() {
        CHECKER_CALL_LOG_LENDS.with(|lends| lends.set(lends.get() + 1));
    }
}

/// Adds `label()` with the counts of `checker` to the call log, on the
/// thread of checker `index`, when the log is on.
pub fn checker_call_log_event(index: usize, checker: &Checker, label: impl FnOnce() -> String) {
    if !checker_call_log_enabled() {
        return;
    }
    let lends = CHECKER_CALL_LOG_LENDS.with(std::cell::Cell::get);
    let seq = CHECKER_CALL_LOG_SEQ.with(|seq| {
        let value = seq.get();
        seq.set(value + 1);
        value
    });
    let line = format!(
        "{} types={} symbols={} instantiations={} lends={}",
        label(),
        checker.type_count,
        checker.symbol_count,
        checker.total_instantiation_count,
        lends
    );
    CHECKER_CALL_LOG
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .push((index, seq, line));
}

/// `checker_call_log_event` for the checker of this checker thread, outside
/// a lend of it. Does nothing off a checker thread or with the log off.
pub fn checker_call_log_event_here(label: impl FnOnce() -> String) {
    if !checker_call_log_enabled() {
        return;
    }
    let Some(index) = worker_index() else {
        return;
    };
    WORKER_CHECKER.with(|slot| {
        if let Ok(slot) = slot.try_borrow()
            && let Some(checker) = slot.as_ref()
        {
            checker_call_log_event(index, checker, label);
        }
    });
}

/// Adds the emit of `source_file` to the call log, after its checker part
/// (emitter/program_emit.rs).
pub fn checker_call_log_emit(source_file: Node) {
    checker_call_log_event_here(|| format!("emit {}", checker_call_log_file(source_file)));
}

/// The file name of `file` for the call log.
pub fn checker_call_log_file(file: Node) -> String {
    source_file_file_name(file).to_string()
}

/// Writes the call log to its file, sorted by checker, when it is on. The
/// tsc command calls it once, at the end of the run.
pub fn write_checker_call_log() {
    let Some(path) = checker_call_log_path() else {
        return;
    };
    let mut lines = std::mem::take(
        &mut *CHECKER_CALL_LOG
            .lock()
            .unwrap_or_else(PoisonError::into_inner),
    );
    lines.sort_by_key(|(index, seq, _)| (*index, *seq));
    let mut text = String::new();
    for (index, seq, line) in lines {
        use std::fmt::Write as _;
        let _ = writeln!(text, "c{index} {seq:06} {line}");
    }
    if let Err(err) = std::fs::write(path, text) {
        eprintln!("GOPORT_CHECKER_CALL_LOG: cannot write {path}: {err}");
    }
}
