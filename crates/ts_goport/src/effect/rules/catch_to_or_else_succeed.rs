//! Port of Effect-TS/tsgo `internal/rules/catch_to_or_else_succeed.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::rules::catch_callback::{catch_callback_call, catch_method_name};
use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: rules.CatchToOrElseSucceed
/// CatchToOrElseSucceed suggests using Effect.orElseSucceed instead of Effect.catch + Effect.succeed.
pub static CATCH_TO_OR_ELSE_SUCCEED: Rule = Rule {
    name: "catchToOrElseSucceed",
    group: "style",
    description: "Suggests using Effect.orElseSucceed instead of Effect.catch + Effect.succeed",
    default_severity: Severity::Suggestion,
    supported_effect: &["v3", "v4"],
    codes: &[377095],
    run: run_catch_to_or_else_succeed,
};

fn run_catch_to_or_else_succeed(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let matches = analyze_catch_to_or_else_succeed(ctx.tp, ctx.source_file);
    let mut diags = Vec::with_capacity(matches.len());
    for m in &matches {
        diags.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::Effect_orElseSucceed_expresses_the_same_recovery_more_directly_than_Effect_0_followed_by_Effect_succeed_effect_catchToOrElseSucceed,
            Vec::new(),
            vec![m.catch_method_name.clone()],
        ));
    }
    diags
}

// Go: rules.CatchToOrElseSucceedMatch
/// CatchToOrElseSucceedMatch holds the AST nodes needed by both the diagnostic rule
/// and the quick-fix for the catchToOrElseSucceed pattern.
#[derive(Clone)]
pub struct CatchToOrElseSucceedMatch {
    pub source_file: Node,
    pub location: TextRange,
    pub callee: Node,
    pub callee_name_node: Node,
    pub catch_method_name: String,
    pub succeed_call_expression: Node,
    pub succeed_argument: Node,
}

// Go: rules.AnalyzeCatchToOrElseSucceed
/// AnalyzeCatchToOrElseSucceed finds Effect.catch callbacks that ignore the error
/// and return Effect.succeed, which can be simplified to Effect.orElseSucceed.
pub fn analyze_catch_to_or_else_succeed(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<CatchToOrElseSucceedMatch> {
    let mut matches = Vec::new();

    let flows = tp.piping_flows(sf, true);
    for flow in flows.iter() {
        for transformation in &flow.transformations {
            let Some(call) = catch_callback_call(tp, transformation, LazyExpressionFlags::THUNK)
            else {
                continue;
            };
            if !tp.is_node_reference_to_effect_module_api(call.expression(), member!("succeed")) {
                continue;
            }
            if call.arguments().is_empty() {
                continue;
            }
            let (callee_name_node, catch_method_name) = catch_method_name(transformation.callee);
            matches.push(CatchToOrElseSucceedMatch {
                source_file: sf,
                location: get_error_range_for_node(sf, transformation.callee),
                callee: transformation.callee,
                callee_name_node,
                catch_method_name,
                succeed_call_expression: call,
                succeed_argument: call.arguments().get(0),
            });
        }
    }

    matches
}
