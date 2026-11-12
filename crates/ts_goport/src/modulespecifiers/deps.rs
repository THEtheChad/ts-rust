//! The parts of Go `internal/module`, `internal/core` (pattern.go) and
//! `internal/stringutil` that modulespecifiers uses.
//!
//! The `module` and `core` functions are the frontend ports, re-exported.
//! `index_after` has no other port. `has_prefix`, `has_suffix` and
//! `has_prefix_and_suffix_without_overlap` differ from the frontend's
//! (`frontend::stringutil_ls`): see the PORT note on `has_prefix`.

use crate::prelude::*;

pub use crate::frontend::module::{
    Pattern, get_conditions, get_package_name_from_types_package_name,
    is_applicable_versioned_types_key, match_pattern_or_exact, try_get_js_extension_for_file,
    try_parse_patterns,
};
pub use crate::scanner_util::compare_booleans;

// Go: core/core.go:714 IndexAfter
pub fn index_after(s: &str, pattern: &str, start_index: usize) -> isize {
    match s.get(start_index..).and_then(|rest| rest.find(pattern)) {
        None => -1,
        Some(matched) => (matched + start_index) as isize,
    }
}

// Go: stringutil/compare.go:74 HasPrefix
// PORT: compares the Go bytes of the port forms (see
// `scanner_util::GO_STRING_MARKER`).
// PORT: the frontend port (`frontend::stringutil_ls::has_prefix`) differs.
// Go slices by byte length and folds with `strings.EqualFold` (Unicode).
// This one has Go's bytes but folds ASCII only, so it differs from Go for a
// case-insensitive name that differs by a non-ASCII case. The frontend one
// folds as Go does but compares the port forms' own bytes, so it differs
// from Go even case-sensitively for names that hold an invalid UTF-8 byte or
// a lone surrogate. Its encoding of byte 0x80 in `b"x\x80.ts"` matches the
// suffix `"\u{10F780}.ts"`, which Go's bytes do not. Lane B10 fixes both
// (`docs/perf-rearch/cleanup/f5.md`).
pub fn has_prefix(s: &str, prefix: &str, case_sensitive: bool) -> bool {
    let (s, prefix) = (go_string_bytes(s), go_string_bytes(prefix));
    if case_sensitive {
        return s.starts_with(&prefix);
    }
    if prefix.len() > s.len() {
        return false;
    }
    s[..prefix.len()].eq_ignore_ascii_case(&prefix)
}

// Go: stringutil/compare.go:84 HasSuffix
// PORT: see `has_prefix`.
pub fn has_suffix(s: &str, suffix: &str, case_sensitive: bool) -> bool {
    let (s, suffix) = (go_string_bytes(s), go_string_bytes(suffix));
    if case_sensitive {
        return s.ends_with(&suffix);
    }
    if suffix.len() > s.len() {
        return false;
    }
    s[s.len() - suffix.len()..].eq_ignore_ascii_case(&suffix)
}
// PORT: Go uses strings.EqualFold (Unicode simple folding). This compares
// ASCII case only, which matches for the ASCII paths in practice.

// Go: stringutil/compare.go:94 HasPrefixAndSuffixWithoutOverlap
pub fn has_prefix_and_suffix_without_overlap(
    s: &str,
    prefix: &str,
    suffix: &str,
    case_sensitive: bool,
) -> bool {
    if go_len(prefix) + go_len(suffix) > go_len(s) {
        return false;
    }
    has_prefix(s, prefix, case_sensitive) && has_suffix(s, suffix, case_sensitive)
}
