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
//! the files to emit (`get_output_paths_for_source_file`). It resolves each
//! path one component at a time, as the kernel does when the write opens
//! it, through every symlink, also a dangling one: a link whose target is
//! missing names that target, which the write (or the emit's directory
//! creation, `mkdir -p`) makes. The rest of a path below its deepest
//! existing directory is missing: the emit makes it as plain directories,
//! so it is kept lexically (`..` drops a component). A path is then keyed
//! by where its write lands:
//!
//! - an existing entry (after its links) by its file identity: device and
//!   inode on unix, volume serial number and file index on Windows;
//! - a missing one by the identity of its deepest existing directory and
//!   the missing rest, lowercased.
//!
//! So `out/left/f.js` with `out/left -> right` and no `out/right` keys as
//! `out/right/f.js` does, before the emit makes `out/right`.
//!
//! Two paths with one key alias. The keys group more than needed (the
//! lowercased rest groups names that only differ in case on a
//! case-sensitive directory): a write that waits for no reason still lands
//! where it would have, after the earlier writes of the files before it,
//! so grouping more never changes an output. Grouping less would, so the
//! scan errs on that side: a component that cannot be read, or a link
//! loop, keys the path by the directory where resolution stopped.
//!
//! A path that changes between the scan and the write (another process
//! makes a symlink meanwhile) is outside the contract, as for Go.

use crate::prelude::*;

use std::ffi::OsString;
use std::path::{Component, Path as OsPathBuf, PathBuf};

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

/// A file identity: device and inode (unix), volume serial number and
/// file index (Windows), or a hash of the lowercased canonical path where
/// neither exists.
type EntryId = (u64, u64);

/// The identity of the existing entry `path`, after its links.
#[cfg(unix)]
fn existing_id(path: &OsPathBuf) -> Option<EntryId> {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(path)
        .ok()
        .map(|meta| (meta.dev(), meta.ino()))
}

/// The identity of the existing entry `path`, after its links (Go
/// `os.SameFile` on Windows compares the same two numbers). Opens files
/// and directories (`FILE_FLAG_BACKUP_SEMANTICS`); a hard link of a file
/// shares its index.
#[cfg(windows)]
fn existing_id(path: &OsPathBuf) -> Option<EntryId> {
    let handle = winapi_util::Handle::from_path_any(path).ok()?;
    let info = winapi_util::file::information(&handle).ok()?;
    Some((info.volume_serial_number(), info.file_index()))
}

#[cfg(not(any(unix, windows)))]
fn existing_id(path: &OsPathBuf) -> Option<EntryId> {
    use std::hash::{Hash, Hasher};
    let path = std::fs::canonicalize(path).ok()?;
    let mut h = std::hash::DefaultHasher::new();
    path.to_string_lossy().to_lowercase().hash(&mut h);
    Some((0, h.finish()))
}

/// Where resolution stands: an existing directory (or entry) with no link
/// in its path, or a missing path below the deepest existing directory.
#[derive(Clone)]
enum Place {
    Existing(PathBuf),
    Missing { base: PathBuf, rest: Vec<OsString> },
}

/// The destinations of one thread's share of the paths, with the
/// resolution of each directory it met.
#[derive(Default)]
struct Scanner {
    directories: FxHashMap<PathBuf, Place>,
    all_regular: bool,
}

/// The most symlinks that one resolution follows (Linux `MAXSYMLINKS`).
const MAX_LINKS: usize = 40;

impl Scanner {
    fn new() -> Self {
        Self {
            directories: FxHashMap::default(),
            all_regular: true,
        }
    }

    fn destination(&mut self, path: &OsPathBuf) -> Destination {
        let mut links = 0;
        let place = match (path.parent(), path.file_name()) {
            (Some(parent), Some(name)) => {
                let dir = self.directory(parent, &mut links);
                self.step(dir, Component::Normal(name), &mut links, true)
            }
            _ => self.resolve(path, &mut links),
        };
        match place {
            Place::Existing(entry) => match existing_id(&entry) {
                Some(id) => Destination::Entry(id),
                None => self.missing_key(entry.parent().unwrap_or(&entry), &[]),
            },
            Place::Missing { base, rest } => self.missing_key(&base, &rest),
        }
    }

    /// The key of the missing path `rest` below the existing `base`.
    fn missing_key(&mut self, base: &OsPathBuf, rest: &[OsString]) -> Destination {
        let rest: Vec<String> = rest
            .iter()
            .map(|part| part.to_string_lossy().to_lowercase())
            .collect();
        Destination::Missing(existing_id(base).unwrap_or_default(), rest.join("/"))
    }

    /// The resolution of the directory `dir` (memoized).
    fn directory(&mut self, dir: &OsPathBuf, links: &mut usize) -> Place {
        if let Some(place) = self.directories.get(dir) {
            return place.clone();
        }
        let place = self.resolve(dir, links);
        self.directories.insert(dir.to_path_buf(), place.clone());
        place
    }

    /// Resolves `path` from its root, component by component.
    fn resolve(&mut self, path: &OsPathBuf, links: &mut usize) -> Place {
        let mut place = Place::Existing(PathBuf::new());
        for component in path.components() {
            place = self.step(place, component, links, false);
        }
        place
    }

    /// Resolves one more `component` from `place`. A link is followed
    /// (also a dangling one) and its target resolved from the link's
    /// directory. `last`: the component is the output itself, whose kind
    /// sets `all_regular`.
    fn step(&mut self, place: Place, component: Component, links: &mut usize, last: bool) -> Place {
        match component {
            Component::Prefix(_) | Component::RootDir => match place {
                Place::Existing(mut dir) => {
                    dir.push(component.as_os_str());
                    Place::Existing(dir)
                }
                missing => missing,
            },
            Component::CurDir => place,
            Component::ParentDir => match place {
                Place::Existing(dir) => Place::Existing(
                    dir.parent()
                        .map_or_else(|| dir.clone(), OsPathBuf::to_path_buf),
                ),
                // `base/rest` with one component less (`rest` is never
                // empty): back at `base` when it was the last.
                Place::Missing { base, mut rest } => {
                    rest.pop();
                    if rest.is_empty() {
                        Place::Existing(base)
                    } else {
                        Place::Missing { base, rest }
                    }
                }
            },
            Component::Normal(name) => match place {
                Place::Missing { base, mut rest } => {
                    rest.push(name.to_os_string());
                    Place::Missing { base, rest }
                }
                Place::Existing(dir) => {
                    let entry = dir.join(name);
                    let Ok(meta) = std::fs::symlink_metadata(&entry) else {
                        return Place::Missing {
                            base: dir,
                            rest: vec![name.to_os_string()],
                        };
                    };
                    if !meta.file_type().is_symlink() {
                        if last && !meta.file_type().is_file() {
                            self.all_regular = false;
                        }
                        return Place::Existing(entry);
                    }
                    if last {
                        self.all_regular = false;
                    }
                    *links += 1;
                    let target = match std::fs::read_link(&entry) {
                        Ok(target) if *links <= MAX_LINKS => target,
                        // A link loop or an unreadable link: the write
                        // fails wherever it is; key it here.
                        _ => {
                            return Place::Missing {
                                base: dir,
                                rest: vec![name.to_os_string()],
                            };
                        }
                    };
                    let mut place = if target.is_absolute() {
                        Place::Existing(PathBuf::new())
                    } else {
                        Place::Existing(dir)
                    };
                    for component in target.components() {
                        place = self.step(place, component, links, false);
                    }
                    place
                }
            },
        }
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

    /// Astra D1-I3 finding 1: a dangling directory link aliases the
    /// directory the emit makes for it, also through a missing chain, a
    /// `..` in the target, an absolute target and a link to a link.
    #[test]
    fn dangling_directory_links_alias_the_directories_the_emit_makes() {
        let dir = scratch("dirlinks");
        let out = dir.join("out");
        std::fs::create_dir(&out).unwrap();
        std::os::unix::fs::symlink("right", out.join("left")).unwrap();
        std::os::unix::fs::symlink("deep/er", out.join("chain")).unwrap();
        std::os::unix::fs::symlink("../out/up", out.join("dotdot")).unwrap();
        std::os::unix::fs::symlink(out.join("abs"), out.join("absolute")).unwrap();
        std::os::unix::fs::symlink("left", out.join("twice")).unwrap();
        let pairs = [
            ("out/left/f.js", "out/right/f.js"),
            ("out/chain/g.js", "out/deep/er/g.js"),
            ("out/dotdot/h.js", "out/up/h.js"),
            ("out/absolute/i.js", "out/abs/i.js"),
            ("out/twice/j.js", "out/right/j.js"),
            ("out/left/x/../k.js", "out/right/k.js"),
        ];
        for (a, b) in pairs {
            let outputs = names(&dir, &[a, b, "out/alone.js"]);
            let result = scan(&outputs);
            assert!(
                result.aliased.contains(&outputs[0]) && result.aliased.contains(&outputs[1]),
                "{a} and {b} share a file"
            );
            assert!(!result.aliased.contains(&outputs[2]), "{a}: alone.js");
        }
        std::fs::remove_dir_all(dir).unwrap();
    }
}

/// Windows file identity (Astra D1-I3 finding 3): hard links of one file
/// and paths through a directory symlink share the volume serial number
/// and file index. Runs on the Windows CI job; this host cannot run it.
#[cfg(all(test, windows))]
mod windows_tests {
    use super::scan;

    #[test]
    fn hard_links_and_case_alias_on_windows() {
        let dir =
            std::env::temp_dir().join(format!("goport-output-aliases-win-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("out")).unwrap();
        std::fs::write(dir.join("out/shared.js"), "").unwrap();
        std::fs::hard_link(dir.join("out/shared.js"), dir.join("out/a.js")).unwrap();
        std::fs::write(dir.join("out/b.js"), "").unwrap();
        let path = |name: &str| dir.join(name).to_string_lossy().into_owned();
        let outputs = vec![
            path("out/shared.js"),
            path("out/a.js"),
            path("out/b.js"),
            // Not made yet, two cases of one name.
            path("out/new/C.js"),
            path("out/new/c.js"),
            path("out/new/d.js"),
        ];
        let result = scan(&outputs);
        for alias in &outputs[..2] {
            assert!(result.aliased.contains(alias), "{alias}");
        }
        assert!(!result.aliased.contains(&outputs[2]));
        assert!(result.aliased.contains(&outputs[3]) && result.aliased.contains(&outputs[4]));
        assert!(!result.aliased.contains(&outputs[5]));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
