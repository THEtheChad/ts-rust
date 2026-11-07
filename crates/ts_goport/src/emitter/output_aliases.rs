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
//!   included), and none is a proper ancestor of it after case folding
//!   (`out/p.js` and `out/p.js/x.js`: one write makes a file where the
//!   other makes a directory), `names_collide`;
//! - no output names a directory entry that looks like an 8.3 short name
//!   (`AB00CD~1.js`) beside another output's entry in that directory:
//!   Windows gives a new file a short name when it makes it, so whether
//!   `AB00CD~1.js` opens `ab00cdefgh.js` depends on which write comes first,
//!   `short_names_may_alias`;
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

/// True when two outputs may name one entry, or one an ancestor directory
/// of another, on a case-insensitive file system. Two names are taken as
/// one when they are equal under full Unicode lowercasing or under full
/// Unicode uppercasing (Windows compares names through its uppercase
/// table). That errs toward "may collide", which costs only parallelism:
/// it groups `σ`, `ς` and `Σ` (equal uppercased, as on Windows), the
/// Kelvin sign `K` with `k` (equal lowercased), dotless `ı` with `i`
/// (equal uppercased), and `ß` with `ss` (equal uppercased; not one name on
/// any file system this port knows of). Dotted `İ` and `i` stay apart (no
/// folding makes them equal), as on Windows. O(n·d) for n outputs of d
/// components: a set of folded names and a lookup per ancestor.
fn names_collide(outputs: &[String]) -> bool {
    if short_names_may_alias(outputs) {
        return true;
    }
    for fold in [str::to_lowercase, str::to_uppercase] {
        let folded: Vec<String> = outputs.iter().map(|path| fold(path)).collect();
        let mut names = FxHashSet::default();
        if !folded.iter().all(|path| names.insert(path.as_str())) {
            return true;
        }
        let is_ancestor = |path: &String| {
            path.char_indices()
                .filter(|&(at, c)| at > 0 && (c == '/' || c == '\\'))
                .any(|(at, _)| names.contains(&path[..at]))
        };
        if folded.iter().any(is_ancestor) {
            return true;
        }
    }
    false
}

/// True when an output path names, in some directory, an entry that looks
/// like an 8.3 short name (a `~` then a digit, as in `AB00CD~1.JS`) and
/// another output names a different entry of that directory. NTFS (with
/// short names on, the default for a system drive) and FAT give a new
/// file a short name when it is made: if `out/ab00cdefgh.js` is made
/// first it gets `AB00CD~1.JS`, and a write of `out/AB00CD~1.js` then
/// opens it; made the other way round, the two are separate files. Which
/// comes first is the write schedule, so such a layout is not ordinary.
/// A short-looking name with no other output beside it (a home dir
/// `C:/Users/RUNNER~1`, above every output) cannot alias an output and
/// does not count. Names are compared uppercased, as Windows does. O(n·d)
/// for n outputs of d components.
fn short_names_may_alias(outputs: &[String]) -> bool {
    let looks_short = |name: &str| {
        name.as_bytes()
            .windows(2)
            .any(|pair| pair[0] == b'~' && pair[1].is_ascii_digit())
    };
    if !outputs.iter().any(|path| looks_short(path)) {
        return false;
    }
    // Each directory met (uppercased): one entry name in it, and whether
    // it holds a short-looking entry and more than one distinct entry.
    let mut dirs: FxHashMap<String, (String, bool, bool)> = FxHashMap::default();
    for path in outputs {
        let path = path.to_uppercase();
        let mut start = 0;
        for (at, c) in path.char_indices().chain([(path.len(), '/')]) {
            if c != '/' && c != '\\' {
                continue;
            }
            let name = &path[start..at];
            let dir = &path[..start];
            start = at + c.len_utf8();
            if name.is_empty() || dir.is_empty() {
                continue;
            }
            let (first, short, several) = dirs
                .entry(dir.to_owned())
                .or_insert_with(|| (name.to_owned(), false, false));
            *short |= looks_short(name);
            *several |= first != name;
            if *short && *several {
                return true;
            }
        }
    }
    false
}

/// Paths per check thread below which the check stays on the calling
/// thread.
const PATHS_PER_THREAD: usize = 512;

/// True when `outputs` lie in an ordinary layout (see the module): the
/// emit's writes may run in parallel. Any thread.
#[must_use]
pub fn ordinary_layout(outputs: &[String]) -> bool {
    if names_collide(outputs) {
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

/// The name rules, on every platform.
#[cfg(test)]
mod name_tests {
    use super::names_collide;

    /// An 8.3-looking name beside another output in its directory may be
    /// that output's short name (lane WIN: Windows gave two results in 30
    /// runs with `out/AB00CD~1.js` and `out/ab00cdefgh.js`).
    #[test]
    fn short_names_beside_other_outputs_may_alias() {
        let collide = |paths: &[&str]| {
            names_collide(
                &paths
                    .iter()
                    .map(|path| (*path).to_owned())
                    .collect::<Vec<_>>(),
            )
        };
        assert!(collide(&["C:/p/out/AB00CD~1.js", "C:/p/out/ab00cdefgh.js"]));
        assert!(collide(&["/p/out/ab00cdefgh.js", "/p/out/ab00cd~1.js"]));
        // A directory: `out/LONGDI~1/a.js` may be `out/longdirname/b.js`'s.
        assert!(collide(&[
            "C:/p/out/LONGDI~1/a.js",
            "C:/p/out/longdirname/b.js"
        ]));
        assert!(collide(&["C:\\p\\out\\LONGDI~1\\a.js", "C:\\p\\out\\b.js"]));
        // Alone in its directory, or above every output: no alias.
        assert!(!collide(&["C:/p/out/AB00CD~1.js"]));
        assert!(!collide(&[
            "C:/Users/RUNNER~1/p/out/a.js",
            "C:/Users/RUNNER~1/p/out/b.js",
        ]));
        assert!(!collide(&["C:/p/out/a~b.js", "C:/p/out/c.js"]));
        assert!(!collide(&["C:/p/out/a.js", "C:/p/out/b.js"]));
    }
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

    /// Astra D1-I3 round 3, P1: an output that is a proper ancestor of
    /// another (case-folded too) is not ordinary, though neither exists.
    #[test]
    fn an_output_that_is_another_output_s_directory_is_not_ordinary() {
        let dir = scratch("prefix");
        for paths in [
            &["out/p.js", "out/p.js/x.js"][..],
            &["out/p.js/x.js", "out/p.js"][..],
            &["out/a/P.JS", "out/a/p.js/deep/x.js"][..],
        ] {
            assert!(!ordinary_layout(&names(&dir, paths)), "{paths:?}");
        }
        // Siblings that only share a prefix of characters stay ordinary.
        assert!(ordinary_layout(&names(
            &dir,
            &["out/p.js", "out/p.jsx/x.js", "out/p/x.js"]
        )));
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// Astra D1-I3 round 3, P2: names equal under full Unicode lowercasing
    /// or uppercasing are taken as one (see `names_collide`).
    #[test]
    fn unicode_case_pairs() {
        use super::names_collide;
        let collide =
            |a: &str, b: &str| names_collide(&[format!("out/{a}.js"), format!("out/{b}.js")]);
        // Windows compares names uppercased: σ, ς and Σ are one name.
        assert!(collide("σ", "ς"));
        assert!(collide("Σ", "ς"));
        assert!(collide("σ", "Σ"));
        // Kelvin sign and k; dotless ı and i; ß and ss (conservative).
        assert!(collide("\u{212A}", "k"));
        assert!(collide("ı", "i"));
        assert!(collide("ß", "ss"));
        assert!(collide("Straße", "STRASSE"));
        // Dotted İ and i: no folding makes them equal.
        assert!(!collide("İ", "i"));
        assert!(!collide("a", "b"));
        // In a directory component too.
        assert!(names_collide(&[
            "out/σ/a.js".to_owned(),
            "out/ς/a.js".to_owned()
        ]));
        // And as an ancestor: `out/Σ` against `out/ς/a.js`.
        assert!(names_collide(&[
            "out/Σ".to_owned(),
            "out/ς/a.js".to_owned()
        ]));
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

/// Windows (Astra D1-I3 findings 3 and round 2; lane WIN): hard links,
/// links and junctions are not ordinary. Runs on the Windows CI job.
#[cfg(all(test, windows))]
mod windows_tests {
    use super::ordinary_layout;
    use std::path::{Path, PathBuf};

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "goport-output-layout-win-{tag}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("out")).unwrap();
        dir
    }

    fn names(dir: &Path, names: &[&str]) -> Vec<String> {
        names
            .iter()
            .map(|name| dir.join(name).to_string_lossy().into_owned())
            .collect()
    }

    /// `mklink /J`: std has no junctions, and they need no privilege.
    /// cmd reads a `/` in a path as a switch.
    fn junction(link: &Path, target: &Path) {
        let native = |path: &Path| path.to_string_lossy().replace('/', "\\");
        let status = std::process::Command::new("cmd")
            .args(["/c", "mklink", "/J"])
            .arg(native(link))
            .arg(native(target))
            .stdout(std::process::Stdio::null())
            .status()
            .unwrap();
        assert!(status.success(), "mklink /J {}", link.display());
    }

    #[test]
    fn hard_links_and_case_are_not_ordinary_on_windows() {
        let dir = scratch("hard");
        let path = |name: &str| dir.join(name).to_string_lossy().into_owned();
        assert!(ordinary_layout(&[path("out/a.js"), path("out/sub/b.js")]));
        std::fs::write(dir.join("out/shared.js"), "").unwrap();
        std::fs::hard_link(dir.join("out/shared.js"), dir.join("out/a.js")).unwrap();
        assert!(!ordinary_layout(&[path("out/a.js")]));
        assert!(!ordinary_layout(&[
            path("out/new/C.js"),
            path("out/new/c.js")
        ]));
        // Windows folds names through its uppercase table: σ, ς and Σ.
        assert!(!ordinary_layout(&[
            path("out/new/\u{3c3}.js"),
            path("out/new/\u{3c2}.js")
        ]));
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// Junctions and symlinks (to directories, to files, dangling) are
    /// reparse points, read without following them: not ordinary. A
    /// symlink needs Developer Mode or an elevated process (CI's runner);
    /// without either those cases are skipped.
    #[test]
    fn junctions_and_symlinks_are_not_ordinary() {
        let dir = scratch("links");
        std::fs::create_dir(dir.join("out/right")).unwrap();
        junction(&dir.join("out/left"), &dir.join("out/right"));
        junction(&dir.join("out/dangling"), &dir.join("out/missing"));
        assert!(!ordinary_layout(&names(&dir, &["out/left/f.js"])));
        assert!(!ordinary_layout(&names(&dir, &["out/dangling/f.js"])));
        assert!(ordinary_layout(&names(&dir, &["out/right/f.js"])));
        let symlinks = std::os::windows::fs::symlink_dir("right", dir.join("out/dirlink"))
            .and_then(|()| std::os::windows::fs::symlink_dir("gone", dir.join("out/dirdangling")))
            .and_then(|()| std::fs::write(dir.join("out/shared.js"), ""))
            .and_then(|()| std::os::windows::fs::symlink_file("shared.js", dir.join("out/a.js")))
            .and_then(|()| std::os::windows::fs::symlink_file("gone.js", dir.join("out/b.js")));
        match symlinks {
            Ok(()) => {
                for paths in [
                    &["out/dirlink/f.js"][..],
                    &["out/dirdangling/f.js"],
                    &["out/a.js"],
                    &["out/b.js"],
                ] {
                    assert!(!ordinary_layout(&names(&dir, paths)), "{paths:?}");
                }
            }
            // ERROR_PRIVILEGE_NOT_HELD: no Developer Mode, not elevated.
            Err(error) if error.raw_os_error() == Some(1314) => {}
            Err(error) => panic!("symlink: {error}"),
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// A file where a directory is needed, a directory at an output and
    /// an output that is another's directory are not ordinary. A file held
    /// open with no sharing still gives its attributes (the handle asks for
    /// `FILE_READ_ATTRIBUTES` only): an existing, locked output is ordinary.
    #[test]
    fn non_directories_and_locked_files() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = scratch("kinds");
        std::fs::write(dir.join("out/sub"), "").unwrap();
        std::fs::create_dir(dir.join("out/a.js")).unwrap();
        assert!(!ordinary_layout(&names(&dir, &["out/sub/a.js"])));
        assert!(!ordinary_layout(&names(&dir, &["out/a.js"])));
        assert!(!ordinary_layout(&names(
            &dir,
            &["out/p.js", "out/p.js/x.js"]
        )));
        std::fs::write(dir.join("out/locked.js"), "").unwrap();
        let held = std::fs::OpenOptions::new()
            .write(true)
            .share_mode(0)
            .open(dir.join("out/locked.js"))
            .unwrap();
        assert!(ordinary_layout(&names(&dir, &["out/locked.js"])));
        drop(held);
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// 8.3 short names (`short_names_may_alias`): on a volume that makes
    /// them, `out/ab00cdefgh.js` made first is also `out/AB00CD~1.js`.
    #[test]
    fn short_names_are_not_ordinary() {
        let dir = scratch("short");
        assert!(!ordinary_layout(&names(
            &dir,
            &["out/AB00CD~1.js", "out/ab00cdefgh.js"]
        )));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
