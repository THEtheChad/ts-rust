//! Port of Effect-TS/tsgo `internal/typeparser/constant_evaluation.go`.

use crate::effect::typeparser::*;
use crate::prelude::*;
use std::rc::Weak;

impl TypeParser<'_> {
    /// EvaluateConstantExpression evaluates the subset of JavaScript expressions
    /// that can be resolved statically without executing user code.
    // Go: typeparser/constant_evaluation.go EvaluateConstantExpression
    pub fn evaluate_constant_expression(&mut self, node: Node, location: Node) -> EvaluatorResult {
        if node.is_nil() {
            return new_result(None, false, false, false);
        }
        let location = if location.is_nil() { node } else { location };

        let number_symbol =
            self.checker
                .get_global_symbol_exported("Number", SymbolFlags::VALUE, None);
        // PORT: Go's `evaluate` closure refers to itself. The entity callback
        // reaches the evaluator through a weak cell that is set once it exists.
        let evaluate_cell: Rc<
            RefCell<Option<Weak<dyn Fn(&mut Checker, Node, Node) -> EvaluatorResult>>>,
        > = Rc::new(RefCell::new(None));
        let cell = evaluate_cell.clone();
        let program = self.program;
        let evaluate: Evaluator = new_evaluator(
            Rc::new(
                move |checker: &mut Checker,
                      entity: Node,
                      entity_location: Node|
                      -> EvaluatorResult {
                    if let Some(result) =
                        number_global_constant(checker, program, number_symbol, entity)
                    {
                        return result;
                    }

                    let result = checker.evaluate_entity(entity, entity_location);
                    if result.value.is_some() || entity.kind() != SyntaxKind::Identifier {
                        return result;
                    }

                    let Some(declaration) =
                        inlinable_constant_declaration(checker, program, entity, entity_location)
                    else {
                        return result;
                    };

                    let evaluate = cell
                        .borrow()
                        .as_ref()
                        .and_then(Weak::upgrade)
                        .expect("EvaluateConstantExpression: evaluator is set before use");
                    let constant = evaluate(&mut *checker, declaration.initializer(), declaration);
                    let mut resolved_other_files = constant.resolved_other_files;
                    if entity_location.is_some()
                        && get_source_file_of_node(entity_location)
                            != get_source_file_of_node(declaration)
                    {
                        resolved_other_files = true;
                    }
                    new_result(
                        constant.value,
                        constant.is_syntactically_string,
                        resolved_other_files,
                        true,
                    )
                },
            ),
            OuterExpressionKinds::OEK_PARENTHESES,
        );
        *evaluate_cell.borrow_mut() = Some(Rc::downgrade(&evaluate));
        evaluate(&mut *self.checker, node, location)
    }

    /// EvaluateConstantNumber evaluates a statically resolvable JavaScript numeric
    /// expression without executing user code.
    // Go: typeparser/constant_evaluation.go EvaluateConstantNumber
    pub fn evaluate_constant_number(&mut self, node: Node, location: Node) -> (f64, bool) {
        let result = self.evaluate_constant_expression(node, location);
        match result.value {
            Some(LiteralValue::Number(number)) => (number.0, true),
            _ => (0.0, false),
        }
    }
}

/// The value of `Number.POSITIVE_INFINITY`, `Number.NEGATIVE_INFINITY` or
/// `Number.NaN` (a property access, or an element access with a string
/// literal) when the receiver resolves to the global `Number`.
fn number_global_constant(
    checker: &mut Checker,
    program: &'static GoProgram,
    number_symbol: SymbolId,
    entity: Node,
) -> Option<EvaluatorResult> {
    let (receiver, property_name) = match entity.kind() {
        SyntaxKind::PropertyAccessExpression => (entity.expression(), entity.name().text()),
        SyntaxKind::ElementAccessExpression
            if is_string_literal_like(entity.argument_expression()) =>
        {
            (entity.expression(), entity.argument_expression().text())
        }
        _ => (Node::NIL, ""),
    };
    if number_symbol.is_nil()
        || receiver.is_nil()
        || TypeParser::new(program, &mut *checker).get_symbol_at_location(receiver) != number_symbol
    {
        return None;
    }
    let value = match property_name {
        "POSITIVE_INFINITY" => crate::jsnum::infinity(1),
        "NEGATIVE_INFINITY" => crate::jsnum::infinity(-1),
        "NaN" => crate::jsnum::nan(),
        _ => return None,
    };
    Some(new_result(
        Some(LiteralValue::Number(value)),
        false,
        false,
        false,
    ))
}

/// The declaration of the constant variable that the identifier `entity`
/// names, when its initializer can stand for it: a variable declaration
/// without a type annotation, with an initializer, and (for a reference at
/// `entity_location`) declared before that use and not at it.
fn inlinable_constant_declaration(
    checker: &mut Checker,
    program: &'static GoProgram,
    entity: Node,
    entity_location: Node,
) -> Option<Node> {
    let symbol = TypeParser::new(program, &mut *checker).get_symbol_at_location(entity);
    if symbol.is_nil() || !checker.is_constant_variable(symbol) {
        return None;
    }
    let declaration = checker.sym(symbol).value_declaration;
    if declaration.is_nil()
        || !is_variable_declaration(declaration)
        || declaration.type_().is_some()
        || declaration.initializer().is_nil()
        || entity_location.is_some()
            && (declaration == entity_location
                || !checker.is_block_scoped_name_declared_before_use(declaration, entity_location))
    {
        return None;
    }
    Some(declaration)
}
