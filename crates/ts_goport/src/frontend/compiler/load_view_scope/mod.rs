//! The scope of a language server program load with the per-load OS view
//! (`vfs::loadview`, loader v2 L1), and the reconciliation after the watch
//! registration.
//!
//! `begin` attaches a new view under the session's host for the load on this
//! thread; the scope detaches it when it ends, before any later load or
//! auto-import work, and keeps the view for the reconciliation
//! (`reconcile_pending`), which frees it.
//!
//! Diagnostics (cargo feature `diagnostics`, `diag.rs`): the counts line,
//! the V-replay oracle and the test hooks.
// PORT: not in Go (perf).

#[cfg(feature = "diagnostics")]
pub mod diag;

use crate::frontend::prelude::*;
use crate::frontend::vfs::loadview::{self, LoadView};
use crate::frontend::{tspath, vfs};
use std::sync::Arc;

/// The scope of one load with the OS view (`begin`).
pub struct LoadViewScope {
    view: Option<Arc<LoadView>>,
    #[cfg(feature = "diagnostics")]
    t0: f64,
}

thread_local! {
    /// The views of the loads that wait for their reconciliation
    /// (`reconcile_pending`).
    static PENDING: RefCell<Vec<Arc<LoadView>>> = const { RefCell::new(Vec::new()) };
}

/// A new view, or with the `diagnostics` feature the next recorded one to
/// replay.
fn new_view() -> Arc<LoadView> {
    #[cfg(feature = "diagnostics")]
    if let Some(view) = diag::next_replay() {
        return view;
    }
    LoadView::new()
}

/// Attaches a new OS view for the load that starts on this thread, when the
/// view is on and the session's host has one (`loadview::attach`).
#[must_use]
pub fn begin() -> Option<LoadViewScope> {
    let view = loadview::attach(new_view)?;
    Some(LoadViewScope {
        view: Some(view),
        #[cfg(feature = "diagnostics")]
        t0: super::load_dump::monotonic_ms(),
    })
}

impl Drop for LoadViewScope {
    fn drop(&mut self) {
        loadview::detach();
        let Some(view) = self.view.take() else {
            return;
        };
        #[cfg(feature = "diagnostics")]
        diag::end_scope(&view, self.t0);
        // The reconciliation after the watch registration checks the view's
        // observations (`reconcile_pending`); it frees the view then. A
        // replayed view (the V-replay oracle) holds no live observation.
        #[cfg(feature = "diagnostics")]
        let reconcile = diag::reconciles(&view);
        #[cfg(not(feature = "diagnostics"))]
        let reconcile = true;
        if reconcile {
            PENDING.with(|pending| pending.borrow_mut().push(view));
        } else {
            crate::execute::build::build_task::drop_in_background(view);
        }
        #[cfg(feature = "diagnostics")]
        diag::after_load();
    }
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
/// one (`PENDING`), started by the session right after the watch
/// registration of the snapshot that the loads made. A thread of its own
/// checks each observation that their serial loaders read against the disk
/// now, in parallel, so the dispatch thread does not wait for it (the first
/// diagnostics of a load go out meanwhile). When it ends, `deliver` runs on
/// this thread (`gostd::local::post_later`) with the file events of the
/// observations that changed; the session sends them through
/// `didChangeWatchedFiles`, so a change that happened between a worker's
/// observation and the watch registration reloads as its watch event would.
/// A change after the checks started is either found by them or reported by
/// the watches, which are registered by then. Returns false (and drops
/// `deliver`) when no load is pending.
///
/// Each check is the OS call that made the observation, compared on what the
/// loader read of it (`loadview::USED_*`): a `file_exists` or
/// `directory_exists` compares the kind, so an unrelated change of a
/// directory's mtime is no change; a `stat`, the whole `FileInfo`; a read,
/// the text's hash (read again: metadata can describe other bytes); a
/// realpath, the path; entries, the names in order, their kinds and the
/// symlink set (the entries follow each link again, so a target that
/// changes kind shows).
pub fn reconcile_pending(deliver: Box<dyn FnOnce(Reconciled)>) -> bool {
    let views = PENDING.with(|pending| std::mem::take(&mut *pending.borrow_mut()));
    if views.is_empty() {
        return false;
    }
    let slot: Arc<std::sync::Mutex<Option<Reconciled>>> = Arc::default();
    let post = {
        let slot = slot.clone();
        crate::gostd::local::post_later(Box::new(move || {
            let reconciled = slot
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .take()
                .unwrap_or_default();
            #[cfg(feature = "diagnostics")]
            diag::note_events(&reconciled.events);
            deliver(reconciled);
        }))
    };
    let run = move || {
        let reconciled = reconcile_views(views);
        *slot
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(reconciled);
        // The job runs on the dispatch thread now (also when this thread
        // panicked: the handle posts when it drops).
        post.post();
    };
    if let Err(err) = std::thread::Builder::new()
        .name("goport-reconcile".to_string())
        .spawn(run)
    {
        // Out of threads, as a parse worker pool would be: no silent loss of
        // the reconciliation.
        panic!("load view reconciliation: cannot start a thread: {err}");
    }
    true
}

/// Checks the observations of `views` (`reconcile_pending`), times it and
/// frees the views.
fn reconcile_views(views: Vec<Arc<LoadView>>) -> Reconciled {
    let t0 = super::load_dump::monotonic_ms();
    let mut reconciled = reconcile(&views);
    reconciled.wall_ms = super::load_dump::monotonic_ms() - t0;
    drop(views);
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
    reconciled
}

/// One observation to check (`reconcile`).
enum Check {
    Stat(Box<str>, Option<FileInfo>, u8),
    Read(Box<str>, Option<u128>),
    Realpath(Box<str>, String),
    Entries(Box<str>, Entries),
}

/// The event of an observation of `path` that changed, from whether it
/// existed (`old`) and exists now (`new`).
fn existence_event(path: &str, old: bool, new: bool) -> ReconcileEvent {
    match (old, new) {
        (false, true) => ReconcileEvent::Created(path.to_string()),
        (true, false) => ReconcileEvent::Deleted(path.to_string()),
        _ => ReconcileEvent::Changed(path.to_string()),
    }
}

/// A replaced path: as a watcher reports a delete and a create.
fn replaced(path: String, out: &mut Vec<ReconcileEvent>) {
    out.push(ReconcileEvent::Deleted(path.clone()));
    out.push(ReconcileEvent::Created(path));
}

/// Checks a stat observation (`check_one`).
fn check_stat(
    os: &dyn vfs::Fs,
    path: &str,
    old: &Option<FileInfo>,
    used: u8,
    out: &mut Vec<ReconcileEvent>,
) -> bool {
    let new = os.stat(path);
    let changed = if used & loadview::USED_FULL != 0 {
        *old != new
    } else {
        (used & loadview::USED_FILE != 0 && vfs::stat_is_file(old) != vfs::stat_is_file(&new))
            || (used & loadview::USED_DIR != 0 && vfs::stat_is_dir(old) != vfs::stat_is_dir(&new))
    };
    if changed {
        // A file that became a directory (or back) is a replaced path.
        if old.is_some() && new.is_some() && vfs::stat_is_dir(old) != vfs::stat_is_dir(&new) {
            replaced(path.to_string(), out);
        } else {
            out.push(existence_event(path, old.is_some(), new.is_some()));
        }
    }
    changed
}

/// The kind of `name` in `entries`: 1 a file, 2 a directory, with 4 added
/// for a link; `None` when it is not there.
fn entry_kind(entries: &Entries, name: &String) -> Option<u8> {
    let link = if entries.symlinks.as_ref().is_some_and(|l| l.contains(name)) {
        4
    } else {
        0
    };
    if entries.files.contains(name) {
        Some(1 | link)
    } else if entries.directories.contains(name) {
        Some(2 | link)
    } else {
        None
    }
}

/// Checks an entries observation (`check_one`): an event per added,
/// removed or changed child, or a change of the directory when only the
/// order changed.
fn check_entries(
    os: &dyn vfs::Fs,
    path: &str,
    old: &Entries,
    out: &mut Vec<ReconcileEvent>,
) -> bool {
    let new = os.get_accessible_entries(path);
    let sorted_links = |entries: &Entries| {
        entries.symlinks.as_ref().map(|links| {
            let mut links: Vec<&String> = links.iter().collect();
            links.sort_unstable();
            links.into_iter().cloned().collect::<Vec<String>>()
        })
    };
    if old.files == new.files
        && old.directories == new.directories
        && sorted_links(old) == sorted_links(&new)
    {
        return false;
    }
    let mut names: Vec<&String> = [&old.files, &old.directories, &new.files, &new.directories]
        .into_iter()
        .flatten()
        .collect();
    names.sort_unstable();
    names.dedup();
    let before = out.len();
    for name in names {
        let child = tspath::combine_paths(path, &[name]);
        match (entry_kind(old, name), entry_kind(&new, name)) {
            (None, Some(_)) => out.push(ReconcileEvent::Created(child)),
            (Some(_), None) => out.push(ReconcileEvent::Deleted(child)),
            (Some(a), Some(b)) if a != b => replaced(child, out),
            _ => {}
        }
    }
    if out.len() == before {
        out.push(ReconcileEvent::Changed(path.to_string()));
    }
    true
}

/// Checks `check` on `os` (the OS file system of this thread): pushes its
/// events when it changed, and gives whether it did.
fn check_one(os: &dyn vfs::Fs, check: &Check, out: &mut Vec<ReconcileEvent>) -> bool {
    match check {
        Check::Stat(path, old, used) => check_stat(os, path, old, *used, out),
        Check::Read(path, old) => {
            let new = os.read_file_shared(path).map(|text| text.hash);
            let changed = *old != new;
            if changed {
                out.push(existence_event(path, old.is_some(), new.is_some()));
            }
            changed
        }
        Check::Realpath(path, old) => {
            let changed = *old != os.realpath(path);
            if changed {
                // The path now names another file: a replaced path.
                replaced(path.to_string(), out);
            }
            changed
        }
        Check::Entries(path, old) => check_entries(os, path, old, out),
    }
}

/// The checks of the observations that the loaders of `views` read.
fn checks_of(views: &[Arc<LoadView>]) -> Vec<Check> {
    let mut checks: Vec<Check> = Vec::new();
    for view in views {
        let demanded = view.demanded();
        let stats = demanded.stats.into_iter();
        checks.extend(stats.map(|(p, v, used)| Check::Stat(p, v, used)));
        let reads = demanded.reads.into_iter();
        checks.extend(reads.map(|(p, v, _)| Check::Read(p, v)));
        let realpaths = demanded.realpaths.into_iter();
        checks.extend(realpaths.map(|(p, v, _)| Check::Realpath(p, v)));
        let entries = demanded.entries.into_iter();
        checks.extend(entries.map(|(p, v, _)| Check::Entries(p, v)));
    }
    checks
}

/// Runs `checks` on this thread's OS file system: their events and how many
/// changed.
fn run_checks(checks: &[Check]) -> (Vec<ReconcileEvent>, usize) {
    let os = vfs::osvfs_fs();
    let mut events = Vec::new();
    let changed = checks
        .iter()
        .filter(|check| check_one(&*os, check, &mut events))
        .count();
    (events, changed)
}

/// Checks the observations of `views` that their loaders read, in parallel
/// (`reconcile_pending`).
fn reconcile(views: &[Arc<LoadView>]) -> Reconciled {
    let checks = checks_of(views);
    let threads = std::thread::available_parallelism()
        .map_or(1, std::num::NonZero::get)
        .clamp(1, 16)
        .min(checks.len().div_ceil(256).max(1));
    let chunk = checks.len().div_ceil(threads).max(1);
    let results: Vec<(Vec<ReconcileEvent>, usize)> = if threads == 1 {
        vec![run_checks(&checks)]
    } else {
        std::thread::scope(|scope| {
            let handles: Vec<_> = checks
                .chunks(chunk)
                .map(|part| scope.spawn(move || run_checks(part)))
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
