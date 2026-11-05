//! PORT: not in Go (determinism). The outputs of one emit that may share a
//! file system destination.
//!
//! Go `Program.Emit` writes each file's outputs inside a parallel work
//! group, so when two output paths name one file (a symlink or a hard
//! link to one inode, a symlinked directory, a case-insensitive directory,
//! or one path emitted by two source files) the content that is left
//! depends on the goroutine schedule: Go is not deterministic there. The
//! port must give one answer, and the one it gives is the one Go gives
//! when its goroutines happen to run in file order: the outputs written
//! one after the other in program file order, each file's in its emitter
//! order (`program_emit::DeferredWrites::finish`).
//!
//! `scan` finds those outputs before the emit, from the output paths of
//! the files to emit (`get_output_paths_for_source_file`). It keys each
//! path by where a write of it lands:
//!
//! - a path that exists (after its symlinks) by its device and inode;
//! - a path that does not exist (or a dangling symlink, after its links)
//!   by the device and inode of its nearest existing ancestor directory
//!   and the rest of the path, lowercased.
//!
//! Two paths with one key alias. The keys group more than needed (the
//! lowercased rest groups names that only differ in case on a
//! case-sensitive directory): a write that waits for no reason still lands
//! where it would have, after the earlier writes of the files before it,
//! so grouping more never changes an output. Grouping less would, so the
//! scan errs on that side: a path that cannot be read is keyed by its
//! nearest ancestor that can.
//!
//! A path that changes between the scan and the write (another process
//! makes a symlink meanwhile) is outside the contract, as for Go.

use crate::prelude::*;

use std::path::{Path as OsPathBuf, PathBuf};

use crate::frontend::outputpaths::ForceEmitPaths;
use crate::frontend::vfs::os_path;

/// The result of `scan`.
#[derive(Default)]
pub struct OutputScan {
    /// False when an output path exists and is not a regular file (a
    /// symlink, a directory, a FIFO, a device, a socket).
    pub all_regular: bool,
    /// The output names whose destination another output of the emit may
    /// share (or that two files of the emit write).
    pub aliased: FxHashSet<String>,
}

/// Where a write of an output path lands.
#[derive(Clone, PartialEq, Eq, Hash)]
enum Destination {
    /// An existing file system entry.
    Entry(EntryId),
    /// A missing entry: the existing directory that its write makes it
    /// in (with the missing directories), and the rest of its path,
    /// lowercased.
    Missing(EntryId, String),
}

/// A device and inode (unix), or the canonical path (elsewhere).
#[cfg(unix)]
type EntryId = (u64, u64);
#[cfg(not(unix))]
type EntryId = PathBuf;

#[cfg(unix)]
fn entry_id(meta: &std::fs::Metadata) -> EntryId {
    use std::os::unix::fs::MetadataExt;
    (meta.dev(), meta.ino())
}

/// The id of the existing entry `path`, after its symlinks.
#[cfg(unix)]
fn existing_id(path: &OsPathBuf) -> Option<EntryId> {
    std::fs::metadata(path).ok().map(|meta| entry_id(&meta))
}

#[cfg(not(unix))]
fn existing_id(path: &OsPathBuf) -> Option<EntryId> {
    std::fs::canonicalize(path)
        .ok()
        .map(|path| PathBuf::from(path.to_string_lossy().to_lowercase()))
}

/// The destinations of one thread's share of the paths, with a cache of
/// the ancestor directories it looked up.
#[derive(Default)]
struct Scanner {
    directories: FxHashMap<PathBuf, Option<EntryId>>,
    all_regular: bool,
}

/// The most symlinks that `destination` follows (Linux `MAXSYMLINKS`).
const MAX_LINKS: usize = 40;

impl Scanner {
    fn new() -> Self {
        Self {
            directories: FxHashMap::default(),
            all_regular: true,
        }
    }

    fn destination(&mut self, path: &OsPathBuf) -> Destination {
        let mut path = path.to_path_buf();
        for _ in 0..MAX_LINKS {
            let Ok(meta) = std::fs::symlink_metadata(&path) else {
                return self.missing(&path);
            };
            if meta.file_type().is_symlink() {
                self.all_regular = false;
                if let Some(id) = existing_id(&path) {
                    return Destination::Entry(id);
                }
                // A dangling link: the write makes its target.
                let Ok(target) = std::fs::read_link(&path) else {
                    return self.missing(&path);
                };
                path = match path.parent() {
                    Some(parent) => parent.join(target),
                    None => target,
                };
                continue;
            }
            if !meta.file_type().is_file() {
                self.all_regular = false;
            }
            #[cfg(unix)]
            return Destination::Entry(entry_id(&meta));
            #[cfg(not(unix))]
            return match existing_id(&path) {
                Some(id) => Destination::Entry(id),
                None => self.missing(&path),
            };
        }
        // A link loop: the write fails wherever it is.
        self.missing(&path)
    }

    /// The destination of the missing entry `path`: its nearest existing
    /// ancestor and the rest, lowercased.
    fn missing(&mut self, path: &OsPathBuf) -> Destination {
        for ancestor in path.ancestors().skip(1) {
            let id = self
                .directories
                .entry(ancestor.to_path_buf())
                .or_insert_with(|| existing_id(ancestor))
                .clone();
            if let Some(id) = id {
                let rest = path
                    .strip_prefix(ancestor)
                    .map_or_else(|_| path.to_string_lossy(), |rest| rest.to_string_lossy());
                return Destination::Missing(id, rest.to_lowercase());
            }
        }
        Destination::Missing(Default::default(), path.to_string_lossy().to_lowercase())
    }
}

/// The output paths of `files` (each with its force flags), in file order,
/// each file's in its emitter's write order. Loading thread (it reads the
/// program and the emit host).
pub fn output_paths(files: &[Node], force: impl Fn(Node) -> ForceEmitPaths) -> Vec<String> {
    let host = crate::program::new_emit_host_without_checker();
    let mut outputs = Vec::new();
    for &file in files {
        let paths =
            crate::program::get_output_paths_for_source_file(file, host.as_ref(), force(file));
        for path in [
            paths.source_map_file_path(),
            paths.js_file_path(),
            paths.declaration_map_path(),
            paths.declaration_file_path(),
        ] {
            if !path.is_empty() {
                outputs.push(path.to_owned());
            }
        }
    }
    outputs
}

/// Paths per scan thread below which the scan stays on the calling thread.
const PATHS_PER_THREAD: usize = 512;

/// The scan of `outputs` (see the module): which may share a destination,
/// and whether each existing one is a regular file. Any thread.
#[must_use]
pub fn scan(outputs: &[String]) -> OutputScan {
    let threads = std::thread::available_parallelism()
        .map_or(1, std::num::NonZeroUsize::get)
        .clamp(1, 8)
        .min(outputs.len().div_ceil(PATHS_PER_THREAD))
        .max(1);
    let chunk = outputs.len().div_ceil(threads).max(1);
    let scan_chunk = |paths: &[String]| {
        let mut scanner = Scanner::new();
        let destinations: Vec<Destination> = paths
            .iter()
            .map(|path| scanner.destination(&os_path(path)))
            .collect();
        (scanner.all_regular, destinations)
    };
    let parts: Vec<(bool, Vec<Destination>)> = if threads == 1 {
        vec![scan_chunk(outputs)]
    } else {
        std::thread::scope(|scope| {
            let workers: Vec<_> = outputs
                .chunks(chunk)
                .map(|paths| scope.spawn(move || scan_chunk(paths)))
                .collect();
            workers
                .into_iter()
                .map(|worker| {
                    worker
                        .join()
                        .unwrap_or_else(|payload| std::panic::resume_unwind(payload))
                })
                .collect()
        })
    };
    let mut all_regular = true;
    let mut first: FxHashMap<Destination, usize> = FxHashMap::default();
    let mut aliased = FxHashSet::default();
    let destinations = parts.into_iter().flat_map(|(regular, destinations)| {
        all_regular &= regular;
        destinations
    });
    for (index, destination) in destinations.enumerate() {
        match first.entry(destination) {
            std::collections::hash_map::Entry::Vacant(entry) => {
                entry.insert(index);
            }
            std::collections::hash_map::Entry::Occupied(entry) => {
                aliased.insert(outputs[*entry.get()].clone());
                aliased.insert(outputs[index].clone());
            }
        }
    }
    OutputScan {
        all_regular,
        aliased,
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::scan;

    fn names(dir: &std::path::Path, names: &[&str]) -> Vec<String> {
        names
            .iter()
            .map(|name| dir.join(name).to_string_lossy().into_owned())
            .collect()
    }

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "goport-output-aliases-{tag}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn distinct_missing_and_existing_outputs_do_not_alias() {
        let dir = scratch("distinct");
        std::fs::write(dir.join("a.js"), "").unwrap();
        let outputs = names(&dir, &["a.js", "b.js", "out/c.js", "out/d.js"]);
        let result = scan(&outputs);
        assert!(result.all_regular);
        assert!(result.aliased.is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn links_directories_case_and_repeats_alias() {
        let dir = scratch("links");
        std::fs::create_dir(dir.join("out")).unwrap();
        std::fs::write(dir.join("out/shared"), "").unwrap();
        std::os::unix::fs::symlink("shared", dir.join("out/a.js")).unwrap();
        std::os::unix::fs::symlink("shared", dir.join("out/b.js")).unwrap();
        std::fs::hard_link(dir.join("out/shared"), dir.join("out/c.js")).unwrap();
        std::os::unix::fs::symlink("missing", dir.join("out/d.js")).unwrap();
        std::os::unix::fs::symlink("out", dir.join("out2")).unwrap();
        let outputs = names(
            &dir,
            &[
                "out/a.js",
                "out/b.js",
                "out/c.js",
                "out/d.js",
                "out/missing",
                "out/sub/e.js",
                "out2/sub/e.js",
                "out/F.js",
                "out/f.js",
                "out/g.js",
                "out/g.js",
                "out/h.js",
            ],
        );
        let result = scan(&outputs);
        assert!(!result.all_regular);
        let mut aliased: Vec<_> = result.aliased.iter().map(String::as_str).collect();
        aliased.sort_unstable();
        let mut expected: Vec<_> = outputs[..11].iter().map(String::as_str).collect();
        expected.sort_unstable();
        expected.dedup();
        assert_eq!(aliased, expected);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_large_scan_finds_aliases_across_threads() {
        let dir = scratch("large");
        let mut outputs: Vec<String> = (0..3000)
            .map(|i| dir.join(format!("f{i}.js")).to_string_lossy().into_owned())
            .collect();
        outputs.push(dir.join("F0.js").to_string_lossy().into_owned());
        let result = scan(&outputs);
        assert_eq!(result.aliased.len(), 2);
        assert!(result.aliased.contains(&outputs[0]));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
