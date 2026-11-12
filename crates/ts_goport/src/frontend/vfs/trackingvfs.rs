//! Go: internal/vfs/trackingvfs/trackingvfs.go
//!
//! Package trackingvfs provides a VFS wrapper that records every file path
//! accessed during compilation. This allows watch mode to know exactly which
//! files and directories the compiler depended on, including non-existent
//! paths from failed module resolution.

use crate::frontend::prelude::*;

// Go: trackingvfs.go:18 FS
/// FS wraps a vfs.FS and records every path accessed via read-like operations.
/// Write operations (WriteFile, Remove, Chtimes) are not tracked since they
/// represent outputs, not dependencies.
///
/// PORT: Go `collections.SyncSet[string]` is a `RefCell<IndexSet<String>>`
/// (one thread). Go `SyncSet.ToSlice` ranges a `sync.Map`, so its order is
/// random; the port keeps insertion order.
pub struct FS {
    pub inner: Rc<dyn Fs>,
    pub seen_files: RefCell<IndexSet<String>>,
}

// Go: trackingvfs.go:23 `var _ vfs.FS = (*FS)(nil)`
impl Fs for FS {
    // Go: vfs/trackingvfs/trackingvfs.go:35 (*FS).CaseSensitivity (ts#64159
    // renamed UseCaseSensitiveFileNames; the port keeps that name and returns
    // the bool)
    fn use_case_sensitive_file_names(&self) -> bool {
        self.inner.use_case_sensitive_file_names()
    }

    // Go: trackingvfs.go:30 (*FS).FileExists
    fn file_exists(&self, path: &str) -> bool {
        self.seen_files.borrow_mut().insert(path.to_string());
        self.inner.file_exists(path)
    }

    // Go: trackingvfs.go:25 (*FS).ReadFile
    fn read_file(&self, path: &str) -> (String, bool) {
        self.seen_files.borrow_mut().insert(path.to_string());
        self.inner.read_file(path)
    }

    // Go: trackingvfs.go:37 to :50 (*FS).WriteFile, AppendFile, Remove and
    // Chtimes: not tracked.
    crate::frontend::vfs::fs_write_through!(inner);

    // Go: trackingvfs.go:51 (*FS).DirectoryExists
    fn directory_exists(&self, path: &str) -> bool {
        self.seen_files.borrow_mut().insert(path.to_string());
        self.inner.directory_exists(path)
    }

    // Go: trackingvfs.go:56 (*FS).GetAccessibleEntries
    fn get_accessible_entries(&self, path: &str) -> Entries {
        self.seen_files.borrow_mut().insert(path.to_string());
        self.inner.get_accessible_entries(path)
    }

    // Go: trackingvfs.go:61 (*FS).Stat
    fn stat(&self, path: &str) -> Option<FileInfo> {
        self.seen_files.borrow_mut().insert(path.to_string());
        self.inner.stat(path)
    }

    // Go: trackingvfs.go:66 (*FS).Realpath
    fn realpath(&self, path: &str) -> String {
        self.seen_files.borrow_mut().insert(path.to_string());
        self.inner.realpath(path)
    }
}
