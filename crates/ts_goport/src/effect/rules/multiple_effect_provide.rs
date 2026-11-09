//! Port of Effect-TS/tsgo `internal/rules/multiple_effect_provide.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

/// MultipleEffectProvide detects consecutive Effect.provide calls with Layer-typed
/// arguments within piping flows and warns that they should be merged into a single
/// provide call to avoid service lifecycle issues.
// Go: rules/multiple_effect_provide.go MultipleEffectProvide
pub static MULTIPLE_EFFECT_PROVIDE: Rule = Rule {
    name: "multipleEffectProvide",
    group: "antipattern",
    description: "Warns against chaining Effect.provide calls which can cause service lifecycle issues",
    default_severity: Severity::Warning,
    supported_effect: &["v3", "v4"],
    codes: &[377033],
    run: run_multiple_effect_provide,
};

fn run_multiple_effect_provide(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let sf = ctx.source_file;
    let matches = analyze_multiple_effect_provide(ctx.tp, sf);
    let mut diags = Vec::with_capacity(matches.len());
    for m in &matches {
        diags.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::This_expression_chains_multiple_Effect_provide_calls_Providing_Layers_in_multiple_calls_in_a_chain_can_break_service_lifecycle_behavior_compared_with_a_single_combined_provide_with_merged_layers_effect_multipleEffectProvide,
            Vec::new(),
            Vec::new(),
        ));
    }
    diags
}

/// MultipleEffectProvideMatch holds the AST nodes needed by both the
/// diagnostic rule and the quick-fix for the multipleEffectProvide pattern.
// Go: rules/multiple_effect_provide.go MultipleEffectProvideMatch
#[derive(Clone, Debug)]
pub struct MultipleEffectProvideMatch {
    /// The source file where the diagnostic should be reported
    pub source_file: Node,
    /// The diagnostic span (error range on the first call in the chunk)
    pub location: TextRange,
    /// The list of call expression nodes in the consecutive provide chain (2+ elements)
    pub chunk: Vec<Node>,
    /// The layer argument nodes from each Effect.provide(layer) call
    pub layer_args: Vec<Node>,
    /// The Effect module identifier node from the first chunk callee
    pub effect_module_node: Node,
}

/// AnalyzeMultipleEffectProvide finds all consecutive Effect.provide call chains
/// with Layer-typed arguments within piping flows.
// Go: rules/multiple_effect_provide.go AnalyzeMultipleEffectProvide
pub fn analyze_multiple_effect_provide(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<MultipleEffectProvideMatch> {
    let mut matches = Vec::new();
    let is_effect_v4 = tp.supported_effect_version() == EffectMajorVersion::V4;

    let flows = tp.piping_flows(sf, true);
    for flow in flows.iter() {
        // Track chunks of consecutive Effect.provide calls with Layer arguments.
        let mut chunk = ProvideChunk::default();
        for transformation in &flow.transformations {
            let Some((call_node, arg)) = layer_provide_call(tp, transformation, is_effect_v4)
            else {
                chunk.finalize(sf, &mut matches);
                continue;
            };

            // Capture Effect module node from the callee's property access expression
            if chunk.calls.is_empty()
                && transformation.callee.kind() == SyntaxKind::PropertyAccessExpression
            {
                chunk.effect_module_node = transformation.callee.expression();
            }

            chunk.calls.push(call_node);
            chunk.layer_args.push(arg);
        }

        // Finalize the last chunk after the loop
        chunk.finalize(sf, &mut matches);
    }

    matches
}

/// A run of consecutive `Effect.provide` calls with Layer arguments in one
/// piping flow (Go's `currentChunk`, `currentLayerArgs` and
/// `currentEffectModuleNode`).
#[derive(Default)]
struct ProvideChunk {
    calls: Vec<Node>,
    layer_args: Vec<Node>,
    effect_module_node: Node,
}

impl ProvideChunk {
    /// Go `finalizeChunk`: reports the chunk when it has two calls or more,
    /// and starts a new one.
    fn finalize(&mut self, sf: Node, matches: &mut Vec<MultipleEffectProvideMatch>) {
        if self.calls.len() >= 2 {
            matches.push(MultipleEffectProvideMatch {
                source_file: sf,
                location: get_error_range_for_node(sf, self.calls[0]),
                chunk: std::mem::take(&mut self.calls),
                layer_args: std::mem::take(&mut self.layer_args),
                effect_module_node: self.effect_module_node,
            });
        }
        self.calls.clear();
        self.layer_args.clear();
        self.effect_module_node = Node::NIL;
    }
}

/// The enclosing call and the Layer argument of `transformation` when it is
/// an `Effect.provide` call with a Layer first argument (and, in Effect v4,
/// without `{ local: true }` options). `None` breaks the chain.
fn layer_provide_call(
    tp: &mut TypeParser<'_>,
    transformation: &PipingFlowTransformation,
    is_effect_v4: bool,
) -> Option<(Node, Node)> {
    // Check if this is an Effect.provide call
    if !tp.is_node_reference_to_effect_module_api(transformation.callee, member!("provide")) {
        return None;
    }

    // Must have arguments
    if transformation.args.is_empty() {
        return None;
    }

    if is_effect_v4
        && transformation.args.len() > 1
        && is_local_provide_options(transformation.args[1])
    {
        return None;
    }

    // Check if the first argument is a Layer type
    let arg = transformation.args[0];
    let arg_type = tp.get_type_at_location(arg);
    if arg_type.is_nil() {
        return None;
    }

    if tp.layer_type(arg_type).is_none() {
        // provide call but not with a Layer argument — breaks the chain
        return None;
    }

    // Find the enclosing call expression for this provide
    let call_node = find_ancestor_kind(transformation.callee, SyntaxKind::CallExpression);
    if call_node.is_nil() {
        return None;
    }
    Some((call_node, arg))
}

/// Whether `options` is an object literal with a `local: true` property
/// assignment.
fn is_local_provide_options(options: Node) -> bool {
    if options.is_nil() || options.kind() != SyntaxKind::ObjectLiteralExpression {
        return false;
    }
    options.properties().iter().any(|property| {
        if property.is_nil() || property.kind() != SyntaxKind::PropertyAssignment {
            return false;
        }
        let name = property.name();
        name.is_some()
            && name.kind() == SyntaxKind::Identifier
            && name.text() == "local"
            && property.initializer().kind() == SyntaxKind::TrueKeyword
    })
}
