//! PORT: not in Go (perf). Emit phase timers and emit routing counts.
//!
//! `GOPORT_EMIT_PHASE_TIMES=1` turns them on in a `diagnostics` feature
//! build. Each emit phase (the JS
//! transforms, the declaration transforms, the print, the source map text
//! and the writes) adds its wall time in nanoseconds to an atomic counter
//! of its phase and of the kind of thread that ran it (the loading thread,
//! a checker, the emit pool, a d.ts twin). The routing counts say where
//! each file's JS part ran (the emit pool or its checker thread), and why,
//! and when the emit started (behind each checker's check or after the
//! whole check). `report` prints them to stderr at the end of the process.
//! Without the feature, the timers are zero-sized no-ops and none of the
//! state below is compiled (`off`); in a diagnostics build with the env
//! unset, each timer is one relaxed load.
//!
//! The print time is the printer's walk of the tree (`write_exported`),
//! which adds the source map mappings as it goes. The source map time is
//! the rest of the map work after the print: the raw map, the
//! `sourceMappingURL` comment and the map's JSON text.

#[cfg(feature = "diagnostics")]
pub use on::{FlushTimer, Timer, enabled, report, route, start, start_flush};

#[cfg(not(feature = "diagnostics"))]
pub use off::{FlushTimer, Timer, enabled, report, route, start, start_flush};

/// One emit phase.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    TransformJs = 0,
    TransformDts = 1,
    Print = 2,
    SourceMap = 3,
    Write = 4,
}

#[cfg(feature = "diagnostics")]
const PHASE_NAMES: [&str; 5] = [
    "transform-js",
    "transform-dts",
    "print",
    "source-map",
    "write",
];

/// Where a file's JS part runs, and why (`program_emit::file_route`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Route {
    /// The whole emit on the emit pool (no d.ts part).
    PoolWhole = 0,
    /// The JS part on the emit pool, the d.ts part on the checker thread.
    PoolSplit = 1,
    /// On the checker: import elision makes the checker the reference
    /// resolver (no `verbatimModuleSyntax`, not a JS file).
    CheckerImportElision = 2,
    /// On the checker: the JSX transform.
    CheckerJsx = 3,
    /// On the checker: `isolatedModules` is off (const enum inlining).
    CheckerNotIsolatedModules = 4,
    /// On the checker: `emitDecoratorMetadata`.
    CheckerDecoratorMetadata = 5,
    /// On the checker: the file may have an enum declaration.
    CheckerEnum = 6,
    /// On the checker: a forced emit (the API).
    CheckerForced = 7,
    /// On the checker: the file has no JS part (`emitDeclarationOnly`,
    /// `noEmit`, a content-mapped file, or a d.ts-only emit).
    CheckerNoJsPart = 8,
    /// On the checker: the emit pool is off (`--singleThreaded`, a trace,
    /// no emit threads, or the emit runs on a checker thread).
    CheckerPoolOff = 9,
    /// A file whose transforms run on a checker thread and print there.
    PrintOnChecker = 10,
    /// A file whose transforms run on a checker thread and print on its
    /// d.ts twin.
    PrintOnTwin = 11,
    /// A file whose emit was sent behind each checker's check (plain
    /// `tsc -p`, `program_emit::start_plain_emit`).
    StartPlainEarly = 12,
    /// A file whose emit was sent behind each checker's check (an
    /// incremental program, `start_emit_batch_with`).
    StartBatch = 13,
    /// A file whose emit was sent by `Program.Emit` (after the check).
    StartAfterCheck = 14,
}

#[cfg(feature = "diagnostics")]
mod on {
    use super::{PHASE_NAMES, Phase, Route};
    use std::cell::Cell;
    use std::sync::atomic::{AtomicU8, AtomicU64, Ordering};
    use std::time::Instant;

    /// The kind of thread that runs a phase, from the thread's name.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum ThreadKind {
        Loading = 0,
        Checker = 1,
        Pool = 2,
        Twin = 3,
        /// The threads that make the deferred writes of the plain early emit
        /// (`program_emit::flush_deferred_writes`).
        Flush = 4,
        Other = 5,
    }

    const KINDS: usize = 6;
    const KIND_NAMES: [&str; KINDS] = ["loading", "checker", "pool", "twin", "flush", "other"];

    const ROUTE_NAMES: [&str; 15] = [
        "js-on-pool (whole file)",
        "js-on-pool (split, d.ts on checker)",
        "js-on-checker: import elision",
        "js-on-checker: jsx",
        "js-on-checker: not isolatedModules",
        "js-on-checker: emitDecoratorMetadata",
        "js-on-checker: enum",
        "js-on-checker: forced emit",
        "on-checker: no js part",
        "on-checker: emit pool off",
        "checker part printed on checker",
        "checker part printed on twin",
        "emit sent with check (plain tsc -p)",
        "emit sent as a batch (incremental)",
        "emit sent after check",
    ];

    #[allow(clippy::declare_interior_mutable_const)]
    const ZERO: AtomicU64 = AtomicU64::new(0);
    #[allow(clippy::declare_interior_mutable_const)]
    const ZERO_ROW: [AtomicU64; 5] = [ZERO; 5];

    static NANOS: [[AtomicU64; 5]; KINDS] = [ZERO_ROW; KINDS];
    static CALLS: [[AtomicU64; 5]; KINDS] = [ZERO_ROW; KINDS];
    /// The wall time of the deferred writes on the loading thread
    /// (`start_flush`), and how many flushes ran.
    static FLUSH_NANOS: AtomicU64 = ZERO;
    static FLUSHES: AtomicU64 = ZERO;
    static ROUTES: [AtomicU64; 15] = [ZERO; 15];

    /// True when `GOPORT_EMIT_PHASE_TIMES` is `1`. Read once; then one
    /// relaxed load and one predictable branch per call.
    #[inline]
    pub fn enabled() -> bool {
        match STATE.load(Ordering::Relaxed) {
            OFF => false,
            ON => true,
            _ => init_enabled(),
        }
    }

    const OFF: u8 = 1;
    const ON: u8 = 2;
    static STATE: AtomicU8 = AtomicU8::new(0);

    #[cold]
    fn init_enabled() -> bool {
        let on = std::env::var("GOPORT_EMIT_PHASE_TIMES").as_deref() == Ok("1");
        STATE.store(if on { ON } else { OFF }, Ordering::Relaxed);
        on
    }

    fn thread_kind() -> ThreadKind {
        thread_local! {
            static KIND: Cell<Option<ThreadKind>> = const { Cell::new(None) };
        }
        KIND.with(|kind| {
            if let Some(value) = kind.get() {
                return value;
            }
            let thread = std::thread::current();
            let name = thread.name().unwrap_or("");
            let value = if name.starts_with("checker-") {
                ThreadKind::Checker
            } else if name.starts_with("emit-") {
                ThreadKind::Pool
            } else if name.starts_with("dts-twin-") {
                ThreadKind::Twin
            } else if name.starts_with("flush-") {
                ThreadKind::Flush
            } else if name == "tsgo" || name == "main" || name == "goport" {
                ThreadKind::Loading
            } else {
                ThreadKind::Other
            };
            kind.set(Some(value));
            value
        })
    }

    /// A running phase timer (`start`). It adds its time when it drops.
    #[must_use]
    pub struct Timer(Option<(Phase, Instant)>);

    /// Starts a timer of `phase` on this thread, when the timers are on.
    pub fn start(phase: Phase) -> Timer {
        if !enabled() {
            return Timer(None);
        }
        Timer(Some((phase, Instant::now())))
    }

    impl Drop for Timer {
        fn drop(&mut self) {
            if let Some((phase, start)) = self.0.take() {
                let nanos = u64::try_from(start.elapsed().as_nanos()).unwrap_or(u64::MAX);
                let kind = thread_kind() as usize;
                NANOS[kind][phase as usize].fetch_add(nanos, Ordering::Relaxed);
                CALLS[kind][phase as usize].fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// A running timer of the deferred writes of a plain early emit, on the
    /// loading thread (`program_emit::flush_deferred_writes`): the wall time
    /// that the writes add after the check. It adds its time when it drops.
    #[must_use]
    pub struct FlushTimer(Option<Instant>);

    /// Starts a `FlushTimer`, when the timers are on.
    pub fn start_flush() -> FlushTimer {
        FlushTimer(enabled().then(Instant::now))
    }

    impl Drop for FlushTimer {
        fn drop(&mut self) {
            if let Some(start) = self.0.take() {
                let nanos = u64::try_from(start.elapsed().as_nanos()).unwrap_or(u64::MAX);
                FLUSH_NANOS.fetch_add(nanos, Ordering::Relaxed);
                FLUSHES.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// Adds `count` to the routing count `route`, when the timers are on.
    pub fn route(route: Route, count: usize) {
        if enabled() && count > 0 {
            ROUTES[route as usize].fetch_add(count as u64, Ordering::Relaxed);
        }
    }

    /// Prints the timers and routing counts to stderr, when they are on. The
    /// tsc command calls it once, at the end of the run.
    pub fn report() {
        if !enabled() {
            return;
        }
        use std::fmt::Write as _;
        let mut text = String::from("emit phase times (GOPORT_EMIT_PHASE_TIMES)\n");
        let _ = writeln!(
            text,
            "  {:<8} {:>14} {:>14} {:>14} {:>14} {:>14}",
            "thread",
            PHASE_NAMES[0],
            PHASE_NAMES[1],
            PHASE_NAMES[2],
            PHASE_NAMES[3],
            PHASE_NAMES[4]
        );
        let mut totals = [0u64; 5];
        for (kind, name) in KIND_NAMES.iter().enumerate() {
            let row: Vec<u64> = (0..5)
                .map(|phase| NANOS[kind][phase].load(Ordering::Relaxed))
                .collect();
            if row.iter().all(|&nanos| nanos == 0) {
                continue;
            }
            let _ = write!(text, "  {name:<8}");
            for (phase, nanos) in row.iter().enumerate() {
                totals[phase] += nanos;
                let calls = CALLS[kind][phase].load(Ordering::Relaxed);
                let _ = write!(text, " {:>8.2}ms/{:<4}", *nanos as f64 / 1e6, calls);
            }
            text.push('\n');
        }
        let _ = write!(text, "  {:<8}", "total");
        for nanos in totals {
            let _ = write!(text, " {:>8.2}ms     ", nanos as f64 / 1e6);
        }
        text.push('\n');
        let flushes = FLUSHES.load(Ordering::Relaxed);
        if flushes > 0 {
            let _ = writeln!(
                text,
                "  deferred writes after the check: {:.2}ms wall ({flushes} flush)",
                FLUSH_NANOS.load(Ordering::Relaxed) as f64 / 1e6
            );
        }
        text.push_str("emit routing (files)\n");
        for (index, name) in ROUTE_NAMES.iter().enumerate() {
            let count = ROUTES[index].load(Ordering::Relaxed);
            if count > 0 {
                let _ = writeln!(text, "  {name:<40} {count}");
            }
        }
        use std::io::Write as _;
        let _ = std::io::stderr().write_all(text.as_bytes());
    }
}

/// The build without `diagnostics`: zero-sized timers, no state.
#[cfg(not(feature = "diagnostics"))]
mod off {
    use super::{Phase, Route};

    #[inline(always)]
    pub fn enabled() -> bool {
        false
    }

    #[must_use]
    pub struct Timer;

    #[inline(always)]
    pub fn start(_phase: Phase) -> Timer {
        Timer
    }

    #[must_use]
    pub struct FlushTimer;

    #[inline(always)]
    pub fn start_flush() -> FlushTimer {
        FlushTimer
    }

    #[inline(always)]
    pub fn route(_route: Route, _count: usize) {}

    #[inline(always)]
    pub fn report() {}
}
