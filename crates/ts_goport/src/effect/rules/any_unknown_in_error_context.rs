//! Port of Effect-TS/tsgo `internal/rules/any_unknown_in_error_context.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

/// AnyUnknownInErrorContext detects 'any' or 'unknown' types in the error (E) or
/// requirements (R) channels of Effect and Layer types.
pub static ANY_UNKNOWN_IN_ERROR_CONTEXT: Rule = Rule {
    name: "anyUnknownInErrorContext",
    group: "correctness",
    description: "Detects 'any' or 'unknown' types in Effect error or requirements channels",
    default_severity: Severity::Off,
    supported_effect: &["v3", "v4"],
    codes: &[377030],
    run: run_any_unknown_in_error_context,
};

struct MatchEntry {
    node: Node,
    message_text: String,
    start: i32,
    end: i32,
}

fn run_any_unknown_in_error_context(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let mut matches: Vec<MatchEntry> = Vec::new();

    // Stack-based traversal
    // PERF: Go walks the tree with a stack of nodes; this stack holds node
    // order indexes and pushes and pops at the same points (effperf4).
    let order = ctx.tp.node_order(ctx.source_file);
    let mut node_to_visit: Vec<u32> = order.children(0).collect();

    // Pop from the end (stack)
    while let Some(i) = node_to_visit.pop() {
        let node = order.node(i);
        if skips_any_unknown_subtree(ctx.tp, node) {
            continue;
        }

        // Enqueue children for visiting
        node_to_visit.extend(order.children(i));

        let Some(message_text) = any_unknown_channels_message(ctx.tp, node) else {
            continue;
        };
        let node_start = get_token_pos_of_node(node, ctx.source_file, false);
        let node_end = node.end();

        // Innermost-node deduplication: remove parent nodes that contain this node
        matches.retain(|m| !(m.start <= node_start && m.end >= node_end));

        matches.push(MatchEntry {
            node,
            message_text,
            start: node_start,
            end: node_end,
        });
    }

    // Report all innermost matching nodes
    let mut diags: Vec<Diagnostic> = Vec::new();
    for m in matches {
        diags.push(ctx.new_diagnostic(
            get_source_file_of_node(m.node),
            ctx.get_error_range(m.node),
            diag::X_0_effect_anyUnknownInErrorContext,
            Vec::new(),
            vec![m.message_text],
        ));
    }

    diags
}

/// Whether the walk skips `node` and its subtree: type nodes, type alias and
/// interface declarations, `as any` expressions, and a parameter, property
/// or variable declaration with an explicit Effect or Layer type annotation
/// (the user typed it intentionally).
fn skips_any_unknown_subtree(tp: &mut TypeParser<'_>, node: Node) -> bool {
    if is_type_node(node) {
        return true;
    }
    match node.kind() {
        SyntaxKind::TypeAliasDeclaration | SyntaxKind::InterfaceDeclaration => true,
        SyntaxKind::AsExpression => {
            let type_node = node.type_();
            type_node.is_some() && type_node.kind() == SyntaxKind::AnyKeyword
        }
        SyntaxKind::Parameter
        | SyntaxKind::PropertyDeclaration
        | SyntaxKind::VariableDeclaration => {
            let type_node = node.type_();
            if type_node.is_nil() {
                return false;
            }
            let annotation_type = tp.get_type_at_location(type_node);
            annotation_type.is_some()
                && (tp.strict_effect_type(annotation_type).is_some()
                    || tp.layer_type(annotation_type).is_some())
        }
        _ => false,
    }
}

/// The error and requirements channels of the type of `node` (for a call
/// expression, its resolved signature's return type) when it is a strict
/// Effect type (tried first) or a Layer type.
fn effect_or_layer_channels(tp: &mut TypeParser<'_>, node: Node) -> Option<(TypeId, TypeId)> {
    // Get the type at this location
    let mut t = tp.get_type_at_location(node);

    // For call expressions, use the resolved signature's return type
    if node.kind() == SyntaxKind::CallExpression {
        let sig = tp.checker.get_resolved_signature_exported(node);
        if sig.is_some() {
            t = tp.checker.get_return_type_of_signature_exported(sig);
        }
    }

    if t.is_nil() {
        return None;
    }

    // Try strict Effect type first, then Layer type
    let (e_type, r_type) = match tp.strict_effect_type(t) {
        Some(eff) => (eff.e, eff.r),
        None => {
            let layer = tp.layer_type(t)?;
            (layer.e, layer.r_in)
        }
    };
    (e_type.is_some() && r_type.is_some()).then_some((e_type, r_type))
}

/// The diagnostic message for `node` when its Effect or Layer type has any
/// or unknown in its requirements or error channel.
fn any_unknown_channels_message(tp: &mut TypeParser<'_>, node: Node) -> Option<String> {
    let (e_type, r_type) = effect_or_layer_channels(tp, node)?;
    let r_flags = tp.checker.ty(r_type).flags();
    let e_flags = tp.checker.ty(e_type).flags();
    let has_any_unknown_r = r_flags.intersects(TypeFlags::ANY | TypeFlags::UNKNOWN);
    let has_any_unknown_e = e_flags.intersects(TypeFlags::ANY | TypeFlags::UNKNOWN);

    if !has_any_unknown_r && !has_any_unknown_e {
        return None;
    }

    let any_or_unknown = |flags: TypeFlags| {
        if flags.intersects(TypeFlags::ANY) {
            "any"
        } else {
            "unknown"
        }
    };

    // Build the channel descriptions
    let mut channels: Vec<String> = Vec::new();
    if has_any_unknown_r {
        channels.push(format!(
            "{} in the requirements channel",
            any_or_unknown(r_flags)
        ));
    }
    if has_any_unknown_e {
        channels.push(format!("{} in the error channel", any_or_unknown(e_flags)));
    }

    // Compose the diagnostic message
    let mut suggestions: Vec<String> = Vec::new();
    suggestions.push(format!(
        "This has {} which is not recommended.",
        channels.join(" and ")
    ));
    if has_any_unknown_r {
        suggestions.push(
            "Only service identifiers should appear in the requirements channel.".to_string(),
        );
    }
    if has_any_unknown_e {
        suggestions.push("Having an unknown or any error type is not useful. Consider instead using specific error types baked by Data.TaggedError for example.".to_string());
    }
    Some(suggestions.join("\n"))
}
