//! Go `internal/execute/tsc`. `tsgo`, `goport`, `goport_emit` and
//! `goport_build` all compile and report through this module, as Go `tsc`
//! does.

pub mod compile;
// init_local keeps one nested if: the parity gate pins the line of
// go_repeat's panic further down this file (diagnostics.rs:1383,
// grandfathered fuzz/1:3909 and five more). Re-pin them there before moving
// that line.
#[allow(clippy::collapsible_if)]
pub mod diagnostics;
pub mod emit;
pub mod help;
pub mod init;
pub mod statistics;
pub mod stdio;

pub use compile::*;
pub use diagnostics::*;
pub use emit::*;
pub use help::*;
pub use init::*;
pub use statistics::*;
