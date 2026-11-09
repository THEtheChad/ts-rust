//! Crate root of `goport_util`: the bottom part of the goport port (Go std
//! pieces, `tspath`, `vfs`, `json`, `fswatch`, `jsonrpc`, `diag`, `locale`,
//! and the syntax model `astdata`, the message catalog `diagnostics` and
//! `jsnum`, which were the crates `ts_ast`, `ts_core`, `ts_diagnostics` and
//! `ts_jsnum`).
//! Its files stay in `crates/ts_goport/src` and keep their module paths.
//! `ts_goport` re-exports each module at its old path, so `crate::` paths in
//! these files and `ts_goport::` paths outside do not change.
//!
//! Two shim modules keep the old paths of the items that moved here:
//! `core` (`gopanic.rs`: `GoPanic`, `go_panic`, `unported!`) and
//! `scanner_util` (`gostring.rs`: the Go string helpers). The upper
//! `core.rs` and `scanner_util.rs` re-export them.

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

pub mod astdata;
#[path = "gopanic.rs"]
pub mod core;
pub mod diag;
pub mod diagnostics;
mod flags_macros;
pub mod fswatch;
pub mod gostd;
pub mod jsnum;
pub mod jsonrpc;
pub mod locale;
#[path = "util_prelude.rs"]
pub mod prelude;
#[path = "gostring.rs"]
pub mod scanner_util;

/// The util part of `crate::frontend`. `ts_goport`'s `frontend/mod.rs`
/// re-exports each module.
pub mod frontend {
    pub mod bundled;
    pub mod core_bfs;
    pub mod core_binarysearch;
    pub mod core_context;
    pub mod core_nodemodules;
    pub mod core_workgroup;
    pub mod json;
    pub mod json_ext;
    pub mod json_indexmap;
    pub mod nativepath;
    pub mod osutil;
    #[path = "util_prelude.rs"]
    pub mod prelude;
    pub mod semver;
    pub mod stringutil_ls;
    pub mod tspath;
    pub mod vfs;
}
