pub mod baseline;
pub mod child;
pub mod contentmappertest;
pub mod fsbaselineutil;
pub mod harnessutil;
pub mod iovfs;
pub mod mock_watch_backend;
#[path = "../../common/mtime_order.rs"]
pub mod mtime_order;
pub mod patience;
pub mod readable_build_info;
pub mod runner;
mod smoke;
pub mod stringtestutil;
pub mod test_sys;
pub mod vfstest;
mod vfstest_test;

/// The mtime of `set_old_mtime`: 2001, older than any build of a test.
pub const OLD_MTIME: std::time::Duration = std::time::Duration::from_secs(1_000_000_000);

/// Sets the mtime of `path` to `OLD_MTIME` (after `UNIX_EPOCH`), so it is
/// older than every output of a later `tsc -b` on the real file system
/// whatever the wall clock does (see `mtime_order`).
// PORT: not in Go; the Go tests run on a test file system with its own clock.
pub fn set_old_mtime(path: &std::path::Path) {
    std::fs::File::options()
        .write(true)
        .open(path)
        .and_then(|file| file.set_modified(std::time::SystemTime::UNIX_EPOCH + OLD_MTIME))
        .unwrap_or_else(|e| panic!("set the mtime of {}: {e}", path.display()));
}

/// `set_old_mtime` for each regular file under `dir`: after it the files of
/// `dir` (the inputs and outputs of the builds so far) are older than every
/// later write. Links are not followed.
pub fn set_old_mtimes(dir: &std::path::Path) {
    let entries = std::fs::read_dir(dir).unwrap_or_else(|e| panic!("read {}: {e}", dir.display()));
    for entry in entries {
        let entry = entry.unwrap_or_else(|e| panic!("read {}: {e}", dir.display()));
        let kind = entry
            .file_type()
            .unwrap_or_else(|e| panic!("type of {}: {e}", entry.path().display()));
        if kind.is_dir() {
            set_old_mtimes(&entry.path());
        } else if kind.is_file() {
            set_old_mtime(&entry.path());
        }
    }
}

/// Go `filepath.EvalSymlinks`: the real path of `path`.
// PORT: `std::fs::canonicalize` gives a verbatim path on Windows
// (`\\?\C:\...`), which Go does not, and which takes no `/` separators.
pub fn eval_symlinks(path: impl AsRef<std::path::Path>) -> std::io::Result<std::path::PathBuf> {
    let real = std::fs::canonicalize(path)?;
    #[cfg(windows)]
    if let Some(rest) = real.to_str().and_then(|s| s.strip_prefix(r"\\?\"))
        && !rest.starts_with(r"UNC\")
    {
        return Ok(std::path::PathBuf::from(rest));
    }
    Ok(real)
}
