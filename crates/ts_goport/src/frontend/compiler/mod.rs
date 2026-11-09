//! Go package `compiler`: program construction and file loading.
pub mod file_include;
pub mod file_loader;
// Lane Q3 owns this file and its cleanup; these locked-in lints still fire
// in it. Drop the allow when Q3's work lands.
#[allow(clippy::needless_borrow)]
pub mod files_parser;
pub mod host;
pub mod include_processor;
#[cfg(feature = "diagnostics")]
pub mod load_dump;
pub mod load_times;
pub mod load_view_scope;
pub mod processing_diagnostic;
pub mod program_lookup;
pub mod program_new;
pub mod project_references;
// Lane Q3 owns this file and its cleanup; these locked-in lints still fire
// in it. Drop the allow when Q3's work lands.
#[allow(clippy::unnecessary_lazy_evaluations)]
pub mod resolve_ahead;
pub mod verify;
pub use file_include::*;
pub use file_loader::*;
pub use files_parser::*;
pub use host::*;
pub use include_processor::*;
pub use processing_diagnostic::*;
pub use program_lookup::*;
pub use program_new::*;
pub use project_references::*;
pub use verify::*;
