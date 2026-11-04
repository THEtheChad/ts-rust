//! Native Effect diagnostics: a port of Effect-TS/tsgo at
//! `@effect/tsgo@0.51.1` (`47cb1ed7704aff44cacaa0f4d2ef24de990cba0e`).
//!
//! The reference patches typescript-go in a few places (`_patches/typescript`).
//! Each patch site here names its patch. The rules run after the checker has
//! checked a source file, with the same checker, and add their diagnostics
//! to the checker's diagnostics (codes 377000 to 377999). Nothing runs unless
//! the Effect extension is on (`TSC_RS_EFFECT=1` or `--effect`,
//! [`switch_on`]; off by default, for Go parity) and the tsconfig has an
//! `@effect/language-service` plugin entry with diagnostics enabled. A
//! standalone API process (`tsgo --api`) runs nothing unless
//! `TSGO_EFFECT_API=1` too (`rulerunner::set_api_process`).
//!
//! `effect::ls` ports the language service hooks of effect-tsgo 0.51.1
//! (`etslshooks`): hover, the inlay hint filter and the hook sites that the
//! later editor lanes fill. It uses `ls`, so it builds in `ts_goport` (its
//! `effect` module declares it); the rest of `effect` is `goport_core`'s.
//!
//! Read `crates/ts_goport/src/effect/PORTING.md` before editing.

use crate::gostd::Context;
use crate::prelude::*;

pub mod codegens;
pub mod configcheck;
pub mod configraw;
pub mod diag;
pub mod directives;
pub mod etscore;
pub mod graph;
pub mod keybuilder;
pub mod layergraph;
pub mod pluginoptions;
pub mod rule;
pub mod rulerunner;
pub mod rules;
pub mod typeparser;

// PORT: not in Go or Effect-TS/tsgo (perf-rearch lane E0). Go at the pin
// loads no plugin, so the Effect extension is off by default, and the
// output (diagnostics, counts, exit code, build info and LSP answers) is
// Go's. `TSC_RS_EFFECT=1` (every bin of the process: tsc, the LSP and the
// API server) or `tsgo --effect` (`take_flag`) turns it on.
/// The environment variable that turns the Effect extension on.
pub const SWITCH_ENV: &str = "TSC_RS_EFFECT";
/// The tsc command line flag that turns the Effect extension on.
pub const SWITCH_FLAG: &str = "--effect";

/// 0: not read yet, 1: off, 2: on.
static SWITCH: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

/// True when the Effect extension is on: `TSC_RS_EFFECT=1` (read once) or
/// `--effect` (`take_flag`). Off, a tsconfig `@effect/language-service`
/// plugin entry gives no Effect options (`etscore::parse_from_plugins` is
/// not called), so no rule, hook, option check or build info field runs.
#[must_use]
pub fn switch_on() -> bool {
    use std::sync::atomic::Ordering;
    match SWITCH.load(Ordering::Relaxed) {
        0 => {
            let on = std::env::var(SWITCH_ENV).as_deref() == Ok("1");
            SWITCH.store(if on { 2 } else { 1 }, Ordering::Relaxed);
            on
        }
        state => state == 2,
    }
}

/// Sets the Effect extension switch for this process. Tests that check
/// Effect behaviour in process call it (child-process tests set
/// `TSC_RS_EFFECT=1` instead).
pub fn set_switch(on: bool) {
    SWITCH.store(if on { 2 } else { 1 }, std::sync::atomic::Ordering::Relaxed);
}

/// Takes `--effect` from the tsc command line `args` (without the program
/// name) and turns the extension on when there was one. As for `--fast`
/// (`fast_mode::take_flag`), a `--effect` that Go's parser reads as an
/// option name becomes an empty argument, which Go's parser skips. The
/// servers (`--lsp`, `--api`) take the switch only from `TSC_RS_EFFECT=1`.
pub fn take_flag(args: &mut [String]) {
    let build = args
        .first()
        .is_some_and(|arg| crate::execute::execute_tsc::is_build_flag(arg));
    if crate::fast_mode::blank_flag(args, build, SWITCH_FLAG) {
        set_switch(true);
    }
}

/// Effect-TS/tsgo patch 005 `RelationError`: a type relation error that the
/// relater reported, kept for Effect rules.
#[derive(Clone, Copy, Debug)]
pub struct RelationError {
    pub source: TypeId,
    pub target: TypeId,
    pub error_node: Node,
}

/// Go `rule.IsEffectCode`.
#[must_use]
pub fn is_effect_code(code: i32) -> bool {
    (377_000..378_000).contains(&code)
}

// Go: etscheckerhooks/init.go afterCheckSourceFile
/// Runs the Effect rules for `source_file` and adds their diagnostics to
/// the checker. `check_source_file` calls it once per file, after the type
/// check and the unused check (the reference calls it before the unused
/// check; see the comment there).
pub fn after_check_source_file(ctx: &Context, c: &mut Checker, source_file: Node) {
    let Some(effect_config) = c.compiler_options.effect.clone() else {
        return;
    };
    let min = rulerunner::min_visible_severity(Some(&effect_config));
    let program = c.program;
    let Ok(diagnostics) = rulerunner::run(
        ctx,
        program,
        c,
        source_file,
        Some(&effect_config),
        None,
        min,
    ) else {
        return;
    };
    for diag in diagnostics {
        c.diagnostics.add(diag);
    }
}

// Go: checker.go (patch 002) GetRelationErrors
impl Checker {
    /// The relation errors collected while checking `sf`. Checks the file
    /// first when it is not checked yet.
    pub fn get_relation_errors(&mut self, sf: Node) -> Vec<RelationError> {
        if !self.source_file_links.get(sf).type_checked {
            // Go passes `c.ctx`, which can be nil; the file is checked either way.
            let ctx = self
                .ctx
                .clone()
                .unwrap_or_else(crate::gostd::context::background);
            self.get_diagnostics(&ctx, sf, false);
        }
        self.effect_relation_errors
            .get(&sf)
            .cloned()
            .unwrap_or_default()
    }
}

// Go: etsexecutehooks/init.go FilterDiagnosticsForExitCode
/// The diagnostics that decide the tsc exit code: Effect diagnostics whose
/// category the plugin options ignore are left out. Non-Effect diagnostics
/// always count.
/// Without Effect options it borrows `diags`, so projects without the plugin
/// pay nothing.
#[must_use]
pub fn filter_diagnostics_for_exit_code<'a>(
    opts: Option<&etscore::EffectPluginOptions>,
    diags: &'a [Diagnostic],
) -> std::borrow::Cow<'a, [Diagnostic]> {
    let Some(opts) = opts else {
        return std::borrow::Cow::Borrowed(diags);
    };
    diags
        .iter()
        .filter(|d| !(is_effect_code(d.code) && should_ignore_for_exit_code(opts, d.category)))
        .cloned()
        .collect::<Vec<_>>()
        .into()
}

fn should_ignore_for_exit_code(
    opts: &etscore::EffectPluginOptions,
    category: crate::diagnostics::Category,
) -> bool {
    use crate::diagnostics::Category;
    match category {
        Category::Suggestion | Category::Message => opts.ignore_effect_suggestions_in_tsc_exit_code,
        Category::Warning => opts.ignore_effect_warnings_in_tsc_exit_code,
        Category::Error => opts.ignore_effect_errors_in_tsc_exit_code,
        _ => false,
    }
}
