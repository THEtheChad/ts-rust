//! Go: internal/execute/watchmanager/watchmanager.go (fswatch directory
//! watches, event accumulation and DoCycle signaling for `tsc --watch` and
//! `tsc -b --watch`).
//!
//! PORT: threads. The frontend `Writer`, file system and backend are `Rc`
//! and stay on the dispatch thread. fswatch callbacks run on the debouncer
//! thread. So the Go `WatchManager` is split: `WatchManager` holds the
//! dispatch-thread fields, and `WatchManagerShared` (an `Arc`) holds the
//! fields that `onWatchEvents` and `handleWatchTerminated` touch
//! (`mu`, `watchedDirs`, the `doCycleCh` sender, `changedMu` and its data).
//! Those two methods live on `WatchManagerShared`. Their `DebugLog` and
//! `warnWriter` output goes to the process stdout (`stdio::CliStdout`): Go
//! passes `sys.Writer()`, which is `os.Stdout` for the real system, and the
//! `Rc` writer cannot cross threads.

use crate::execute::watchmanager::prelude::*;

use std::io::Write;
use std::sync::{Arc, Condvar, Mutex};

use crate::execute::tsc::{Writer, write_str};
use crate::frontend::core_ls_ext;
use crate::frontend::tspath;
use crate::fswatch;
use crate::gostd::errors;

// Go: watchmanager.go:15 watchedDir
/// PORT: shared as `Arc<WatchedDir>`; Go compares `*watchedDir` pointers
/// (`Arc::ptr_eq`). Go sets `closer` after the watch starts, so it sits in a
/// `Mutex` (`None` is a nil closer).
pub struct WatchedDir {
    /// ts#64159: the spelling of the watched directory (the map key is its
    /// path key).
    pub dir: String,
    pub closer: Mutex<Option<Box<dyn fswatch::Watch>>>,
    pub recursive: bool,
}

impl WatchedDir {
    // PORT: Go `wd.closer.Close()` (the error is ignored).
    fn close_closer(&self) {
        if let Some(closer) = self.closer.lock().unwrap().as_ref() {
            let _ = closer.close();
        }
    }
}

// Go: watchmanager.go:21 dirWatchUpdate
// ts#64159: `key` is the path key of `dir`.
#[derive(Clone)]
struct DirWatchUpdate {
    key: String,
    dir: String,
    recursive: bool,
}

/// Go `CaseSensitivity.PathKey` of a rooted, normalized directory.
fn path_key(dir: &str, use_case_sensitive_file_names: bool) -> String {
    tspath::to_path(dir, "", use_case_sensitive_file_names).0
}

// Go: watchmanager.go:35 WatchManager
/// WatchManager manages fswatch directory watches, event accumulation,
/// and DoCycle signaling. It is shared by the CLI watcher and the build
/// mode orchestrator.
///
/// Locking contract:
///   - Call Lock/Unlock around the entire DoCycle body.
///   - ReconcileWatches must be called under Lock.
///   - CloseAllWatches and handleWatchTerminated manage their own locking.
pub struct WatchManager {
    pub backend: Option<Rc<dyn WatchBackend>>,

    /// DebugLog receives verbose watch diagnostics when non-nil
    pub debug_log: Option<Writer>,

    /// PORT: only `onWatchEvents` writes to it in Go; that runs on the
    /// callback thread and writes to the process stdout (see the file
    /// comment).
    pub warn_writer: Writer,
    pub dir_exists: Box<dyn Fn(&str) -> bool>,
    /// Go `caseSensitivity` (ts#64159): `true` is case-sensitive.
    pub use_case_sensitive_file_names: bool,

    /// PORT: the fields the callback thread shares (see the file comment).
    pub shared: Arc<WatchManagerShared>,
}

/// PORT: the `WatchManager` fields that the fswatch callback thread
/// touches.
pub struct WatchManagerShared {
    /// Go `mu`. Go locks it in `Lock` and unlocks it in `Unlock`.
    pub mu: GoMutex,
    /// Go `watchedDirs` (guarded by `mu` in Go; the `Mutex` is Rust's
    /// data lock, taken only for short reads and writes). ts#64159: keyed
    /// by the path key of the directory.
    pub watched_dirs: Mutex<FxHashMap<String, Arc<WatchedDir>>>,
    /// Go `doCycleCh` (see `DoCycleCh`).
    pub do_cycle_ch: DoCycleCh,

    pub changed_mu: Mutex<WatchManagerChanged>,
}

/// PORT: the `WatchManager` fields that Go `changedMu` guards.
#[derive(Default)]
pub struct WatchManagerChanged {
    /// Go `map[string]fswatch.EventKind`; `None` is Go's nil map.
    pub changed_paths: Option<FxHashMap<String, fswatch::EventKind>>,
    pub changed_overflow: bool,
}

/// PORT: Go `sync.Mutex` that one method locks and another unlocks
/// (`Lock` and `Unlock` around a DoCycle body). A std `MutexGuard` cannot
/// cross those calls, so this is a flag and a condition variable.
#[derive(Default)]
pub struct GoMutex {
    locked: Mutex<bool>,
    cond: Condvar,
}

impl GoMutex {
    /// Go `m.Lock()`.
    pub fn lock(&self) {
        let mut locked = self.locked.lock().unwrap();
        while *locked {
            locked = self.cond.wait(locked).unwrap();
        }
        *locked = true;
    }

    /// Go `m.Unlock()`.
    pub fn unlock(&self) {
        let mut locked = self.locked.lock().unwrap();
        if !*locked {
            panic!("sync: unlock of unlocked mutex");
        }
        *locked = false;
        self.cond.notify_one();
    }
}

/// PORT: Go `doCycleCh`, a `chan struct{}` of capacity 1 that `RunLoop`
/// receives from and `signalDoCycle` sends to without blocking. A Go send
/// while the receiver waits hands the value to it and leaves the buffer
/// empty, so a second send fits before the receiver runs. A std
/// `sync_channel(1)` keeps the first value in its slot until the receiver
/// thread runs, and then drops the second send. `recv` is the whole
/// `RunLoop` select, with the `ctx.Done()` case (`wake`).
#[derive(Default)]
pub struct DoCycleCh {
    state: Mutex<DoCycleState>,
    cond: Condvar,
    /// The shutdown faults that tests inject (`test_hooks`).
    #[cfg(feature = "test-hooks")]
    faults: test_hooks::ShutdownFaults,
}

#[derive(Default)]
struct DoCycleState {
    /// A value is in the buffer.
    buffered: bool,
    /// The receiver waits in `recv` and no case has reached it yet.
    waiting: bool,
    /// The case that reached the waiting receiver first (Go: the send or
    /// close that wakes a goroutine parked in a select completes that
    /// select with its own case).
    handed: Option<DoCycleCase>,
    /// The context of `run_loop` is done (`wake`).
    woken: bool,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum DoCycleCase {
    Value,
    Done,
}

impl DoCycleCh {
    /// Go `select { case ch <- struct{}{}: default: }`. False when the
    /// value does not fit.
    pub fn try_send(&self) -> bool {
        let mut state = self.state.lock().unwrap();
        if state.waiting {
            state.waiting = false;
            state.handed = Some(DoCycleCase::Value);
        } else if state.buffered {
            return false;
        } else {
            state.buffered = true;
        }
        self.cond.notify_one();
        true
    }

    /// Go `select { case <-ctx.Done(): ...; case <-ch: ... }` of
    /// `RunLoop`, where `wake` is the `ctx.Done()` case: waits until a case
    /// is ready. True when the select takes a value. When both cases are
    /// ready, it picks one at random, as Go does (`go_select_picks_first`).
    /// A wait ends with the case that came first.
    pub fn recv(&self) -> bool {
        let mut state = self.state.lock().unwrap();
        if !state.buffered && !state.woken {
            state.waiting = true;
            state = self
                .cond
                .wait_while(state, |state| state.handed.is_none())
                .unwrap();
        }
        if let Some(case) = state.handed.take() {
            return case == DoCycleCase::Value;
        }
        if !state.buffered || (state.woken && go_select_picks_first()) {
            return false;
        }
        state.buffered = false;
        true
    }

    /// The `ctx.Done()` case of the `RunLoop` select: ends the wait in
    /// `recv`, and makes the case ready for each later one until
    /// `reset_wake`.
    pub fn wake(&self) {
        let mut state = self.state.lock().unwrap();
        state.woken = true;
        if state.waiting {
            state.waiting = false;
            state.handed = Some(DoCycleCase::Done);
        }
        self.cond.notify_all();
    }

    /// A new `RunLoop`, with a new context.
    fn reset_wake(&self) {
        self.state.lock().unwrap().woken = false;
    }

    /// Go `select { case <-ch: return true; default: return false }`.
    #[cfg(test)]
    fn try_recv(&self) -> bool {
        std::mem::take(&mut self.state.lock().unwrap().buffered)
    }
}

/// Go `selectgo` (runtime/select.go) polls the cases of a select in a
/// random order (`cheaprandn`), so when two cases are ready, each is taken
/// half the time. True for the first case of `RunLoop`'s select
/// (`ctx.Done()`).
fn go_select_picks_first() -> bool {
    use std::hash::BuildHasher;
    // `RandomState::new` gives new keys on each call.
    std::collections::hash_map::RandomState::new().hash_one(()) & 1 == 0
}

// Go: watchmanager.go:53 NewWatchManager
// ts#64159: the watches are keyed by path key under `use_case_sensitive_file_names`.
pub fn new_watch_manager(
    warn_writer: Writer,
    dir_exists: Box<dyn Fn(&str) -> bool>,
    use_case_sensitive_file_names: bool,
) -> WatchManager {
    WatchManager {
        backend: None,
        debug_log: None,
        warn_writer,
        dir_exists,
        use_case_sensitive_file_names,
        shared: Arc::new(WatchManagerShared {
            mu: GoMutex::default(),
            watched_dirs: Mutex::new(FxHashMap::default()),
            do_cycle_ch: DoCycleCh::default(),
            changed_mu: Mutex::new(WatchManagerChanged::default()),
        }),
    }
}

impl WatchManager {
    // Go: watchmanager.go:63 WatchManager.SetBackend
    pub fn set_backend(&mut self, b: Rc<dyn WatchBackend>) {
        self.backend = Some(b);
    }

    // Go: watchmanager.go:65 WatchManager.Backend
    pub fn backend(&self) -> Option<Rc<dyn WatchBackend>> {
        self.backend.clone()
    }

    // Go: watchmanager.go:67 WatchManager.EnsureDefaultBackend
    pub fn ensure_default_backend(&mut self) {
        if self.backend.is_none() {
            let fsw = fswatch::default();
            self.backend = Some(Rc::new(FsWatchBackend { inner: fsw.clone() }));
            if let Some(debug_log) = &self.debug_log {
                write_str(
                    debug_log,
                    &format!("[watch] using {} backend\n", fsw.name()),
                );
            }
        }
    }

    // Go: watchmanager.go:77 WatchManager.Lock
    pub fn lock(&self) {
        self.shared.mu.lock();
    }

    // Go: watchmanager.go:79 WatchManager.Unlock
    pub fn unlock(&self) {
        self.shared.mu.unlock();
    }

    // Go: watchmanager.go:81 WatchManager.DoCycleCh
    pub fn do_cycle_ch(&self) -> &DoCycleCh {
        &self.shared.do_cycle_ch
    }

    // Go: watchmanager.go:83 WatchManager.DrainEvents
    /// PORT: Go returns a nil map when nothing changed; the port returns an
    /// empty map.
    pub fn drain_events(&self) -> (FxHashMap<String, fswatch::EventKind>, bool) {
        let mut changed = self.shared.changed_mu.lock().unwrap();
        let changed_paths = changed.changed_paths.take().unwrap_or_default();
        let overflow = changed.changed_overflow;
        changed.changed_overflow = false;
        drop(changed);
        (changed_paths, overflow)
    }

    // Go: watchmanager.go:93 WatchManager.ForceOverflow
    pub fn force_overflow(&self) {
        let mut changed = self.shared.changed_mu.lock().unwrap();
        changed.changed_overflow = true;
    }

    // Go: watchmanager.go:171 WatchManager.CloseAllWatches
    pub fn close_all_watches(&self) {
        self.shared.mu.lock();
        let closers: Vec<Arc<WatchedDir>> = {
            let mut watched_dirs = self.shared.watched_dirs.lock().unwrap();
            let mut closers = Vec::with_capacity(watched_dirs.len());
            for (_dir, wd) in watched_dirs.drain() {
                closers.push(wd);
            }
            closers
        };
        self.shared.mu.unlock();
        for c in &closers {
            c.close_closer();
        }
    }

    // Go: watchmanager.go:184 WatchManager.createDirWatchRequest
    fn create_dir_watch_request(
        &self,
        update: &DirWatchUpdate,
        entry: &Arc<WatchedDir>,
    ) -> WatchDirectoryRequest {
        let shared = self.shared.clone();
        let identity = entry.clone();
        let key = update.key.clone();
        // PORT: the callback runs on the fswatch debouncer thread; it logs
        // to stdout when DebugLog was set when the watch was made (Go reads
        // DebugLog at event time; callers set it before the first watch).
        let debug_log = self.debug_log.is_some();
        WatchDirectoryRequest {
            dir: update.dir.clone(),
            recursive: entry.recursive,
            ignore: Some(Arc::new(should_ignore_watch_path)),
            callback: Arc::new(move |events: Vec<fswatch::Event>, err: Option<GoError>| {
                if let Some(e) = &err
                    && errors::is(e, &fswatch::ERR_WATCH_TERMINATED)
                {
                    shared.handle_watch_terminated(debug_log, &key, &identity);
                    return;
                }
                shared.on_watch_events(debug_log, events, err);
            }),
        }
    }

    // Go: watchmanager.go:199 WatchManager.ResolveDesiredDirs
    pub fn resolve_desired_dirs(
        &self,
        desired_dirs: &FxHashMap<String, bool>,
    ) -> FxHashMap<String, bool> {
        // ts#64159: ancestors that differ only in case are one watch.
        let mut resolved_by_path: FxHashMap<String, DirWatchUpdate> =
            FxHashMap::with_capacity_and_hasher(desired_dirs.len(), Default::default());
        for (dir, recursive) in desired_dirs {
            // ts#64366: Only directories on disk can be watched. The embedded libs (bundled:///libs) exist in the FS but not on disk.
            if !tspath::is_rooted_disk_path(dir) {
                if let Some(debug_log) = &self.debug_log {
                    write_str(debug_log, &format!("[watch] not a disk path: {dir}\n"));
                }
                continue;
            }
            let mut watch_dir = dir.clone();
            let mut watch_recursive = *recursive;
            while !(self.dir_exists)(&watch_dir) {
                let parent = tspath::get_directory_path(&watch_dir);
                if parent == watch_dir {
                    break;
                }
                watch_dir = parent;
                watch_recursive = false; // ancestor fallbacks are always non-recursive
            }
            // ts#64366: CanWatchDirectory only guards against falling back to an ancestor that is too generic to watch
            // (/, /home, ...). A directory that exists and was asked for is watched at any depth, otherwise a
            // project that lives near the filesystem root (say /app or /srv/app) would never be watched.
            if !(self.dir_exists)(&watch_dir)
                || (watch_dir != *dir && !can_watch_directory(&watch_dir))
            {
                if let Some(debug_log) = &self.debug_log {
                    write_str(
                        debug_log,
                        &format!("[watch] no watchable ancestor for {dir}\n"),
                    );
                }
                continue;
            }
            if watch_dir != *dir
                && let Some(debug_log) = &self.debug_log
            {
                write_str(
                    debug_log,
                    &format!("[watch] resolved {dir} to ancestor {watch_dir}\n"),
                );
            }
            let key = path_key(&watch_dir, self.use_case_sensitive_file_names);
            if let Some(existing) = resolved_by_path.get_mut(&key) {
                existing.recursive = existing.recursive || watch_recursive;
            } else {
                resolved_by_path.insert(
                    key.clone(),
                    DirWatchUpdate {
                        key,
                        dir: watch_dir,
                        recursive: watch_recursive,
                    },
                );
            }
        }
        let mut resolved: FxHashMap<String, bool> =
            FxHashMap::with_capacity_and_hasher(resolved_by_path.len(), Default::default());
        for watch in resolved_by_path.into_values() {
            resolved.insert(watch.dir, watch.recursive);
        }
        resolved
    }

    // Go: watchmanager.go:246 WatchManager.ReconcileWatches
    // PORT: Go ranges over `wm.watchedDirs` while the callbacks delete
    // entries (Go allows that). The port passes a copy of the map and the
    // callbacks change the live map.
    pub fn reconcile_watches(&self, desired_dirs: &FxHashMap<String, bool>) -> Result<(), GoError> {
        if self.backend.is_none() {
            return Ok(());
        }

        // ts#64159: the watches are keyed by path key, so a change of case
        // only does not close and open a watch.
        let mut desired_by_path: FxHashMap<String, DirWatchUpdate> =
            FxHashMap::with_capacity_and_hasher(desired_dirs.len(), Default::default());
        for (dir, recursive) in desired_dirs {
            let key = path_key(dir, self.use_case_sensitive_file_names);
            if let Some(existing) = desired_by_path.get_mut(&key) {
                existing.recursive = existing.recursive || *recursive;
            } else {
                desired_by_path.insert(
                    key.clone(),
                    DirWatchUpdate {
                        key,
                        dir: dir.clone(),
                        recursive: *recursive,
                    },
                );
            }
        }

        let mut additions: Vec<DirWatchUpdate> = Vec::new();
        let mut changes: Vec<DirWatchUpdate> = Vec::new();

        let watched_dirs: FxHashMap<String, Arc<WatchedDir>> =
            self.shared.watched_dirs.lock().unwrap().clone();
        let mut on_added = |_key: &String, desired: &DirWatchUpdate| {
            if let Some(debug_log) = &self.debug_log {
                write_str(
                    debug_log,
                    &format!(
                        "[watch] watching directory {} (recursive={})\n",
                        desired.dir, desired.recursive
                    ),
                );
            }
            additions.push(desired.clone());
        };
        let mut on_removed = |key: &String, wd: &Arc<WatchedDir>| {
            if let Some(debug_log) = &self.debug_log {
                write_str(
                    debug_log,
                    &format!("[watch] closing stale dir watch: {}\n", wd.dir),
                );
            }
            wd.close_closer();
            self.shared.watched_dirs.lock().unwrap().remove(key);
        };
        let mut on_changed = |key: &String, wd: &Arc<WatchedDir>, desired: &DirWatchUpdate| {
            if let Some(debug_log) = &self.debug_log {
                write_str(
                    debug_log,
                    &format!(
                        "[watch] recreating dir watch {} (recursive {}→{})\n",
                        wd.dir, wd.recursive, desired.recursive
                    ),
                );
            }
            wd.close_closer();
            self.shared.watched_dirs.lock().unwrap().remove(key);
            changes.push(desired.clone());
        };
        core_ls_ext::diff_maps_func::<String, Arc<WatchedDir>, DirWatchUpdate>(
            &watched_dirs,
            &desired_by_path,
            |wd: &Arc<WatchedDir>, desired: &DirWatchUpdate| wd.recursive == desired.recursive,
            Some(&mut on_added),
            Some(&mut on_removed),
            Some(&mut on_changed),
        );
        additions.append(&mut changes);
        self.create_dir_watches(additions)
    }

    // Go: watchmanager.go:295 WatchManager.createDirWatches
    fn create_dir_watches(&self, updates: Vec<DirWatchUpdate>) -> Result<(), GoError> {
        if updates.is_empty() {
            return Ok(());
        }
        let mut requests: Vec<WatchDirectoryRequest> = Vec::with_capacity(updates.len());
        let mut entries: Vec<Arc<WatchedDir>> = Vec::with_capacity(updates.len());
        for update in &updates {
            let entry = Arc::new(WatchedDir {
                dir: update.dir.clone(),
                closer: Mutex::new(None),
                recursive: update.recursive,
            });
            requests.push(self.create_dir_watch_request(update, &entry));
            entries.push(entry);
        }
        let backend = self.backend.as_ref().expect("watchmanager: backend is set");
        let err = match backend.watch_directories(requests) {
            Ok(closers) => {
                // PORT: the closers are set before `watched_dirs` is locked,
                // so no closer lock is taken under that lock.
                let mut closers = closers.into_iter();
                for entry in &entries {
                    let closer = closers.next().expect(
                        "index out of range: WatchDirectories returns one closer per request",
                    );
                    *entry.closer.lock().unwrap() = Some(closer);
                }
                let mut watched_dirs = self.shared.watched_dirs.lock().unwrap();
                for (update, entry) in updates.into_iter().zip(entries) {
                    watched_dirs.insert(update.key, entry);
                }
                return Ok(());
            }
            Err(err) => err,
        };
        if let Some(debug_log) = &self.debug_log {
            for update in &updates {
                write_str(
                    debug_log,
                    &format!(
                        "[watch] failed to watch directory {}: {}\n",
                        update.dir,
                        err.error()
                    ),
                );
            }
        }
        Err(err)
    }

    // Go: watchmanager.go:373 WatchManager.IsPathUnderWatch
    // ts#64159: Go `CaseSensitivity.ContainsPath` on the watched spelling,
    // under the manager's case sensitivity.
    pub fn is_path_under_watch(&self, path: &str) -> bool {
        let watched_dirs = self.shared.watched_dirs.lock().unwrap();
        for watch in watched_dirs.values() {
            if tspath::relative_path_within_directory(
                &watch.dir,
                path,
                self.use_case_sensitive_file_names,
            )
            .is_some()
            {
                return true;
            }
        }
        false
    }

    // Go: watchmanager.go:382 WatchManager.RunLoop
    // PORT: Go selects on `ctx.Done()` and `doCycleCh`. Here a waker on
    // `ctx.Done()` makes that case ready in the channel
    // (`DoCycleCh::wake`), so a SIGINT or SIGTERM ends a wait at once, as
    // in Go, and `recv` is the whole select: when both cases are ready it
    // picks one at random, as Go does. `doCycle` is the caller's DoCycle
    // method value. After a signal, the cycle waits until the debouncer has
    // delivered the fire that sent it (`fswatch::wait_for_fires`), as Go's
    // does: otherwise it can drain a deleted directory's event before that
    // fire's "watch terminated" overflow, and then build a second time.
    pub fn run_loop(&self, ctx: &Context, do_cycle: &mut dyn FnMut()) {
        let do_cycle_ch = &self.shared.do_cycle_ch;
        #[cfg(feature = "test-hooks")]
        if do_cycle_ch.faults.timed_shutdown() {
            return test_hooks::run_loop_polling(self, ctx, do_cycle);
        }
        do_cycle_ch.reset_wake();
        let done = ctx.done();
        // `None` from `register_waker`: the context is done already. A nil
        // channel (`done` is `None`) is never ready.
        let waker = done.as_ref().map(|done| {
            let shared = self.shared.clone();
            done.register_waker(move || {
                #[cfg(feature = "test-hooks")]
                shared.do_cycle_ch.faults.delay_cancel();
                shared.do_cycle_ch.wake();
            })
        });
        if let Some(None) = waker {
            do_cycle_ch.wake();
        }
        while do_cycle_ch.recv() {
            fswatch::wait_for_fires();
            do_cycle();
        }
        if let (Some(done), Some(Some(id))) = (&done, waker) {
            done.unregister_waker(id);
        }
        self.close_all_watches();
    }
}

impl WatchManagerShared {
    // Go: watchmanager.go:99 WatchManager.signalDoCycle
    pub fn signal_do_cycle(&self) {
        // Signal sent; the DoCycle loop will pick it up. Or a signal is
        // already pending; coalesced.
        let _ = self.do_cycle_ch.try_send();
    }

    // Go: watchmanager.go:108 WatchManager.onWatchEvents
    // PORT: runs on the fswatch callback thread. `debug_log` is whether Go
    // `wm.DebugLog` is non-nil; the text goes to the process stdout, as does
    // the `warnWriter` warning (see the file comment).
    pub fn on_watch_events(
        &self,
        debug_log: bool,
        events: Vec<fswatch::Event>,
        err: Option<GoError>,
    ) {
        if let Some(err) = err {
            if errors::is(&err, &fswatch::ERR_OVERFLOW) {
                if debug_log {
                    write_stdout("[watch] event overflow, triggering rebuild\n");
                }
                {
                    let mut changed = self.changed_mu.lock().unwrap();
                    changed.changed_overflow = true;
                }
                self.signal_do_cycle();
                return;
            }
            write_stdout(&format!("Warning: File watch error: {}\n", err.error()));
            return;
        }

        if !events.is_empty() {
            if debug_log {
                let mut text = format!("[watch] {} event(s): ", events.len());
                for (i, e) in events.iter().enumerate() {
                    if i > 0 {
                        text.push_str(", ");
                    }
                    if i >= 5 {
                        text.push_str(&format!("... and {} more", events.len() - i));
                        break;
                    }
                    text.push_str(&format!("{} {}", e.kind.string(), e.path));
                }
                text.push('\n');
                write_stdout(&text);
            }
            {
                let mut changed = self.changed_mu.lock().unwrap();
                let changed_paths = changed.changed_paths.get_or_insert_with(|| {
                    FxHashMap::with_capacity_and_hasher(events.len(), Default::default())
                });
                for e in &events {
                    changed_paths.insert(e.path.clone(), e.kind);
                }
            }
            self.signal_do_cycle();
        }
    }

    // Go: watchmanager.go:151 WatchManager.handleWatchTerminated
    // PORT: runs on the fswatch callback thread (see onWatchEvents).
    // ts#64159: `key` is the path key of the watch.
    pub fn handle_watch_terminated(&self, debug_log: bool, key: &str, identity: &Arc<WatchedDir>) {
        if debug_log {
            write_stdout(&format!("[watch] watch terminated: {}\n", identity.dir));
        }
        let mut stale_closer: Option<Arc<WatchedDir>> = None;
        self.mu.lock();
        {
            let mut watched_dirs = self.watched_dirs.lock().unwrap();
            if let Some(wd) = watched_dirs.get(key)
                && Arc::ptr_eq(wd, identity)
            {
                stale_closer = Some(wd.clone());
                watched_dirs.remove(key);
            }
        }
        self.mu.unlock();
        if let Some(stale_closer) = stale_closer {
            stale_closer.close_closer();
        }
        {
            let mut changed = self.changed_mu.lock().unwrap();
            changed.changed_overflow = true;
        }
        self.signal_do_cycle();
    }
}

// Go: watchmanager.go:326 DirWatchSet
/// DirWatchSet accumulates the set of directories that should be watched while
/// answering coverage queries efficiently. A directory is "covered" when it is
/// already present in the set, or when it is contained within a recursive watch
/// directory already in the set.
// PORT: `names` maps each canonical key to the spelling first registered
// for it (ts#64210).
pub struct DirWatchSet {
    opts: tspath::ComparePathsOptions,
    dirs: FxHashMap<String, bool>,
    names: FxHashMap<String, String>,
}

// Go: watchmanager.go:331 NewDirWatchSet
pub fn new_dir_watch_set(opts: tspath::ComparePathsOptions) -> DirWatchSet {
    DirWatchSet {
        opts,
        dirs: FxHashMap::default(),
        names: FxHashMap::default(),
    }
}

impl DirWatchSet {
    // PORT: not in Go at the pin. ts#64159 removed `DirWatchSet.canonical`
    // (watchmanager.go at 673a5f17d713); `Set` and `Covered` now call
    // `caseSensitivity.PathKey` (`path_key`) inline.
    fn canonical(&self, dir: &str) -> String {
        path_key(dir, self.opts.use_case_sensitive_file_names)
    }

    // Go: watchmanager.go:338 DirWatchSet.Set (ts#64210)
    pub fn set(&mut self, dir: &str, recursive: bool) {
        let original = dir;
        let dir = self.canonical(dir);
        if !self.names.contains_key(&dir) {
            self.names.insert(dir.clone(), original.to_string());
        }
        let entry = self.dirs.entry(dir).or_insert(false);
        *entry = *entry || recursive;
    }

    // Go: watchmanager.go:348 DirWatchSet.Covered
    pub fn covered(&self, dir: &str) -> bool {
        let mut dir = self.canonical(dir);
        if self.dirs.contains_key(&dir) {
            return true;
        }
        let root_length = tspath::get_root_length(&dir);
        while dir.len() > root_length {
            dir = tspath::get_directory_path(&dir);
            if self.dirs.get(&dir).copied().unwrap_or(false) {
                return true;
            }
        }
        false
    }

    // Go: watchmanager.go:365 DirWatchSet.Dirs (ts#64210)
    // PORT: Go ranges over a map; the result is a map, so the order does
    // not matter.
    pub fn dirs(&self) -> FxHashMap<String, bool> {
        let mut dirs: FxHashMap<String, bool> =
            FxHashMap::with_capacity_and_hasher(self.dirs.len(), Default::default());
        for (key, recursive) in &self.dirs {
            dirs.insert(self.names[key].clone(), *recursive);
        }
        dirs
    }
}

// PORT: Go `fmt.Fprintf(w, ...)` on the callback thread, where `w` is the
// real system's `os.Stdout` (see the file comment): one write of
// `stdio::CliStdout`. Errors are ignored as in Go. `text` is in the port
// form, so this writes its Go bytes.
fn write_stdout(text: &str) {
    let _ = crate::execute::tsc::write_go_output(
        &mut crate::execute::tsc::stdio::CliStdout,
        text.as_bytes(),
    );
}

/// Tests (`test-hooks`): faults of the shutdown of `run_loop` that the
/// shutdown tests must catch (`tests::run_loop_ends_at_once_when_its_context_is_cancelled`
/// and its fault tests). Each `DoCycleCh` has its own, so a fault reaches
/// only the watch manager of the test that sets it.
#[cfg(feature = "test-hooks")]
mod test_hooks {
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::time::Duration;

    use super::{Context, WatchManager};

    /// The wait of `run_loop_polling` on the cycle channel.
    const POLL: Duration = Duration::from_millis(50);

    #[derive(Default)]
    pub(super) struct ShutdownFaults {
        /// `run_loop` waits with a timeout and looks at the context after
        /// each timeout (`run_loop_polling`), as the port once did: the
        /// cancel ends the loop only at the next timeout.
        timed_shutdown: AtomicBool,
        /// The waker of the context sleeps this long (ms) before its wake.
        cancel_delay_ms: AtomicU64,
    }

    impl ShutdownFaults {
        pub(super) fn set_timed_shutdown(&self, on: bool) {
            self.timed_shutdown.store(on, Ordering::Relaxed);
        }

        pub(super) fn set_cancel_delay(&self, delay: Duration) {
            let ms = u64::try_from(delay.as_millis()).unwrap_or(u64::MAX);
            self.cancel_delay_ms.store(ms, Ordering::Relaxed);
        }

        pub(super) fn timed_shutdown(&self) -> bool {
            self.timed_shutdown.load(Ordering::Relaxed)
        }

        /// On the cancel path, in the waker of `run_loop`.
        pub(super) fn delay_cancel(&self) {
            let ms = self.cancel_delay_ms.load(Ordering::Relaxed);
            if ms > 0 {
                std::thread::sleep(Duration::from_millis(ms));
            }
        }
    }

    /// `run_loop` with the timed shutdown fault: no waker, a wait of `POLL`
    /// on the cycle channel, then a look at the context.
    pub(super) fn run_loop_polling(wm: &WatchManager, ctx: &Context, do_cycle: &mut dyn FnMut()) {
        let ch = &wm.shared.do_cycle_ch;
        loop {
            let mut state = ch.state.lock().unwrap();
            // As `recv`, so the tests see the loop wait.
            state.waiting = true;
            let (mut state, _) = ch
                .cond
                .wait_timeout_while(state, POLL, |state| !state.buffered)
                .unwrap();
            state.waiting = false;
            if std::mem::take(&mut state.buffered) {
                drop(state);
                do_cycle();
            } else if ctx.err().is_some() {
                break;
            }
        }
        wm.close_all_watches();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    /// The longest wait for a step that ends at once when it works. A lost
    /// send or wake fails the test at this limit and does not hang it.
    const LIMIT: Duration = Duration::from_secs(20);

    /// Runs `ch.recv()` on a new thread. `recv_timeout(LIMIT)` on the
    /// result gives what `recv` returned, so a lost send or wake fails the
    /// test and does not hang it.
    fn recv_on_thread(ch: &Arc<DoCycleCh>) -> mpsc::Receiver<bool> {
        let (tx, rx) = mpsc::channel();
        let receiver = ch.clone();
        std::thread::spawn(move || {
            let _ = tx.send(receiver.recv());
        });
        rx
    }

    /// `recv_on_thread`, and returns once that `recv` waits.
    fn waiting_receiver(ch: &Arc<DoCycleCh>) -> mpsc::Receiver<bool> {
        let rx = recv_on_thread(ch);
        let end = Instant::now() + LIMIT;
        while !ch.state.lock().unwrap().waiting {
            assert!(Instant::now() < end, "the receiver does not wait");
            std::thread::yield_now();
        }
        rx
    }

    // PORT: not in Go. `DoCycleCh` keeps the Go channel rule: without a
    // waiting receiver one value fits; a waiting receiver takes the first
    // value, so a second one fits in the buffer.
    #[test]
    fn do_cycle_ch_hands_a_value_to_a_waiting_receiver() {
        let ch = Arc::new(DoCycleCh::default());
        assert!(ch.try_send());
        assert!(!ch.try_send());
        assert!(ch.try_recv());
        assert!(!ch.try_recv());

        let receiver = waiting_receiver(&ch);
        assert!(ch.try_send());
        assert!(ch.try_send());
        assert!(!ch.try_send());
        assert_eq!(receiver.recv_timeout(LIMIT), Ok(true));
        assert!(ch.try_recv());
        assert!(!ch.try_recv());
    }

    // PORT: not in Go. `wake` is the `ctx.Done()` case of the `RunLoop`
    // select: it ends a wait in `recv` with no value, and each later one.
    #[test]
    fn do_cycle_ch_wake_ends_the_wait() {
        let ch = Arc::new(DoCycleCh::default());
        let receiver = waiting_receiver(&ch);
        ch.wake();
        assert_eq!(receiver.recv_timeout(LIMIT), Ok(false));
        assert_eq!(recv_on_thread(&ch).recv_timeout(LIMIT), Ok(false));
        ch.reset_wake();
        assert!(ch.try_send());
        assert_eq!(recv_on_thread(&ch).recv_timeout(LIMIT), Ok(true));
    }

    // Go `selectgo` (runtime/select.go) polls the ready cases in a random
    // order. With a value and the context both ready, `recv` takes each
    // about half the time: in 200 tries each comes 60 to 140 times (more
    // than 5 standard deviations from 100).
    #[test]
    fn do_cycle_ch_picks_a_ready_case_at_random() {
        let ch = DoCycleCh::default();
        ch.wake();
        let mut values = 0;
        for _ in 0..200 {
            ch.try_send();
            if ch.recv() {
                values += 1;
            }
        }
        assert!((60..=140).contains(&values), "{values} values of 200");
    }

    // In Go, the send or close that finds the receiver parked in the
    // select completes the select with its own case. The other case that
    // comes before the receiver runs does not change it: a send stays in
    // the buffer.
    #[test]
    fn do_cycle_ch_wait_ends_with_the_case_that_came_first() {
        let ch = Arc::new(DoCycleCh::default());
        let receiver = waiting_receiver(&ch);
        assert!(ch.try_send());
        ch.wake();
        assert_eq!(receiver.recv_timeout(LIMIT), Ok(true));
        assert!(!ch.try_recv());

        ch.reset_wake();
        let receiver = waiting_receiver(&ch);
        ch.wake();
        assert!(ch.try_send());
        assert!(!ch.try_send());
        assert_eq!(receiver.recv_timeout(LIMIT), Ok(false));
        assert!(ch.try_recv());
    }

    // PORT: not in Go. A cancel ends a waiting `run_loop` at once, as Go's
    // select does. (The loop once waited on the cycle channel with a 50 ms
    // timeout and ended up to 50 ms after a signal.) Measured in this
    // process, from the cancel to the end of `run_loop`: the waker, the
    // wake, the end of the wait and the cleanup after it. On Linux the test
    // also checks, before each cancel, that the loop's thread waits with no
    // timeout (`wait_check`). The fault tests below show that a shutdown
    // that waits for a timer and a delay on the cancel path both fail it.
    #[test]
    fn run_loop_ends_at_once_when_its_context_is_cancelled() {
        let ends = cancel_to_end_runs(|_| {});
        for check in &ends.checks {
            if let WaitCheck::Failed(why) = check {
                panic!("run_loop's wait: {why}");
            }
        }
        assert!(
            ends.median() < AT_ONCE,
            "median {:?} from the cancel to the end of run_loop, all {:?}",
            ends.median(),
            ends.took
        );
    }

    /// The fault of the old loop: a wait with a timeout, then a look at the
    /// context (`test_hooks::run_loop_polling`). The cancel ends the loop
    /// only at the next timeout, past `AT_ONCE`, and on Linux the wait check
    /// sees the timeout.
    #[cfg(feature = "test-hooks")]
    #[test]
    fn run_loop_shutdown_test_catches_a_timed_shutdown() {
        let ends = cancel_to_end_runs(|wm| wm.shared.do_cycle_ch.faults.set_timed_shutdown(true));
        assert!(
            ends.median() >= AT_ONCE,
            "a timed shutdown passes: median {:?}, all {:?}",
            ends.median(),
            ends.took
        );
        for check in &ends.checks {
            assert!(
                !matches!(check, WaitCheck::Untimed),
                "the wait check passes a timed wait"
            );
        }
    }

    /// A delay of 50 ms on the cancel path (the waker sleeps before its wake)
    /// takes the end past `AT_ONCE`. The wait itself has no timeout.
    #[cfg(feature = "test-hooks")]
    #[test]
    fn run_loop_shutdown_test_catches_a_cancel_delay() {
        let ends = cancel_to_end_runs(|wm| {
            wm.shared
                .do_cycle_ch
                .faults
                .set_cancel_delay(Duration::from_millis(50));
        });
        assert!(
            ends.median() >= AT_ONCE,
            "a delayed cancel passes: median {:?}, all {:?}",
            ends.median(),
            ends.took
        );
        for check in &ends.checks {
            assert!(
                !matches!(check, WaitCheck::Failed(_)),
                "the wait check fails an untimed wait: {check:?}"
            );
        }
    }

    /// The longest median time from the cancel to the end of `run_loop`.
    /// Only our code runs in it (the waker, the wake, the end of the wait,
    /// `close_all_watches`); it takes well under 1 ms on an idle host, and
    /// the rest is room for a loaded one: under 96 CPU burners the median
    /// reached 7.2 ms (Q7), so 25 ms keeps the test off the edge while the
    /// injected 50 ms faults (medians about 54 ms) still exceed it.
    const AT_ONCE: Duration = Duration::from_millis(25);

    /// The runs of `cancel_to_end_runs`.
    const RUNS: usize = 20;

    /// What `RUNS` loops that a cancel ended took, sorted, and the wait
    /// check of each.
    struct LoopEnds {
        took: Vec<Duration>,
        checks: Vec<WaitCheck>,
    }

    impl LoopEnds {
        fn median(&self) -> Duration {
            self.took[self.took.len() / 2]
        }
    }

    /// `RUNS` times `cancel_to_end`, each with a new watch manager that
    /// `set` prepares. A skipped wait check is reported once.
    fn cancel_to_end_runs(set: fn(&WatchManager)) -> LoopEnds {
        let mut ends = LoopEnds {
            took: Vec::new(),
            checks: Vec::new(),
        };
        for _ in 0..RUNS {
            let (took, check) = cancel_to_end(set);
            ends.took.push(took);
            ends.checks.push(check);
        }
        crate::gostd::slices::stable_sort_by(&mut ends.took, Ord::cmp);
        if let Some(WaitCheck::Skipped(why)) = ends.checks.first() {
            eprintln!("run_loop's wait check skipped: {why}");
        }
        eprintln!(
            "cancel to the end of run_loop: median {:?}, all {:?}",
            ends.median(),
            ends.took
        );
        ends
    }

    /// Runs `run_loop` on a new thread, on a watch manager that `set`
    /// prepares, over a context that this thread cancels once the loop
    /// waits. Returns the time from the cancel to the end of `run_loop`
    /// and the check of the loop's wait. Every outcome of the check
    /// cancels, and every wait is bounded by `LIMIT`: a loop that does not
    /// wait or does not end fails the test, and does not hang it.
    fn cancel_to_end(set: fn(&WatchManager)) -> (Duration, WaitCheck) {
        use crate::gostd::context;
        let (ready_tx, ready_rx) = mpsc::channel();
        let (end_tx, end_rx) = mpsc::channel();
        std::thread::spawn(move || {
            let wm = new_watch_manager(
                std::rc::Rc::new(std::cell::RefCell::new(Vec::<u8>::new())),
                Box::new(|_| false),
                true,
            );
            set(&wm);
            let (ctx, cancel) = context::with_cancel(&context::background());
            let _ = ready_tx.send((wm.shared.clone(), cancel, this_thread()));
            let mut cycles = 0;
            wm.run_loop(&ctx, &mut || cycles += 1);
            let _ = end_tx.send((Instant::now(), cycles));
        });
        let (shared, cancel, task) = ready_rx
            .recv_timeout(LIMIT)
            .expect("the loop's thread did not start");
        // `recv` sets `waiting` under the lock, and its wait gives the lock
        // back. So once this sees the flag, the loop's thread has left the
        // lock and waits, or is about to.
        let end = Instant::now() + LIMIT;
        let mut check = WaitCheck::Failed(format!("run_loop did not wait within {LIMIT:?}"));
        while Instant::now() < end {
            if shared.do_cycle_ch.state.lock().unwrap().waiting {
                check = wait_check(task.as_deref(), end);
                break;
            }
            std::thread::yield_now();
        }
        let start = Instant::now();
        cancel();
        let (ended, cycles) = end_rx
            .recv_timeout(LIMIT)
            .expect("run_loop did not end after the cancel");
        assert_eq!(cycles, 0, "run_loop ran a cycle");
        (ended.saturating_duration_since(start), check)
    }

    /// The result of `wait_check`.
    #[derive(Debug)]
    enum WaitCheck {
        /// The loop's thread blocks in a futex wait with no timeout.
        Untimed,
        /// No check: not Linux, an architecture with no futex number here,
        /// or no `/proc` of the thread.
        Skipped(String),
        /// A wait with a timeout, or no futex wait seen in time.
        Failed(String),
    }

    /// The /proc directory of this thread (`/proc/<pid>/task/<tid>`), if
    /// /proc has it.
    fn this_thread() -> Option<std::path::PathBuf> {
        if !cfg!(target_os = "linux") {
            return None;
        }
        let task = std::fs::read_link("/proc/thread-self").ok()?;
        Some(std::path::Path::new("/proc").join(task))
    }

    /// Waits, until `end`, for the thread of `task` (`this_thread`) to
    /// block in a futex wait, and checks that the wait has no timeout. While
    /// a thread blocks, its `syscall` file in /proc gives the call and its
    /// arguments: `futex(uaddr, op, val, timeout, ..)`, where a wait op
    /// (`FUTEX_WAIT`, `FUTEX_WAIT_BITSET`) has a null timeout when it waits
    /// until a wake (std's `Condvar::wait`; `wait_timeout` passes a time).
    /// Another futex op (the wake of the mutex's unlock) or a running
    /// thread is passed over.
    fn wait_check(task: Option<&std::path::Path>, end: Instant) -> WaitCheck {
        const FUTEX_WAIT: u64 = 0;
        const FUTEX_WAIT_BITSET: u64 = 9;
        /// The op without `FUTEX_PRIVATE_FLAG` and `FUTEX_CLOCK_REALTIME`.
        const FUTEX_CMD_MASK: u64 = 0x7f;
        let Some(task) = task else {
            return WaitCheck::Skipped("no /proc/thread-self (not Linux, or no /proc)".to_string());
        };
        let futex = if cfg!(target_arch = "x86_64") {
            "202"
        } else if cfg!(any(target_arch = "aarch64", target_arch = "riscv64")) {
            "98"
        } else {
            return WaitCheck::Skipped("no futex syscall number for this architecture".to_string());
        };
        let file = task.join("syscall");
        while Instant::now() < end {
            let call = match std::fs::read_to_string(&file) {
                Ok(call) => call,
                Err(error) => return WaitCheck::Skipped(format!("{}: {error}", file.display())),
            };
            let fields: Vec<&str> = call.split_whitespace().collect();
            let op = fields
                .get(2)
                .and_then(|op| u64::from_str_radix(op.trim_start_matches("0x"), 16).ok());
            if fields.first() == Some(&futex)
                && let Some(op) = op
                && matches!(op & FUTEX_CMD_MASK, FUTEX_WAIT | FUTEX_WAIT_BITSET)
            {
                return if fields.get(4) == Some(&"0x0") {
                    WaitCheck::Untimed
                } else {
                    WaitCheck::Failed(format!("run_loop waits with a timeout: {}", call.trim()))
                };
            }
            std::thread::yield_now();
        }
        WaitCheck::Failed(format!(
            "no futex wait of run_loop's thread seen within {LIMIT:?}"
        ))
    }
}
