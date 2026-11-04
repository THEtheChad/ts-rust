//! The scope of a language server program load with the per-load OS view
//! (`vfs::loadview`, loader v2 L1, `GOPORT_LOAD_VIEW=1`): `begin` attaches a
//! new view under the session's host for the load on this thread, and the
//! scope detaches it when it ends, before any later load or auto-import
//! work. When it ends it:
//! - reports the view's counts and the OS calls that bypassed it
//!   (`GOPORT_LOAD_TIMES`, one `goport load-view:` line on stderr);
//! - appends the view's record to `GOPORT_LOAD_VIEW_DUMP` (the V-replay
//!   oracle reads it back with `GOPORT_LOAD_VIEW_REPLAY`);
//! - gives the view to the free thread.
//!
//! `GOPORT_LOAD_VIEW_REPLAY=<file>`: each load of this process takes the next
//! recorded view of `<file>` (frozen: it stores nothing). A demand that the
//! record does not have is a live OS call, counted as `replay_miss`
//! (`GOPORT_LOAD_VIEW_REPLAY_LOG=1` names each). With the serial loader, this
//! is "the serial loader run against V".
// PORT: not in Go (perf).

use crate::frontend::prelude::*;
use crate::frontend::vfs::loadview::{self, LoadView, Observations};
use std::cell::Cell;
use std::sync::{Arc, OnceLock};

/// The scope of one load with the OS view (`begin`).
pub struct LoadViewScope {
    view: Arc<LoadView>,
    t0: f64,
}

thread_local! {
    /// The recorded views of `GOPORT_LOAD_VIEW_REPLAY` that the loads of
    /// this thread did not take yet, read at the first load.
    static REPLAY: RefCell<Option<std::collections::VecDeque<Arc<LoadView>>>> =
        const { RefCell::new(None) };
    /// What the last load's view observed, until the reconciliation takes
    /// it (`take_last_observations`).
    static LAST_OBSERVATIONS: RefCell<Option<Observations>> = const { RefCell::new(None) };
    /// Tests: keep the last load's observations (`take_last_observations`)
    /// also with no reconciliation.
    static KEEP_OBSERVATIONS: Cell<bool> = const { Cell::new(false) };
    /// Tests: the records of this thread's loads (`take_records`), when
    /// kept (`keep_records`).
    static RECORDS: RefCell<Option<Vec<u8>>> = const { RefCell::new(None) };
    /// The counts of the last load's view on this thread (`last_counts`).
    static LAST_COUNTS: Cell<Option<(loadview::ViewCounts, u64)>> = const { Cell::new(None) };
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
    REPLAY.with(|replay| {
        *replay.borrow_mut() = records.map(|bytes| {
            LoadView::read_records(bytes)
                .expect("load view records")
                .into()
        });
    });
}

/// The counts of the last load's view on this thread, and its OS calls
/// that bypassed the view.
#[must_use]
pub fn last_counts() -> Option<(loadview::ViewCounts, u64)> {
    LAST_COUNTS.with(Cell::get)
}

fn env_file(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|file| !file.is_empty())
}

fn dump_file() -> Option<&'static str> {
    static FILE: OnceLock<Option<String>> = OnceLock::new();
    FILE.get_or_init(|| env_file("GOPORT_LOAD_VIEW_DUMP"))
        .as_deref()
}

fn replay_file() -> Option<&'static str> {
    static FILE: OnceLock<Option<String>> = OnceLock::new();
    FILE.get_or_init(|| env_file("GOPORT_LOAD_VIEW_REPLAY"))
        .as_deref()
}

/// The next recorded view to replay, if `GOPORT_LOAD_VIEW_REPLAY` is set.
fn next_replay() -> Option<Arc<LoadView>> {
    let set = REPLAY.with(|replay| replay.borrow().is_some());
    let file = if set { "" } else { replay_file()? };
    REPLAY.with(|replay| {
        let mut replay = replay.borrow_mut();
        let queue = replay.get_or_insert_with(|| {
            let bytes = std::fs::read(file)
                .unwrap_or_else(|err| panic!("GOPORT_LOAD_VIEW_REPLAY {file}: {err}"));
            LoadView::read_records(&bytes)
                .unwrap_or_else(|err| panic!("GOPORT_LOAD_VIEW_REPLAY {file}: {err}"))
                .into()
        });
        let next = queue.pop_front();
        if next.is_none() {
            eprintln!("goport load-view: replay: no recorded view left; a new one");
        }
        next
    })
}

/// Attaches a new OS view for the load that starts on this thread, when the
/// view is on and the session's host has one (`loadview::attach`).
#[must_use]
pub fn begin() -> Option<LoadViewScope> {
    let view = loadview::attach(|| next_replay().unwrap_or_else(LoadView::new))?;
    Some(LoadViewScope {
        view,
        t0: super::load_dump::monotonic_ms(),
    })
}

/// Tests: keep each load's observations for `take_last_observations`.
pub fn keep_observations(on: bool) {
    KEEP_OBSERVATIONS.with(|keep| keep.set(on));
}

/// What the last load's view on this thread observed (its sorted entries),
/// once.
#[must_use]
pub fn take_last_observations() -> Option<Observations> {
    LAST_OBSERVATIONS.with(|last| last.borrow_mut().take())
}

/// True when the loads keep their observations (`take_last_observations`).
fn keeps_observations() -> bool {
    KEEP_OBSERVATIONS.with(Cell::get)
}

impl LoadViewScope {
    /// The view of this load.
    #[must_use]
    pub fn view(&self) -> &Arc<LoadView> {
        &self.view
    }
}

impl Drop for LoadViewScope {
    fn drop(&mut self) {
        let bypass = loadview::detach();
        LAST_COUNTS.with(|last| last.set(Some((self.view.counts(), bypass))));
        if super::load_dump::times_enabled() {
            use std::io::Write;
            let t1 = super::load_dump::monotonic_ms();
            let line = format!(
                "goport load-view: t0={:.3} t1={:.3} bypass={bypass} {}\n",
                self.t0,
                t1,
                self.view.counts().describe()
            );
            let _ = std::io::stderr().write_all(line.as_bytes());
        }
        RECORDS.with(|records| {
            if let Some(records) = records.borrow_mut().as_mut() {
                self.view.write_record(records);
            }
        });
        if let Some(file) = dump_file() {
            use std::io::Write;
            let mut out = Vec::new();
            self.view.write_record(&mut out);
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(file)
            {
                let _ = f.write_all(&out);
            }
        }
        if keeps_observations() {
            let observations = self.view.observations();
            LAST_OBSERVATIONS.with(|last| *last.borrow_mut() = Some(observations));
        }
        let view = std::mem::take(&mut self.view);
        crate::execute::build::build_task::drop_in_background(view);
    }
}
