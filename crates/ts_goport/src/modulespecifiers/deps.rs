//! The parts of Go `internal/module`, `internal/core` (pattern.go) and
//! `internal/stringutil` that modulespecifiers uses.
//!
//! The `module` and `core` functions are the frontend ports, re-exported.
//! `index_after` has no other port. The `stringutil` functions are the
//! frontend's (`frontend::stringutil_ls`), re-exported.

use crate::prelude::*;

pub use crate::frontend::module::{
    Pattern, get_conditions, get_package_name_from_types_package_name,
    is_applicable_versioned_types_key, match_pattern_or_exact, try_get_js_extension_for_file,
    try_parse_patterns,
};
pub use crate::frontend::stringutil_ls::{
    has_prefix, has_prefix_and_suffix_without_overlap, has_suffix,
};
pub use crate::scanner_util::compare_booleans;

// Go: core/core.go:714 IndexAfter
pub fn index_after(s: &str, pattern: &str, start_index: usize) -> isize {
    match s.get(start_index..).and_then(|rest| rest.find(pattern)) {
        None => -1,
        Some(matched) => (matched + start_index) as isize,
    }
}
