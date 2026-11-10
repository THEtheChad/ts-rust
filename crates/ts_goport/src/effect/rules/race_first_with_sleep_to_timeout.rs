//! Port of Effect-TS/tsgo `internal/rules/race_first_with_sleep_to_timeout.go`.

use crate::effect::diag;
use crate::effect::etscore::*;
use crate::effect::rule::*;
use crate::effect::typeparser::*;
use crate::prelude::*;

/// RaceFirstWithSleepToTimeout suggests timeoutOrElse when one side of an
/// Effect first-completion race is a hand-rolled timer arm.
// Go: rules/race_first_with_sleep_to_timeout.go RaceFirstWithSleepToTimeout
pub static RACE_FIRST_WITH_SLEEP_TO_TIMEOUT: Rule = Rule {
    name: "raceFirstWithSleepToTimeout",
    group: "style",
    description: "Suggests Effect.timeoutOrElse when Effect.raceFirst has exactly one sleep- or delay-based timer arm",
    default_severity: Severity::Suggestion,
    supported_effect: &["v4"],
    codes: &[377121],
    run: run_race_first_with_sleep_to_timeout,
};

fn run_race_first_with_sleep_to_timeout(ctx: &mut RuleContext<'_, '_>) -> Vec<Diagnostic> {
    let matches = analyze_race_first_with_sleep_to_timeout(ctx.tp, ctx.source_file);
    let mut diagnostics = Vec::with_capacity(matches.len());
    for m in &matches {
        diagnostics.push(ctx.new_diagnostic(
            m.source_file,
            m.location,
            diag::This_Effect_first_completion_race_has_exactly_one_sleep_or_delay_based_timer_arm_Effect_timeoutOrElse_expresses_the_timeout_and_fallback_directly_effect_raceFirstWithSleepToTimeout,
            Vec::new(),
            vec![],
        ));
    }
    diagnostics
}

// Go: rules/race_first_with_sleep_to_timeout.go RaceFirstWithSleepToTimeoutMatch
#[derive(Clone, Debug)]
pub struct RaceFirstWithSleepToTimeoutMatch {
    pub source_file: Node,
    pub location: TextRange,
}

/// AnalyzeRaceFirstWithSleepToTimeout finds Effect.raceFirst calls, and
/// two-element literal Effect.raceAllFirst calls, with exactly one timer arm.
// PORT: Go also returns nil for a nil `tp` or checker; the port has neither.
// Go: rules/race_first_with_sleep_to_timeout.go AnalyzeRaceFirstWithSleepToTimeout
pub fn analyze_race_first_with_sleep_to_timeout(
    tp: &mut TypeParser<'_>,
    sf: Node,
) -> Vec<RaceFirstWithSleepToTimeoutMatch> {
    if sf.is_nil() || tp.supported_effect_version() != EffectMajorVersion::V4 {
        return Vec::new();
    }

    let mut matches: Vec<RaceFirstWithSleepToTimeoutMatch> = Vec::new();
    let flows = tp.piping_flows(sf, true);
    for flow in flows.iter() {
        for i in 0..flow.transformations.len() {
            let transformation = &flow.transformations[i];
            if transformation.callee.is_nil() {
                continue;
            }
            let Some((left, right)) = race_arms(tp, flow, i) else {
                continue;
            };
            if is_race_first_timer_flow(tp, left.as_deref())
                == is_race_first_timer_flow(tp, right.as_deref())
            {
                continue;
            }
            matches.push(RaceFirstWithSleepToTimeoutMatch {
                source_file: sf,
                location: get_error_range_for_node(sf, transformation.callee),
            });
        }
    }
    matches
}

/// One arm of a race: the longest piping flow of the arm expression.
type RaceArm = Option<Rc<PartialPipingFlow>>;

/// The longest piping flow rooted at the race arm `node`.
fn longest_race_arm_at(tp: &mut TypeParser<'_>, node: Node) -> RaceArm {
    tp.longest_piping_flow_at(node, true)
        .map(|found| Rc::new(found.partial_piping_flow.clone()))
}

/// The two arms of the race at step `i` of `flow`: the flow before it and
/// the argument of a two-arm `raceFirst`, or the two elements of the array
/// that `raceAllFirst` races. `None` when the step is neither.
fn race_arms(tp: &mut TypeParser<'_>, flow: &PipingFlow, i: usize) -> Option<(RaceArm, RaceArm)> {
    let transformation = &flow.transformations[i];
    if tp.is_node_reference_to_effect_module_api(transformation.callee, member!("raceFirst")) {
        // An onWinner option has observable behavior that timeoutOrElse
        // cannot carry over, so only match the two-arm form.
        if transformation.args.len() != 1 {
            return None;
        }
        let left = flow.copy_prefix(i as i32);
        let right = longest_race_arm_at(tp, transformation.args[0]);
        return Some((left, right));
    }
    if !tp.is_node_reference_to_effect_module_api(transformation.callee, member!("raceAllFirst")) {
        return None;
    }
    let input = flow.copy_prefix(i as i32)?;
    if input.subject.node.is_nil() {
        return None;
    }
    let array = skip_parentheses(input.subject.node);
    if array.is_nil()
        || array.kind() != SyntaxKind::ArrayLiteralExpression
        || array.elements().len() != 2
    {
        return None;
    }
    let left = longest_race_arm_at(tp, array.elements().get(0));
    let right = longest_race_arm_at(tp, array.elements().get(1));
    Some((left, right))
}

// Go: rules/race_first_with_sleep_to_timeout.go isRaceFirstTimerFlow
fn is_race_first_timer_flow(tp: &mut TypeParser<'_>, flow: Option<&PartialPipingFlow>) -> bool {
    let Some(flow) = flow else {
        return false;
    };
    if flow.transformations.is_empty() {
        return false;
    }

    for i in 0..flow.transformations.len() {
        let transformation = &flow.transformations[i];
        if transformation.callee.is_nil() {
            continue;
        }
        if tp.is_node_reference_to_effect_module_api(transformation.callee, member!("sleep")) {
            return i == 0 && timer_trailing_transformations(tp, &flow.transformations[i + 1..]);
        }
        if tp.is_node_reference_to_effect_module_api(transformation.callee, member!("delay")) {
            return timer_trailing_transformations(tp, &flow.transformations[i + 1..]);
        }
    }
    false
}

// Go: rules/race_first_with_sleep_to_timeout.go timerTrailingTransformations
fn timer_trailing_transformations(
    tp: &mut TypeParser<'_>,
    transformations: &[PipingFlowTransformation],
) -> bool {
    for t in transformations {
        let callee = t.callee;
        if callee.is_nil()
            || !tp.is_node_reference_to_effect_module_api(callee, member!("flatMap"))
                && !tp.is_node_reference_to_effect_module_api(callee, member!("andThen"))
                && !tp.is_node_reference_to_effect_module_api(callee, member!("as"))
                && !tp.is_node_reference_to_effect_module_api(callee, member!("map"))
        {
            return false;
        }
    }
    true
}
