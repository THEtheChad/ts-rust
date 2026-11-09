//! Crate root of `goport_lsproto`: Go `internal/lsp/lsproto` with the
//! generated protocol types. Its files stay in `crates/ts_goport/src/lsp` and
//! keep their module paths. `ts_goport`'s `lsp/mod.rs` re-exports `lsproto`.
//! The `goport_util` modules that lsproto files reach with `crate::` paths
//! are re-exported here.

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

pub use goport_util::{core, frontend, gostd, jsonrpc, scanner_util, unported};

/// The lsproto part of `crate::lsp`.
pub mod lsp {
    pub mod lsproto;
}
