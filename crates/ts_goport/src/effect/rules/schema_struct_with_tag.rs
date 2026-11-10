//! Port of Effect-TS/tsgo `internal/rules/schema_struct_with_tag.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

/// SchemaStructWithTag detects Schema.Struct(...) calls where the argument
/// contains a _tag property assigned to Schema.Literal("someString"), and
/// suggests using Schema.TaggedStruct instead.
// Go: rules/schema_struct_with_tag.go SchemaStructWithTag
pub static SCHEMA_STRUCT_WITH_TAG: Rule = Rule {
    name: "schemaStructWithTag",
    group: "style",
    description: "Suggests using Schema.TaggedStruct instead of Schema.Struct with _tag field",
    default_severity: Severity::Suggestion,
    supported_effect: &["v3", "v4"],
    codes: &[377036],
    run: run_schema_struct_with_tag,
};

fn run_schema_struct_with_tag(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let matches = analyze_schema_struct_with_tag(ctx.tp, ctx.source_file);
    let mut diags = Vec::with_capacity(matches.len());
    for m in &matches {
        diags.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::This_Schema_Struct_includes_a_tag_field_Schema_TaggedStruct_is_the_tagged_struct_form_for_this_pattern_and_makes_the_tag_optional_in_the_constructor_effect_schemaStructWithTag,
            Vec::new(),
            vec![],
        ));
    }
    diags
}

/// SchemaStructWithTagMatch holds the AST nodes needed by both the diagnostic rule
/// and the quick-fix for the schemaStructWithTag pattern.
// Go: rules/schema_struct_with_tag.go SchemaStructWithTagMatch
#[derive(Clone, Debug, Default)]
pub struct SchemaStructWithTagMatch {
    pub source_file: Node,
    /// Pre-computed error range for the diagnostic
    pub location: TextRange,
    /// The full Schema.Struct(...) call expression
    pub call_node: Node,
    /// The string value extracted from Schema.Literal("...")
    pub tag_value: String,
    /// All property assignment nodes in the object literal except _tag
    pub other_properties: Vec<Node>,
    /// The expression node for the Schema part of Schema.Struct
    pub schema_expr: Node,
}

/// AnalyzeSchemaStructWithTag finds all Schema.Struct({ _tag: Schema.Literal("..."), ... })
/// call expressions and returns match structs for each.
// Go: rules/schema_struct_with_tag.go AnalyzeSchemaStructWithTag
pub fn analyze_schema_struct_with_tag(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<SchemaStructWithTagMatch> {
    let mut matches: Vec<SchemaStructWithTagMatch> = Vec::new();

    // Stack-based traversal
    // PERF: Go walks the tree with a stack of nodes; this stack holds node
    // order indexes and pushes and pops at the same points (effperf4).
    let order = tp.node_order(sf);
    let mut node_to_visit: Vec<u32> = order.children(0).collect();

    while let Some(i) = node_to_visit.pop() {
        let node = order.node(i);
        if node.kind() == SyntaxKind::CallExpression
            && let (m, true) = analyze_schema_struct_with_tag_node(tp, sf, node)
        {
            matches.push(m);
        }

        // Enqueue children
        node_to_visit.extend(order.children(i));
    }

    matches
}

/// analyzeSchemaStructWithTagNode checks a call expression for Schema.Struct({ _tag: Schema.Literal("...") }).
// Go: rules/schema_struct_with_tag.go analyzeSchemaStructWithTagNode
fn analyze_schema_struct_with_tag_node(
    tp: &mut TypeParser<'_>,
    sf: Node,
    node: Node,
) -> (SchemaStructWithTagMatch, bool) {
    match schema_struct_with_tag_match(tp, sf, node) {
        Some(m) => (m, true),
        None => (SchemaStructWithTagMatch::default(), false),
    }
}

/// The match of a `Schema.Struct({ _tag: Schema.Literal("..."), ... })`
/// call `node`: the first `_tag` property decides.
fn schema_struct_with_tag_match(
    tp: &mut TypeParser<'_>,
    sf: Node,
    node: Node,
) -> Option<SchemaStructWithTagMatch> {
    if node.kind() != SyntaxKind::CallExpression {
        return None;
    }
    let call = node;

    // Check if this is Schema.Struct
    if !tp.is_node_reference_to_effect_schema_module_api(call.expression(), member!("Struct")) {
        return None;
    }

    // Must have exactly 1 argument
    if call.argument_list().is_nil() || call.arguments().len() != 1 {
        return None;
    }

    let obj_lit = call.arguments().get(0);
    if obj_lit.is_nil()
        || obj_lit.kind() != SyntaxKind::ObjectLiteralExpression
        || obj_lit.property_list().is_nil()
    {
        return None;
    }

    // Look for a _tag property assignment
    let prop = obj_lit.properties().iter().find(|&prop| {
        prop.is_some()
            && prop.kind() == SyntaxKind::PropertyAssignment
            && prop.name().is_some()
            && prop.name().kind() == SyntaxKind::Identifier
            && get_text_of_node(prop.name()) == "_tag"
    })?;

    // Found _tag property — check if its initializer is Schema.Literal("...")
    let tag_value = schema_literal_string(tp, prop.initializer())?;

    // Collect all properties except _tag
    let other_props: Vec<Node> = obj_lit.properties().iter().filter(|&p| p != prop).collect();

    // Extract the Schema expression from Schema.Struct (the left side of the property access)
    let mut schema_expr = Node::NIL;
    if call.expression().kind() == SyntaxKind::PropertyAccessExpression {
        schema_expr = call.expression().expression();
    }

    Some(SchemaStructWithTagMatch {
        source_file: sf,
        location: get_error_range_for_node(sf, node),
        call_node: node,
        tag_value,
        other_properties: other_props,
        schema_expr,
    })
}

/// The string of `init` when it is `Schema.Literal("...")` with exactly one
/// string literal argument.
fn schema_literal_string(tp: &mut TypeParser<'_>, init: Node) -> Option<String> {
    if init.is_nil() || init.kind() != SyntaxKind::CallExpression {
        return None;
    }
    if !tp.is_node_reference_to_effect_schema_module_api(init.expression(), member!("Literal")) {
        return None;
    }
    // Schema.Literal must have exactly 1 argument that is a string literal
    if init.argument_list().is_nil() || init.arguments().len() != 1 {
        return None;
    }
    let literal = init.arguments().get(0);
    (literal.kind() == SyntaxKind::StringLiteral).then(|| literal.text().to_string())
}
