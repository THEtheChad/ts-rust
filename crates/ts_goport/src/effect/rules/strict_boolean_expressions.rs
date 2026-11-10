//! Port of Effect-TS/tsgo `internal/rules/strict_boolean_expressions.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;
use std::collections::VecDeque;

/// StrictBooleanExpressions enforces that expressions in conditional positions
/// are strictly boolean-typed, reporting non-boolean types as diagnostics.
// Go: rules/strict_boolean_expressions.go StrictBooleanExpressions
pub static STRICT_BOOLEAN_EXPRESSIONS: Rule = Rule {
    name: "strictBooleanExpressions",
    group: "style",
    description: "Enforces boolean types in conditional expressions for type safety",
    default_severity: Severity::Off,
    supported_effect: &["v3", "v4"],
    codes: &[377029],
    run: run_strict_boolean_expressions,
};

fn run_strict_boolean_expressions(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let mut diags = Vec::new();
    let mut condition_checks: FxHashMap<Node, bool> = FxHashMap::default();

    // Breadth-first traversal matching the reference implementation
    // PERF: the queue holds node order indexes (effperf4).
    let order = ctx.tp.node_order(ctx.source_file);
    let mut node_to_visit: VecDeque<u32> = order.children(0).collect();

    while let Some(i) = node_to_visit.pop_front() {
        let node = order.node(i);
        // Enqueue children
        node_to_visit.extend(order.children(i));

        for node_to_check in condition_operands(&mut condition_checks, node) {
            if node_to_check.is_nil()
                || !is_condition_check(&condition_checks, node_to_check.parent())
            {
                continue;
            }
            for type_name in non_boolean_condition_type_names(ctx.tp, node_to_check) {
                // Report the error
                let sf = ctx.source_file;
                diags.push(ctx.new_diagnostic(
                    sf,
                    ctx.get_error_range(node_to_check),
                    diag::Unexpected_0_type_in_condition_expected_strictly_a_boolean_instead_effect_strictBooleanExpressions,
                    Vec::new(),
                    vec![type_name],
                ));
            }
        }
    }

    diags
}

/// Whether `n` is recorded as a condition check.
fn is_condition_check(checks: &FxHashMap<Node, bool>, n: Node) -> bool {
    checks.get(&n).copied().unwrap_or(false)
}

/// The operands of `node` that are checked as conditions: the condition of
/// an `if`, `while` or conditional expression, the operand of `!`, and both
/// sides of `||` and `&&`. Records `node` as a condition check (a logical
/// operator only when its parent is one).
fn condition_operands(condition_checks: &mut FxHashMap<Node, bool>, node: Node) -> Vec<Node> {
    match node.kind() {
        SyntaxKind::IfStatement | SyntaxKind::WhileStatement => {
            condition_checks.insert(node, true);
            vec![node.expression()]
        }
        SyntaxKind::ConditionalExpression => {
            condition_checks.insert(node, true);
            vec![node.condition()]
        }
        SyntaxKind::PrefixUnaryExpression if node.operator() == SyntaxKind::ExclamationToken => {
            condition_checks.insert(node, true);
            vec![node.operand()]
        }
        SyntaxKind::BinaryExpression => {
            let op_kind = node.operator_token().kind();
            if op_kind != SyntaxKind::BarBarToken && op_kind != SyntaxKind::AmpersandAmpersandToken
            {
                return Vec::new();
            }
            if is_condition_check(condition_checks, node.parent()) {
                condition_checks.insert(node, true);
            }
            vec![node.left(), node.right()]
        }
        _ => Vec::new(),
    }
}

/// The names of the non-boolean types (union members unrolled; boolean,
/// boolean literal and never types skipped; each name once) of the
/// condition `node_to_check`, through its base constraint.
fn non_boolean_condition_type_names(tp: &mut TypeParser<'_>, node_to_check: Node) -> Vec<String> {
    let mut type_names = Vec::new();
    let node_type = tp.get_type_at_location(node_to_check);
    if node_type.is_nil() {
        return type_names;
    }

    let constrained_type = tp.checker.get_base_constraint_of_type_exported(node_type);
    let mut types_to_check: Vec<TypeId> = Vec::new();
    if constrained_type.is_some() {
        types_to_check.push(constrained_type);
    } else {
        types_to_check.push(node_type);
    }
    let mut seen_type_names: FxHashSet<String> = FxHashSet::default();

    while let Some(t) = types_to_check.pop() {
        let flags = tp.checker.ty(t).flags;

        // Unroll union types
        if flags.intersects(TypeFlags::UNION) {
            types_to_check.extend(tp.unroll_union_members(t));
            continue;
        }

        // Skip boolean and never types
        if flags.intersects(TypeFlags::BOOLEAN | TypeFlags::BOOLEAN_LITERAL | TypeFlags::NEVER) {
            continue;
        }

        let type_name = tp.checker.type_to_string_exported(t);
        if seen_type_names.insert(type_name.clone()) {
            type_names.push(type_name);
        }
    }
    type_names
}
