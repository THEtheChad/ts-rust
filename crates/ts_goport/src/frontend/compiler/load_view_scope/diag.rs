//! Diagnostics of the load view's scope (cargo feature `diagnostics`; never
//! in a shipped build):
//! - the `goport load-view:` line of each load (`GOPORT_LOAD_TIMES`): the
//!   view's counts and the live OS calls that bypassed it;
//! - the V-replay oracle: `GOPORT_LOAD_VIEW_DUMP=<file>` appends each load's
//!   view record; `GOPORT_LOAD_VIEW_REPLAY=<file>` makes each load of the
//!   process take the next recorded view (frozen: it stores nothing; a demand
//!   it lacks is a live OS call, counted as `replay_miss`). With the serial
//!   loader (`GOPORT_RESOLVE_AHEAD=0`) that is "the serial loader run against
//!   V";
//! - test hooks: the records and counts of this thread's loads, a replay
//!   set in process, and a hook after each load (a change after the load,
//!   before the watch registration).
// PORT: not in Go (perf diagnostics).
//
// The whole module is under the feature (its `mod` item); the items that read
// an environment variable repeat the `cfg` so that `quality.sh` sees it.

use crate::frontend::prelude::*;
use crate::frontend::vfs::loadview::{self, LoadView, diag::ViewCounts};
use std::cell::Cell;
use std::sync::{Arc, OnceLock};

thread_local! {
    /// The recorded views that the loads of this thread did not take yet.
    static REPLAY: RefCell<Option<std::collections::VecDeque<Arc<LoadView>>>> =
        const { RefCell::new(None) };
    /// The records of this thread's loads, when kept (`keep_records`).
    static RECORDS: RefCell<Option<Vec<u8>>> = const { RefCell::new(None) };
    /// The counts of the last load's view, and its bypassing OS calls.
    static LAST_COUNTS: Cell<Option<(ViewCounts, u64)>> = const { Cell::new(None) };
    static AFTER_LOAD: RefCell<Option<Rc<dyn Fn()>>> = const { RefCell::new(None) };
}

fn env_file(name: &'static str, cell: &'static OnceLock<Option<String>>) -> Option<&'static str> {
    cell.get_or_init(|| std::env::var(name).ok().filter(|file| !file.is_empty()))
        .as_deref()
}

static DUMP: OnceLock<Option<String>> = OnceLock::new();
static REPLAY_FILE: OnceLock<Option<String>> = OnceLock::new();

/// The next recorded view to replay: from `set_replay`, else from
/// `GOPORT_LOAD_VIEW_REPLAY`.
#[cfg(feature = "diagnostics")]
pub(super) fn next_replay() -> Option<Arc<LoadView>> {
    let set = REPLAY.with(|replay| replay.borrow().is_some());
    if !set {
        let file = env_file("GOPORT_LOAD_VIEW_REPLAY", &REPLAY_FILE)?;
        let bytes = std::fs::read(file)
            .unwrap_or_else(|err| panic!("GOPORT_LOAD_VIEW_REPLAY {file}: {err}"));
        set_replay(Some(&bytes));
    }
    let next = REPLAY.with(|replay| replay.borrow_mut().as_mut()?.pop_front());
    if next.is_none() {
        eprintln!("goport load-view: replay: no recorded view left; a new one");
    }
    next
}

/// The end of a load's scope: its counts line, record and test hooks.
#[cfg(feature = "diagnostics")]
pub(super) fn end_scope(view: &Arc<LoadView>, t0: (f64, u64)) {
    LoadView::keep_observations(view, t0.1);
    let t0 = t0.0;
    loadview::diag::flush_logs();
    let bypass = loadview::diag::take_bypass();
    let counts = view.counts();
    LAST_COUNTS.with(|last| last.set(Some((counts, bypass))));
    if crate::frontend::compiler::load_times::times_enabled() {
        use std::io::Write;
        let t1 = crate::frontend::compiler::load_times::monotonic_ms();
        let line = format!(
            "goport load-view: t0={t0:.3} t1={t1:.3} bypass={bypass} {}\n",
            counts.describe()
        );
        let _ = std::io::stderr().write_all(line.as_bytes());
    }
    RECORDS.with(|records| {
        if let Some(records) = records.borrow_mut().as_mut() {
            view.write_record(records);
        }
    });
    if let Some(file) = env_file("GOPORT_LOAD_VIEW_DUMP", &DUMP) {
        use std::io::Write;
        let mut out = Vec::new();
        view.write_record(&mut out);
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(file)
        {
            let _ = f.write_all(&out);
        }
    }
}

/// Runs the test hook of the end of a load (`set_after_load`).
pub(super) fn after_load() {
    if let Some(hook) = AFTER_LOAD.with(|after| after.borrow().clone()) {
        hook();
    }
}

/// Tests: keep the record of each load's view (`take_records`).
pub fn keep_records(on: bool) {
    RECORDS.with(|records| *records.borrow_mut() = on.then(Vec::new));
}

/// Tests: the records kept since `keep_records` (the format of
/// `GOPORT_LOAD_VIEW_DUMP`).
#[must_use]
pub fn take_records() -> Vec<u8> {
    RECORDS.with(|records| records.borrow_mut().replace(Vec::new()).unwrap_or_default())
}

/// Tests: the loads of this thread replay `records` (as
/// `GOPORT_LOAD_VIEW_REPLAY`), or (`None`) the environment again.
pub fn set_replay(records: Option<&[u8]>) {
    let views = records.map(|bytes| {
        LoadView::read_records(bytes)
            .unwrap_or_else(|err| panic!("load view records: {err}"))
            .into()
    });
    REPLAY.with(|replay| *replay.borrow_mut() = views);
}

/// The counts of the last load's view on this thread, and its OS calls
/// that bypassed the view, once.
#[must_use]
pub fn last_counts() -> Option<(ViewCounts, u64)> {
    LAST_COUNTS.with(Cell::take)
}

/// Tests: `hook` runs when each load's scope ends on this thread (after the
/// load, before the watch registration).
pub fn set_after_load(hook: Option<Rc<dyn Fn()>>) {
    AFTER_LOAD.with(|after| *after.borrow_mut() = hook);
}
