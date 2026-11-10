//! Port of Effect-TS/tsgo `internal/rules/global_timers.go`.

use crate::diagnostics::Message;
use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: rules/global_timers.go globalTimerAlternative
#[derive(Clone, Copy, Debug)]
pub struct GlobalTimerAlternative {
    pub name: &'static str,
    pub alternative: &'static str,
}

// PORT: Go keeps a `map[string]globalTimerAlternative` and ranges over it in
// random order. The global symbols of `setTimeout` and `setInterval` differ,
// and the match loop breaks on the first match, so the order does not change
// the output.
// Go: rules/global_timers.go globalTimerAlternatives
static GLOBAL_TIMER_ALTERNATIVES: &[(&str, GlobalTimerAlternative)] = &[
    (
        "setTimeout",
        GlobalTimerAlternative {
            name: "setTimeout",
            alternative: "Effect.sleep or Schedule",
        },
    ),
    (
        "setInterval",
        GlobalTimerAlternative {
            name: "setInterval",
            alternative: "Schedule or Effect.repeat",
        },
    ),
];

// Go: rules/global_timers.go GlobalTimers
pub static GLOBAL_TIMERS: Rule = Rule {
    name: "globalTimers",
    group: "effectNative",
    description: "Warns when using setTimeout/setInterval outside Effect generators instead of Effect.sleep/Schedule",
    default_severity: Severity::Off,
    supported_effect: &["v3", "v4"],
    codes: &[377072],
    run: run_global_timers_rule,
};

fn run_global_timers_rule(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    run_global_timers(ctx, false)
}

// Go: rules/global_timers.go GlobalTimersInEffect
pub static GLOBAL_TIMERS_IN_EFFECT: Rule = Rule {
    name: "globalTimersInEffect",
    group: "effectNative",
    description: "Warns when using setTimeout/setInterval inside Effect generators instead of Effect.sleep/Schedule",
    default_severity: Severity::Off,
    supported_effect: &["v3", "v4"],
    codes: &[377073],
    run: run_global_timers_in_effect,
};

fn run_global_timers_in_effect(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    run_global_timers(ctx, true)
}

// Go: rules/global_timers.go runGlobalTimers
fn run_global_timers(ctx: &mut RuleContext<'_, '_>, check_in_effect: bool) -> Vec<Diagnostic> {
    let global_symbols = resolve_global_timers(ctx.tp);
    if global_symbols.is_empty() {
        return Vec::new();
    }

    let mut message = diag::This_code_uses_1_the_corresponding_Effect_timer_API_is_0_from_Effect_effect_globalTimers;
    if check_in_effect {
        message = diag::This_Effect_code_uses_1_the_corresponding_timer_API_in_this_context_is_0_from_Effect_effect_globalTimersInEffect;
    }

    let mut diags: Vec<Diagnostic> = Vec::new();
    fn visit(
        ctx: &mut RuleContext<'_, '_>,
        diags: &mut Vec<Diagnostic>,
        global_symbols: &[(&'static str, SymbolId)],
        message: &'static Message,
        check_in_effect: bool,
        node: Node,
    ) {
        if node.kind() != SyntaxKind::CallExpression {
            return;
        }
        let Some(alt) = global_timer_call(ctx.tp, node, global_symbols, check_in_effect) else {
            return;
        };
        diags.push(ctx.new_diagnostic(
            ctx.source_file,
            get_error_range_for_node(ctx.source_file, node),
            message,
            Vec::new(),
            vec![alt.alternative.to_string(), alt.name.to_string()],
        ));
    }

    let sf = ctx.source_file;
    // PERF: Go walks the tree; the node order has the same nodes in the same
    // order (effperf4).
    for &n in ctx.tp.node_order(sf).nodes() {
        visit(
            ctx,
            &mut diags,
            &global_symbols,
            message,
            check_in_effect,
            n,
        );
    }

    diags
}

/// The global timer functions that resolve in the program, by name.
// PORT: Go `map[string]*ast.Symbol`; a list in GLOBAL_TIMER_ALTERNATIVES
// order (see the note there).
fn resolve_global_timers(tp: &mut TypeParser<'_>) -> Vec<(&'static str, SymbolId)> {
    let mut global_symbols: Vec<(&'static str, SymbolId)> =
        Vec::with_capacity(GLOBAL_TIMER_ALTERNATIVES.len());
    for &(name, _) in GLOBAL_TIMER_ALTERNATIVES {
        let symbol = tp
            .checker
            .resolve_name_exported(name, Node::NIL, SymbolFlags::VALUE, false);
        if symbol.is_some() {
            global_symbols.push((name, symbol));
        }
    }
    global_symbols
}

/// The Effect alternative of a call (inside an Effect when
/// `check_in_effect`, else outside one) whose callee resolves to one of the
/// global timer functions.
fn global_timer_call(
    tp: &mut TypeParser<'_>,
    node: Node,
    global_symbols: &[(&'static str, SymbolId)],
    check_in_effect: bool,
) -> Option<&'static GlobalTimerAlternative> {
    let in_effect = tp
        .get_effect_context_flags(node)
        .intersects(EffectContextFlags::IN_EFFECT);
    if in_effect != check_in_effect {
        return None;
    }
    let sym = tp.get_symbol_at_location(node.expression());
    let resolved = tp.resolve_to_global_symbol(sym);
    if resolved.is_nil() {
        return None;
    }
    let &(name, _) = global_symbols
        .iter()
        .find(|&&(_, global_symbol)| resolved == global_symbol)?;
    let alt = GLOBAL_TIMER_ALTERNATIVES
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, a)| a)
        .expect("global timer alternative");
    Some(alt)
}
