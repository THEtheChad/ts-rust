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
use crate::frontend::{tspath, vfs};
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
    /// The views of the loads that wait for their reconciliation
    /// (`reconcile_pending`).
    static PENDING: RefCell<Vec<Arc<LoadView>>> = const { RefCell::new(Vec::new()) };
    /// Tests: runs when a load's scope ends (after the load, before the
    /// watch registration).
    static AFTER_LOAD: RefCell<Option<Rc<dyn Fn()>>> = const { RefCell::new(None) };
    /// Tests: the events of the last reconciliation on this thread.
    static LAST_EVENTS: RefCell<Option<Vec<ReconcileEvent>>> = const { RefCell::new(None) };
}

thread_local! {
    static RECONCILE_OVERRIDE: Cell<Option<bool>> = const { Cell::new(None) };
}

/// Tests: turns the reconciliation on or off for this thread (`None`: the
/// environment).
pub fn set_reconcile(on: Option<bool>) {
    RECONCILE_OVERRIDE.with(|cell| cell.set(on));
}

/// Tests: `hook` runs when each load's scope ends on this thread.
pub fn set_after_load(hook: Option<Rc<dyn Fn()>>) {
    AFTER_LOAD.with(|after| *after.borrow_mut() = hook);
}

/// Tests: the events of the last reconciliation on this thread, once.
#[must_use]
pub fn take_last_events() -> Option<Vec<ReconcileEvent>> {
    LAST_EVENTS.with(|last| last.borrow_mut().take())
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
        loadview::flush_trace();
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
        // The reconciliation after the watch registration checks the view's
        // observations (`reconcile_pending`); it frees the view then.
        if reconcile_enabled() {
            PENDING.with(|pending| pending.borrow_mut().push(view));
        } else {
            crate::execute::build::build_task::drop_in_background(view);
        }
        if let Some(hook) = AFTER_LOAD.with(|after| after.borrow().clone()) {
            hook();
        }
    }
}

/// True when a load's view is reconciled after its watch registration
/// (`GOPORT_LOAD_VIEW=1`, unless `GOPORT_LOAD_VIEW_RECONCILE=0`).
#[must_use]
pub fn reconcile_enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    if let Some(on) = RECONCILE_OVERRIDE.with(Cell::get) {
        return on && loadview::enabled();
    }
    loadview::enabled()
        && *ON
            .get_or_init(|| std::env::var("GOPORT_LOAD_VIEW_RECONCILE").map_or(true, |v| v != "0"))
}

/// A file event that the reconciliation found, as a file watcher would
/// report it (`didChangeWatchedFiles`).
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ReconcileEvent {
    Created(String),
    Changed(String),
    Deleted(String),
}

/// What a reconciliation did.
#[derive(Debug, Default)]
pub struct Reconciled {
    /// The file events of the observations that changed, sorted, no
    /// duplicates.
    pub events: Vec<ReconcileEvent>,
    /// The observations checked, and those that changed.
    pub checked: usize,
    pub changed: usize,
    pub threads: usize,
    pub wall_ms: f64,
}

/// The reconciliation of the loads that ended on this thread since the last
/// one (`PENDING`): it checks each observation that their serial loaders
/// read against the disk now, in parallel, and gives the file events of
/// those that changed. The caller (the session, right after the watch
/// registration of the snapshot that the loads made) sends them through
/// `didChangeWatchedFiles`, so a change that happened between a worker's
/// observation and the watch registration reloads as its watch event
/// would. `None` when no load is pending.
///
/// Each check is the OS call that made the observation, compared on what the
/// loader read of it (`loadview::USED_*`): a `file_exists` or
/// `directory_exists` compares the kind, so an unrelated change of a
/// directory's mtime is no change; a `stat`, the whole `FileInfo`; a read,
/// the text's hash (read again: metadata can describe other bytes); a
/// realpath, the path; entries, the names in order, their kinds and the
/// symlink set (the entries follow each link again, so a target that
/// changes kind shows).
#[must_use]
pub fn reconcile_pending() -> Option<Reconciled> {
    let views = PENDING.with(|pending| std::mem::take(&mut *pending.borrow_mut()));
    if views.is_empty() {
        return None;
    }
    let t0 = super::load_dump::monotonic_ms();
    let reconciled = reconcile(&views);
    let wall_ms = super::load_dump::monotonic_ms() - t0;
    crate::execute::build::build_task::drop_in_background(views);
    let reconciled = Reconciled {
        wall_ms,
        ..reconciled
    };
    if super::load_dump::times_enabled() {
        use std::io::Write;
        let line = format!(
            "goport load-view-reconcile: t0={t0:.3} wall={:.3} threads={} checked={} changed={} events={}\n",
            reconciled.wall_ms,
            reconciled.threads,
            reconciled.checked,
            reconciled.changed,
            reconciled.events.len()
        );
        let _ = std::io::stderr().write_all(line.as_bytes());
    }
    LAST_EVENTS.with(|last| *last.borrow_mut() = Some(reconciled.events.clone()));
    Some(reconciled)
}

/// One observation to check (`reconcile`).
enum Check {
    Stat(Box<str>, Option<FileInfo>, u8),
    Read(Box<str>, Option<u128>),
    Realpath(Box<str>, String),
    Entries(Box<str>, Entries),
}

/// The events of an observation of `path` that existed (`old`) and exists
/// now (`new`), and changed.
fn existence_events(path: &str, old: bool, new: bool, out: &mut Vec<ReconcileEvent>) {
    match (old, new) {
        (false, true) => out.push(ReconcileEvent::Created(path.to_string())),
        (true, false) => out.push(ReconcileEvent::Deleted(path.to_string())),
        _ => out.push(ReconcileEvent::Changed(path.to_string())),
    }
}

/// Checks `check` on `os` (the OS file system of this thread): pushes its
/// events when it changed, and gives whether it did.
fn check_one(os: &dyn vfs::Fs, check: &Check, out: &mut Vec<ReconcileEvent>) -> bool {
    match check {
        Check::Stat(path, old, used) => {
            let new = os.stat(path);
            let is_file = |info: &Option<FileInfo>| info.as_ref().is_some_and(|i| !i.is_dir());
            let is_dir = |info: &Option<FileInfo>| info.as_ref().is_some_and(FileInfo::is_dir);
            let changed = if used & loadview::USED_FULL != 0 {
                *old != new
            } else {
                (used & loadview::USED_FILE != 0 && is_file(old) != is_file(&new))
                    || (used & loadview::USED_DIR != 0 && is_dir(old) != is_dir(&new))
            };
            if changed {
                // A file that became a directory (or back) is a delete and a
                // create.
                if old.is_some() && new.is_some() && is_dir(old) != is_dir(&new) {
                    out.push(ReconcileEvent::Deleted(path.to_string()));
                    out.push(ReconcileEvent::Created(path.to_string()));
                } else {
                    existence_events(path, old.is_some(), new.is_some(), out);
                }
            }
            changed
        }
        Check::Read(path, old) => {
            let new = os.read_file_shared(path).map(|text| text.hash);
            let changed = *old != new;
            if changed {
                existence_events(path, old.is_some(), new.is_some(), out);
            }
            changed
        }
        Check::Realpath(path, old) => {
            let new = os.realpath(path);
            let changed = *old != new;
            if changed {
                // The path now names another file: as a watcher reports a
                // replaced file.
                out.push(ReconcileEvent::Deleted(path.to_string()));
                out.push(ReconcileEvent::Created(path.to_string()));
            }
            changed
        }
        Check::Entries(path, old) => {
            let new = os.get_accessible_entries(path);
            let sorted = |links: &Option<FxHashSet<String>>| {
                let mut links: Vec<&String> = links.iter().flatten().collect();
                links.sort_unstable();
                links.into_iter().cloned().collect::<Vec<String>>()
            };
            let changed = old.files != new.files
                || old.directories != new.directories
                || sorted(&old.symlinks) != sorted(&new.symlinks)
                || old.symlinks.is_some() != new.symlinks.is_some();
            if changed {
                let kind = |entries: &Entries, name: &String| {
                    if entries.files.contains(name) {
                        Some(1u8)
                    } else if entries.directories.contains(name) {
                        Some(2)
                    } else {
                        None
                    }
                };
                let link = |entries: &Entries, name: &String| {
                    entries.symlinks.as_ref().is_some_and(|l| l.contains(name))
                };
                let mut names: Vec<&String> = old
                    .files
                    .iter()
                    .chain(&old.directories)
                    .chain(&new.files)
                    .chain(&new.directories)
                    .collect();
                names.sort_unstable();
                names.dedup();
                let mut any = false;
                for name in names {
                    let child = tspath::combine_paths(path, &[name]);
                    let (o, n) = (kind(old, name), kind(&new, name));
                    match (o, n) {
                        (None, Some(_)) => out.push(ReconcileEvent::Created(child)),
                        (Some(_), None) => out.push(ReconcileEvent::Deleted(child)),
                        (Some(a), Some(b)) if a != b || link(old, name) != link(&new, name) => {
                            out.push(ReconcileEvent::Deleted(child.clone()));
                            out.push(ReconcileEvent::Created(child));
                        }
                        _ => continue,
                    }
                    any = true;
                }
                // Only the order changed.
                if !any {
                    out.push(ReconcileEvent::Changed(path.to_string()));
                }
            }
            changed
        }
    }
}

/// Checks the observations of `views` that their loaders read, in parallel
/// (`reconcile_pending`).
fn reconcile(views: &[Arc<LoadView>]) -> Reconciled {
    let mut checks: Vec<Check> = Vec::new();
    for view in views {
        let demanded = view.demanded();
        checks.extend(
            demanded
                .stats
                .into_iter()
                .map(|(p, v, used)| Check::Stat(p, v, used)),
        );
        checks.extend(
            demanded
                .reads
                .into_iter()
                .map(|(p, v, _)| Check::Read(p, v)),
        );
        checks.extend(
            demanded
                .realpaths
                .into_iter()
                .map(|(p, v, _)| Check::Realpath(p, v)),
        );
        checks.extend(
            demanded
                .entries
                .into_iter()
                .map(|(p, v, _)| Check::Entries(p, v)),
        );
    }
    let threads = std::thread::available_parallelism()
        .map_or(1, std::num::NonZero::get)
        .clamp(1, 16)
        .min(checks.len().div_ceil(256).max(1));
    let chunk = checks.len().div_ceil(threads).max(1);
    let results: Vec<(Vec<ReconcileEvent>, usize)> = if threads == 1 {
        let os = vfs::osvfs_fs();
        let mut events = Vec::new();
        let changed = checks
            .iter()
            .filter(|check| check_one(&*os, check, &mut events))
            .count();
        vec![(events, changed)]
    } else {
        std::thread::scope(|scope| {
            let handles: Vec<_> = checks
                .chunks(chunk)
                .map(|part| {
                    scope.spawn(move || {
                        let os = vfs::osvfs_fs();
                        let mut events = Vec::new();
                        let changed = part
                            .iter()
                            .filter(|check| check_one(&*os, check, &mut events))
                            .count();
                        (events, changed)
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or_default())
                .collect()
        })
    };
    let mut events: Vec<ReconcileEvent> = Vec::new();
    let mut changed = 0;
    for (part, count) in results {
        events.extend(part);
        changed += count;
    }
    events.sort_unstable();
    events.dedup();
    Reconciled {
        events,
        checked: checks.len(),
        changed,
        threads,
        wall_ms: 0.0,
    }
}
