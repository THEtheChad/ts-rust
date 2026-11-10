//! The module references of a file's top-level statements, which the
//! import rules (`nodeBuiltinImport`, `obsoleteMatchImport`,
//! `obsoleteSchemaImport`) each walked the same way.

use crate::effect::typeparser::*;
use crate::prelude::*;

/// The string literal module specifiers that the top-level statements of
/// `sf` reference, in source order: an import declaration's specifier, an
/// export declaration's specifier when `include_exports` is set, and the
/// single argument of `require("...")` initializing a variable declaration.
pub fn top_level_module_references(sf: Node, include_exports: bool) -> Vec<Node> {
    let mut specifiers = Vec::new();
    for stmt in sf.statements().iter() {
        match stmt.kind() {
            SyntaxKind::ImportDeclaration => specifiers.extend(string_module_specifier(stmt)),
            SyntaxKind::ExportDeclaration if include_exports => {
                specifiers.extend(string_module_specifier(stmt));
            }
            SyntaxKind::VariableStatement => {
                let decl_list = stmt.declaration_list();
                if decl_list.is_nil() || decl_list.declarations().is_nil() {
                    continue;
                }
                specifiers.extend(
                    decl_list
                        .declarations()
                        .nodes()
                        .iter()
                        .filter_map(|decl| require_call_specifier(decl.initializer())),
                );
            }
            _ => {}
        }
    }
    specifiers
}

/// The module specifier of an import or export declaration when it is a
/// string literal.
fn string_module_specifier(decl: Node) -> Option<Node> {
    let specifier = decl.module_specifier();
    (specifier.is_some() && specifier.kind() == SyntaxKind::StringLiteral).then_some(specifier)
}

/// The string literal argument of `initializer` when it is a
/// `require("...")` call with exactly one argument.
fn require_call_specifier(initializer: Node) -> Option<Node> {
    if initializer.is_nil() || initializer.kind() != SyntaxKind::CallExpression {
        return None;
    }
    let callee = initializer.expression();
    if callee.is_nil()
        || callee.kind() != SyntaxKind::Identifier
        || get_text_of_node(callee) != "require"
        || initializer.arguments().len() != 1
    {
        return None;
    }
    let arg = initializer.arguments().get(0);
    (arg.kind() == SyntaxKind::StringLiteral).then_some(arg)
}

/// The module references of `sf` (imports, exports and requires) to
/// `package` or one of its subpaths (`<package>/...`).
pub fn package_module_references(sf: Node, package: &str) -> Vec<Node> {
    top_level_module_references(sf, true)
        .into_iter()
        .filter(|specifier| {
            let text = specifier.text();
            text == package
                || text
                    .strip_prefix(package)
                    .is_some_and(|rest| rest.starts_with('/'))
        })
        .collect()
}
