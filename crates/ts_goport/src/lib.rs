//! Faithful Rust port of the pinned typescript-go binder and checker.
//! Read `crates/ts_goport/PORTING.md` before editing.
//!
//! Three parts of `src` build as their own crates (`parts/`): `goport_util`
//! (`src/goport_util_root.rs`), `goport_lsproto`
//! (`src/goport_lsproto_root.rs`) and `goport_core`
//! (`src/goport_core_root.rs`: everything below the language service). Their
//! root files declare their modules; this crate re-exports them at the same
//! paths. This crate keeps the language service and the command line: `api`,
//! `cmd`, `format`, `ls`, `lsp` and `project`.

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
compile_error!("build ts_goport for wasm with --no-default-features, as crates/ts_wasm does");

// The port below the language service (crate split step 2): each module keeps its path.
pub use goport_core::{
    ast, astdata, astnav, baseline, binder, checker, contentmapper, core, declarations, diag,
    diagnostics, diagnostics_loc, emitter, evaluator, execute, flags, frontend, fswatch, gostd,
    ipc, jsnum, jsonrpc, leak_arena, locale, modulespecifiers, options, options_json, pprof,
    prelude, printer, program, pseudochecker, scanner_util, sourcemap, spanmap, thp_guard, tracing,
    transformers, transpile, tuning,
};
// The jemalloc arena layout at start: Linux only (THP).
#[cfg(all(feature = "jemalloc", target_os = "linux"))]
pub use goport_core::jemalloc_layout;
/// `go_enum!` and `go_flags!` (`#[macro_export]` in goport_util).
mod flags_macros {
    pub(crate) use goport_util::{go_enum, go_flags};
}
// Macros that goport_util exports. `crate::go_assert` and `crate::unported`
// keep working.
pub use goport_util::{go_assert, unported};
// Macros that goport_core exports (`#[macro_export]`), at their
// `ts_goport::` paths from before crate split step 2.
pub use goport_core::{args, effect_cached, effect_export_member};

// Language-service port (Go `internal/{ls,lsp,project,format,api}`) and the command line.
pub mod api;
pub mod cmd;
pub mod format;
pub mod ls;
pub mod lsp;
pub mod project;

/// Native Effect diagnostics (`goport_core::effect`) and Effect's language
/// service hooks. `effect::ls` (src/effect/ls) uses `ls`, so it builds in
/// this crate, at its old path.
pub mod effect {
    pub use goport_core::effect::*;
    pub mod ls;
}
