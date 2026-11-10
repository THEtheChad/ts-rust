//! Port of Go `tspath/extensionrewrite.go` (ts#64581).
//!
//! PORT: Go `CaseSensitivity` is the port's `use_case_sensitive_file_names`
//! flag. Go `RootedFilePath` and `ModuleSpecifier` are `&str`, so their
//! methods are free functions. A Go `(value, bool)` result is an `Option`:
//! `None` is Go's unchanged value with `false`.

use super::extension::{has_ts_file_extension, is_declaration_file_name};
use super::path::path_is_relative;
use crate::frontend::stringutil_ls::equal_fold;

// Go: tspath/extensionrewrite.go:3 ExtensionRewrite
/// One content mapper output extension: a file or specifier that ends with
/// `source` gets `target` in its place.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct ExtensionRewrite {
    pub source: String,
    pub target: String,
}

// Go: tspath/extensionrewrite.go:8 getExtensionRewrite
// PORT: Go compares the bytes of the path suffix. A suffix that does not
// start on a char boundary cannot be a Rust `&str`; a valid source never
// starts with a continuation byte, so it does not match.
fn get_extension_rewrite<'a>(
    path: &str,
    rewrites: &'a [ExtensionRewrite],
    use_case_sensitive_file_names: bool,
) -> Option<&'a ExtensionRewrite> {
    let mut best: Option<&ExtensionRewrite> = None;
    for rewrite in rewrites {
        let best_len = best.map_or(0, |best| best.source.len());
        if rewrite.source.len() <= best_len || path.len() <= rewrite.source.len() {
            continue;
        }
        let start = path.len() - rewrite.source.len();
        if !path.is_char_boundary(start) {
            continue;
        }
        if equal(
            &path[start..],
            &rewrite.source,
            use_case_sensitive_file_names,
        ) {
            best = Some(rewrite);
        }
    }
    best
}

/// Go `CaseSensitivity.getEqualityComparer()(a, b)` (tspath/path.go:1057):
/// `==`, or Go `strings.EqualFold` when the case is ignored.
fn equal(a: &str, b: &str, use_case_sensitive_file_names: bool) -> bool {
    if use_case_sensitive_file_names {
        return a == b;
    }
    equal_fold(a.as_bytes(), b.as_bytes())
}

impl ExtensionRewrite {
    // Go: tspath/extensionrewrite.go:22 ExtensionRewrite.isIdentity
    fn is_identity(&self, use_case_sensitive_file_names: bool) -> bool {
        equal(&self.source, &self.target, use_case_sensitive_file_names)
    }
}

// Go: tspath/extensionrewrite.go:26 RootedFilePath.GetExtensionRewrite
/// The rewrite with the longest source that ends `file`, if one does.
#[must_use]
pub fn get_file_extension_rewrite<'a>(
    file: &str,
    rewrites: &'a [ExtensionRewrite],
    use_case_sensitive_file_names: bool,
) -> Option<&'a ExtensionRewrite> {
    get_extension_rewrite(file, rewrites, use_case_sensitive_file_names)
}

// Go: tspath/extensionrewrite.go:30 RootedFilePath.RewriteExtension
/// `file` with its rewritten extension. `None` when no rewrite matches or the
/// match is an identity rewrite.
#[must_use]
pub fn rewrite_file_extension(
    file: &str,
    rewrites: &[ExtensionRewrite],
    use_case_sensitive_file_names: bool,
) -> Option<String> {
    let rewrite = get_file_extension_rewrite(file, rewrites, use_case_sensitive_file_names)?;
    if rewrite.is_identity(use_case_sensitive_file_names) {
        return None;
    }
    // Go `f.RemoveExtension(suffix).AppendSuffix(rewrite.Target)`, where the
    // suffix is the matched end of the path.
    let stem = &file[..file.len() - rewrite.source.len()];
    Some(format!("{stem}{}", rewrite.target))
}

// Go: tspath/extensionrewrite.go:39 ModuleSpecifier.RewriteExtension
/// `specifier` with its rewritten extension. `None` when no rewrite matches
/// or the match is an identity rewrite.
#[must_use]
pub fn rewrite_module_specifier_extension(
    specifier: &str,
    rewrites: &[ExtensionRewrite],
    use_case_sensitive_file_names: bool,
) -> Option<String> {
    let rewrite = get_extension_rewrite(specifier, rewrites, use_case_sensitive_file_names)?;
    if rewrite.is_identity(use_case_sensitive_file_names) {
        return None;
    }
    let stem = &specifier[..specifier.len() - rewrite.source.len()];
    Some(format!("{stem}{}", rewrite.target))
}

// Go: tspath/extensionrewrite.go:47 ModuleSpecifier.ShouldRewriteExtension
/// Whether a relative, non-declaration `specifier` gets a new extension: by a
/// non-identity rewrite, or else because it has a TS extension.
#[must_use]
pub fn should_rewrite_module_specifier_extension(
    specifier: &str,
    rewrites: &[ExtensionRewrite],
    use_case_sensitive_file_names: bool,
) -> bool {
    if !path_is_relative(specifier) || is_declaration_file_name(specifier) {
        return false;
    }
    if let Some(rewrite) = get_extension_rewrite(specifier, rewrites, use_case_sensitive_file_names)
    {
        return !rewrite.is_identity(use_case_sensitive_file_names);
    }
    has_ts_file_extension(specifier)
}
