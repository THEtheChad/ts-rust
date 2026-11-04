//! The per-load frozen OS view **V** (perf/first-principles loader v2, lane
//! L0+L1, `docs/perf-rearch/design/loader-v2.md`). Opt-in:
//! `GOPORT_LOAD_VIEW=1`.
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
//!   :64), so one stat answers all three, with the same collapsed errors
//!   (`ENOENT`, `EACCES` → false/None);
//! - `read`: the decoded text (BOM and UTF-16 decoding, partial reads
//!   discarded, as `Common.read_file`) as an `Arc<str>` with its xxh3-128
//!   hash, or the failure;
//! - `realpath`: the answer, which is the original path when the OS call
//!   fails;
//! - `entries`: `get_accessible_entries` with its order and symlink set.
//!
//! **Who fills it.** The serial loader (a miss is the live OS call, stored)
//! and the parse and resolve-ahead workers of the load, through a worker
//! `ViewFs` with the same OS calls. A worker reads a file only when V's stat
//! says it is a regular file (no speculative read of a FIFO or a device);
//! otherwise it gets a failed read and V stores no read.
//!
//! **Semantics.** With nothing that the load observes changing on disk
//! during the load, every V answer equals the live answer, so the load is
//! byte-identical to the serial loader. Otherwise the load equals the
//! serial loader run against V (`GOPORT_LOAD_VIEW_REPLAY`); recovery from a
//! change between a worker's observation and the watch registration is not
//! guaranteed by V itself (Astra's loader-v2 review, finding 2).
// PORT: not in Go (perf).

use crate::frontend::prelude::*;
use std::cell::Cell;
use std::sync::atomic::{AtomicU64, Ordering};
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

/// True when `GOPORT_LOAD_VIEW=1` (or a test turned it on for its thread,
/// `set_enabled`).
#[must_use]
pub fn enabled() -> bool {
    if let Some(on) = ENABLED_OVERRIDE.with(Cell::get) {
        return on;
    }
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("GOPORT_LOAD_VIEW").is_ok_and(|value| value == "1"))
}

/// Tests: turns V on or off for this thread (`None`: the environment).
pub fn set_enabled(on: Option<bool>) {
    ENABLED_OVERRIDE.with(|cell| cell.set(on));
}

thread_local! {
    static ENABLED_OVERRIDE: Cell<Option<bool>> = const { Cell::new(None) };
    /// The view of the load that runs on this thread (`attach`).
    static ATTACHED: RefCell<Option<Arc<LoadView>>> = const { RefCell::new(None) };
    /// The number of loader `ViewFs` values alive on this thread.
    static LOADER_HOSTS: Cell<usize> = const { Cell::new(0) };
    /// `os_view_fs` of this thread.
    static OS_VIEW_FS: std::cell::OnceCell<Rc<dyn Fs>> = const { std::cell::OnceCell::new() };
    /// Nonzero while a `ViewFs` makes its OS call on this thread.
    static IN_VIEW: Cell<u32> = const { Cell::new(0) };
    /// The OS calls made on this thread while a view was attached that did
    /// not go through V (`note_os_call`).
    static BYPASS: Cell<u64> = const { Cell::new(0) };
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

/// Attaches a new view for the load that starts on this thread, and
/// returns it. `None` (nothing attached) when V is off, when no loader
/// `ViewFs` lives on this thread (the session's host is another file
/// system), or when a view is attached already (a nested load uses the
/// outer one). `view` makes the view (a new one, or a recorded one to
/// replay).
pub fn attach(view: impl FnOnce() -> Arc<LoadView>) -> Option<Arc<LoadView>> {
    if !enabled() || LOADER_HOSTS.with(Cell::get) == 0 {
        return None;
    }
    if ATTACHED.with(|attached| attached.borrow().is_some()) {
        return None;
    }
    let view = view();
    ATTACHED.with(|attached| *attached.borrow_mut() = Some(view.clone()));
    BYPASS.with(|bypass| bypass.set(0));
    Some(view)
}

/// Detaches the view of this thread's load, and gives the OS calls that
/// bypassed V during it (`note_os_call`).
pub fn detach() -> u64 {
    ATTACHED.with(|attached| attached.borrow_mut().take());
    BYPASS.with(|bypass| bypass.replace(0))
}

/// The view attached on this thread, for the workers of its load.
#[must_use]
pub fn current() -> Option<Arc<LoadView>> {
    ATTACHED.with(|attached| attached.borrow().clone())
}

/// Called by each OS file system call (osvfs.rs): counts a call made on a
/// thread with an attached view that does not come from a `ViewFs` (a live
/// OS call inside the load scope that bypassed V).
#[inline]
pub fn note_os_call() {
    if IN_VIEW.with(Cell::get) == 0 && ATTACHED.with(|attached| attached.borrow().is_some()) {
        BYPASS.with(|bypass| bypass.set(bypass.get() + 1));
    }
}

/// Runs `f`, an OS call of a `ViewFs`.
fn in_view<R>(f: impl FnOnce() -> R) -> R {
    struct Guard;
    impl Drop for Guard {
        fn drop(&mut self) {
            IN_VIEW.with(|depth| depth.set(depth.get() - 1));
        }
    }
    IN_VIEW.with(|depth| depth.set(depth.get() + 1));
    let _guard = Guard;
    f()
}

/// The kinds of V's entries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Stat = 0,
    Read = 1,
    Realpath = 2,
    Entries = 3,
}

pub const KINDS: [Kind; 4] = [Kind::Stat, Kind::Read, Kind::Realpath, Kind::Entries];

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
enum Who {
    Loader,
    Worker,
}

const SHARDS: usize = 64;

struct Slot<V> {
    value: V,
    /// A worker stored it.
    by_worker: bool,
    /// What the serial loader read of it (`USED_*`); 0 when only workers
    /// did. The reconciliation checks only this.
    used: u8,
}

/// The loader read whether a stat entry is a file (`file_exists`).
pub const USED_FILE: u8 = 1;
/// The loader read whether a stat entry is a directory (`directory_exists`).
pub const USED_DIR: u8 = 2;
/// The loader read the whole entry (a `stat`, a read, a realpath, entries).
pub const USED_FULL: u8 = 4;

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

    /// Every entry, sorted by path.
    fn sorted(&self) -> Vec<(Box<str>, V)>
    where
        V: Clone,
    {
        let mut all: Vec<(Box<str>, V)> = Vec::new();
        for shard in &self.shards {
            let shard = shard
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            all.extend(
                shard
                    .iter()
                    .map(|(k, slot)| (k.clone(), slot.value.clone())),
            );
        }
        all.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        all
    }

    fn len(&self) -> usize {
        self.shards
            .iter()
            .map(|shard| {
                shard
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .len()
            })
            .sum()
    }

    fn insert_recorded(&self, path: &str, value: V) {
        self.shard(path).insert(
            path.into(),
            Slot {
                value,
                by_worker: false,
                used: 0,
            },
        );
    }
}

/// The counts of a view (`LoadView::counts`).
#[derive(Default)]
struct Counters {
    /// Per kind: loader demands answered by V, loader demands that V did
    /// not have (a live OS call, stored), loader misses whose store found an
    /// entry that a worker stored meanwhile, worker stores, and worker
    /// lookups that found an entry the loader stored (the worker was late).
    hit: [AtomicU64; 4],
    miss: [AtomicU64; 4],
    race: [AtomicU64; 4],
    hit_worker: [AtomicU64; 4],
    fill: [AtomicU64; 4],
    late: [AtomicU64; 4],
    /// Worker reads not made because V's stat did not show a regular file.
    skipped_reads: AtomicU64,
    /// A replayed (frozen) view: loader demands that it did not have.
    replay_miss: AtomicU64,
}

/// The per-load frozen OS view. `Sync`: the workers of the load and its
/// loader share it.
#[derive(Default)]
pub struct LoadView {
    stats: Shards<Option<FileInfo>>,
    reads: Shards<Option<SharedText>>,
    realpaths: Shards<String>,
    entries: Shards<Entries>,
    counters: Counters,
    /// A recorded view (`GOPORT_LOAD_VIEW_REPLAY`): it stores nothing.
    frozen: bool,
}

/// The counts of a view, for `GOPORT_LOAD_TIMES`.
#[derive(Clone, Copy, Debug, Default)]
pub struct ViewCounts {
    pub hit: [u64; 4],
    pub hit_worker: [u64; 4],
    pub miss: [u64; 4],
    pub race: [u64; 4],
    pub fill: [u64; 4],
    pub late: [u64; 4],
    pub entries: [u64; 4],
    pub skipped_reads: u64,
    pub replay_miss: u64,
}

impl ViewCounts {
    /// One `key=value` text for the times line.
    #[must_use]
    pub fn describe(&self) -> String {
        use std::fmt::Write as _;
        let total = |v: &[u64; 4]| v.iter().sum::<u64>();
        let hits = total(&self.hit);
        let demands = hits + total(&self.miss);
        let mut out = format!(
            "demands={demands} hits={hits} hit_rate={:.4} worker_hits={} misses={} races={} fills={} late={} skipped_reads={} replay_miss={}",
            if demands == 0 {
                0.0
            } else {
                hits as f64 / demands as f64
            },
            total(&self.hit_worker),
            total(&self.miss),
            total(&self.race),
            total(&self.fill),
            total(&self.late),
            self.skipped_reads,
            self.replay_miss,
        );
        for kind in KINDS {
            let k = kind as usize;
            let _ = write!(
                out,
                " {}={}/{}/{}/{}/{}",
                kind.name(),
                self.hit[k],
                self.miss[k],
                self.fill[k],
                self.late[k],
                self.entries[k]
            );
        }
        out
    }
}

fn load_counts(values: &[AtomicU64; 4]) -> [u64; 4] {
    std::array::from_fn(|i| values[i].load(Ordering::Relaxed))
}

impl LoadView {
    #[must_use]
    pub fn new() -> Arc<Self> {
        Arc::new(LoadView::default())
    }

    /// The counts so far.
    #[must_use]
    pub fn counts(&self) -> ViewCounts {
        let c = &self.counters;
        ViewCounts {
            hit: load_counts(&c.hit),
            hit_worker: load_counts(&c.hit_worker),
            miss: load_counts(&c.miss),
            race: load_counts(&c.race),
            fill: load_counts(&c.fill),
            late: load_counts(&c.late),
            entries: [
                self.stats.len() as u64,
                self.reads.len() as u64,
                self.realpaths.len() as u64,
                self.entries.len() as u64,
            ],
            skipped_reads: c.skipped_reads.load(Ordering::Relaxed),
            replay_miss: c.replay_miss.load(Ordering::Relaxed),
        }
    }

    /// `map` of the entry of `path` in `shards`, or of `load()` stored there
    /// (first answer wins). The OS call runs with no lock held.
    /// `used`: what a loader demand reads of the entry (`USED_*`).
    #[allow(clippy::too_many_arguments)]
    fn get<V, R>(
        &self,
        shards: &Shards<V>,
        kind: Kind,
        who: Who,
        used: u8,
        path: &str,
        load: impl FnOnce() -> V,
        map: impl FnOnce(&V) -> R,
    ) -> R {
        let k = kind as usize;
        let c = &self.counters;
        {
            let mut shard = shards.shard(path);
            if let Some(slot) = shard.get_mut(path) {
                match who {
                    Who::Loader => {
                        slot.used |= used;
                        c.hit[k].fetch_add(1, Ordering::Relaxed);
                        if slot.by_worker {
                            c.hit_worker[k].fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    Who::Worker if !slot.by_worker => {
                        c.late[k].fetch_add(1, Ordering::Relaxed);
                    }
                    Who::Worker => {}
                }
                return map(&slot.value);
            }
        }
        let value = in_view(load);
        if let Some(trace) = call_trace() {
            use std::io::Write;
            let mut trace = trace
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let who = match who {
                Who::Loader => "loader",
                Who::Worker => "worker",
            };
            let _ = writeln!(trace, "{}\t{path}\t{who}", kind.name());
        }
        if self.frozen {
            if who == Who::Loader {
                c.replay_miss.fetch_add(1, Ordering::Relaxed);
                if replay_log() {
                    eprintln!("goport load-view: replay miss {} {path}", kind.name());
                }
            }
            return map(&value);
        }
        let mut shard = shards.shard(path);
        match shard.get_mut(path) {
            Some(slot) => {
                if who == Who::Loader {
                    slot.used |= used;
                    c.race[k].fetch_add(1, Ordering::Relaxed);
                    c.hit[k].fetch_add(1, Ordering::Relaxed);
                    if slot.by_worker {
                        c.hit_worker[k].fetch_add(1, Ordering::Relaxed);
                    }
                }
                map(&slot.value)
            }
            None => {
                match who {
                    Who::Loader => {
                        if let Some(log) = miss_log() {
                            use std::io::Write;
                            let mut log = log
                                .lock()
                                .unwrap_or_else(std::sync::PoisonError::into_inner);
                            let _ = writeln!(log, "miss {} {path}", kind.name());
                        }
                        c.miss[k].fetch_add(1, Ordering::Relaxed)
                    }
                    Who::Worker => c.fill[k].fetch_add(1, Ordering::Relaxed),
                };
                let result = map(&value);
                shard.insert(
                    path.into(),
                    Slot {
                        value,
                        by_worker: who == Who::Worker,
                        used: if who == Who::Loader { used } else { 0 },
                    },
                );
                result
            }
        }
    }

    fn stat_with<R>(
        &self,
        os: &dyn Fs,
        who: Who,
        used: u8,
        path: &str,
        map: impl FnOnce(&Option<FileInfo>) -> R,
    ) -> R {
        self.get(
            &self.stats,
            Kind::Stat,
            who,
            used,
            path,
            || os.stat(path),
            map,
        )
    }

    fn read(&self, os: &dyn Fs, who: Who, path: &str) -> Option<SharedText> {
        self.get(
            &self.reads,
            Kind::Read,
            who,
            USED_FULL,
            path,
            || os.read_file_shared(path),
            Clone::clone,
        )
    }

    /// A worker's read: from V, or only for a regular file (by V's stat)
    /// the OS read stored in V. Else a failed read that V does not store.
    fn speculative_read(&self, os: &dyn Fs, path: &str) -> Option<SharedText> {
        if let Some(found) = {
            let shard = self.reads.shard(path);
            shard.get(path).map(|slot| {
                if !slot.by_worker {
                    self.counters.late[Kind::Read as usize].fetch_add(1, Ordering::Relaxed);
                }
                slot.value.clone()
            })
        } {
            return found;
        }
        let regular = self.stat_with(os, Who::Worker, 0, path, |info| {
            info.as_ref().is_some_and(|info| info.mode().is_regular())
        });
        if !regular {
            self.counters.skipped_reads.fetch_add(1, Ordering::Relaxed);
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
            Kind::Realpath,
            who,
            USED_FULL,
            path,
            || os.realpath(path),
            Clone::clone,
        )
    }

    fn entries(&self, os: &dyn Fs, who: Who, path: &str) -> Entries {
        self.get(
            &self.entries,
            Kind::Entries,
            who,
            USED_FULL,
            path,
            || os.get_accessible_entries(path),
            Clone::clone,
        )
    }

    /// The observations that the serial loader read (`Slot::used`), with
    /// what it read of each: what the reconciliation checks.
    #[must_use]
    pub fn demanded(&self) -> Demanded {
        fn used<V: Clone, T>(shards: &Shards<V>, map: impl Fn(&V) -> T) -> Vec<(Box<str>, T, u8)> {
            let mut all = Vec::new();
            for shard in &shards.shards {
                let shard = shard
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                all.extend(
                    shard
                        .iter()
                        .filter(|(_, slot)| slot.used != 0)
                        .map(|(path, slot)| (path.clone(), map(&slot.value), slot.used)),
                );
            }
            all.sort_unstable_by(|a, b| a.0.cmp(&b.0));
            all
        }
        Demanded {
            stats: used(&self.stats, Clone::clone),
            reads: used(&self.reads, |text| text.as_ref().map(|text| text.hash)),
            realpaths: used(&self.realpaths, Clone::clone),
            entries: used(&self.entries, Clone::clone),
        }
    }

    /// Every observation of the view, sorted by kind and path
    /// (the record).
    #[must_use]
    pub fn observations(&self) -> Observations {
        Observations {
            stats: self.stats.sorted(),
            reads: self
                .reads
                .sorted()
                .into_iter()
                .map(|(path, text)| (path, text.map(|text| text.hash)))
                .collect(),
            realpaths: self.realpaths.sorted(),
            entries: self.entries.sorted(),
        }
    }

    /// Appends the record of this view to `out` (`GOPORT_LOAD_VIEW_DUMP`,
    /// read back by `read_records`).
    pub fn write_record(&self, out: &mut Vec<u8>) {
        fn put_str(out: &mut Vec<u8>, s: &str) {
            out.extend_from_slice(&(s.len() as u64).to_le_bytes());
            out.extend_from_slice(s.as_bytes());
        }
        fn put_u64(out: &mut Vec<u8>, v: u64) {
            out.extend_from_slice(&v.to_le_bytes());
        }
        fn put_info(out: &mut Vec<u8>, info: &Option<FileInfo>) {
            match info {
                None => out.push(0),
                Some(info) => {
                    out.push(1);
                    put_str(out, &info.name);
                    put_u64(out, info.size as u64);
                    put_u64(out, u64::from(info.mode.0));
                    match info.mod_time.map(|t| {
                        t.duration_since(SystemTime::UNIX_EPOCH)
                            .map_or((0u8, 0u128), |d| (1, d.as_nanos()))
                    }) {
                        None => out.push(0),
                        Some((_, nanos)) => {
                            out.push(1);
                            out.extend_from_slice(&nanos.to_le_bytes());
                        }
                    }
                }
            }
        }
        out.extend_from_slice(b"VIEW");
        let stats = self.stats.sorted();
        put_u64(out, stats.len() as u64);
        for (path, info) in &stats {
            put_str(out, path);
            put_info(out, info);
        }
        let reads = self.reads.sorted();
        put_u64(out, reads.len() as u64);
        for (path, text) in &reads {
            put_str(out, path);
            match text {
                None => out.push(0),
                Some(text) => {
                    out.push(1);
                    put_str(out, &text.text);
                }
            }
        }
        let realpaths = self.realpaths.sorted();
        put_u64(out, realpaths.len() as u64);
        for (path, real) in &realpaths {
            put_str(out, path);
            put_str(out, real);
        }
        let entries = self.entries.sorted();
        put_u64(out, entries.len() as u64);
        for (path, entries) in &entries {
            put_str(out, path);
            put_u64(out, entries.files.len() as u64);
            for name in &entries.files {
                put_str(out, name);
            }
            put_u64(out, entries.directories.len() as u64);
            for name in &entries.directories {
                put_str(out, name);
            }
            match &entries.symlinks {
                None => out.push(0),
                Some(links) => {
                    out.push(1);
                    let mut links: Vec<&String> = links.iter().collect();
                    links.sort_unstable();
                    put_u64(out, links.len() as u64);
                    for name in links {
                        put_str(out, name);
                    }
                }
            }
        }
    }

    /// The views recorded in `bytes` (`write_record`), frozen, in their
    /// order.
    pub fn read_records(bytes: &[u8]) -> Result<Vec<Arc<LoadView>>, String> {
        struct Reader<'a> {
            bytes: &'a [u8],
            at: usize,
        }
        impl Reader<'_> {
            fn take(&mut self, n: usize) -> Result<&[u8], String> {
                let end = self
                    .at
                    .checked_add(n)
                    .filter(|&end| end <= self.bytes.len());
                let end = end.ok_or_else(|| format!("truncated record at {}", self.at))?;
                let s = &self.bytes[self.at..end];
                self.at = end;
                Ok(s)
            }
            fn u8(&mut self) -> Result<u8, String> {
                Ok(self.take(1)?[0])
            }
            fn u64(&mut self) -> Result<u64, String> {
                Ok(u64::from_le_bytes(self.take(8)?.try_into().unwrap()))
            }
            fn string(&mut self) -> Result<String, String> {
                let n = self.u64()? as usize;
                String::from_utf8(self.take(n)?.to_vec()).map_err(|e| e.to_string())
            }
        }
        let mut r = Reader { bytes, at: 0 };
        let mut views = Vec::new();
        while r.at < bytes.len() {
            if r.take(4)? != b"VIEW" {
                return Err(format!("bad record magic at {}", r.at - 4));
            }
            let view = LoadView {
                frozen: true,
                ..LoadView::default()
            };
            for _ in 0..r.u64()? {
                let path = r.string()?;
                let info = match r.u8()? {
                    0 => None,
                    _ => {
                        let name = r.string()?;
                        let size = r.u64()? as i64;
                        let mode = FileMode(r.u64()? as u32);
                        let mod_time = match r.u8()? {
                            0 => None,
                            _ => {
                                let nanos = u128::from_le_bytes(r.take(16)?.try_into().unwrap());
                                Some(
                                    SystemTime::UNIX_EPOCH
                                        + std::time::Duration::new(
                                            (nanos / 1_000_000_000) as u64,
                                            (nanos % 1_000_000_000) as u32,
                                        ),
                                )
                            }
                        };
                        Some(FileInfo {
                            name,
                            size,
                            mode,
                            mod_time,
                        })
                    }
                };
                view.stats.insert_recorded(&path, info);
            }
            for _ in 0..r.u64()? {
                let path = r.string()?;
                let text = match r.u8()? {
                    0 => None,
                    _ => Some(SharedText::new(r.string()?)),
                };
                view.reads.insert_recorded(&path, text);
            }
            for _ in 0..r.u64()? {
                let path = r.string()?;
                let real = r.string()?;
                view.realpaths.insert_recorded(&path, real);
            }
            for _ in 0..r.u64()? {
                let path = r.string()?;
                let mut entries = Entries::default();
                for _ in 0..r.u64()? {
                    entries.files.push(r.string()?);
                }
                for _ in 0..r.u64()? {
                    entries.directories.push(r.string()?);
                }
                if r.u8()? != 0 {
                    let mut links = FxHashSet::default();
                    for _ in 0..r.u64()? {
                        links.insert(r.string()?);
                    }
                    entries.symlinks = Some(links);
                }
                view.entries.insert_recorded(&path, entries);
            }
            views.push(Arc::new(view));
        }
        Ok(views)
    }
}

/// `GOPORT_LOAD_VIEW_TRACE=<file>`: each OS call that a view makes (the
/// backend calls of the load: loader misses and worker fills), as
/// `kind<TAB>path<TAB>who`, for `scripts/perf-rearch/loader-v2/fsbench.c`.
fn call_trace() -> Option<&'static Mutex<std::io::BufWriter<std::fs::File>>> {
    static LOG: OnceLock<Option<Mutex<std::io::BufWriter<std::fs::File>>>> = OnceLock::new();
    LOG.get_or_init(|| {
        let file = std::env::var("GOPORT_LOAD_VIEW_TRACE").ok()?;
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(file)
            .ok()
            .map(|f| Mutex::new(std::io::BufWriter::new(f)))
    })
    .as_ref()
}

/// Flushes `GOPORT_LOAD_VIEW_TRACE` (at the end of each load).
pub fn flush_trace() {
    if let Some(trace) = call_trace() {
        use std::io::Write;
        let _ = trace
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .flush();
    }
}

/// `GOPORT_LOAD_VIEW_MISS_LOG=<file>`: each loader demand that V did not
/// have, appended to `<file>` (worker readiness analysis).
fn miss_log() -> Option<&'static Mutex<std::fs::File>> {
    static LOG: OnceLock<Option<Mutex<std::fs::File>>> = OnceLock::new();
    LOG.get_or_init(|| {
        let file = std::env::var("GOPORT_LOAD_VIEW_MISS_LOG").ok()?;
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(file)
            .ok()
            .map(Mutex::new)
    })
    .as_ref()
}

fn replay_log() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("GOPORT_LOAD_VIEW_REPLAY_LOG").is_ok_and(|v| v == "1"))
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

/// Every observation of a view (`LoadView::observations`). Reads keep only
/// their hash.
#[derive(Default)]
pub struct Observations {
    pub stats: Vec<(Box<str>, Option<FileInfo>)>,
    pub reads: Vec<(Box<str>, Option<u128>)>,
    pub realpaths: Vec<(Box<str>, String)>,
    pub entries: Vec<(Box<str>, Entries)>,
}

impl Observations {
    /// The number of observations.
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
            |view, who| {
                view.stat_with(&*self.os, who, USED_FILE, path, |info| {
                    info.as_ref().is_some_and(|info| !info.is_dir())
                })
            },
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
            |view, who| {
                view.stat_with(&*self.os, who, USED_DIR, path, |info| {
                    info.as_ref().is_some_and(FileInfo::is_dir)
                })
            },
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
            |view, who| view.stat_with(&*self.os, who, USED_FULL, path, Clone::clone),
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
