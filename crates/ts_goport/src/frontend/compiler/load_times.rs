//! `GOPORT_LOAD_TIMES=1` (cargo feature `diagnostics`; never in a shipped
//! build): the loader's phase times of each program load on stderr
//! (`LoadTimes`, the S0 line and the loader v2 L0 phases: CLOCK_MONOTONIC
//! timestamps and the thread's `RUSAGE_THREAD` utime, stime and minor
//! faults), and the publish and bind phases (`PhaseTimer`).
// PORT: not in Go (perf diagnostics).

use std::fmt::Write as _;
use std::sync::OnceLock;
use std::time::Instant;

/// True when `GOPORT_LOAD_TIMES` is set (not `0`).
// The module is under the feature (its `mod` item); the `cfg` repeats it
// for `quality.sh`.
#[cfg(feature = "diagnostics")]
#[must_use]
pub fn times_enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| {
        std::env::var("GOPORT_LOAD_TIMES").is_ok_and(|value| !value.is_empty() && value != "0")
    })
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
