//! Port of Effect-TS/tsgo `internal/typeparser/reconstruct.go`.

use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: typeparser/reconstruct.go nodeText
/// nodeText extracts the source text of a node from the source file.
pub fn node_text(sf: Node, node: Node) -> String {
    if node.is_nil() || sf.is_nil() {
        return String::new();
    }
    let text = source_file_text(sf);
    let pos = node.pos();
    let end = node.end();
    if pos >= 0 && end >= pos && end as usize <= text.len() {
        return go_cut_slice(&text, pos as usize, end as usize).into_owned();
    }
    String::new()
}

// Go: typeparser/reconstruct.go ReconstructPipingFlow
/// ReconstructPipingFlow reconstructs a piping flow into a string expression
/// by applying transformations sequentially.
/// For example: subject with transformations [f, g] becomes "g(f(subject))".
///
/// Note: Effect.fn and Effect.fnUntraced transformations cannot be reconstructed
/// as a chain since they are part of the Effect.fn call itself. In this case,
/// the original subject node text is returned.
pub fn reconstruct_piping_flow(
    sf: Node,
    subject: Option<&PipingFlowSubject>,
    transformations: &[PipingFlowTransformation],
) -> String {
    let Some(subject) = subject else {
        return String::new();
    };
    if sf.is_nil() {
        return String::new();
    }

    let is_effect_fn = |t: &PipingFlowTransformation| {
        t.kind == TransformationKind::EffectFn || t.kind == TransformationKind::EffectFnUntraced
    };
    let mut result = node_text(sf, subject.node);
    // When every transformation is effectFn or effectFnUntraced, reconstruction
    // is not possible: the original node text stands. Otherwise Effect.fn
    // transformations cannot be reconstructed as part of a chain and are skipped.
    for t in transformations.iter().filter(|t| !is_effect_fn(t)) {
        result = apply_transformation_text(sf, t, &result);
    }
    result
}

/// The text of `t` applied to `result`: `callee(result)` for a single-arg
/// call or a constant, `callee(args...)(result)` for a curried pipe or
/// pipeable step.
fn apply_transformation_text(sf: Node, t: &PipingFlowTransformation, result: &str) -> String {
    let callee_text = node_text(sf, t.callee);
    if t.kind == TransformationKind::Call || t.args.is_empty() {
        return callee_text + "(" + result + ")";
    }
    let args_text: Vec<String> = t.args.iter().map(|&arg| node_text(sf, arg)).collect();
    callee_text + "(" + &args_text.join(", ") + ")(" + result + ")"
}
