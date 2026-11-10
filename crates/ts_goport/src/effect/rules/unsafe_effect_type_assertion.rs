// Go: internal/rules/unsafe_effect_type_assertion.go

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

pub static UNSAFE_EFFECT_TYPE_ASSERTION: Rule = Rule {
    name: "unsafeEffectTypeAssertion",
    group: "correctness",
    description: "Detects unsafe type assertions that narrow Effect, Stream, or Layer error or requirements channels",
    default_severity: Severity::Off,
    supported_effect: &["v3", "v4"],
    codes: &[377075],
    run: run_unsafe_effect_type_assertion,
};

fn run_unsafe_effect_type_assertion(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let matches = analyze_unsafe_effect_type_assertion(ctx.tp, ctx.source_file);
    let mut diags = Vec::with_capacity(matches.len());
    for m in &matches {
        let related = unsafe_effect_type_assertion_related_information(ctx, m);
        diags.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::This_type_assertion_unsafely_narrows_the_error_or_requirements_channels_effect_unsafeEffectTypeAssertion,
            related,
            Vec::new(),
        ));
    }
    diags
}

#[derive(Clone, Debug)]
pub struct UnsafeEffectTypeAssertionChannel {
    pub name: String,
    pub original: String,
    pub asserted: String,
}

#[derive(Clone, Debug)]
pub struct UnsafeEffectTypeAssertionMatch {
    pub source_file: Node,
    pub location: TextRange,
    pub assertion_node: Node,
    pub expression_node: Node,
    pub location_node: Node,
    pub channels: Vec<UnsafeEffectTypeAssertionChannel>,
}

// Go: rules/unsafe_effect_type_assertion.go AnalyzeUnsafeEffectTypeAssertion
pub fn analyze_unsafe_effect_type_assertion(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<UnsafeEffectTypeAssertionMatch> {
    if sf.is_nil() {
        return Vec::new();
    }

    let mut matches = Vec::new();
    // PERF: Go walks the tree with a stack of nodes; this stack holds node
    // order indexes and pushes and pops at the same points (effperf4).
    let order = tp.node_order(sf);
    let mut nodes_to_visit: Vec<u32> = order.children(0).collect();

    while let Some(i) = nodes_to_visit.pop() {
        let node = order.node(i);
        nodes_to_visit.extend(order.children(i));
        if node.kind() == SyntaxKind::AsExpression
            || node.kind() == SyntaxKind::TypeAssertionExpression
        {
            matches.extend(unsafe_assertion_match(tp, sf, node));
        }
    }

    matches
}

/// The match of one `as` or `<T>` assertion whose expression and asserted
/// type are both an Effect, Stream or Layer, when the assertion narrows the
/// error or requirements channel to a type the original is not assignable
/// to.
fn unsafe_assertion_match(
    tp: &mut TypeParser<'_>,
    sf: Node,
    node: Node,
) -> Option<UnsafeEffectTypeAssertionMatch> {
    let expr = node.expression();
    if expr.is_nil() {
        return None;
    }

    let original_type = tp.get_type_at_location(expr);
    let asserted_type = tp.get_type_at_location(node);
    if original_type.is_nil() || asserted_type.is_nil() {
        return None;
    }

    let (original_e, original_r) = parse_effect_stream_or_layer(tp, original_type)?;
    let (asserted_e, asserted_r) = parse_effect_stream_or_layer(tp, asserted_type)?;

    let c = &mut *tp.checker;
    let channels: Vec<UnsafeEffectTypeAssertionChannel> = [
        unsafe_channel(c, "error", original_e, asserted_e),
        unsafe_channel(c, "requirements", original_r, asserted_r),
    ]
    .into_iter()
    .flatten()
    .collect();
    if channels.is_empty() {
        return None;
    }

    let type_node = node.type_();
    let location_node = if type_node.is_some() { type_node } else { node };
    Some(UnsafeEffectTypeAssertionMatch {
        source_file: sf,
        location: get_error_range_for_node(sf, node),
        assertion_node: node,
        expression_node: expr,
        location_node,
        channels,
    })
}

/// The error and requirements channel types of an Effect, a Stream or a
/// Layer (its `RIn`), or `None` for any other type.
fn parse_effect_stream_or_layer(tp: &mut TypeParser<'_>, t: TypeId) -> Option<(TypeId, TypeId)> {
    if let Some(effect) = tp.effect_type(t) {
        return Some((effect.e, effect.r));
    }
    if let Some(stream) = tp.stream_type(t) {
        return Some((stream.e, stream.r));
    }
    if let Some(layer) = tp.layer_type(t) {
        return Some((layer.e, layer.r_in));
    }
    None
}

/// The channel `name` when its original type (not `any`) is not assignable
/// to the asserted one.
fn unsafe_channel(
    c: &mut Checker,
    name: &str,
    original: TypeId,
    asserted: TypeId,
) -> Option<UnsafeEffectTypeAssertionChannel> {
    if original.is_nil()
        || asserted.is_nil()
        || is_any_type(c, original)
        || c.is_type_assignable_to(original, asserted)
    {
        return None;
    }
    Some(UnsafeEffectTypeAssertionChannel {
        name: name.to_string(),
        original: c.type_to_string_exported(original),
        asserted: c.type_to_string_exported(asserted),
    })
}

// Go: rules/unsafe_effect_type_assertion.go isAnyType
// PORT: takes the checker to read the type flags.
fn is_any_type(c: &Checker, t: TypeId) -> bool {
    t.is_some() && c.ty(t).flags.intersects(TypeFlags::ANY)
}

// Go: rules/unsafe_effect_type_assertion.go unsafeEffectTypeAssertionRelatedInformation
fn unsafe_effect_type_assertion_related_information(
    ctx: &RuleContext<'_, '_>,
    m: &UnsafeEffectTypeAssertionMatch,
) -> Vec<Diagnostic> {
    if m.channels.is_empty() {
        return Vec::new();
    }

    let mut location_node = m.location_node;
    if location_node.is_nil() {
        location_node = m.assertion_node;
    }

    let mut related = Vec::with_capacity(m.channels.len());
    for channel in &m.channels {
        related.push(ctx.new_diagnostic(
            m.source_file,
            get_error_range_for_node(m.source_file, location_node),
            diag::The_0_channel_is_narrowed_from_1_to_2_effect_unsafeEffectTypeAssertion,
            Vec::new(),
            vec![
                channel.name.clone(),
                channel.original.clone(),
                channel.asserted.clone(),
            ],
        ));
    }
    related
}
