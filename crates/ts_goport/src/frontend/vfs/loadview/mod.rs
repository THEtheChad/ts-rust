//! The per-load frozen OS view **V** (perf/first-principles loader v2, lane
//! L0+L1, `docs/perf-rearch/design/loader-v2.md`). Opt-in for now:
//! `GOPORT_LOAD_VIEW=1` is a rollout aid. It goes once the reconciliation
//! (`compiler::load_view_scope`) passes Astra's review and the timing window
//! confirms the gain; the view then becomes the language server's only load
//! path, unless a measurement gives a reason to keep the serial OS path.
//!
//! **Placement.** V sits under the language server's `OverlayFS.host`: the
//! session's host is `bundled::wrap_fs(os_view_fs())`, and `ViewFs` in loader
//! mode answers from the view that is attached on its thread (`attach`), or
//! from the OS when none is. Overlays, the snapshot's caches (cached files,
//! lookup cache, aliases, seen files, missing directories) and the config
//! registry all sit above the host, so the serial loader makes every call
//! it makes without V, in its order, and only a call that would reach the OS
//! reaches V. Bundled paths never reach it (the bundled wrapper answers
//! them), and a session on another file system has no `ViewFs`, so `attach`
//! attaches nothing there.
//!
//! **What V holds.** Exactly what the OS-level call returns
//! (`osvfs::OsFs`), keyed by the exact path string (each casing is its own
//! key), first answer wins:
//! - `stat`: the full `FileInfo` or `None`. `FileExists` and
//!   `DirectoryExists` are `vfs.Common`'s stat with `IsDir` (internal.go:59,
//!   :64; `stat_is_file`, `stat_is_dir`), so one stat answers all three, with
//!   the same collapsed errors (`ENOENT`, `EACCES` → false/None);
//! - `read`: the decoded text (BOM and UTF-16 decoding, partial reads
//!   discarded, as `Common.read_file`) as an `Arc<str>` with its xxh3-128
//!   hash, or the failure;
//! - `realpath`: the answer, which is the original path when the OS call
//!   fails;
//! - `entries`: `get_accessible_entries` with its order and symlink set.
//!
//! Each entry also records what the serial loader read of it (`USED_*`),
//! which the reconciliation checks after the watch registration.
//!
//! **Who fills it.** The serial loader (a miss is the live OS call, stored)
//! and the parse and resolve-ahead workers of the load, through a worker
//! `ViewFs` (`worker_fs`) with the same OS calls. A worker reads a file only
//! when V's stat says it is a regular file (no speculative read of a FIFO or
//! a device); otherwise it gets a failed read and V stores no read.
//!
//! **Semantics.** With nothing that the load observes changing on disk
//! during the load, every V answer equals the live answer, so the load is
//! byte-identical to the serial loader. Otherwise the load equals the
//! serial loader run against V (the V-replay oracle, `diag`); the
//! reconciliation after the watch registration reloads what changed.
//!
//! **Diagnostics** (cargo feature `diagnostics`, `diag.rs`): the counters,
//! the count of live OS calls in the load scope that bypassed V, the call
//! trace, the record and replay of a view, and the test hooks.
// PORT: not in Go (perf).

#[cfg(feature = "diagnostics")]
pub mod diag;

use crate::frontend::prelude::*;
use std::cell::Cell;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::SystemTime;

/// A file text that readers share, with its xxh3-128 hash (the hash of a
/// cached file, project/overlayfs.rs).
#[derive(Clone, Debug)]
pub struct SharedText {
    pub text: Arc<str>,
    pub hash: u128,
}

impl SharedText {
    /// The shared form of a read text: one copy and one hash.
    #[must_use]
    pub fn new(text: String) -> Self {
        let text: Arc<str> = Arc::from(text);
        let hash = xxhash_rust::xxh3::xxh3_128(text.as_bytes());
        SharedText { text, hash }
    }
}

/// True when the language server loads through the per-load OS view
/// (`GOPORT_LOAD_VIEW=1`, a rollout aid; see the module docs).
#[must_use]
pub fn enabled() -> bool {
    #[cfg(feature = "diagnostics")]
    if let Some(on) = diag::enabled_override() {
        return on;
    }
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("GOPORT_LOAD_VIEW").is_ok_and(|value| value == "1"))
}

thread_local! {
    /// The view of the load that runs on this thread (`attach`).
    static ATTACHED: RefCell<Option<Arc<LoadView>>> = const { RefCell::new(None) };
    /// The number of loader `ViewFs` values alive on this thread.
    static LOADER_HOSTS: Cell<usize> = const { Cell::new(0) };
    /// `os_view_fs` of this thread.
    static OS_VIEW_FS: std::cell::OnceCell<Rc<dyn Fs>> = const { std::cell::OnceCell::new() };
}

/// The loader `ViewFs` over `osvfs_fs()` of this thread, one per thread (so
/// `bundled::is_wrapped_os_fs` knows its wrapper).
pub fn os_view_fs() -> Rc<dyn Fs> {
    OS_VIEW_FS.with(|cell| {
        cell.get_or_init(|| Rc::new(ViewFs::loader(super::osvfs_fs())))
            .clone()
    })
}

/// `os_view_fs` of this thread if it was made.
#[must_use]
pub fn existing_os_view_fs() -> Option<Rc<dyn Fs>> {
    OS_VIEW_FS.with(|cell| cell.get().cloned())
}

/// A worker thread's OS file system for a load: `bundled::wrap_fs` of the
/// thread's OS file system, under the load's view (`ViewFs::worker`) when
/// it has one.
pub fn worker_fs(view: Option<&Arc<LoadView>>) -> Rc<dyn Fs> {
    let os = super::osvfs_fs();
    match view {
        Some(view) => crate::frontend::bundled::wrap_fs(Rc::new(ViewFs::worker(os, view.clone()))),
        None => crate::frontend::bundled::wrap_fs(os),
    }
}

/// Attaches a new view for the load that starts on this thread, and
/// returns it. `None` (nothing attached) when V is off, when no loader
/// `ViewFs` lives on this thread (the session's host is another file
/// system), or when a view is attached already (a nested load uses the
/// outer one). `view` makes the view.
pub fn attach(view: impl FnOnce() -> Arc<LoadView>) -> Option<Arc<LoadView>> {
    if !enabled() || LOADER_HOSTS.with(Cell::get) == 0 {
        return None;
    }
    if ATTACHED.with(|attached| attached.borrow().is_some()) {
        return None;
    }
    let view = view();
    ATTACHED.with(|attached| *attached.borrow_mut() = Some(view.clone()));
    #[cfg(feature = "diagnostics")]
    diag::begin_scope();
    Some(view)
}

/// Detaches the view of this thread's load.
pub fn detach() {
    ATTACHED.with(|attached| attached.borrow_mut().take());
}

/// The view attached on this thread, for the workers of its load.
#[must_use]
pub fn current() -> Option<Arc<LoadView>> {
    ATTACHED.with(|attached| attached.borrow().clone())
}

/// The kinds of V's entries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Stat = 0,
    Read = 1,
    Realpath = 2,
    Entries = 3,
}

impl Kind {
    #[must_use]
    pub fn name(self) -> &'static str {
        match self {
            Kind::Stat => "stat",
            Kind::Read => "read",
            Kind::Realpath => "realpath",
            Kind::Entries => "entries",
        }
    }
}

/// Who asks V: the serial loader (a demand) or a worker (speculation).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Who {
    Loader,
    Worker,
}

/// The loader read whether a stat entry is a file (`file_exists`).
pub const USED_FILE: u8 = 1;
/// The loader read whether a stat entry is a directory (`directory_exists`).
pub const USED_DIR: u8 = 2;
/// The loader read the whole entry (a `stat`, a read, a realpath, entries).
pub const USED_FULL: u8 = 4;

struct Slot<V> {
    value: V,
    /// A worker stored it.
    by_worker: bool,
    /// What the serial loader read of it (`USED_*`); 0 when only workers
    /// did. The reconciliation checks only this.
    used: u8,
}

const SHARDS: usize = 64;

struct Shards<V> {
    shards: [Mutex<FxHashMap<Box<str>, Slot<V>>>; SHARDS],
}

impl<V> Default for Shards<V> {
    fn default() -> Self {
        Shards {
            shards: std::array::from_fn(|_| Mutex::default()),
        }
    }
}

impl<V> Shards<V> {
    fn shard(&self, path: &str) -> std::sync::MutexGuard<'_, FxHashMap<Box<str>, Slot<V>>> {
        use std::hash::BuildHasher;
        let hash = rustc_hash::FxBuildHasher.hash_one(path);
        self.shards[(hash >> 40) as usize % SHARDS]
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// `map` of each entry that `keep` keeps, sorted by path.
    fn collect<T>(
        &self,
        keep: impl Fn(&Slot<V>) -> bool,
        map: impl Fn(&Slot<V>) -> T,
    ) -> Vec<(Box<str>, T)> {
        let mut all = Vec::new();
        for shard in &self.shards {
            let shard = shard
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            all.extend(
                shard
                    .iter()
                    .filter(|(_, slot)| keep(slot))
                    .map(|(path, slot)| (path.clone(), map(slot))),
            );
        }
        all.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        all
    }
}

/// The per-load frozen OS view. `Sync`: the workers of the load and its
/// loader share it.
#[derive(Default)]
pub struct LoadView {
    stats: Shards<Option<FileInfo>>,
    reads: Shards<Option<SharedText>>,
    realpaths: Shards<String>,
    entries: Shards<Entries>,
    #[cfg(feature = "diagnostics")]
    diag: diag::ViewDiag,
}

impl LoadView {
    #[must_use]
    pub fn new() -> Arc<Self> {
        Arc::new(LoadView::default())
    }

    /// `map` of the entry of `path` in `shards`, or of `load()` stored there
    /// (first answer wins). The OS call runs with no lock held. `used`: what
    /// a loader demand reads of the entry (`USED_*`).
    fn get<V, R>(
        &self,
        shards: &Shards<V>,
        ask: (Kind, Who, u8),
        path: &str,
        load: impl FnOnce() -> V,
        map: impl FnOnce(&V) -> R,
    ) -> R {
        let (kind, who, used) = ask;
        if let Some(slot) = shards.shard(path).get_mut(path) {
            self.found(slot, kind, who, used, false);
            return map(&slot.value);
        }
        let value = self.os_call(kind, who, path, load);
        #[cfg(feature = "diagnostics")]
        if self.diag.frozen() {
            self.diag.replay_miss(kind, who, path);
            return map(&value);
        }
        let mut shard = shards.shard(path);
        if let Some(slot) = shard.get_mut(path) {
            self.found(slot, kind, who, used, true);
            return map(&slot.value);
        }
        #[cfg(feature = "diagnostics")]
        self.diag.stored(kind, who, path);
        let result = map(&value);
        let by_worker = who == Who::Worker;
        let used = if by_worker { 0 } else { used };
        shard.insert(
            path.into(),
            Slot {
                value,
                by_worker,
                used,
            },
        );
        result
    }

    /// Notes a demand or a worker lookup that found `slot` (`race`: after
    /// its own OS call).
    #[inline]
    fn found<V>(&self, slot: &mut Slot<V>, kind: Kind, who: Who, used: u8, race: bool) {
        if who == Who::Loader {
            slot.used |= used;
        }
        #[cfg(feature = "diagnostics")]
        self.diag.found(kind, who, slot.by_worker, race);
        #[cfg(not(feature = "diagnostics"))]
        let _ = (kind, race);
    }

    /// `load()`, the OS call of a view.
    #[inline]
    fn os_call<V>(&self, kind: Kind, who: Who, path: &str, load: impl FnOnce() -> V) -> V {
        #[cfg(feature = "diagnostics")]
        return diag::os_call(kind, who, path, load);
        #[cfg(not(feature = "diagnostics"))]
        {
            let _ = (kind, who, path);
            load()
        }
    }

    fn stat_with<R>(
        &self,
        os: &dyn Fs,
        ask: (Who, u8),
        path: &str,
        map: impl FnOnce(&Option<FileInfo>) -> R,
    ) -> R {
        let (who, used) = ask;
        self.get(
            &self.stats,
            (Kind::Stat, who, used),
            path,
            || os.stat(path),
            map,
        )
    }

    fn read(&self, os: &dyn Fs, who: Who, path: &str) -> Option<SharedText> {
        self.get(
            &self.reads,
            (Kind::Read, who, USED_FULL),
            path,
            || os.read_file_shared(path),
            Clone::clone,
        )
    }

    /// A worker's read: from V, or only for a regular file (by V's stat)
    /// the OS read stored in V. Else a failed read that V does not store.
    fn speculative_read(&self, os: &dyn Fs, path: &str) -> Option<SharedText> {
        let regular = self.stat_with(os, (Who::Worker, 0), path, |info| {
            info.as_ref().is_some_and(|info| info.mode().is_regular())
        });
        if !regular {
            #[cfg(feature = "diagnostics")]
            self.diag.skipped_read();
            return None;
        }
        let text = self.read(os, Who::Worker, path);
        // The language server's snapshot records the realpath of each
        // node_modules file that it caches (project/snapshotfs.rs
        // `record_realpath_alias`): the worker makes that OS call too.
        if text.is_some() && path.contains("/node_modules/") {
            let _ = self.realpath(os, Who::Worker, path);
        }
        text
    }

    fn realpath(&self, os: &dyn Fs, who: Who, path: &str) -> String {
        self.get(
            &self.realpaths,
            (Kind::Realpath, who, USED_FULL),
            path,
            || os.realpath(path),
            Clone::clone,
        )
    }

    fn entries(&self, os: &dyn Fs, who: Who, path: &str) -> Entries {
        self.get(
            &self.entries,
            (Kind::Entries, who, USED_FULL),
            path,
            || os.get_accessible_entries(path),
            Clone::clone,
        )
    }

    /// The observations that the serial loader read (`Slot::used`), with
    /// what it read of each: what the reconciliation checks.
    #[must_use]
    pub fn demanded(&self) -> Demanded {
        fn used<V, T>(shards: &Shards<V>, map: impl Fn(&V) -> T) -> Vec<(Box<str>, T, u8)> {
            shards
                .collect(|slot| slot.used != 0, |slot| (map(&slot.value), slot.used))
                .into_iter()
                .map(|(path, (value, used))| (path, value, used))
                .collect()
        }
        Demanded {
            stats: used(&self.stats, Clone::clone),
            reads: used(&self.reads, |text| text.as_ref().map(|text| text.hash)),
            realpaths: used(&self.realpaths, Clone::clone),
            entries: used(&self.entries, Clone::clone),
        }
    }
}

/// The observations of a view that its load's serial loader read
/// (`LoadView::demanded`), each with what it read (`USED_*`). Reads keep
/// only their hash.
#[derive(Default)]
pub struct Demanded {
    pub stats: Vec<(Box<str>, Option<FileInfo>, u8)>,
    pub reads: Vec<(Box<str>, Option<u128>, u8)>,
    pub realpaths: Vec<(Box<str>, String, u8)>,
    pub entries: Vec<(Box<str>, Entries, u8)>,
}

impl Demanded {
    #[must_use]
    pub fn len(&self) -> usize {
        self.stats.len() + self.reads.len() + self.realpaths.len() + self.entries.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// The OS file system with V under it (see the module docs). Loader mode
/// (`loader`) asks the view attached on its thread, or the OS when none is.
/// Worker mode (`worker`) asks its view and reads only regular files.
pub struct ViewFs {
    os: Rc<dyn Fs>,
    worker: Option<Arc<LoadView>>,
}

impl ViewFs {
    /// The loader `ViewFs` over `os` (the session's host).
    pub fn loader(os: Rc<dyn Fs>) -> Self {
        LOADER_HOSTS.with(|hosts| hosts.set(hosts.get() + 1));
        ViewFs { os, worker: None }
    }

    /// A worker's `ViewFs` over `os` (the worker thread's OS file system)
    /// for the load of `view`.
    pub fn worker(os: Rc<dyn Fs>, view: Arc<LoadView>) -> Self {
        ViewFs {
            os,
            worker: Some(view),
        }
    }

    /// `f` with this file system's view and who asks, else `live()`.
    #[inline]
    fn with<R>(&self, f: impl FnOnce(&LoadView, Who) -> R, live: impl FnOnce() -> R) -> R {
        if let Some(view) = &self.worker {
            return f(view, Who::Worker);
        }
        let view = ATTACHED.with(|attached| attached.borrow().clone());
        match view {
            Some(view) => f(&view, Who::Loader),
            None => live(),
        }
    }
}

impl Drop for ViewFs {
    fn drop(&mut self) {
        if self.worker.is_none() {
            LOADER_HOSTS.with(|hosts| hosts.set(hosts.get() - 1));
        }
    }
}

impl Fs for ViewFs {
    fn use_case_sensitive_file_names(&self) -> bool {
        self.os.use_case_sensitive_file_names()
    }

    fn file_exists(&self, path: &str) -> bool {
        self.with(
            |view, who| view.stat_with(&*self.os, (who, USED_FILE), path, super::stat_is_file),
            || self.os.file_exists(path),
        )
    }

    fn read_file(&self, path: &str) -> (String, bool) {
        match self.read_file_shared(path) {
            Some(text) => (text.text.to_string(), true),
            None => (String::new(), false),
        }
    }

    fn read_file_shared(&self, path: &str) -> Option<SharedText> {
        self.with(
            |view, who| match who {
                Who::Loader => view.read(&*self.os, who, path),
                Who::Worker => view.speculative_read(&*self.os, path),
            },
            || self.os.read_file_shared(path),
        )
    }

    fn write_file(&self, path: &str, data: &str) -> Result<(), FsError> {
        self.os.write_file(path, data)
    }

    fn append_file(&self, path: &str, data: &str) -> Result<(), FsError> {
        self.os.append_file(path, data)
    }

    fn remove(&self, path: &str) -> Result<(), FsError> {
        self.os.remove(path)
    }

    fn chtimes(
        &self,
        path: &str,
        a_time: Option<SystemTime>,
        m_time: Option<SystemTime>,
    ) -> Result<(), FsError> {
        self.os.chtimes(path, a_time, m_time)
    }

    fn directory_exists(&self, path: &str) -> bool {
        self.with(
            |view, who| view.stat_with(&*self.os, (who, USED_DIR), path, super::stat_is_dir),
            || self.os.directory_exists(path),
        )
    }

    fn get_accessible_entries(&self, path: &str) -> Entries {
        self.with(
            |view, who| view.entries(&*self.os, who, path),
            || self.os.get_accessible_entries(path),
        )
    }

    fn stat(&self, path: &str) -> Option<FileInfo> {
        self.with(
            |view, who| view.stat_with(&*self.os, (who, USED_FULL), path, Clone::clone),
            || self.os.stat(path),
        )
    }

    fn realpath(&self, path: &str) -> String {
        self.with(
            |view, who| view.realpath(&*self.os, who, path),
            || self.os.realpath(path),
        )
    }
}
