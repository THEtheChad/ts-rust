//! The reconciliation of the language server's load views after the watch
//! registration (loader v2 L1).
//!
//! A session's `Reconciler` keeps the views of its loads (`add`) until their
//! reconciliation has a final result. Right after the watch registration of
//! the snapshot that the loads made, the session starts it (`start`): a job
//! on the process's reconcile threads (`pool`) checks every observation that
//! the loads' serial loaders read against the disk now, and the result
//! comes back to the dispatch thread (`gostd::local::post_later`), where the
//! session sends the file events of the changes through
//! `didChangeWatchedFiles`, so a change between a worker's observation and
//! the registration reloads as its watch event would. A change after the
//! checks started is found by them or by the watches, registered by then.
//!
//! - **One job in flight per session.** Views of later loads wait in the
//!   reconciler and go into the next job, which starts when the result of
//!   the job in flight comes back: no observation is lost, and the session's
//!   threads and views are bounded.
//! - **Failure is not "no change".** A job that fails (a check or the plan
//!   panics) gives `Outcome::Failed`; its views stay. The reconciler tries
//!   once more, then reports every observation of those views as possibly
//!   changed (`possibly_changed`), as a flood of watch events would.
//! - **Cancellation.** `cancel` (the session closes) marks the job in flight
//!   cancelled and forgets the views: the job's remaining checks do not run,
//!   its result is dropped on arrival, and nothing waits for its I/O.
//!
//! **What a check compares.** The OS call that made the observation, on what
//! the loader read of it (`loadview::USED_*`): a `file_exists` or
//! `directory_exists` compares the kind, so an unrelated change of a
//! directory's mtime is no change; a `stat`, the whole `FileInfo`; a read,
//! the hash of a new read that never blocks on a file that is not a regular
//! file (a FIFO is a change, with no read); a realpath, the path; entries,
//! the names in order, their kinds and the symlink set (the entries follow
//! each link again, so a target that changes kind shows).
//!
//! **Events.** Each changed path gets one transition (`Transition`), merged
//! over all the checks of the path, and its events in that transition's
//! order: a replaced path (a symlink repointed, a file that became a
//! directory) is a delete and then a create, which the session's change
//! processing reads as a change (`OverlayFS::process_changes`).
// PORT: not in Go (perf).

use super::pool;
use crate::frontend::prelude::*;
use crate::frontend::vfs::loadview::{self, LoadView};
use crate::frontend::{tspath, vfs};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

/// A file event that the reconciliation found, as a file watcher would
/// report it (`didChangeWatchedFiles`). The events of one path keep their
/// order (`Transition::events`).
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum ReconcileEvent {
    Created(String),
    Changed(String),
    Deleted(String),
}

/// What happened to a path between the observation and the check.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Transition {
    Created,
    Changed,
    Deleted,
    /// Another file or kind of file at the path: a delete, then a create.
    Replaced,
}

impl Transition {
    /// The transition of a path that two checks found.
    fn merge(self, other: Transition) -> Transition {
        use Transition::{Changed, Created, Deleted, Replaced};
        match (self, other) {
            (a, b) if a == b => a,
            (Replaced, _) | (_, Replaced) | (Created, Deleted) | (Deleted, Created) => Replaced,
            (Created, Changed) | (Changed, Created) => Created,
            (Deleted, Changed) | (Changed, Deleted) => Deleted,
            _ => Replaced,
        }
    }

    /// The transition of an observation that existed (`old`) and exists now
    /// (`new`), and changed.
    fn of(old: bool, new: bool) -> Transition {
        match (old, new) {
            (false, true) => Transition::Created,
            (true, false) => Transition::Deleted,
            _ => Transition::Changed,
        }
    }

    /// The events of the transition of `path`, in order.
    fn push_events(self, path: String, out: &mut Vec<ReconcileEvent>) {
        match self {
            Transition::Created => out.push(ReconcileEvent::Created(path)),
            Transition::Changed => out.push(ReconcileEvent::Changed(path)),
            Transition::Deleted => out.push(ReconcileEvent::Deleted(path)),
            Transition::Replaced => {
                out.push(ReconcileEvent::Deleted(path.clone()));
                out.push(ReconcileEvent::Created(path));
            }
        }
    }
}

/// The transitions that some checks found, before they are merged per path.
type Found = Vec<(String, Transition)>;

/// The events of `found`: one transition per path (`Transition::merge`), in
/// path order, each with its events in order.
fn events_of(found: impl IntoIterator<Item = (String, Transition)>) -> Vec<ReconcileEvent> {
    let mut per_path: BTreeMap<String, Transition> = BTreeMap::new();
    for (path, transition) in found {
        per_path
            .entry(path)
            .and_modify(|t| *t = t.merge(transition))
            .or_insert(transition);
    }
    let mut events = Vec::with_capacity(per_path.len());
    for (path, transition) in per_path {
        transition.push_events(path, &mut events);
    }
    events
}

/// One observation to check.
enum Check {
    Stat(Box<str>, Option<FileInfo>, u8),
    Read(Box<str>, Option<u128>),
    Realpath(Box<str>, String),
    Entries(Box<str>, Entries),
}

/// Checks a stat observation.
fn check_stat(os: &dyn vfs::Fs, path: &str, old: &Option<FileInfo>, used: u8, out: &mut Found) {
    let new = os.stat(path);
    let changed = if used & loadview::USED_FULL != 0 {
        *old != new
    } else {
        (used & loadview::USED_FILE != 0 && vfs::stat_is_file(old) != vfs::stat_is_file(&new))
            || (used & loadview::USED_DIR != 0 && vfs::stat_is_dir(old) != vfs::stat_is_dir(&new))
    };
    if !changed {
        return;
    }
    // A file that became a directory (or back) is a replaced path.
    let transition =
        if old.is_some() && new.is_some() && vfs::stat_is_dir(old) != vfs::stat_is_dir(&new) {
            Transition::Replaced
        } else {
            Transition::of(old.is_some(), new.is_some())
        };
    out.push((path.to_string(), transition));
}

/// Checks a read observation with a read that never blocks
/// (`Fs::read_regular_file_shared`): a path that is no longer a regular file
/// is a replaced path, with no read.
fn check_read(os: &dyn vfs::Fs, path: &str, old: Option<u128>, out: &mut Found) {
    let transition = match os.read_regular_file_shared(path) {
        vfs::RegularRead::Text(text) if old == Some(text.hash) => return,
        vfs::RegularRead::Failed if old.is_none() => return,
        vfs::RegularRead::Text(_) => Transition::of(old.is_some(), true),
        vfs::RegularRead::Failed => Transition::Deleted,
        vfs::RegularRead::NotRegular if old.is_none() => Transition::Created,
        vfs::RegularRead::NotRegular => Transition::Replaced,
    };
    out.push((path.to_string(), transition));
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

/// The links of `entries`, sorted.
fn sorted_links(entries: &Entries) -> Option<Vec<&String>> {
    entries.symlinks.as_ref().map(|links| {
        let mut links: Vec<&String> = links.iter().collect();
        links.sort_unstable();
        links
    })
}

/// Checks an entries observation: a transition per added, removed or changed
/// child, or a change of the directory when only the order changed.
fn check_entries(os: &dyn vfs::Fs, path: &str, old: &Entries, out: &mut Found) {
    let new = os.get_accessible_entries(path);
    if old.files == new.files
        && old.directories == new.directories
        && sorted_links(old) == sorted_links(&new)
    {
        return;
    }
    let mut names: Vec<&String> = [&old.files, &old.directories, &new.files, &new.directories]
        .into_iter()
        .flatten()
        .collect();
    names.sort_unstable();
    names.dedup();
    let before = out.len();
    for name in names {
        let transition = match (entry_kind(old, name), entry_kind(&new, name)) {
            (None, Some(_)) => Transition::Created,
            (Some(_), None) => Transition::Deleted,
            (Some(a), Some(b)) if a != b => Transition::Replaced,
            _ => continue,
        };
        out.push((tspath::combine_paths(path, &[name]), transition));
    }
    if out.len() == before {
        out.push((path.to_string(), Transition::Changed));
    }
}

/// Checks `check` on `os` (the OS file system of this thread).
fn check_one(os: &dyn vfs::Fs, check: &Check, out: &mut Found) {
    match check {
        Check::Stat(path, old, used) => check_stat(os, path, old, *used, out),
        Check::Read(path, old) => check_read(os, path, *old, out),
        Check::Realpath(path, old) => {
            if *old != os.realpath(path) {
                // The path now names another file: a replaced path.
                out.push((path.to_string(), Transition::Replaced));
            }
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

/// Every observation that the loaders of `views` read, as possibly changed:
/// the events of a reconciliation that could not check them (an observed
/// path that existed is changed, one that did not is created). There are as
/// many as a flood of watch events, which the session's change processing
/// takes as one (`FileChangeSummary::has_excessive_watch_events`).
fn possibly_changed(views: &[Arc<LoadView>]) -> Vec<ReconcileEvent> {
    let existed = |exists: bool| {
        if exists {
            Transition::Changed
        } else {
            Transition::Created
        }
    };
    let mut found = Found::new();
    for check in checks_of(views) {
        let (path, transition) = match check {
            Check::Stat(path, old, _) => (path, existed(old.is_some())),
            Check::Read(path, old) => (path, existed(old.is_some())),
            Check::Realpath(path, _) | Check::Entries(path, _) => (path, Transition::Changed),
        };
        found.push((path.into(), transition));
    }
    events_of(found)
}

/// The result of a job.
pub(super) enum Outcome {
    /// The events of the changes (none when nothing changed).
    Done(Vec<ReconcileEvent>),
    /// The checks could not all run: nothing is known.
    Failed,
}

/// The checks of one task.
const CHUNK: usize = 512;

/// A reconciliation job on the reconcile threads (`pool`): a plan task makes
/// the checks of the views, then a task per `CHUNK` checks runs them. The
/// last task to end posts the outcome to the session's dispatch thread.
struct Job {
    views: Vec<Arc<LoadView>>,
    checks: OnceLock<Vec<Check>>,
    /// The check tasks that have not ended.
    left: AtomicUsize,
    found: Mutex<Found>,
    failed: AtomicBool,
    cancel: Arc<AtomicBool>,
    /// Where the outcome goes, and the handle that posts it.
    done: Mutex<Option<(Arc<Mutex<Option<Outcome>>>, crate::gostd::local::Post)>>,
    #[cfg(feature = "diagnostics")]
    diag: super::diag::JobDiag,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl Job {
    /// The plan task: makes the checks and queues their tasks.
    fn plan(job: Arc<Job>) {
        let planned = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            #[cfg(feature = "diagnostics")]
            super::diag::inject_failure();
            checks_of(&job.views)
        }));
        let Ok(checks) = planned else {
            job.failed.store(true, Ordering::SeqCst);
            Job::finish(&job);
            return;
        };
        let tasks = checks.len().div_ceil(CHUNK);
        let _ = job.checks.set(checks);
        if tasks == 0 {
            Job::finish(&job);
            return;
        }
        job.left.store(tasks, Ordering::SeqCst);
        pool::submit((0..tasks).map(|task| {
            let job = job.clone();
            Box::new(move || Job::check(&job, task)) as pool::Task
        }));
    }

    /// A check task: runs the checks of chunk `task`, unless the job was
    /// cancelled or failed.
    fn check(job: &Arc<Job>, task: usize) {
        if !job.cancel.load(Ordering::SeqCst) && !job.failed.load(Ordering::SeqCst) {
            let checks = job.checks.get().map_or(&[][..], |checks| {
                &checks[task * CHUNK..((task + 1) * CHUNK).min(checks.len())]
            });
            let ran = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let os = vfs::osvfs_fs();
                let mut found = Found::new();
                for check in checks {
                    #[cfg(feature = "diagnostics")]
                    super::diag::slow_check();
                    check_one(&*os, check, &mut found);
                }
                found
            }));
            match ran {
                Ok(found) => lock(&job.found).extend(found),
                Err(_) => job.failed.store(true, Ordering::SeqCst),
            }
        }
        if job.left.fetch_sub(1, Ordering::SeqCst) == 1 {
            Job::finish(job);
        }
    }

    /// Posts the outcome to the dispatch thread, once.
    fn finish(job: &Arc<Job>) {
        let Some((slot, post)) = lock(&job.done).take() else {
            return;
        };
        let outcome = if job.failed.load(Ordering::SeqCst) {
            Outcome::Failed
        } else {
            Outcome::Done(events_of(std::mem::take(&mut *lock(&job.found))))
        };
        #[cfg(feature = "diagnostics")]
        job.diag
            .report(job.checks.get().map_or(0, Vec::len), &outcome);
        *lock(&slot) = Some(outcome);
        // The job runs on the dispatch thread now.
        drop(post);
    }
}

/// The sender of a reconciliation's events (the session's
/// `didChangeWatchedFiles`).
pub type Deliver = Rc<dyn Fn(Vec<ReconcileEvent>)>;

/// A session's reconciliation state (see the module docs).
#[derive(Default)]
pub struct Reconciler {
    /// The views of the session's loads with no final result yet, oldest
    /// first.
    pending: Vec<Arc<LoadView>>,
    /// The cancel flag of the job in flight, and how many of the first
    /// `pending` views it checks.
    in_flight: Option<(Arc<AtomicBool>, usize)>,
    /// The failed jobs in a row for the first views.
    failures: u8,
}

/// A view set fails this many times in a row before it is reported as
/// possibly changed.
const ATTEMPTS: u8 = 2;

impl Reconciler {
    /// Takes the views of loads that ended (`super::take_finished_views`).
    pub fn add(&mut self, views: Vec<Arc<LoadView>>) {
        self.pending.extend(views);
    }

    /// True while a job runs.
    #[must_use]
    pub fn in_flight(&self) -> bool {
        self.in_flight.is_some()
    }

    /// The session closes: the job in flight is cancelled (nothing waits for
    /// it, and its result is dropped), and the views are freed.
    pub fn cancel(&mut self) {
        if let Some((cancel, _)) = self.in_flight.take() {
            cancel.store(true, Ordering::SeqCst);
        }
        let views = std::mem::take(&mut self.pending);
        if !views.is_empty() {
            crate::execute::build::build_task::drop_in_background(views);
        }
        self.failures = 0;
    }

    /// Starts a job for the pending views, unless one is in flight (it
    /// starts the next one when its result comes back) or none waits.
    pub fn start(this: &Rc<RefCell<Reconciler>>, deliver: Deliver) {
        let mut r = this.borrow_mut();
        if r.in_flight.is_some() || r.pending.is_empty() {
            return;
        }
        let views = r.pending.clone();
        let count = views.len();
        let cancel = Arc::new(AtomicBool::new(false));
        r.in_flight = Some((cancel.clone(), count));
        drop(r);
        let slot: Arc<Mutex<Option<Outcome>>> = Arc::default();
        let post = {
            let this = Rc::downgrade(this);
            let slot = slot.clone();
            let cancel = cancel.clone();
            crate::gostd::local::post_later(Box::new(move || {
                if cancel.load(Ordering::SeqCst) {
                    return;
                }
                let Some(this) = this.upgrade() else {
                    return;
                };
                let outcome = lock(&slot).take().unwrap_or(Outcome::Failed);
                Reconciler::finish(&this, outcome, deliver);
            }))
        };
        let job = Arc::new(Job {
            views,
            checks: OnceLock::new(),
            left: AtomicUsize::new(0),
            found: Mutex::default(),
            failed: AtomicBool::new(false),
            cancel,
            done: Mutex::new(Some((slot, post))),
            #[cfg(feature = "diagnostics")]
            diag: super::diag::JobDiag::start(),
        });
        pool::submit([Box::new(move || Job::plan(job)) as pool::Task]);
    }

    /// The outcome of the job in flight, on the dispatch thread: its views
    /// leave on a final result (done, or failed `ATTEMPTS` times and reported
    /// as possibly changed), the events go out, and the next job starts (a
    /// retry, or the views that came meanwhile).
    fn finish(this: &Rc<RefCell<Reconciler>>, outcome: Outcome, deliver: Deliver) {
        let events = {
            let mut r = this.borrow_mut();
            let Some((_, count)) = r.in_flight.take() else {
                return;
            };
            let events = match outcome {
                Outcome::Done(events) => Some(events),
                Outcome::Failed if r.failures + 1 < ATTEMPTS => {
                    r.failures += 1;
                    None
                }
                Outcome::Failed => Some(possibly_changed(&r.pending[..count])),
            };
            if events.is_some() {
                r.failures = 0;
                let done: Vec<Arc<LoadView>> = r.pending.drain(..count).collect();
                crate::execute::build::build_task::drop_in_background(done);
            }
            events
        };
        if let Some(events) = events {
            #[cfg(feature = "diagnostics")]
            super::diag::note_events(&events);
            if !events.is_empty() {
                deliver(events);
            }
        }
        Reconciler::start(this, deliver);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Astra L1 finding 1: a replaced path keeps delete before create, also
    // when other checks of the path found a change, and paths keep their
    // own order.
    #[test]
    fn a_replaced_path_deletes_then_creates() {
        let found = vec![
            ("/p/b".to_string(), Transition::Changed),
            ("/p/a".to_string(), Transition::Replaced),
            ("/p/a".to_string(), Transition::Changed),
            ("/p/c".to_string(), Transition::Created),
            ("/p/c".to_string(), Transition::Deleted),
        ];
        assert_eq!(
            events_of(found),
            vec![
                ReconcileEvent::Deleted("/p/a".to_string()),
                ReconcileEvent::Created("/p/a".to_string()),
                ReconcileEvent::Changed("/p/b".to_string()),
                ReconcileEvent::Deleted("/p/c".to_string()),
                ReconcileEvent::Created("/p/c".to_string()),
            ]
        );
    }

    #[test]
    fn transitions_merge_to_the_strongest() {
        use Transition::{Changed, Created, Deleted, Replaced};
        assert_eq!(Created.merge(Changed), Created);
        assert_eq!(Changed.merge(Deleted), Deleted);
        assert_eq!(Created.merge(Deleted), Replaced);
        assert_eq!(Changed.merge(Replaced), Replaced);
        assert_eq!(Changed.merge(Changed), Changed);
    }
}
