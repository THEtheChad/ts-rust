// Go: internal/typeparser/module_identifier.go

use crate::effect::typeparser::*;
use crate::prelude::*;

/// FindEffectModuleIdentifier finds the local name for the Effect module from imports.
/// It checks both namespace imports (import * as X from "effect/Effect") and
/// named imports (import { Effect } from "effect"), falling back to "Effect".
pub fn find_effect_module_identifier(sf: Node) -> String {
    find_module_identifier_for_package(sf, "effect", "Effect")
}

/// FindModuleIdentifier resolves the imported identifier name for the given export
/// from the "effect" package. Falls back to the provided exportName if not found.
/// It checks named imports: import { ExportName as Alias } from "effect".
pub fn find_module_identifier(sf: Node, export_name: &str) -> String {
    find_module_identifier_for_package(sf, "effect", export_name)
}

/// FindModuleIdentifierForPackage resolves the imported identifier name for the given
/// module from an arbitrary package. It checks:
///   - Namespace imports: import * as X from "<packageName>/<moduleName>"
///   - Named imports: import { <moduleName> as X } from "<packageName>"
///
/// Falls back to moduleName if not found.
pub fn find_module_identifier_for_package(
    sf: Node,
    package_name: &str,
    module_name: &str,
) -> String {
    if sf.is_nil() {
        return module_name.to_string();
    }
    sf.statements()
        .iter()
        .find_map(|stmt| import_local_name(stmt, package_name, module_name))
        .unwrap_or_else(|| module_name.to_string())
}

/// The local name that the import declaration `stmt` gives `module_name` of
/// `package_name`, as a namespace import of `<package>/<module>` or a named
/// import from `<package>`.
fn import_local_name(stmt: Node, package_name: &str, module_name: &str) -> Option<String> {
    if stmt.kind() != SyntaxKind::ImportDeclaration
        || stmt.module_specifier().is_nil()
        || stmt.import_clause().is_nil()
    {
        return None;
    }
    let specifier = unquoted_module_specifier(stmt);
    let named_bindings = stmt.import_clause().named_bindings();
    if named_bindings.is_nil() {
        return None;
    }
    // Check namespace import: import * as X from "<packageName>/<moduleName>"
    if named_bindings.kind() == SyntaxKind::NamespaceImport
        && specifier == format!("{package_name}/{module_name}")
        && named_bindings.name().is_some()
    {
        return Some(get_text_of_node(named_bindings.name()));
    }
    // Check named imports: import { <moduleName> as X } from "<packageName>"
    if named_bindings.kind() == SyntaxKind::NamedImports && specifier == package_name {
        return named_import_local_name(named_bindings, module_name);
    }
    None
}

/// The module specifier text of `import_decl` without its quotes.
pub(crate) fn unquoted_module_specifier(import_decl: Node) -> String {
    let specifier = get_text_of_node(import_decl.module_specifier());
    let bytes = specifier.as_bytes();
    if bytes.len() >= 2 && (bytes[0] == b'"' || bytes[0] == b'\'') {
        // PORT: Go slices bytes; the result is only compared with names.
        return String::from_utf8_lossy(&bytes[1..bytes.len() - 1]).into_owned();
    }
    specifier
}

/// The local name of `export_name` in the `NamedImports` node
/// `named_imports`: the name of the first specifier whose imported name
/// (its property name if set, as in `import { Layer as X }`, otherwise its
/// name) is `export_name`.
pub(crate) fn named_import_local_name(named_imports: Node, export_name: &str) -> Option<String> {
    // PORT: Go skips a nil Elements list; a nil list has no elements here.
    named_imports.elements().iter().find_map(|spec| {
        let imported = if spec.property_name().is_some() {
            spec.property_name()
        } else {
            spec.name()
        };
        (get_text_of_node(imported) == export_name).then(|| get_text_of_node(spec.name()))
    })
}
