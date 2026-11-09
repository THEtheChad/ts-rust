//! The tuning knobs of the shipped binaries: every `GOPORT_*` environment
//! variable that a build without the `diagnostics` and `test-hooks`
//! features reads at run time. Not a Go port.
//!
//! A knob changes how a run uses the machine (threads, processes, huge
//! pages), never its output: the same input gives the same stdout, stderr,
//! exit code, emit, build info and LSP answers at any setting. A value that
//! does not parse counts as unset. The list, with defaults, is in
//! `docs/perf-rearch/quality.md` ("Environment variables").
//!
//! The product options are elsewhere: `TSC_RS_EFFECT` (`effect::switch_on`)
//! and `TSGO_EFFECT_API` (`effect::rulerunner::set_api_process`) turn on
//! output that Go does not give. Diagnostic and verification variables are
//! read only in a `diagnostics` feature build, test hooks only in a
//! `test-hooks` one.

use std::sync::OnceLock;

/// The thread counts that the environment sets, read once per process.
struct Threads {
    bind: Option<usize>,
    emit: Option<usize>,
    parse: Option<usize>,
    mapped: Option<usize>,
    resolve_ahead: Option<usize>,
}

fn threads() -> &'static Threads {
    static THREADS: OnceLock<Threads> = OnceLock::new();
    THREADS.get_or_init(|| {
        let count = |name: &str| std::env::var(name).ok()?.parse().ok();
        Threads {
            bind: count("GOPORT_BIND_THREADS"),
            emit: count("GOPORT_EMIT_THREADS"),
            parse: count("GOPORT_PARSE_THREADS"),
            mapped: count("GOPORT_MAPPED_THREADS"),
            resolve_ahead: count("GOPORT_RESOLVE_AHEAD_THREADS"),
        }
    })
}

/// `GOPORT_BIND_THREADS`: the bind threads of every load (below 2 binds
/// serially). Default: the thread budget (`program::bind_thread_count`).
#[must_use]
pub fn bind_threads() -> Option<usize> {
    threads().bind
}

/// `GOPORT_EMIT_THREADS`: the emit pool's threads at any core count, up to
/// its maximum; 0 runs every emit on the checker threads. Default: the
/// cores that the checkers leave (`program::emit_thread_count`).
#[must_use]
pub fn emit_threads() -> Option<usize> {
    threads().emit
}

/// `GOPORT_PARSE_THREADS`: the parse workers next to the loading thread; 0
/// parses on the loading thread only. Default: the thread budget
/// (`files_parser::prefetch_worker_count`), more for a large program.
#[must_use]
pub fn parse_threads() -> Option<usize> {
    threads().parse
}

/// `GOPORT_MAPPED_THREADS`: the workers of content-mapped parse jobs.
/// Default: as many as the parse workers (`files_parser::mapped_worker_count`).
#[must_use]
pub fn mapped_threads() -> Option<usize> {
    threads().mapped
}

/// `GOPORT_RESOLVE_AHEAD_THREADS`: the resolve-ahead workers. Default: the
/// parse threads of a program that is not large, less the loading thread
/// (`resolve_ahead::worker_count`).
#[must_use]
pub fn resolve_ahead_threads() -> Option<usize> {
    threads().resolve_ahead
}

/// `GOPORT_LAUNCH`: `0` never runs the work in a worker process, `1`
/// always does (bin/tsgo.rs `launch`). Default (None): a worker when the
/// run gets 4 KiB pages. Set `0` to measure the compiler's own CPU and RSS
/// through `wait4`, or where a process cannot exec another (qemu user
/// mode).
#[must_use]
pub fn launch() -> Option<bool> {
    match std::env::var_os("GOPORT_LAUNCH") {
        Some(value) if value == "0" => Some(false),
        Some(value) if value == "1" => Some(true),
        _ => None,
    }
}

/// `GOPORT_THP_GUARD=0`: the THP guard does nothing, so transparent huge
/// pages stay on (`thp_guard::thp_guard`). Default: the guard runs. It
/// allocates only when the variable is set, so the guard can read it before
/// the first heap allocation.
#[must_use]
pub fn thp_guard_off() -> bool {
    std::env::var_os("GOPORT_THP_GUARD").is_some_and(|value| value == "0")
}

/// `GOPORT_THP_GUARD_MIB`: the THP guard's limit, in MiB free in 2 MiB
/// blocks. Default (None): `thp_guard::DEFAULT_MIN_FREE_MIB`. It allocates
/// only when the variable is set.
#[must_use]
pub fn thp_guard_min_free_mib() -> Option<u64> {
    std::env::var_os("GOPORT_THP_GUARD_MIB")?
        .to_str()?
        .parse()
        .ok()
}
