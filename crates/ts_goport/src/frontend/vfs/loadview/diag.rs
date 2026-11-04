//! Diagnostics of the per-load OS view (cargo feature `diagnostics`; never
//! in a shipped build):
//! - the counters of each view (demands found or missed, races, worker
//!   fills, late workers, skipped reads), printed by the load scope with
//!   `GOPORT_LOAD_TIMES`;
//! - the count of live OS calls made on a loading thread inside the load
//!   scope that bypassed the view (`note_os_call`, called by `osvfs::OsFs`);
//! - `GOPORT_LOAD_VIEW_TRACE=<file>`: every OS call a view makes
//!   (`kind<TAB>path<TAB>who`, for `scripts/perf-rearch/loader-v2/fsbench.c`);
//! - `GOPORT_LOAD_VIEW_MISS_LOG=<file>`: each loader demand the view missed;
//! - the record of a view and a frozen view read back from it (the V-replay
//!   oracle, `compiler::load_view_scope::diag`); `GOPORT_LOAD_VIEW_REPLAY_LOG=1`
//!   names each demand that a replayed view lacks;
//! - test hooks (`set_enabled`).
// PORT: not in Go (perf diagnostics).

use super::{Kind, LoadView, Shards, SharedText, Slot, Who};
use crate::frontend::prelude::*;
use std::cell::Cell;
use std::io::Write as _;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::SystemTime;

thread_local! {
    static ENABLED_OVERRIDE: Cell<Option<bool>> = const { Cell::new(None) };
    /// Nonzero while a view makes its OS call on this thread.
    static IN_VIEW: Cell<u32> = const { Cell::new(0) };
    /// The OS calls made on this thread while a view was attached that did
    /// not come from a view (`note_os_call`).
    static BYPASS: Cell<u64> = const { Cell::new(0) };
}

/// Tests: turns the view on or off for this thread (`None`: the
/// environment).
pub fn set_enabled(on: Option<bool>) {
    ENABLED_OVERRIDE.with(|cell| cell.set(on));
}

pub(super) fn enabled_override() -> Option<bool> {
    ENABLED_OVERRIDE.with(Cell::get)
}

/// A load scope starts on this thread (`attach`).
pub(super) fn begin_scope() {
    BYPASS.with(|bypass| bypass.set(0));
}

/// The OS calls that bypassed the view on this thread since its load
/// started (`attach`).
#[must_use]
pub fn take_bypass() -> u64 {
    BYPASS.with(|bypass| bypass.replace(0))
}

/// Called by each OS file system call (osvfs.rs): counts a call made on a
/// thread with an attached view that does not come from a view (a live OS
/// call inside the load scope that bypassed V).
#[inline]
pub fn note_os_call() {
    if IN_VIEW.with(Cell::get) == 0 && super::current().is_some() {
        BYPASS.with(|bypass| bypass.set(bypass.get() + 1));
    }
}

/// Runs `load`, a view's OS call: not a bypass, and traced.
pub(super) fn os_call<V>(kind: Kind, who: Who, path: &str, load: impl FnOnce() -> V) -> V {
    struct Guard;
    impl Drop for Guard {
        fn drop(&mut self) {
            IN_VIEW.with(|depth| depth.set(depth.get() - 1));
        }
    }
    IN_VIEW.with(|depth| depth.set(depth.get() + 1));
    let value = {
        let _guard = Guard;
        load()
    };
    if let Some(trace) = env_file_log("GOPORT_LOAD_VIEW_TRACE", &TRACE) {
        let who = match who {
            Who::Loader => "loader",
            Who::Worker => "worker",
        };
        let _ = writeln!(lock(trace), "{}\t{path}\t{who}", kind.name());
    }
    value
}

type Log = Mutex<std::io::BufWriter<std::fs::File>>;
static TRACE: OnceLock<Option<Log>> = OnceLock::new();
static MISS_LOG: OnceLock<Option<Log>> = OnceLock::new();

/// The file of the environment variable `name`, opened to append, once.
fn env_file_log(name: &str, cell: &'static OnceLock<Option<Log>>) -> Option<&'static Log> {
    cell.get_or_init(|| {
        let file = std::env::var(name).ok().filter(|f| !f.is_empty())?;
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(file)
            .ok()
            .map(|f| Mutex::new(std::io::BufWriter::new(f)))
    })
    .as_ref()
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Flushes the trace and the miss log (at the end of each load).
pub fn flush_logs() {
    for (name, cell) in [
        ("GOPORT_LOAD_VIEW_TRACE", &TRACE),
        ("GOPORT_LOAD_VIEW_MISS_LOG", &MISS_LOG),
    ] {
        if let Some(log) = env_file_log(name, cell) {
            let _ = lock(log).flush();
        }
    }
}

fn replay_log() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("GOPORT_LOAD_VIEW_REPLAY_LOG").is_ok_and(|v| v == "1"))
}

/// The counters and the replay state of one view.
#[derive(Default)]
pub(super) struct ViewDiag {
    /// Per kind: loader demands found in the view, those found in a
    /// worker's entry, loader misses (a live OS call, stored), loader misses
    /// whose store found an entry a worker stored meanwhile, worker stores,
    /// and worker lookups that found an entry the loader stored (the worker
    /// was late).
    hit: [AtomicU64; 4],
    hit_worker: [AtomicU64; 4],
    miss: [AtomicU64; 4],
    race: [AtomicU64; 4],
    fill: [AtomicU64; 4],
    late: [AtomicU64; 4],
    /// Worker reads not made because the view's stat did not show a
    /// regular file.
    skipped_reads: AtomicU64,
    /// A replayed (frozen) view: loader demands that it did not have.
    replay_miss: AtomicU64,
    /// A recorded view read back (`LoadView::read_records`): it stores
    /// nothing.
    frozen: bool,
}

fn bump(counter: &AtomicU64) {
    counter.fetch_add(1, Ordering::Relaxed);
}

impl ViewDiag {
    pub(super) fn frozen(&self) -> bool {
        self.frozen
    }

    pub(super) fn found(&self, kind: Kind, who: Who, by_worker: bool, race: bool) {
        let k = kind as usize;
        match who {
            Who::Loader => {
                bump(&self.hit[k]);
                if by_worker {
                    bump(&self.hit_worker[k]);
                }
                if race {
                    bump(&self.race[k]);
                }
            }
            Who::Worker if !by_worker => bump(&self.late[k]),
            Who::Worker => {}
        }
    }

    pub(super) fn stored(&self, kind: Kind, who: Who, path: &str) {
        let k = kind as usize;
        match who {
            Who::Loader => {
                bump(&self.miss[k]);
                if let Some(log) = env_file_log("GOPORT_LOAD_VIEW_MISS_LOG", &MISS_LOG) {
                    let _ = writeln!(lock(log), "miss {} {path}", kind.name());
                }
            }
            Who::Worker => bump(&self.fill[k]),
        }
    }

    pub(super) fn replay_miss(&self, kind: Kind, who: Who, path: &str) {
        if who == Who::Loader {
            bump(&self.replay_miss);
            if replay_log() {
                eprintln!("goport load-view: replay miss {} {path}", kind.name());
            }
        }
    }

    pub(super) fn skipped_read(&self) {
        bump(&self.skipped_reads);
    }
}

/// The counts of a view.
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

const KINDS: [Kind; 4] = [Kind::Stat, Kind::Read, Kind::Realpath, Kind::Entries];

impl ViewCounts {
    /// One `key=value` text for the `goport load-view:` line.
    #[must_use]
    pub fn describe(&self) -> String {
        use std::fmt::Write as _;
        let total = |v: &[u64; 4]| v.iter().sum::<u64>();
        let hits = total(&self.hit);
        let demands = hits + total(&self.miss);
        let rate = if demands == 0 {
            0.0
        } else {
            hits as f64 / demands as f64
        };
        let mut out = format!(
            "demands={demands} hits={hits} hit_rate={rate:.4} worker_hits={} misses={} races={} fills={} late={} skipped_reads={} replay_miss={}",
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

impl<V> Shards<V> {
    fn len(&self) -> usize {
        self.shards.iter().map(|shard| lock(shard).len()).sum()
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

/// Keeps every entry (`Shards::collect`).
fn all<V>(_: &Slot<V>) -> bool {
    true
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

impl LoadView {
    /// True for a recorded view read back (`read_records`), which the
    /// V-replay oracle replays.
    #[must_use]
    pub fn is_frozen(&self) -> bool {
        self.diag.frozen
    }

    /// The counts so far.
    #[must_use]
    pub fn counts(&self) -> ViewCounts {
        let d = &self.diag;
        ViewCounts {
            hit: load_counts(&d.hit),
            hit_worker: load_counts(&d.hit_worker),
            miss: load_counts(&d.miss),
            race: load_counts(&d.race),
            fill: load_counts(&d.fill),
            late: load_counts(&d.late),
            entries: [
                self.stats.len() as u64,
                self.reads.len() as u64,
                self.realpaths.len() as u64,
                self.entries.len() as u64,
            ],
            skipped_reads: d.skipped_reads.load(Ordering::Relaxed),
            replay_miss: d.replay_miss.load(Ordering::Relaxed),
        }
    }

    /// Every observation of the view, sorted by kind and path.
    #[must_use]
    pub fn observations(&self) -> Observations {
        Observations {
            stats: self.stats.collect(all, |slot| slot.value.clone()),
            reads: self
                .reads
                .collect(all, |slot| slot.value.as_ref().map(|text| text.hash)),
            realpaths: self.realpaths.collect(all, |slot| slot.value.clone()),
            entries: self.entries.collect(all, |slot| slot.value.clone()),
        }
    }

    /// Appends the record of this view to `out` (`read_records` reads it
    /// back): its entries in path order, texts in full.
    pub fn write_record(&self, out: &mut Vec<u8>) {
        out.extend_from_slice(b"VIEW");
        let stats = self.stats.collect(all, |slot| slot.value.clone());
        put_list(out, &stats, |out, info| put_info(out, info.as_ref()));
        let reads = self.reads.collect(all, |slot| slot.value.clone());
        put_list(out, &reads, |out, text| {
            put_option(out, text.as_ref(), |out, text| put_str(out, &text.text));
        });
        let realpaths = self.realpaths.collect(all, |slot| slot.value.clone());
        put_list(out, &realpaths, |out, real| put_str(out, real));
        let entries = self.entries.collect(all, |slot| slot.value.clone());
        put_list(out, &entries, put_entries);
    }

    /// The views recorded in `bytes` (`write_record`), frozen, in their
    /// order.
    pub fn read_records(bytes: &[u8]) -> Result<Vec<Arc<LoadView>>, String> {
        let mut r = Reader { bytes, at: 0 };
        let mut views = Vec::new();
        while r.at < bytes.len() {
            if r.take(4)? != b"VIEW" {
                return Err(format!("bad record magic at {}", r.at - 4));
            }
            let view = LoadView {
                diag: ViewDiag {
                    frozen: true,
                    ..ViewDiag::default()
                },
                ..LoadView::default()
            };
            r.list(|r, path| Ok(view.stats.insert_recorded(path, r.info()?)))?;
            r.list(|r, path| {
                let text = r.option(|r| Ok(SharedText::new(r.string()?)))?;
                Ok(view.reads.insert_recorded(path, text))
            })?;
            r.list(|r, path| Ok(view.realpaths.insert_recorded(path, r.string()?)))?;
            r.list(|r, path| Ok(view.entries.insert_recorded(path, r.entries()?)))?;
            views.push(Arc::new(view));
        }
        Ok(views)
    }
}

fn put_u64(out: &mut Vec<u8>, v: u64) {
    out.extend_from_slice(&v.to_le_bytes());
}

fn put_str(out: &mut Vec<u8>, s: &str) {
    put_u64(out, s.len() as u64);
    out.extend_from_slice(s.as_bytes());
}

fn put_option<T>(out: &mut Vec<u8>, v: Option<&T>, put: impl FnOnce(&mut Vec<u8>, &T)) {
    match v {
        None => out.push(0),
        Some(v) => {
            out.push(1);
            put(out, v);
        }
    }
}

fn put_list<T>(out: &mut Vec<u8>, list: &[(Box<str>, T)], put: impl Fn(&mut Vec<u8>, &T)) {
    put_u64(out, list.len() as u64);
    for (path, value) in list {
        put_str(out, path);
        put(out, value);
    }
}

fn put_info(out: &mut Vec<u8>, info: Option<&FileInfo>) {
    put_option(out, info, |out, info| {
        put_str(out, &info.name);
        put_u64(out, info.size as u64);
        put_u64(out, u64::from(info.mode.0));
        let nanos = info.mod_time.map(|t| {
            t.duration_since(SystemTime::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos())
        });
        put_option(out, nanos.as_ref(), |out, nanos| {
            out.extend_from_slice(&nanos.to_le_bytes());
        });
    });
}

fn put_entries(out: &mut Vec<u8>, entries: &Entries) {
    let names = |out: &mut Vec<u8>, names: &[&String]| {
        put_u64(out, names.len() as u64);
        for name in names {
            put_str(out, name);
        }
    };
    names(out, &entries.files.iter().collect::<Vec<_>>());
    names(out, &entries.directories.iter().collect::<Vec<_>>());
    let links = entries.symlinks.as_ref().map(|links| {
        let mut links: Vec<&String> = links.iter().collect();
        links.sort_unstable();
        links
    });
    put_option(out, links.as_ref(), |out, links| names(out, links));
}

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

    fn u64(&mut self) -> Result<u64, String> {
        let bytes: [u8; 8] = self.take(8)?.try_into().map_err(|_| "u64".to_string())?;
        Ok(u64::from_le_bytes(bytes))
    }

    fn string(&mut self) -> Result<String, String> {
        let n = self.u64()? as usize;
        String::from_utf8(self.take(n)?.to_vec()).map_err(|e| e.to_string())
    }

    fn option<T>(
        &mut self,
        read: impl FnOnce(&mut Self) -> Result<T, String>,
    ) -> Result<Option<T>, String> {
        match self.take(1)?[0] {
            0 => Ok(None),
            _ => read(self).map(Some),
        }
    }

    fn list(
        &mut self,
        mut each: impl FnMut(&mut Self, &str) -> Result<(), String>,
    ) -> Result<(), String> {
        for _ in 0..self.u64()? {
            let path = self.string()?;
            each(self, &path)?;
        }
        Ok(())
    }

    fn names(&mut self) -> Result<Vec<String>, String> {
        (0..self.u64()?).map(|_| self.string()).collect()
    }

    fn info(&mut self) -> Result<Option<FileInfo>, String> {
        self.option(|r| {
            let name = r.string()?;
            let size = r.u64()? as i64;
            let mode = FileMode(r.u64()? as u32);
            let mod_time = r.option(|r| {
                let bytes: [u8; 16] = r.take(16)?.try_into().map_err(|_| "u128".to_string())?;
                let nanos = u128::from_le_bytes(bytes);
                Ok(SystemTime::UNIX_EPOCH
                    + std::time::Duration::new(
                        (nanos / 1_000_000_000) as u64,
                        (nanos % 1_000_000_000) as u32,
                    ))
            })?;
            Ok(FileInfo {
                name,
                size,
                mode,
                mod_time,
            })
        })
    }

    fn entries(&mut self) -> Result<Entries, String> {
        let files = self.names()?;
        let directories = self.names()?;
        let symlinks = self.option(|r| Ok(r.names()?.into_iter().collect()))?;
        Ok(Entries {
            files,
            directories,
            symlinks,
        })
    }
}
