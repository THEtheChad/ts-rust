//! The mtime order that `tsc -b` on the real file system needs, for the tests
//! and the `goport_watch` test driver (each includes this file with
//! `#[path]`).
//!
//! Go `getUpToDateStatus` (build/buildtask.go; the port's `build_task.rs`)
//! decides by mtime: a project is up to date when its build info is newer
//! than its config, its extended configs and its package.json files (a source
//! newer than the build info is still up to date when its text hash matches
//! the build info's version). File mtimes come from the wall clock, and on
//! MS-A2 timesyncd steps it back by 1.1 to 1.9 s every 32 s
//! (`scripts/perf-rearch/clock-steps.py`); a file system with coarse
//! timestamps also gives a write right after another one the same mtime. So
//! the order of two writes is not the order of their mtimes, and Go does the
//! same. A test that needs an order makes it:
//! - an edit after a build that must be newer than the build's outputs is
//!   written until it is (`write_until_after` of `newest_mtime`);
//! - an input written before a build that must stay older than the build's
//!   outputs gets an old mtime (`go_baselines`'s `support::set_old_mtimes`).

use std::path::Path;
use std::time::{Duration, SystemTime};

/// Runs `write`, which writes `path`, until `path` gets a later mtime than
/// `time` (often `newest_mtime` of the project). The loop ends when the wall
/// clock passes `time`. Each write before the last one is not reported to a
/// watch: only the last counts.
pub fn write_until_after(path: &Path, time: SystemTime, mut write: impl FnMut()) {
    loop {
        write();
        if mtime(path) > time {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// The latest mtime of a file under `dir`. Symbolic links are not followed.
pub fn newest_mtime(dir: &Path) -> SystemTime {
    let entries = std::fs::read_dir(dir).unwrap_or_else(|e| panic!("read {}: {e}", dir.display()));
    entries
        .map(|entry| {
            let entry = entry.unwrap_or_else(|e| panic!("read {}: {e}", dir.display()));
            let path = entry.path();
            match entry.file_type() {
                Ok(kind) if kind.is_dir() => newest_mtime(&path),
                _ => mtime(&path),
            }
        })
        .max()
        .unwrap_or(SystemTime::UNIX_EPOCH)
}

/// The mtime of `path` itself (a link's own time).
fn mtime(path: &Path) -> SystemTime {
    std::fs::symlink_metadata(path)
        .and_then(|metadata| metadata.modified())
        .unwrap_or_else(|e| panic!("mtime of {}: {e}", path.display()))
}
