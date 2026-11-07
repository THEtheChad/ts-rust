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

/// `TSC_RS_EFFECT=1`, read once.
static ENV_ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
/// Set by `--effect` (`take_flag`) or `enable`. It only ever goes from off
/// to on, so a concurrent first read of the environment cannot undo it.
static FORCED_ON: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// True when the Effect extension is on: `TSC_RS_EFFECT=1` (read once) or
/// `--effect` (`take_flag`). Off, a tsconfig `@effect/language-service`
/// plugin entry gives no Effect options (`etscore::parse_from_plugins` is
/// not called), so no rule, hook, option check or build info field runs.
#[must_use]
pub fn switch_on() -> bool {
    FORCED_ON.load(std::sync::atomic::Ordering::Acquire)
        || *ENV_ON.get_or_init(|| std::env::var(SWITCH_ENV).as_deref() == Ok("1"))
}

/// Turns the Effect extension on for this process (`--effect`, and tests that
/// check Effect behaviour in process; child-process tests set
/// `TSC_RS_EFFECT=1` instead). There is no way to turn it off again.
pub fn enable() {
    FORCED_ON.store(true, std::sync::atomic::Ordering::Release);
}

/// Takes `--effect` from the tsc command line `args` (without the program
/// name) and turns the extension on when there was one. A `--effect` that
/// Go's parser reads as an option name becomes an empty argument, which
/// Go's parser skips (`blank_flag`). The servers (`--lsp`, `--api`) take
/// the switch only from `TSC_RS_EFFECT=1`.
pub fn take_flag(args: &mut [String]) {
    let build = args
        .first()
        .is_some_and(|arg| crate::execute::execute_tsc::is_build_flag(arg));
    if blank_flag(args, build, SWITCH_FLAG) {
        enable();
    }
}

/// Makes each `flag` (`--effect`) of `args` that Go's parser reads as an
/// option name an empty argument, and returns true when there was one. A
/// flag that an option before it takes as its value stays: Go sets
/// `tsgo --outDir --effect` to outDir "--effect". `build`: the build command
/// line (its options differ). Go's parser skips an empty argument, and an
/// option that does not take the flag as its value does not take an empty
/// argument either (a boolean takes only "true", "false" or "null"; a list
/// value that starts with "-" is empty, as is an empty one; a tsconfig-only
/// option takes no value that starts with "-"). So the other arguments
/// parse as in Go, and only Go's unknown option error for the flag goes.
/// Not in a response file (`@file`): Go reads it as an unknown option.
// PORT: not in Go (followups49). The walk is Go's `parseStrings` with the
// port's `parse_option_value` on a scratch parser, so each option takes
// the arguments it takes in Go.
fn blank_flag(args: &mut [String], build: bool, flag: &str) -> bool {
    if !args.iter().any(|arg| arg == flag) {
        return false;
    }
    use crate::frontend::tsoptions::{
        BUILD_OPTIONS_DID_YOU_MEAN_DIAGNOSTICS, COMPILER_OPTIONS_DID_YOU_MEAN_DIAGNOSTICS,
        get_input_option_name, parse_command_line_worker,
    };
    let diagnostics = if build {
        &*BUILD_OPTIONS_DID_YOU_MEAN_DIAGNOSTICS
    } else {
        &*COMPILER_OPTIONS_DID_YOU_MEAN_DIAGNOSTICS
    };
    let mut parser = parse_command_line_worker(diagnostics, &[], None, String::new());
    let mut taken = false;
    let mut i = 0;
    while i < args.len() {
        if args[i] == flag {
            args[i].clear();
            taken = true;
            i += 1;
            continue;
        }
        let arg = &args[i];
        i += 1;
        // Go: `case '-':`. An empty argument, a response file and a file
        // name take no other argument.
        if !arg.starts_with('-') {
            continue;
        }
        let name = get_input_option_name(arg);
        if let Some(opt) = parser
            .options_map
            .get_option_declaration_from_name(name, true)
        {
            i = parser.parse_option_value(
                args,
                i,
                opt,
                diagnostics.option_type_mismatch_diagnostic,
            );
        }
    }
    taken
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

#[cfg(test)]
mod tests {
    use super::{SWITCH_FLAG, blank_flag};

    fn owned(args: &[&str]) -> Vec<String> {
        args.iter().map(|arg| (*arg).to_string()).collect()
    }

    /// `blank_flag` on `args`: the arguments after it, and whether it took
    /// a `--effect`.
    fn blank(args: &[&str], build: bool) -> (Vec<String>, bool) {
        let mut args = owned(args);
        let taken = blank_flag(&mut args, build, SWITCH_FLAG);
        (args, taken)
    }

    /// `--effect` is taken where Go's parser reads an option name, and kept
    /// where an option takes it as its value (followups49: each case gives
    /// Go's output at fed0bf24149f with the taken flag as "").
    #[test]
    fn take_flag_reads_the_command_line_as_go() {
        let taken = |args: &[&str]| (owned(args), true);
        let kept = |args: &[&str]| (owned(args), false);
        // An option name, also twice; other spellings are not the flag.
        assert_eq!(blank(&["--effect", "a.ts"], false), taken(&["", "a.ts"]));
        assert_eq!(
            blank(&["--effect", "--noEmit", "--effect"], false),
            taken(&["", "--noEmit", ""])
        );
        assert_eq!(
            blank(&["--EFFECT", "-effect", "--effect"], false),
            taken(&["--EFFECT", "-effect", ""])
        );
        // The value of a string (also by short name), number or enum option.
        for args in [
            ["--outDir", "--effect"],
            ["-p", "--effect"],
            ["--checkers", "--effect"],
            ["--target", "--effect"],
        ] {
            assert_eq!(blank(&args, false), kept(&args));
        }
        // A boolean, a list and a tsconfig-only option do not take it, so
        // the next argument stays a file name.
        assert_eq!(
            blank(&["--strict", "--effect", "true"], false),
            taken(&["--strict", "", "true"])
        );
        assert_eq!(
            blank(&["--lib", "--effect", "es5"], false),
            taken(&["--lib", "", "es5"])
        );
        assert_eq!(
            blank(&["--plugins", "--effect", "foo"], false),
            taken(&["--plugins", "", "foo"])
        );
        // After an option and its value.
        assert_eq!(
            blank(&["--outDir", "out", "--effect"], false),
            taken(&["--outDir", "out", ""])
        );
        // The build command line has its own options: `--generateTrace`
        // takes a value there, `--outDir` is unknown.
        let args = ["-b", "--generateTrace", "--effect"];
        assert_eq!(blank(&args, true), kept(&args));
        assert_eq!(
            blank(&["-b", "--outDir", "--effect"], true),
            taken(&["-b", "--outDir", ""])
        );
    }
}
