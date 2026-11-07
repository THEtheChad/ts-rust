//! Loader diagnostics for the perf work (perf/first-principles, stage S0;
//! cargo feature `diagnostics`, never in a shipped build: the module and
//! every call site are compiled only with it):
//!
//! - `GOPORT_LOAD_TIMES=1` (load_times.rs): one line per program load on
//!   stderr with the loader's phase times.
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

/// The file of `GOPORT_LOAD_DUMP`, if set.
#[cfg(feature = "diagnostics")]
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
pub use crate::frontend::vfs::cachedvfs::sorted_lines;

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
