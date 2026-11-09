// Go: internal/rules/obsolete_match_import.go

use super::module_references::package_module_references;
use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

pub static OBSOLETE_MATCH_IMPORT: Rule = Rule {
    name: "obsoleteMatchImport",
    group: "correctness",
    description: "Warns when importing @effect/match in projects targeting Effect v4, where Match is included directly in the effect package",
    default_severity: Severity::Warning,
    supported_effect: &["v4"],
    codes: &[377129],
    run: run_obsolete_match_import,
};

fn run_obsolete_match_import(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let matches = analyze_obsolete_match_import(ctx.tp, ctx.source_file);
    let mut diags = Vec::with_capacity(matches.len());
    for m in &matches {
        diags.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::This_module_reference_imports_0_which_is_obsolete_in_Effect_v4_In_Effect_v4_pattern_matching_is_provided_directly_by_Match_from_effect_or_effect_SlashMatch_effect_obsoleteMatchImport,
            Vec::new(),
            vec![m.imported_specifier.clone()],
        ));
    }
    diags
}

#[derive(Clone, Debug)]
pub struct ObsoleteMatchImportMatch {
    pub source_file: Node,
    pub location: TextRange,
    pub imported_specifier: String,
}

// Go: rules/obsolete_match_import.go AnalyzeObsoleteMatchImport
pub fn analyze_obsolete_match_import(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<ObsoleteMatchImportMatch> {
    if sf.is_nil() || tp.supported_effect_version() != EffectMajorVersion::V4 {
        return Vec::new();
    }
    package_module_references(sf, "@effect/match")
        .into_iter()
        .map(|specifier| ObsoleteMatchImportMatch {
            source_file: sf,
            location: get_error_range_for_node(sf, specifier),
            imported_specifier: specifier.text().to_string(),
        })
        .collect()
}
