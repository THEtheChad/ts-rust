//! Port of internal/modulespecifiers (compare.go, preferences.go,
//! specifiers.go, types.go, util.go).
//!
//! The package also needs small parts of internal/module,
//! internal/outputpaths, internal/packagejson and internal/symlinks. Those
//! packages are ported under `src/frontend/`; `deps.rs` re-exports the
//! parts that modulespecifiers calls. `packagejson.rs` is a thread-safe copy
//! of the frontend package.json model (see that file). `symlinks.rs` ports
//! Go `internal/symlinks` for both. `host.rs` holds the Go
//! `compiler.Program` methods that implement
//! `ModuleSpecifierGenerationHost`.

pub mod compare;
pub mod deps;
pub mod entrypoint_ending;
pub mod host;
pub mod packagejson;
pub mod preferences;
pub mod specifiers;
pub mod symlinks;
pub mod types;
pub mod util;

pub use compare::*;
pub use entrypoint_ending::*;
pub use host::ProgramHost;
pub use preferences::*;
pub use specifiers::*;
pub use types::*;
pub use util::*;

// PORT: the Go tspath and semver packages are ported under
// `src/frontend/`. This module reuses them so `Path` is one type across
// both.
pub use crate::frontend::semver;

/// Go `internal/tspath`, as used by this package.
pub mod tspath {
    pub use crate::frontend::tspath::extension::*;
    pub use crate::frontend::tspath::path::*;
}
