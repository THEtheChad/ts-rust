//! Port of Effect-TS/tsgo `internal/rules/missing_effect_service_dependency.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

/// MissingEffectServiceDependency checks that Effect.Service dependencies satisfy
/// all required layer inputs. It detects when a class extending Effect.Service has
/// required services (from the layer's RIn) that are not provided in the dependencies
/// configuration option. V3-only, default severity off.
// Go: rules/missing_effect_service_dependency.go MissingEffectServiceDependency
pub static MISSING_EFFECT_SERVICE_DEPENDENCY: Rule = Rule {
    name: "missingEffectServiceDependency",
    group: "style",
    description: "Checks that Effect.Service dependencies satisfy all required layer inputs",
    default_severity: Severity::Off,
    supported_effect: &["v3"],
    codes: &[377039, 377040],
    run: run_missing_effect_service_dependency,
};

fn run_missing_effect_service_dependency(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
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
            let d = check_service_dependencies(ctx, node);
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

// Go: the `excludeNever` closure of checkServiceDependencies.
fn exclude_never(tp: &mut TypeParser<'_>, t: TypeId) -> bool {
    tp.checker.ty(t).flags.intersects(TypeFlags::NEVER)
}

/// checkServiceDependencies checks if a class extending Effect.Service has all
/// required service dependencies satisfied.
// Go: rules/missing_effect_service_dependency.go checkServiceDependencies
fn check_service_dependencies(ctx: &mut RuleContext<'_, '_>, node: Node) -> Vec<Diagnostic> {
    // Check if this class extends Effect.Service
    let service_result = ctx.tp.extends_effect_v3_service(node);
    let Some(service_result) = service_result else {
        return Vec::new();
    };

    let class_name = service_result.class_name;
    let options = service_result.options;

    let Some(layer) = service_default_layer(ctx.tp, class_name, node) else {
        return Vec::new();
    };

    // Use a shared memory map for both required and provided services
    let mut services_memory: FxHashMap<String, TypeId> = FxHashMap::default();

    // Get all required service indexes from RIn
    let required_result = ctx.tp.append_to_unique_types_map(
        &mut services_memory,
        layer.r_in,
        Some(&mut exclude_never),
    );
    let required_indexes = required_result.all_indexes;

    if required_indexes.is_empty() {
        return Vec::new();
    }

    // Process dependencies to find provided services
    let mut provided_indexes: FxHashMap<String, bool> = FxHashMap::default();
    for dep_type in dependency_types(ctx.tp, options) {
        // Parse each dependency as Layer type
        let Some(dep_layer) = ctx.tp.layer_type(dep_type) else {
            continue;
        };
        // Add the ROut of this dependency to provided services
        let provided_result = ctx.tp.append_to_unique_types_map(
            &mut services_memory,
            dep_layer.r_out,
            Some(&mut exclude_never),
        );
        for idx in provided_result.all_indexes {
            provided_indexes.insert(idx, true);
        }
    }

    // Find missing indexes: required but not provided, and build the diagnostic
    let mut missing_type_names: Vec<String> = Vec::new();
    for idx in &required_indexes {
        if provided_indexes.get(idx).copied().unwrap_or(false) {
            continue;
        }
        let t = services_memory.get(idx).copied().unwrap_or(TypeId::NIL);
        if t.is_some() {
            missing_type_names.push(ctx.tp.checker.type_to_string_exported(t));
        }
    }

    if missing_type_names.is_empty() {
        return Vec::new();
    }
    vec![missing_services_diagnostic(
        ctx,
        class_name,
        &missing_type_names,
    )]
}

/// The Layer of the `DefaultWithoutDependencies` (or else `Default`) static
/// of the service class named `class_name`.
fn service_default_layer(
    tp: &mut TypeParser<'_>,
    class_name: Node,
    node: Node,
) -> Option<Rc<Layer>> {
    // Get the class symbol and type
    let class_sym = tp.get_symbol_at_location(class_name);
    if class_sym.is_nil() {
        return None;
    }
    let class_type = tp.checker.get_type_of_symbol_at_location(class_sym, node);
    if class_type.is_nil() {
        return None;
    }

    // Try DefaultWithoutDependencies first, then fall back to Default
    let mut default_prop = tp
        .checker
        .get_property_of_type_exported(class_type, "DefaultWithoutDependencies");
    if default_prop.is_nil() {
        default_prop = tp
            .checker
            .get_property_of_type_exported(class_type, "Default");
    }
    if default_prop.is_nil() {
        return None;
    }

    let default_type = tp
        .checker
        .get_type_of_symbol_at_location(default_prop, node);
    if default_type.is_nil() {
        return None;
    }

    // Parse as Layer type to get RIn
    tp.layer_type(default_type)
}

/// The union members of the element type of the `dependencies` option of
/// the service options `options` (empty without one).
fn dependency_types(tp: &mut TypeParser<'_>, options: Node) -> Vec<TypeId> {
    if options.is_nil() {
        return Vec::new();
    }
    let options_type = tp.get_type_at_location(options);
    if options_type.is_nil() {
        return Vec::new();
    }
    let dependencies_prop = tp
        .checker
        .get_property_of_type_exported(options_type, "dependencies");
    if dependencies_prop.is_nil() {
        return Vec::new();
    }
    let dependencies_type = tp
        .checker
        .get_type_of_symbol_at_location(dependencies_prop, options);
    if dependencies_type.is_nil() {
        return Vec::new();
    }
    // Get the number index type to extract individual dependency types
    let number_index_type = tp.checker.get_number_index_type(dependencies_type);
    if number_index_type.is_nil() {
        return Vec::new();
    }
    tp.unroll_union_members(number_index_type)
}

/// The diagnostic on `class_name` for the services `missing_type_names`
/// (one, or several as 'X', 'Y').
fn missing_services_diagnostic(
    ctx: &mut RuleContext<'_, '_>,
    class_name: Node,
    missing_type_names: &[String],
) -> Diagnostic {
    if missing_type_names.len() == 1 {
        // Singular
        return ctx.new_diagnostic(
            ctx.source_file,
            ctx.get_error_range(class_name),
            diag::Service_0_is_required_but_not_provided_by_dependencies_effect_missingEffectServiceDependency,
            Vec::new(),
            args![missing_type_names[0]],
        );
    }

    // Plural: format as 'X', 'Y'
    let mut quoted_names: Vec<String> = Vec::new();
    for name in missing_type_names {
        quoted_names.push(format!("'{name}'"));
    }
    let formatted = quoted_names.join(", ");
    ctx.new_diagnostic(
        ctx.source_file,
        ctx.get_error_range(class_name),
        diag::Services_0_are_required_but_not_provided_by_dependencies_effect_missingEffectServiceDependency,
        Vec::new(),
        args![formatted],
    )
}
