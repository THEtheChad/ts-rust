//! Crate root of `goport_core`: the port below the language service (binder,
//! checker, parser, emitter, `execute`, `effect`, ...). It is everything of
//! `ts_goport` but `api`, `cmd`, `format`, `ls`, `lsp` and `project`, which
//! stay in `ts_goport` (crate split step 2). An edit on either side rebuilds
//! only that side and the crates above it.
//! Its files stay in `crates/ts_goport/src` and keep their module paths.
//! `ts_goport` re-exports each module at its old path, so `crate::` paths in
//! these files and `ts_goport::` paths outside do not change.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_mut,
    clippy::pedantic,
    clippy::all,
    non_snake_case
)]
// The clippy::all lints that are at zero in this crate (code-quality lane
// Q4), so CI's clippy keeps them there. Keep the same list in the four goport
// crate roots (lib.rs, goport_core_root.rs, goport_util_root.rs,
// goport_lsproto_root.rs); add a lint once it reaches zero.
#![warn(
    clippy::assign_op_pattern,
    clippy::blocks_in_conditions,
    clippy::borrow_deref_ref,
    clippy::clone_on_copy,
    clippy::cloned_ref_to_slice_refs,
    clippy::collapsible_if,
    clippy::derivable_impls,
    clippy::doc_lazy_continuation,
    clippy::double_ended_iterator_last,
    clippy::empty_line_after_doc_comments,
    clippy::err_expect,
    clippy::excessive_precision,
    clippy::explicit_auto_deref,
    clippy::explicit_counter_loop,
    clippy::field_reassign_with_default,
    clippy::four_forward_slashes,
    clippy::identity_op,
    clippy::int_plus_one,
    clippy::items_after_test_module,
    clippy::len_zero,
    clippy::let_and_return,
    clippy::manual_contains,
    clippy::manual_find,
    clippy::manual_flatten,
    clippy::manual_is_ascii_check,
    clippy::manual_is_multiple_of,
    clippy::manual_pattern_char_comparison,
    clippy::manual_range_contains,
    clippy::manual_retain,
    clippy::manual_strip,
    clippy::manual_swap,
    clippy::manual_unwrap_or_default,
    clippy::map_entry,
    clippy::match_like_matches_macro,
    clippy::match_single_binding,
    clippy::needless_bool,
    clippy::needless_borrow,
    clippy::needless_borrows_for_generic_args,
    clippy::needless_late_init,
    clippy::needless_option_as_deref,
    clippy::needless_return,
    clippy::ok_expect,
    clippy::precedence,
    clippy::question_mark,
    clippy::redundant_closure,
    clippy::redundant_field_names,
    clippy::redundant_guards,
    clippy::redundant_locals,
    clippy::single_match,
    clippy::tabs_in_doc_comments,
    clippy::unit_arg,
    clippy::unnecessary_cast,
    clippy::unnecessary_get_then_check,
    clippy::unnecessary_lazy_evaluations,
    clippy::unnecessary_mut_passed,
    clippy::unnecessary_sort_by,
    clippy::unnecessary_to_owned,
    clippy::useless_conversion,
    clippy::vec_init_then_push,
    clippy::while_let_loop,
    clippy::write_with_newline
)]

// jemalloc does not build for wasm (see Cargo.toml).
#[cfg(all(feature = "jemalloc", target_family = "wasm"))]
compile_error!("build goport_core for wasm without its jemalloc feature, as crates/ts_wasm does");

pub mod ast;
pub use goport_util::astdata;
pub mod baseline;
pub mod binder;
pub mod checker;
// Go `internal/contentmapper` and `internal/spanmap` (tsgo#4712).
pub mod contentmapper;
// Lane Q3 owns this file and its cleanup; these locked-in lints still fire
// in it. Drop the allow when Q3's work lands.
#[allow(
    clippy::collapsible_if,
    clippy::derivable_impls,
    clippy::explicit_auto_deref,
    clippy::manual_is_multiple_of
)]
pub mod core;
pub mod declarations;
// Native Effect diagnostics (Effect-TS/tsgo port).
pub mod effect;
pub use goport_util::diag;
pub use goport_util::diagnostics;
pub mod diagnostics_loc;
pub mod emitter;
pub mod evaluator;
pub mod execute;
pub mod flags;
/// The leaked bump arena of a thread (AST and checker arenas).
pub mod leak_arena;
/// `go_enum!` and `go_flags!` (`#[macro_export]` in goport_util).
mod flags_macros {
    pub(crate) use goport_util::{go_enum, go_flags};
}
pub mod frontend;
// Go `internal/ipc` (tsgo#4712).
pub mod ipc;
// The jemalloc arena layout at start: Linux only (THP).
#[cfg(all(feature = "jemalloc", target_os = "linux"))]
pub mod jemalloc_layout;
pub use goport_util::jsnum;
pub use goport_util::locale;
pub mod modulespecifiers;
pub mod options;
// The JSON form of `core.CompilerOptions` (moved out of `api` in bump B wave 3).
pub mod options_json;
pub mod pprof;
pub mod prelude;
pub mod printer;
// Lane Q3 owns this file and its cleanup; these locked-in lints still fire
// in it. Drop the allow when Q3's work lands.
#[allow(
    clippy::collapsible_if,
    clippy::manual_contains,
    clippy::needless_borrow,
    clippy::useless_conversion
)]
pub mod program;
pub mod pseudochecker;
pub mod scanner_util;
pub mod sourcemap;
pub mod spanmap;
/// Test builds only: one test thread at a time builds node stores.
#[cfg(test)]
mod test_lock;
pub mod thp_guard;
pub mod tracing;
pub mod transformers;
pub mod transpile;
pub mod tuning;

// Macros that goport_util exports. `crate::go_assert` and `crate::unported`
// keep working.
pub use goport_util::{go_assert, unported};

pub mod astnav;
pub use goport_util::{fswatch, gostd, jsonrpc};
