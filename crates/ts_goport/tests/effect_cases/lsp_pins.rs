//! Pinned answers of the Effect language service (lane W2-EF2).
//!
//! The diagnostic cases check the Effect rules through `tsc`; nothing else
//! checks the code fixes, refactors, hovers and completions that the
//! extension adds to the language server. These tests pin the answers that
//! the server gave when they were written, so a refactor of that code shows
//! any change. The expected answers are not from the Effect-TS/tsgo reference.
//!
//! A pin takes a case of a suite (its project and `node_modules` links), and
//! optionally replaces the case's one source file with a fixture of
//! `lsp_pins/<fixture>.ts`. It runs `TSC_RS_EFFECT=1 tsgo --lsp --stdio` in
//! the case's cwd, opens each source file of the case and asks:
//! - `textDocument/diagnostic`;
//! - `textDocument/codeAction` (`only: quickfix`) for each Effect diagnostic
//!   (code 377xxx), with that diagnostic in the context, without the
//!   "Disable <rule>" fixes that every Effect diagnostic has;
//! - with `REFACTORS`: `textDocument/codeAction` (`only: refactor`) at the
//!   start of each identifier, keeping the Effect refactors (kind
//!   `refactor.rewrite.effect.*`);
//! - with `HOVERS`: `textDocument/hover` at the start of each identifier,
//!   keeping the Layer hovers (those that list what a Layer provides or
//!   requires, or link its graph);
//! - with `COMPLETIONS`: `textDocument/completion` after each `.` on a line
//!   with `extends`, keeping the snippet items (the client has no snippet
//!   support, so only the Effect completions are snippets).
//!
//! Answers that give the same result at several positions are written once,
//! with every position. `lsp_pins/<pin>.txt` holds the expected text;
//! `EFFECT_LSP_PINS_BLESS=1` writes it instead of comparing.

use std::fmt::Write as _;
use std::fs;
use std::path::Path;

use crate::effect_lsp::json::Json;
use crate::effect_lsp::{Server, file_uri};
use crate::{CaseDir, DIR, MARK, SUITES};

/// The client capabilities: pull diagnostics, code action literals and
/// markdown hovers.
const CAPABILITIES: &str = r#"{"textDocument":{"diagnostic":{"dynamicRegistration":false},"codeAction":{"codeActionLiteralSupport":{"codeActionKind":{"valueSet":["quickfix","refactor","source"]}}},"hover":{"contentFormat":["markdown","plaintext"]}}}"#;

/// What a pin asks besides the diagnostics and their quick fixes.
const FIXES: u8 = 0;
const REFACTORS: u8 = 1;
const HOVERS: u8 = 2;
const COMPLETIONS: u8 = 4;

/// One test per pin: `<test name>: <suite> <case> [<fixture>] <asks>;`.
macro_rules! pins {
    ($($test:ident: $suite:literal $case:literal $($fixture:literal)? => $asks:expr;)*) => {
        $(
            #[test]
            fn $test() {
                run(stringify!($test), $suite, $case, [$($fixture)?].first().copied(), $asks);
            }
        )*
    };
}

pins! {
    // Schema generation (`ls/schemagen.rs`): the three type-to-Schema refactors.
    schemagen_v3: "effect_v3" "schemaSync" "schemagen" => REFACTORS;
    schemagen_v4: "effect_v4" "schemaSync" "schemagen" => REFACTORS;
    // `effectSchemaSelfInClasses` and the other extends-clause completions.
    schema_self_in_classes_v3: "effect_v3" "schemaSync" "schema_self_in_classes" => COMPLETIONS;
    schema_self_in_classes_v4: "effect_v4" "schemaSync" "schema_self_in_classes" => COMPLETIONS;
    // `writeTagClassAccessors` (v3 only).
    write_tag_class_accessors: "effect_v3" "schemaSync" "write_tag_class_accessors" => REFACTORS;
    // `pipeableToDatafirst` and the other refactors on pipes.
    pipeable_to_datafirst_v3: "effect_v3" "schemaSync" "pipeable_to_datafirst" => REFACTORS;
    pipeable_to_datafirst_v4: "effect_v4" "schemaSync" "pipeable_to_datafirst" => REFACTORS;
    // Layer hovers (`ls/hover.rs` `format_layer_hover`).
    layer_graph_simple: "effect_v4" "layerGraphSimple" => HOVERS;
    layer_graph_multiple: "effect_v4" "layerGraphMultiple" => HOVERS;
    layer_graph_follow_symbols: "effect_v4" "layerGraphFollowSymbols" => HOVERS;
    layer_graph_documented: "effect_v4" "layerGraphDocumented" => HOVERS;
    layer_graph_effect: "effect_v4" "layerGraphEffect" => HOVERS;
    layer_merge_all_v3: "effect_v3" "layerMergeAllWithDependencies" => HOVERS;
    layer_merge_all_v4: "effect_v4" "layerMergeAllWithDependencies" => HOVERS;
    layer_merge_all_non_property_access: "effect_v4" "layerMergeAllWithDependenciesNonPropertyAccess" => FIXES;
    // Quick fixes of the rules this lane splits.
    catch_all_to_map_error_v3: "effect_v3" "catchAllToMapError" => FIXES;
    catch_all_to_map_error_v4: "effect_v4" "catchAllToMapError" => FIXES;
    catch_all_to_map_error_effect_fn: "effect_v4" "catchAllToMapError_effectFn" => FIXES;
    catch_to_or_else_succeed_v3: "effect_v3" "catchToOrElseSucceed" => FIXES;
    catch_to_or_else_succeed_v4: "effect_v4" "catchToOrElseSucceed" => FIXES;
    promise_in_effect_success: "effect_v4" "promiseInEffectSuccess" => FIXES;
    schema_literal_non_finite: "effect_v4" "schemaLiteralNonFinite" => FIXES;
    unsafe_effect_type_assertion_v3: "effect_v3" "unsafeEffectTypeAssertion" => FIXES;
    unsafe_effect_type_assertion_v4: "effect_v4" "unsafeEffectTypeAssertion" => FIXES;
    extends_native_error_v3: "effect_v3" "extendsNativeError" => FIXES;
    extends_native_error_v4: "effect_v4" "extendsNativeError" => FIXES;
    global_timers_v3: "effect_v3" "globalTimers" => FIXES;
    global_timers_v4: "effect_v4" "globalTimersInEffect" => FIXES;
    match_effect_to_match_v3: "effect_v3" "matchEffectToMatch" => FIXES;
    match_effect_to_match_v4: "effect_v4" "matchEffectToMatch" => FIXES;
    run_of_exit_to_run_exit_v3: "effect_v3" "runOfExitToRunExit" => FIXES;
    run_of_exit_to_run_exit_v4: "effect_v4" "runOfExitToRunExit" => FIXES;
    prefer_succeed_some_or_none_v3: "effect_v3" "preferSucceedSomeOrNone" => FIXES;
    prefer_succeed_some_or_none_v4: "effect_v4" "preferSucceedSomeOrNone" => FIXES;
    service_not_as_class: "effect_v4" "serviceNotAsClass" => FIXES;
    // `effectFnOpportunity` (the rule's parser and the fix's replacement).
    effect_fn_opportunity_basic_v3: "effect_v3" "effectFnOpportunity_basic" => FIXES;
    effect_fn_opportunity_basic: "effect_v4" "effectFnOpportunity_basic" => FIXES;
    effect_fn_opportunity_comments: "effect_v4" "effectFnOpportunity_comments" => FIXES;
    effect_fn_opportunity_gen_with_this: "effect_v4" "effectFnOpportunity_genWithThis" => FIXES;
    effect_fn_opportunity_hoisted_declaration: "effect_v4" "effectFnOpportunity_hoistedDeclaration" => FIXES;
    effect_fn_opportunity_inferred: "effect_v4" "effectFnOpportunity_inferred" => FIXES;
    effect_fn_opportunity_inferred_layer: "effect_v4" "effectFnOpportunity_inferredLayer" => FIXES;
    effect_fn_opportunity_inferred_layer_this: "effect_v4" "effectFnOpportunity_inferredLayerThis" => FIXES;
    effect_fn_opportunity_inferred_of: "effect_v4" "effectFnOpportunity_inferredOf" => FIXES;
    effect_fn_opportunity_inferred_service_make: "effect_v4" "effectFnOpportunity_inferredServiceMake" => FIXES;
    effect_fn_opportunity_layer_comments: "effect_v4" "effectFnOpportunity_layerComments" => FIXES;
    effect_fn_opportunity_multiple_returns: "effect_v4" "effectFnOpportunity_multipleReturns" => FIXES;
    effect_fn_opportunity_nested_initializer: "effect_v4" "effectFnOpportunity_nestedInitializer" => FIXES;
    effect_fn_opportunity_no_pipe_with_span: "effect_v4" "effectFnOpportunity_noPipeWithSpan" => FIXES;
    effect_fn_opportunity_overloads_ok: "effect_v4" "effectFnOpportunity_overloadsOk" => FIXES;
    effect_fn_opportunity_params: "effect_v4" "effectFnOpportunity_params" => FIXES;
    effect_fn_opportunity_params_in_pipe: "effect_v4" "effectFnOpportunity_paramsInPipe" => FIXES;
    effect_fn_opportunity_pipe_with_span: "effect_v4" "effectFnOpportunity_pipeWithSpan" => FIXES;
    effect_fn_opportunity_plain_few: "effect_v4" "effectFnOpportunity_plainFew" => FIXES;
    effect_fn_opportunity_plain_many: "effect_v4" "effectFnOpportunity_plainMany" => FIXES;
    effect_fn_opportunity_return_type: "effect_v4" "effectFnOpportunity_returnType" => FIXES;
    effect_fn_opportunity_sibling_declarators: "effect_v4" "effectFnOpportunity_siblingDeclarators" => FIXES;
    effect_fn_opportunity_source_text: "effect_v4" "effectFnOpportunity_sourceText" => FIXES;
    effect_fn_opportunity_type_params: "effect_v4" "effectFnOpportunity_typeParams" => FIXES;
    effect_fn_opportunity_unary_pipeables: "effect_v4" "effectFnOpportunity_unaryPipeables" => FIXES;
}

/// Runs the pin `test` and compares (or, blessing, writes) its answers.
fn run(test: &str, suite: &str, name: &str, fixture: Option<&str>, asks: u8) {
    let case = &SUITES[suite].cases[name];
    let files = with_fixture(&case.files, fixture);
    let case_dir = CaseDir::create(suite, name, &format!("pin-{test}"), &files);
    let mut server = Server::start(&case_dir.real_cwd, CAPABILITIES);
    let mut out = String::new();
    for (path, text) in files.iter().filter(|(path, _)| is_source(path)) {
        let _ = writeln!(out, "{MARK}file {path}");
        let uri = file_uri(&case_dir.real.join(path));
        ask_file(&mut server, &uri, text, asks, &mut out);
    }
    drop(server);
    let actual = case_dir.normalize(&out);
    let expected_path = Path::new(DIR).join("lsp_pins").join(format!("{test}.txt"));
    if std::env::var_os("EFFECT_LSP_PINS_BLESS").is_some() {
        fs::write(&expected_path, &actual)
            .unwrap_or_else(|e| panic!("write {}: {e}", expected_path.display()));
    } else {
        let expected = fs::read_to_string(&expected_path).unwrap_or_else(|e| {
            panic!(
                "read {}: {e} (EFFECT_LSP_PINS_BLESS=1 writes it)",
                expected_path.display()
            )
        });
        assert!(
            actual == expected,
            "{test}: the answers differ from {}; project kept in {}\n--- actual\n{actual}",
            expected_path.display(),
            case_dir.dir.display()
        );
    }
    case_dir.remove();
}

/// The files of a case, with its one source file's text replaced by
/// `lsp_pins/<fixture>.ts` when a fixture is given.
fn with_fixture(files: &[(String, String)], fixture: Option<&str>) -> Vec<(String, String)> {
    let mut files = files.to_vec();
    if let Some(fixture) = fixture {
        let path = Path::new(DIR)
            .join("lsp_pins")
            .join(format!("{fixture}.ts"));
        let text =
            fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
        let mut sources = files.iter_mut().filter(|(path, _)| is_source(path));
        let source = sources.next().expect("a source file");
        assert!(
            sources.next().is_none(),
            "a fixture replaces one source file"
        );
        source.1 = text;
    }
    files
}

/// Whether a case file is a source file of the project (not a config or a
/// package file).
fn is_source(path: &str) -> bool {
    !path.contains("node_modules/")
        && [".ts", ".tsx", ".mts", ".cts"]
            .iter()
            .any(|ext| path.ends_with(ext))
}

/// Asks the server about one file and writes the answers into `out`.
fn ask_file(server: &mut Server, uri: &str, text: &str, asks: u8, out: &mut String) {
    let document = format!(r#"{{"uri":"{uri}"}}"#);
    server.notify(
        "textDocument/didOpen",
        &format!(
            r#"{{"textDocument":{{"uri":"{uri}","languageId":"typescript","version":1,"text":{}}}}}"#,
            Json::String(text.to_string()).compact()
        ),
    );
    let diagnostics = result(
        &Json::parse(&server.request(
            "textDocument/diagnostic",
            &format!(r#"{{"textDocument":{document}}}"#),
        )),
        out,
        "diagnostic",
    );
    let items = diagnostics
        .as_ref()
        .and_then(|d| d.get("items"))
        .map(|items| items.as_array().to_vec())
        .unwrap_or_default();
    for diagnostic in items.iter().filter(|d| is_effect_diagnostic(d)) {
        let range = diagnostic
            .get("range")
            .expect("a diagnostic range")
            .compact();
        let params = format!(
            r#"{{"textDocument":{document},"range":{range},"context":{{"diagnostics":[{}],"only":["quickfix"]}}}}"#,
            diagnostic.compact()
        );
        let code = diagnostic
            .get("code")
            .map(Json::compact)
            .unwrap_or_default();
        let answer = without_disable_fixes(&server.request("textDocument/codeAction", &params));
        result(&answer, out, &format!("quickfix {code} {range}"));
    }
    let mut grouped = Grouped::default();
    for (line, character) in identifier_starts(text) {
        let position = format!(r#"{{"line":{line},"character":{character}}}"#);
        let at = format!("{line}:{character}");
        if asks & REFACTORS != 0 {
            let params = format!(
                r#"{{"textDocument":{document},"range":{{"start":{position},"end":{position}}},"context":{{"diagnostics":[],"only":["refactor"]}}}}"#
            );
            let answer = Json::parse(&server.request("textDocument/codeAction", &params));
            grouped.add("refactor", &at, effect_refactors(&answer));
        }
        if asks & HOVERS != 0 {
            let params = format!(r#"{{"textDocument":{document},"position":{position}}}"#);
            let answer = Json::parse(&server.request("textDocument/hover", &params));
            grouped.add("hover", &at, layer_hover(&answer));
        }
    }
    if asks & COMPLETIONS != 0 {
        for (line, character) in extends_member_positions(text) {
            let params = format!(
                r#"{{"textDocument":{document},"position":{{"line":{line},"character":{character}}},"context":{{"triggerKind":2,"triggerCharacter":"."}}}}"#
            );
            let answer = Json::parse(&server.request("textDocument/completion", &params));
            grouped.add(
                "completion",
                &format!("{line}:{character}"),
                snippet_items(&answer),
            );
        }
    }
    grouped.write(out);
}

/// A code-action answer without the actions of the `effectDisable` fix
/// ("Disable <rule> for this line" and "... for entire file"), which every
/// Effect diagnostic has.
fn without_disable_fixes(answer: &str) -> Json {
    let mut answer = Json::parse(answer);
    if let Json::Object(members) = &mut answer
        && let Some((_, Json::Array(actions))) = members.iter_mut().find(|(k, _)| k == "result")
    {
        actions.retain(|action| {
            !action
                .get("title")
                .and_then(Json::as_str)
                .is_some_and(|title| {
                    title.starts_with("Disable ")
                        && (title.ends_with(" for this line")
                            || title.ends_with(" for entire file"))
                })
        });
    }
    answer
}

/// Writes the `result` (or `error`) of an answer under `title`, and returns
/// the result.
fn result(answer: &Json, out: &mut String, title: &str) -> Option<Json> {
    let _ = writeln!(out, "{MARK}{title}");
    if let Some(error) = answer.get("error") {
        let _ = writeln!(out, "error {}", error.pretty());
        return None;
    }
    let value = answer.get("result").cloned().unwrap_or(Json::Null);
    let _ = writeln!(out, "{}", value.pretty());
    Some(value)
}

/// Answers kept by position: each distinct answer once, in the order of its
/// first position, with all its positions.
#[derive(Default)]
struct Grouped {
    entries: Vec<(String, Vec<String>, String)>,
}

impl Grouped {
    /// Adds the kept part of an answer at `at` (`None`: nothing kept).
    fn add(&mut self, kind: &str, at: &str, kept: Option<Json>) {
        let Some(kept) = kept else { return };
        let text = kept.pretty();
        match self
            .entries
            .iter_mut()
            .find(|(k, _, t)| k == kind && *t == text)
        {
            Some((_, positions, _)) => positions.push(at.to_string()),
            None => self
                .entries
                .push((kind.to_string(), vec![at.to_string()], text)),
        }
    }

    fn write(&self, out: &mut String) {
        for (kind, positions, text) in &self.entries {
            let _ = writeln!(out, "{MARK}{kind} {}\n{text}", positions.join(" "));
        }
    }
}

/// An Effect diagnostic: code 377000 to 377999.
fn is_effect_diagnostic(diagnostic: &Json) -> bool {
    diagnostic
        .get("code")
        .and_then(|code| match code {
            Json::Number(n) => n.parse::<u32>().ok(),
            _ => None,
        })
        .is_some_and(|code| (377_000..378_000).contains(&code))
}

/// The Effect refactors of a code-action answer, or its error.
fn effect_refactors(answer: &Json) -> Option<Json> {
    if let Some(error) = answer.get("error") {
        return Some(Json::Object(vec![("error".to_string(), error.clone())]));
    }
    let actions: Vec<Json> = answer
        .get("result")
        .map(Json::as_array)
        .unwrap_or_default()
        .iter()
        .filter(|action| {
            action
                .get("kind")
                .and_then(Json::as_str)
                .is_some_and(|kind| kind.starts_with("refactor.rewrite.effect."))
        })
        .cloned()
        .collect();
    (!actions.is_empty()).then_some(Json::Array(actions))
}

/// A hover answer that lists what a Layer provides or requires, or links its
/// graph, or an error.
fn layer_hover(answer: &Json) -> Option<Json> {
    if let Some(error) = answer.get("error") {
        return Some(Json::Object(vec![("error".to_string(), error.clone())]));
    }
    let result = answer.get("result")?;
    let text = result.compact();
    [
        "provides: ",
        "requires: ",
        "Show full graph",
        "Show outline",
    ]
    .iter()
    .any(|marker| text.contains(marker))
    .then(|| result.clone())
}

/// The snippet items of a completion answer, or its error.
fn snippet_items(answer: &Json) -> Option<Json> {
    if let Some(error) = answer.get("error") {
        return Some(Json::Object(vec![("error".to_string(), error.clone())]));
    }
    let result = answer.get("result")?;
    let items = result.get("items").unwrap_or(result);
    let snippets: Vec<Json> = items
        .as_array()
        .iter()
        .filter(|item| item.get("insertTextFormat") == Some(&Json::Number("2".to_string())))
        .cloned()
        .collect();
    (!snippets.is_empty()).then_some(Json::Array(snippets))
}

/// The 0-based line and UTF-16 character of each identifier start of `text`.
fn identifier_starts(text: &str) -> Vec<(usize, usize)> {
    let is_part = |c: char| c.is_alphanumeric() || c == '_' || c == '$';
    let mut starts = Vec::new();
    for (line, line_text) in text.split('\n').enumerate() {
        let mut character = 0;
        let mut previous = ' ';
        for c in line_text.chars() {
            if is_part(c) && !is_part(previous) && !c.is_ascii_digit() {
                starts.push((line, character));
            }
            previous = c;
            character += c.len_utf16();
        }
    }
    starts
}

/// The position after each `.` on a line that contains `extends`.
fn extends_member_positions(text: &str) -> Vec<(usize, usize)> {
    let mut positions = Vec::new();
    for (line, line_text) in text.split('\n').enumerate() {
        if !line_text.contains("extends") {
            continue;
        }
        let mut character = 0;
        for c in line_text.chars() {
            character += c.len_utf16();
            if c == '.' {
                positions.push((line, character));
            }
        }
    }
    positions
}
