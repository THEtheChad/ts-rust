//! The `Effect.catch` and `Effect.catchAll` transformations whose callback
//! returns a call, which `catchAllToMapError` and `catchToOrElseSucceed` each
//! matched the same way.

use crate::effect::typeparser::*;
use crate::prelude::*;

/// The call that the callback of a `catch`/`catchAll` transformation
/// returns: the transformation's callee references `Effect.catch` or
/// `Effect.catchAll`, its first argument read as a lazy expression with
/// `flags` is a call expression, and that call has a callee. The `catch`
/// reference is checked before `catchAll`, as both rules did.
pub fn catch_callback_call(
    tp: &mut TypeParser<'_>,
    transformation: &PipingFlowTransformation,
    flags: LazyExpressionFlags,
) -> Option<Node> {
    if !tp.is_node_reference_to_effect_module_api(transformation.callee, member!("catch"))
        && !tp.is_node_reference_to_effect_module_api(transformation.callee, member!("catchAll"))
    {
        return None;
    }
    let callback = *transformation.args.first()?;
    let lazy = parse_lazy_expression(callback, flags)?;
    let call = lazy.expression;
    if call.is_nil() || call.kind() != SyntaxKind::CallExpression || call.expression().is_nil() {
        return None;
    }
    Some(call)
}

/// The name node of a catch callee and its text: the property name of
/// `Effect.catch` or `Effect.catchAll`, or no node and `"catch"` when the
/// callee is not a property access with a name.
pub fn catch_method_name(callee: Node) -> (Node, String) {
    if callee.kind() == SyntaxKind::PropertyAccessExpression && callee.name().is_some() {
        return (callee.name(), callee.name().text().to_string());
    }
    (Node::NIL, "catch".to_string())
}
