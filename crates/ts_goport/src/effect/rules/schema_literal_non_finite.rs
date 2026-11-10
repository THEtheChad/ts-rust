//! Port of Effect-TS/tsgo `internal/rules/schema_literal_non_finite.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: rules/schema_literal_non_finite.go SchemaLiteralNonFinite
pub static SCHEMA_LITERAL_NON_FINITE: Rule = Rule {
    name: "schemaLiteralNonFinite",
    group: "correctness",
    description: "Reports statically known non-finite numbers passed to Schema literal constructors",
    default_severity: Severity::Error,
    supported_effect: &["v4"],
    codes: &[377106],
    run: run_schema_literal_non_finite,
};

fn run_schema_literal_non_finite(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let mut diags = Vec::new();

    fn visit(ctx: &mut RuleContext<'_, '_>, diags: &mut Vec<Diagnostic>, node: Node) {
        if node.kind() != SyntaxKind::CallExpression {
            return;
        }
        for value in schema_literal_values(ctx.tp, node) {
            let (n, ok) = ctx.tp.evaluate_constant_number(value, value);
            if ok && (n.is_infinite() || n.is_nan()) {
                let sf = ctx.source_file;
                diags.push(ctx.new_diagnostic(
                    sf,
                    get_error_range_for_node(sf, value),
                    diag::Schema_literal_values_must_be_finite_numbers_0_throws_during_schema_construction_Use_a_finite_literal_or_model_non_finite_values_with_a_refined_Schema_Number_effect_schemaLiteralNonFinite,
                    Vec::new(),
                    vec![get_text_of_node(value)],
                ));
            }
        }
    }

    let sf = ctx.source_file;
    // PERF: Go walks the tree; the node order has the same nodes in the same
    // order (effperf4).
    for &n in ctx.tp.node_order(sf).nodes() {
        visit(ctx, &mut diags, n);
    }
    diags
}

/// The literal values of a call with arguments to `Schema.Literals` (the
/// elements of its array literal argument), or to `Schema.Literal` or
/// `Schema.tag` (its first argument); else none.
fn schema_literal_values(tp: &mut TypeParser<'_>, call: Node) -> Vec<Node> {
    if call.argument_list().is_nil() || call.arguments().is_empty() {
        return Vec::new();
    }
    if tp.is_node_reference_to_effect_schema_module_api(call.expression(), member!("Literals")) {
        let arg = call.arguments().get(0);
        if arg.is_some() && arg.kind() == SyntaxKind::ArrayLiteralExpression {
            return arg.elements().to_vec();
        }
        return Vec::new();
    }
    if tp.is_node_reference_to_effect_schema_module_api(call.expression(), member!("Literal"))
        || tp.is_node_reference_to_effect_schema_module_api(call.expression(), member!("tag"))
    {
        return vec![call.arguments().get(0)];
    }
    Vec::new()
}
