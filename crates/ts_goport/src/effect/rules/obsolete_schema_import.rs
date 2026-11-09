//! Port of Effect-TS/tsgo `internal/rules/obsolete_schema_import.go`.

use super::module_references::package_module_references;
use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: rules.ObsoleteSchemaImport
pub static OBSOLETE_SCHEMA_IMPORT: Rule = Rule {
    name: "obsoleteSchemaImport",
    group: "correctness",
    description: "Warns when importing @effect/schema in projects targeting Effect v4, where Schema is included directly in the effect package",
    default_severity: Severity::Warning,
    supported_effect: &["v4"],
    codes: &[377128],
    run: run_obsolete_schema_import,
};

fn run_obsolete_schema_import(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let matches = analyze_obsolete_schema_import(ctx.tp, ctx.source_file);
    let mut diags = Vec::with_capacity(matches.len());
    for m in &matches {
        diags.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::This_module_reference_imports_0_which_is_obsolete_in_Effect_v4_In_Effect_v4_Schema_is_provided_directly_by_Schema_from_effect_or_effect_SlashSchema_effect_obsoleteSchemaImport,
            Vec::new(),
            vec![m.imported_specifier.clone()],
        ));
    }
    diags
}

// Go: rules.ObsoleteSchemaImportMatch
#[derive(Clone)]
pub struct ObsoleteSchemaImportMatch {
    pub source_file: Node,
    pub location: TextRange,
    pub imported_specifier: String,
}

// Go: rules.AnalyzeObsoleteSchemaImport
// PORT: Go also returns nil for a nil type parser; the Rust one is never nil.
pub fn analyze_obsolete_schema_import(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<ObsoleteSchemaImportMatch> {
    if sf.is_nil() || tp.supported_effect_version() != EffectMajorVersion::V4 {
        return Vec::new();
    }
    package_module_references(sf, "@effect/schema")
        .into_iter()
        .map(|specifier| ObsoleteSchemaImportMatch {
            source_file: sf,
            location: get_error_range_for_node(sf, specifier),
            imported_specifier: specifier.text().to_string(),
        })
        .collect()
}
