//! PORT: not in Go (determinism). Whether an emit's outputs lie in an
//! ordinary layout, where their writes may run in parallel.
//!
//! Go `Program.Emit` writes each file's outputs inside a parallel work
//! group. When two outputs reach one file, or one output's directory
//! creation decides whether another's write fails (a symlink on the way, a
//! file where a directory is needed, a hard link, names that differ only in
//! case), what is left depends on the goroutine schedule: Go is not
//! deterministic there. The port must give one answer on every run.
//!
//! Rather than model which writes depend on each other, `ordinary_layout`
//! accepts only a layout where none can: then the writes stay parallel.
//! Any other layout writes everything one at a time in program file order,
//! each file's in its emitter's order, directory creation included
//! (`program_emit::DeferredWrites::finish`). That is the schedule of Go's
//! goroutines running in file order, so the answer is one that Go gives.
//!
//! A layout is ordinary when, for every output path:
//! - no existing component of it is a symlink (dangling ones included; on
//!   Windows also a junction or other name-surrogate reparse point);
//! - every existing ancestor is a directory;
//! - the output does not exist, or is a regular file with one link;
//! - no other output equals it after case folding (one path emitted twice
//!   included);
//! - every metadata read succeeds (a failed read is not ordinary: the check
//!   fails closed).
//!
//! A path that changes between the check and the write (another process
//! makes a symlink meanwhile) is outside the contract, as for Go.

use crate::prelude::*;

use std::path::{Path as OsPathBuf, PathBuf};

use crate::frontend::outputpaths::ForceEmitPaths;
use crate::frontend::vfs::os_path;

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

/// What `lstat` says of one existing component.
enum Entry {
    Missing,
    Directory,
    /// A regular file with one link.
    File,
    /// Anything else (a symlink, a hard-linked file, a FIFO, a device), or
    /// a failed read.
    Unordinary,
}

#[cfg(not(windows))]
fn entry(path: &OsPathBuf) -> Entry {
    match std::fs::symlink_metadata(path) {
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Entry::Missing,
        Err(_) => Entry::Unordinary,
        Ok(meta) => {
            let kind = meta.file_type();
            if kind.is_dir() {
                Entry::Directory
            } else if kind.is_file() && links(&meta) == 1 {
                Entry::File
            } else {
                Entry::Unordinary
            }
        }
    }
}

#[cfg(unix)]
fn links(meta: &std::fs::Metadata) -> u64 {
    std::os::unix::fs::MetadataExt::nlink(meta)
}

#[cfg(not(any(unix, windows)))]
fn links(_meta: &std::fs::Metadata) -> u64 {
    // No link count here: only one link can be told safely, so none is.
    2
}

/// Windows: the attributes of the entry itself (`FILE_FLAG_OPEN_REPARSE_POINT`,
/// so a link or junction is not followed), through a handle opened for
/// `FILE_READ_ATTRIBUTES` only, which a write-only or locked file still
/// grants; its link count comes from the same handle.
#[cfg(windows)]
fn entry(path: &OsPathBuf) -> Entry {
    use std::os::windows::fs::OpenOptionsExt;
    const FILE_READ_ATTRIBUTES: u32 = 0x80;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_ATTRIBUTE_DIRECTORY: u64 = 0x10;
    const FILE_ATTRIBUTE_REPARSE_POINT: u64 = 0x400;
    let file = std::fs::OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path);
    let file = match file {
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Entry::Missing,
        Err(_) => return Entry::Unordinary,
        Ok(file) => file,
    };
    let Ok(info) = winapi_util::file::information(&file) else {
        return Entry::Unordinary;
    };
    let attributes = info.file_attributes();
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        Entry::Unordinary
    } else if attributes & FILE_ATTRIBUTE_DIRECTORY != 0 {
        Entry::Directory
    } else if info.number_of_links() == 1
        && winapi_util::file::typ(&file).is_ok_and(|kind| kind.is_disk())
    {
        Entry::File
    } else {
        Entry::Unordinary
    }
}

/// One thread's share of the check, with the directories it has judged.
struct Checker {
    /// Each ancestor directory met: true when it and its ancestors are a
    /// directory or missing (no link, no non-directory, no failed read).
    directories: FxHashMap<PathBuf, bool>,
}

impl Checker {
    /// The directory `dir` and its ancestors may hold outputs: each is a
    /// directory or missing.
    fn directory_ok(&mut self, dir: &OsPathBuf) -> bool {
        if let Some(&ok) = self.directories.get(dir) {
            return ok;
        }
        let ok = match entry(dir) {
            Entry::Directory | Entry::Missing => {
                dir.parent().is_none_or(|parent| self.directory_ok(parent))
            }
            Entry::File | Entry::Unordinary => false,
        };
        self.directories.insert(dir.to_path_buf(), ok);
        ok
    }

    /// The output `path` lies in an ordinary layout (but for case
    /// collisions, which `ordinary_layout` checks across outputs).
    fn output_ok(&mut self, path: &OsPathBuf) -> bool {
        matches!(entry(path), Entry::Missing | Entry::File)
            && path.parent().is_none_or(|parent| self.directory_ok(parent))
    }
}

/// Paths per check thread below which the check stays on the calling
/// thread.
const PATHS_PER_THREAD: usize = 512;

/// True when `outputs` lie in an ordinary layout (see the module): the
/// emit's writes may run in parallel. Any thread.
#[must_use]
pub fn ordinary_layout(outputs: &[String]) -> bool {
    let mut folded = FxHashSet::default();
    if !outputs
        .iter()
        .all(|path| folded.insert(path.to_lowercase()))
    {
        return false;
    }
    let threads = std::thread::available_parallelism()
        .map_or(1, std::num::NonZeroUsize::get)
        .clamp(1, 8)
        .min(outputs.len().div_ceil(PATHS_PER_THREAD))
        .max(1);
    let chunk = outputs.len().div_ceil(threads).max(1);
    let check = |paths: &[String]| {
        let mut checker = Checker {
            directories: FxHashMap::default(),
        };
        paths.iter().all(|path| checker.output_ok(&os_path(path)))
    };
    if threads == 1 {
        return check(outputs);
    }
    std::thread::scope(|scope| {
        let workers: Vec<_> = outputs
            .chunks(chunk)
            .map(|paths| scope.spawn(move || check(paths)))
            .collect();
        workers.into_iter().fold(true, |ok, worker| {
            let chunk_ok = worker
                .join()
                .unwrap_or_else(|payload| std::panic::resume_unwind(payload));
            ok && chunk_ok
        })
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::ordinary_layout;
    use std::os::unix::fs::symlink;

    fn names(dir: &std::path::Path, names: &[&str]) -> Vec<String> {
        names
            .iter()
            .map(|name| dir.join(name).to_string_lossy().into_owned())
            .collect()
    }

    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("goport-output-layout-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("out")).unwrap();
        dir
    }

    #[test]
    fn plain_new_and_existing_outputs_are_ordinary() {
        let dir = scratch("plain");
        std::fs::write(dir.join("out/a.js"), "").unwrap();
        let outputs = names(
            &dir,
            &["out/a.js", "out/b.js", "out/sub/c.js", "out/sub/d.js"],
        );
        assert!(ordinary_layout(&outputs));
        // Many outputs: the check runs on several threads.
        let many: Vec<String> = (0..3000)
            .map(|i| {
                dir.join(format!("out/f{i}.js"))
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        assert!(ordinary_layout(&many));
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// Each of these makes the layout not ordinary.
    #[test]
    fn links_non_directories_hard_links_and_case_are_not_ordinary() {
        let cases: [(&str, &dyn Fn(&std::path::Path), &[&str]); 9] = [
            (
                "dangling dir link",
                &|d| symlink("right", d.join("out/left")).unwrap(),
                &["out/left/f.js", "out/right/g.js"],
            ),
            (
                "dir link",
                &|d| {
                    std::fs::create_dir(d.join("out/right")).unwrap();
                    symlink("right", d.join("out/left")).unwrap()
                },
                &["out/left/f.js"],
            ),
            (
                "link to a file",
                &|d| symlink("right.js", d.join("out/left")).unwrap(),
                &["out/left/f.js", "out/right.js"],
            ),
            (
                "file link",
                &|d| {
                    std::fs::write(d.join("out/shared"), "").unwrap();
                    symlink("shared", d.join("out/a.js")).unwrap()
                },
                &["out/a.js"],
            ),
            (
                "hard link",
                &|d| {
                    std::fs::write(d.join("out/shared"), "").unwrap();
                    std::fs::hard_link(d.join("out/shared"), d.join("out/a.js")).unwrap()
                },
                &["out/a.js"],
            ),
            (
                "file where a dir is needed",
                &|d| std::fs::write(d.join("out/sub"), "").unwrap(),
                &["out/sub/a.js"],
            ),
            (
                "dir at an output",
                &|d| std::fs::create_dir(d.join("out/a.js")).unwrap(),
                &["out/a.js"],
            ),
            ("case-only names", &|_| {}, &["out/a.js", "out/A.js"]),
            ("one path twice", &|_| {}, &["out/a.js", "out/a.js"]),
        ];
        for (label, setup, paths) in cases {
            let dir = scratch(&label.replace(' ', "-"));
            setup(&dir);
            assert!(!ordinary_layout(&names(&dir, paths)), "{label}");
            std::fs::remove_dir_all(dir).unwrap();
        }
    }

    /// An unreadable directory fails closed.
    #[test]
    fn an_unreadable_component_is_not_ordinary() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("unreadable");
        std::fs::create_dir(dir.join("out/locked")).unwrap();
        std::fs::set_permissions(
            dir.join("out/locked"),
            std::fs::Permissions::from_mode(0o000),
        )
        .unwrap();
        let readable = std::fs::read_dir(dir.join("out/locked")).is_ok();
        let result = ordinary_layout(&names(&dir, &["out/locked/a.js"]));
        std::fs::set_permissions(
            dir.join("out/locked"),
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        // Root reads anything; elsewhere the lstat of the output fails.
        assert!(readable || !result);
        std::fs::remove_dir_all(dir).unwrap();
    }
}

/// Windows (Astra D1-I3 findings 3 and round 2): hard links, links and
/// junctions are not ordinary. Runs on the Windows CI job; this host cannot
/// run it.
#[cfg(all(test, windows))]
mod windows_tests {
    use super::ordinary_layout;

    #[test]
    fn hard_links_and_case_are_not_ordinary_on_windows() {
        let dir =
            std::env::temp_dir().join(format!("goport-output-layout-win-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("out")).unwrap();
        let path = |name: &str| dir.join(name).to_string_lossy().into_owned();
        assert!(ordinary_layout(&[path("out/a.js"), path("out/sub/b.js")]));
        std::fs::write(dir.join("out/shared.js"), "").unwrap();
        std::fs::hard_link(dir.join("out/shared.js"), dir.join("out/a.js")).unwrap();
        assert!(!ordinary_layout(&[path("out/a.js")]));
        assert!(!ordinary_layout(&[
            path("out/new/C.js"),
            path("out/new/c.js")
        ]));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
