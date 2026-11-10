//! Port of Effect-TS/tsgo `internal/rules/non_object_effect_service_type.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: rules.NonObjectEffectServiceType
/// NonObjectEffectServiceType checks that Effect.Service option properties
/// (succeed, sync, effect, scoped) do not resolve to primitive types.
/// V3-only, default severity error.
pub static NON_OBJECT_EFFECT_SERVICE_TYPE: Rule = Rule {
    name: "nonObjectEffectServiceType",
    group: "correctness",
    description: "Ensures Effect.Service types are objects, not primitives",
    default_severity: Severity::Error,
    supported_effect: &["v3"],
    codes: &[377048],
    run: run_non_object_effect_service_type,
};

fn run_non_object_effect_service_type(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    // V3-only rule
    if ctx.tp.supported_effect_version() != EffectMajorVersion::V3 {
        return Vec::new();
    }

    let mut diags = Vec::new();

    // Stack-based traversal
    // PERF: Go walks the tree with a stack of nodes; this stack holds node
    // order indexes and pushes and pops at the same points (effperf4).
    let order = ctx.tp.node_order(ctx.source_file);
    let mut node_to_visit: Vec<u32> = order.children(0).collect();

    while let Some(i) = node_to_visit.pop() {
        let node = order.node(i);
        if node.kind() == SyntaxKind::ClassDeclaration {
            let d = check_service_property_types(ctx, node);
            if !d.is_empty() {
                diags.extend(d);
                continue; // skip children
            }
        }

        // Enqueue children
        node_to_visit.extend(order.children(i));
    }

    diags
}

// Go: rules.checkServicePropertyTypes
/// checkServicePropertyTypes checks if a class extending Effect.Service has option
/// properties that resolve to primitive types.
fn check_service_property_types(ctx: &mut RuleContext<'_, '_>, node: Node) -> Vec<Diagnostic> {
    let Some(service_result) = ctx.tp.extends_effect_v3_service(node) else {
        return Vec::new();
    };

    let options = service_result.options;
    if options.is_nil() || options.kind() != SyntaxKind::ObjectLiteralExpression {
        return Vec::new();
    }

    let obj_lit = options;

    let mut diags = Vec::new();

    for prop in obj_lit.properties() {
        if prop.is_nil() || prop.kind() != SyntaxKind::PropertyAssignment {
            continue;
        }
        let pa = prop;
        if pa.name().is_nil() || pa.name().kind() != SyntaxKind::Identifier {
            continue;
        }

        let property_name = get_text_of_node(pa.name());
        let initializer = pa.initializer();
        if initializer.is_nil() {
            continue;
        }

        if service_option_has_primitive_service(ctx.tp, &property_name, initializer) {
            diags.push(ctx.new_diagnostic(
                ctx.source_file,
                ctx.get_error_range(pa.name()),
                diag::Effect_Service_is_declared_with_a_primitive_service_type_Effect_Service_models_object_shaped_services_primitive_values_use_Context_Tag_or_Effect_Tag_directly_effect_nonObjectEffectServiceType,
                Vec::new(),
                Vec::new(),
            ));
        }
    }

    diags
}

/// Whether the service option `property_name` (`succeed`, `sync`, `effect`
/// or `scoped`) with the value `initializer` declares a primitive service
/// type: the value itself, the return of one of its call signatures, or the
/// success type of its Effect (or of the Effect one of its call signatures
/// returns).
fn service_option_has_primitive_service(
    tp: &mut TypeParser<'_>,
    property_name: &str,
    initializer: Node,
) -> bool {
    if !matches!(property_name, "succeed" | "sync" | "effect" | "scoped") {
        return false;
    }
    let value_type = tp.get_type_at_location(initializer);
    if value_type.is_nil() {
        return false;
    }
    match property_name {
        "succeed" => is_primitive_type(tp, value_type),
        "sync" => any_call_return_type(tp, value_type, |tp, return_type| {
            return_type.is_some() && is_primitive_type(tp, return_type)
        }),
        _ => {
            // Try direct EffectType parse first
            if let Some(effect_result) = tp.effect_type(value_type) {
                return is_primitive_type(tp, effect_result.a);
            }
            // Fall back to call signatures
            any_call_return_type(tp, value_type, |tp, return_type| {
                if return_type.is_nil() {
                    return false;
                }
                tp.effect_type(return_type)
                    .is_some_and(|effect_return_result| {
                        is_primitive_type(tp, effect_return_result.a)
                    })
            })
        }
    }
}

/// Whether `f` holds for the return type of one of the call signatures of
/// `t`, read in order up to the first that does.
fn any_call_return_type(
    tp: &mut TypeParser<'_>,
    t: TypeId,
    mut f: impl FnMut(&mut TypeParser<'_>, TypeId) -> bool,
) -> bool {
    let signatures = tp
        .checker
        .get_signatures_of_type_exported(t, SignatureKind::CALL);
    signatures.into_iter().any(|sig| {
        let return_type = tp.checker.get_return_type_of_signature_exported(sig);
        f(tp, return_type)
    })
}

// Go: rules.isPrimitiveType
/// isPrimitiveType checks if a type (or any member of a union type) is a primitive type.
fn is_primitive_type(tp: &mut TypeParser<'_>, t: TypeId) -> bool {
    let primitive_flags = TypeFlags::STRING
        | TypeFlags::NUMBER
        | TypeFlags::BOOLEAN
        | TypeFlags::STRING_LITERAL
        | TypeFlags::NUMBER_LITERAL
        | TypeFlags::BOOLEAN_LITERAL
        | TypeFlags::UNDEFINED
        | TypeFlags::NULL;

    for member in tp.unroll_union_members(t) {
        if tp.checker.ty(member).flags().intersects(primitive_flags) {
            return true;
        }
    }
    false
}
