//! Not a Go port (perf/first-principles A1): the language server reuses a
//! program whose changed file keeps its synthetic imports (a JSX runtime or
//! `importHelpers` import), where Go builds a new one. The project then
//! reports Go's update kind for a new program (`SAME_FILE_NAMES`), and
//! keeps the storage of the old program.

use std::rc::Rc;
use std::sync::Arc;

use ts_goport::frontend::compiler::NewProgram;
use ts_goport::lsp::lsproto;
use ts_goport::project::{ProgramUpdateKind, Session};

use super::projecttestutil::{self, files};
use super::util::*;

const TSX_CONFIG: &str = r#"{"compilerOptions":{"jsx":"react-jsx","module":"esnext","moduleResolution":"bundler","baseUrl":".","types":[]}}"#;
const A_URI: &str = "file:///src/a.tsx";
const A_TEXT: &str = "export const A = () => <div />;\n";
const C_URI: &str = "file:///src/c.ts";
const C_TEXT: &str = "export const c = 1;\n";

fn setup() -> (Rc<Session>, projecttestutil::SessionUtils) {
    let (session, utils) = projecttestutil::setup(files(&[
        ("/src/tsconfig.json", TSX_CONFIG),
        (
            "/src/node_modules/react/package.json",
            r#"{"name":"react","version":"19.0.0","types":"index.d.ts"}"#,
        ),
        ("/src/node_modules/react/index.d.ts", "export {};\n"),
        (
            "/src/node_modules/react/jsx-runtime.d.ts",
            "export declare function jsx(type: any, props: any): any;\nexport declare namespace JSX { interface IntrinsicElements { [name: string]: any } type Element = any; }\n",
        ),
        ("/src/a.tsx", A_TEXT),
        ("/src/c.ts", C_TEXT),
    ]));
    open_kind(
        &session,
        A_URI,
        A_TEXT,
        lsproto::LanguageKind::TYPE_SCRIPT_REACT,
    );
    open(&session, C_URI, C_TEXT);
    let _ = language_service(&session, A_URI);
    (session, utils)
}

fn update_kind(session: &Rc<Session>) -> ProgramUpdateKind {
    session
        .snapshot()
        .project_collection
        .configured_project(&path("/src/tsconfig.json"))
        .expect("configured project")
        .borrow()
        .program_update_kind
}

/// The new program shares the loads of the old one.
fn same_storage(a: &NewProgram, b: &NewProgram) -> bool {
    Arc::ptr_eq(
        &a.processed_files.resolved_modules,
        &b.processed_files.resolved_modules,
    )
}

/// The JSX runtime specifier of `/src/a.tsx` and whether its import
/// declaration is in the program's own `a.tsx`.
fn jsx_specifier(p: &NewProgram) -> (ts_goport::core::Node, bool) {
    let specifier = p
        .processed_files
        .jsx_runtime_import_specifiers
        .as_ref()
        .and_then(|map| map.get(&path("/src/a.tsx")))
        .expect("a.tsx has a JSX runtime import")
        .specifier;
    let file = p.get_source_file("/src/a.tsx").unwrap();
    (specifier, specifier.parent().parent() == file.root)
}

child_test! {
    fn tsx_body_edit_reuses_storage_with_gos_update_kind() {
        let (session, _) = setup();
        let old = program(&session, A_URI);
        let (old_specifier, in_root) = jsx_specifier(&old);
        assert!(in_root);
        edit(&session, A_URI, 2, (0, 31), (0, 31), " // x");
        let new = program(&session, A_URI);
        assert!(!same_program(&old, &new));
        assert!(same_storage(&old, &new), "a body edit reuses the program");
        assert_eq!(update_kind(&session), ProgramUpdateKind::SAME_FILE_NAMES);
        let (new_specifier, in_root) = jsx_specifier(&new);
        assert!(in_root, "the new specifier is in the new a.tsx");
        assert_ne!(new_specifier, old_specifier);
        // The old program keeps its specifier, in its own a.tsx.
        assert_eq!(jsx_specifier(&old), (old_specifier, true));
        // The checker reads the new version.
        edit(&session, A_URI, 3, (0, 0), (0, 0), "const n: string = 1;\n");
        let newer = program(&session, A_URI);
        assert!(same_storage(&new, &newer));
        assert_eq!(sem_diag_count(&newer, "/src/a.tsx"), 1);
        // A `.ts` edit is Go's reuse.
        edit(&session, C_URI, 2, (0, 0), (0, 0), "// c\n");
        let _ = language_service(&session, C_URI);
        assert_eq!(update_kind(&session), ProgramUpdateKind::CLONED);
    }
}

child_test! {
    fn tsx_runtime_change_builds_again() {
        let (session, _) = setup();
        let old = program(&session, A_URI);
        edit(&session, A_URI, 2, (0, 0), (0, 0), "/** @jsxImportSource preact */\n");
        let new = program(&session, A_URI);
        assert!(!same_storage(&old, &new), "a new runtime import builds again");
        assert_ne!(update_kind(&session), ProgramUpdateKind::CLONED);
        // A pragma that names the configured runtime keeps the import.
        let old = new;
        edit(&session, A_URI, 3, (0, 0), (1, 0), "/** @jsxImportSource react */\n");
        let new = program(&session, A_URI);
        assert!(!same_storage(&old, &new));
        let old = new;
        edit(&session, A_URI, 4, (0, 0), (1, 0), "");
        let new = program(&session, A_URI);
        assert!(same_storage(&old, &new));
        assert_eq!(update_kind(&session), ProgramUpdateKind::SAME_FILE_NAMES);
    }
}

child_test! {
    // Go publishes the config file diagnostics after a rebuild
    // (`shouldPublishProgramDiagnostics`: kind > Cloned), so a `.tsx` edit
    // publishes them, and a `.ts` edit does not.
    fn tsx_body_edit_publishes_config_diagnostics_as_go_does() {
        let (session, utils) = setup();
        session.wait_for_background_tasks();
        let tsconfig_calls = || {
            utils
                .client()
                .publish_diagnostics_calls()
                .iter()
                .filter(|c| c.uri.0 == "file:///src/tsconfig.json")
                .count()
        };
        let initial = tsconfig_calls();
        assert!(initial > 0, "baseUrl gives a config diagnostic");
        edit(&session, A_URI, 2, (0, 31), (0, 31), " // x");
        let _ = language_service(&session, A_URI);
        session.wait_for_background_tasks();
        assert_eq!(tsconfig_calls(), initial + 1);
        edit(&session, C_URI, 2, (0, 0), (0, 0), "// c\n");
        let _ = language_service(&session, C_URI);
        session.wait_for_background_tasks();
        assert_eq!(tsconfig_calls(), initial + 1);
    }
}

// ---------------------------------------------------------------------------
// The verify mode (`GOPORT_VERIFY_REUSE`, a `diagnostics` feature build) across processes: the session runs
// on real files, and the verify child is this test binary, running
// `verify_child_entry` with the in-process content mapper spawner.
// ---------------------------------------------------------------------------

#[cfg(feature = "diagnostics")]
const VERIFY_CHILD_TEST: &str = "project_lsp::synthetic_reuse_test::verify_child_entry";

/// The verify child of the tests below: a no-op unless the verify mode runs
/// this binary as its child (`project::reuse_verify::CHILD_ENV`).
#[cfg(feature = "diagnostics")]
#[test]
fn verify_child_entry() {
    if std::env::var_os(ts_goport::project::reuse_verify::CHILD_ENV).is_none() {
        return;
    }
    let code = std::thread::Builder::new()
        .stack_size(256 << 20)
        .spawn(|| {
            ts_goport::project::reuse_verify::child_main_with(Some(
                crate::support::contentmappertest::new_spawner(),
            ))
        })
        .unwrap()
        .join()
        .unwrap();
    std::process::exit(code);
}

/// A temp directory with `files`, removed at the end.
#[cfg(feature = "diagnostics")]
struct TempProject(std::path::PathBuf);

#[cfg(feature = "diagnostics")]
impl TempProject {
    fn new(label: &str, files: &[(&str, &str)]) -> Self {
        let dir =
            std::env::temp_dir().join(format!("ts_goport_verify_{label}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        for (name, text) in files {
            let path = dir.join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        TempProject(dir)
    }
    fn dir(&self) -> String {
        self.0.to_string_lossy().replace('\\', "/")
    }
    fn uri(&self, name: &str) -> String {
        format!("file://{}/{name}", self.dir())
    }
}

#[cfg(feature = "diagnostics")]
impl Drop for TempProject {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A session on the OS file system in `dir`, with content mappers on.
#[cfg(feature = "diagnostics")]
fn os_session(dir: &str) -> Rc<Session> {
    ts_goport::project::new_session(&ts_goport::project::SessionInit {
        background_ctx: bg(),
        options: Rc::new(ts_goport::project::SessionOptions {
            typings_location: String::new(),
            run_external_code: true,
            ..projecttestutil::session_options(dir)
        }),
        fs: ts_goport::frontend::bundled::wrap_fs(ts_goport::frontend::vfs::osvfs_fs()),
        client: None,
        logger: None,
        npm_executor: None,
        spawner: Some(crate::support::contentmappertest::new_spawner()),
        content_mapper_logger: None,
        parse_cache: None,
        content_mapped_parse_cache: None,
    })
}

/// Runs `body` in a child test process with the verify mode on (a
/// difference or an error panics), and returns the verify log.
#[cfg(feature = "diagnostics")]
fn with_verify(test: &str, body: impl FnOnce() + Send + 'static) -> Vec<String> {
    with_verify_mode(test, "all-panic", body)
}

/// `with_verify` with the verify mode `mode`.
#[cfg(feature = "diagnostics")]
fn with_verify_mode(test: &str, mode: &str, body: impl FnOnce() + Send + 'static) -> Vec<String> {
    let log = std::env::temp_dir().join(format!(
        "ts_goport_verify_log_{}_{}",
        test.rsplit("::").next().unwrap(),
        std::process::id()
    ));
    let _ = std::fs::remove_file(&log);
    let log_name = log.to_string_lossy().to_string();
    let args = [
        "--exact",
        VERIFY_CHILD_TEST,
        "--nocapture",
        "--test-threads=1",
    ]
    .join("\u{1f}");
    crate::support::child::run_test_in_child_with_env(
        test,
        &[
            ("GOPORT_VERIFY_REUSE", mode),
            ("GOPORT_VERIFY_REUSE_LOG", &log_name),
            ("GOPORT_VERIFY_REUSE_CHILD_ARGS", &args),
        ],
        body,
    );
    let lines = std::fs::read_to_string(&log).unwrap_or_default();
    let _ = std::fs::remove_file(&log);
    lines.lines().map(str::to_string).collect()
}

/// The parent side of the tests below runs here (`with_verify` re-runs the
/// test in a child, and only there is the variable set).
#[cfg(feature = "diagnostics")]
fn in_verify_child() -> bool {
    std::env::var_os("GOPORT_VERIFY_REUSE").is_some()
}

#[cfg(feature = "diagnostics")]
#[test]
fn verify_mode_checks_a_mapped_project_in_a_child_process() {
    let lines = with_verify(
        "project_lsp::synthetic_reuse_test::verify_mode_checks_a_mapped_project_in_a_child_process",
        || {
            if !in_verify_child() {
                return;
            }
            let mapper = crate::support::contentmappertest::package_json(
                crate::support::contentmappertest::SUPPLEMENTAL_MAPPER,
            );
            let p = TempProject::new(
                "mapped",
                &[
                    (
                        "tsconfig.json",
                        r#"{ "compilerOptions": { "strict": true, "importHelpers": true, "isolatedModules": true, "types": [] }, "contentMappers": [{ "package": "mapper", "extensions": [".box"] }] }"#,
                    ),
                    ("node_modules/mapper/package.json", &mapper),
                    (
                        "node_modules/tslib/package.json",
                        r#"{"name":"tslib","version":"2.8.0","types":"tslib.d.ts"}"#,
                    ),
                    (
                        "node_modules/tslib/tslib.d.ts",
                        "export declare function __awaiter(a: any, b: any, c: any, d: any): any;\n",
                    ),
                    ("app.box", "declare const supplementalValue: number;\n"),
                    ("main.ts", "const value: number = supplementalValue;\n"),
                ],
            );
            let session = os_session(&p.dir());
            open(
                &session,
                &p.uri("main.ts"),
                "const value: number = supplementalValue;\n",
            );
            let _ = program(&session, &p.uri("main.ts"));
            // A closed mapped file changes on disk (the child maps the disk text).
            std::fs::write(
                p.0.join("app.box"),
                "declare const supplementalValue: string;\n",
            )
            .unwrap();
            watch(&session, &[(CHANGED, &p.uri("app.box"))]);
            let _ = program(&session, &p.uri("main.ts"));
            // An open mapped file is edited (the child maps the editor text).
            let box_uri = p.uri("app.box");
            open_kind(
                &session,
                &box_uri,
                "declare const supplementalValue: string;\n",
                lsproto::LanguageKind("box".into()),
            );
            let _ = program(&session, &p.uri("main.ts"));
            edit(&session, &box_uri, 2, (0, 0), (0, 0), "// edited\n");
            let _ = program(&session, &p.uri("main.ts"));
            session.close();
        },
    );
    if in_verify_child() {
        return;
    }
    let ok = lines
        .iter()
        .filter(|l| l.starts_with("goport verify-reuse: OK"))
        .count();
    assert!(ok >= 2, "{lines:?}");
    assert!(
        lines
            .iter()
            .all(|l| l.starts_with("goport verify-reuse: OK")),
        "{lines:?}"
    );
}

#[cfg(feature = "diagnostics")]
#[test]
fn verify_mode_checks_the_inferred_project_in_a_child_process() {
    let lines = with_verify(
        "project_lsp::synthetic_reuse_test::verify_mode_checks_the_inferred_project_in_a_child_process",
        || {
            if !in_verify_child() {
                return;
            }
            let p = TempProject::new(
                "inferred",
                &[
                    (
                        "node_modules/react/package.json",
                        r#"{"name":"react","version":"19.0.0","types":"index.d.ts"}"#,
                    ),
                    ("node_modules/react/index.d.ts", "export {};\n"),
                    (
                        "node_modules/react/jsx-runtime.d.ts",
                        "export declare function jsx(type: any, props: any): any;\nexport declare namespace JSX { interface IntrinsicElements { [name: string]: any } type Element = any; }\n",
                    ),
                    ("a.tsx", "export const A = () => <div />;\n"),
                    ("b.ts", "export const b = 1;\n"),
                ],
            );
            let session = os_session(&p.dir());
            let options = ts_goport::options::CompilerOptions {
                jsx: ts_goport::flags::JsxEmit::REACT_JSX,
                module: ts_goport::flags::ModuleKind::ES_NEXT,
                module_resolution: ts_goport::flags::ModuleResolutionKind::BUNDLER,
                ..Default::default()
            };
            session
                .did_change_compiler_options_for_inferred_projects(&bg(), Some(Rc::new(options)));
            let a = p.uri("a.tsx");
            open_kind(
                &session,
                &a,
                "export const A = () => <div />;\n",
                lsproto::LanguageKind::TYPE_SCRIPT_REACT,
            );
            open(&session, &p.uri("b.ts"), "export const b = 1;\n");
            let old = program(&session, &a);
            assert!(old.processed_files.jsx_runtime_import_specifiers.is_some());
            for (version, text) in [(2, " // 1"), (3, " // 2")] {
                edit(&session, &a, version, (0, 31), (0, 31), text);
                let _ = program(&session, &a);
            }
            edit(&session, &p.uri("b.ts"), 2, (0, 0), (0, 0), "// b\n");
            let _ = program(&session, &a);
            session.close();
        },
    );
    if in_verify_child() {
        return;
    }
    let ok = lines
        .iter()
        .filter(|l| l.starts_with("goport verify-reuse: OK"))
        .count();
    assert!(ok >= 3, "{lines:?}");
    assert!(
        lines
            .iter()
            .all(|l| l.starts_with("goport verify-reuse: OK")),
        "{lines:?}"
    );
}

// Review follow-up (Astra, A1 at 7972cba71): when the disk changes with no
// watch event, the server keeps its snapshot (config, package.json and
// source texts), and so does Go's rebuild. The child gets the snapshot's
// answers, so the verify mode reports OK, not MISMATCH. A new file under
// the include glob changes the child's own config parse (the server keeps
// its root files, as Go's rebuild does): the child cannot rebuild the
// server's inputs, which is an ERROR, not a MISMATCH.
#[cfg(feature = "diagnostics")]
#[test]
fn verify_mode_uses_the_snapshot_when_the_disk_changes_without_events() {
    let lines = with_verify_mode(
        "project_lsp::synthetic_reuse_test::verify_mode_uses_the_snapshot_when_the_disk_changes_without_events",
        "all",
        || {
            if !in_verify_child() {
                return;
            }
            let p = TempProject::new(
                "no_events",
                &[
                    (
                        "tsconfig.json",
                        r#"{"compilerOptions":{"jsx":"react-jsx","module":"esnext","moduleResolution":"bundler","types":[]},"include":["src"]}"#,
                    ),
                    (
                        "node_modules/react/package.json",
                        r#"{"name":"react","version":"19.0.0","types":"index.d.ts"}"#,
                    ),
                    ("node_modules/react/index.d.ts", "export {};\n"),
                    (
                        "node_modules/react/jsx-runtime.d.ts",
                        "export declare function jsx(type: any, props: any): any;\nexport declare namespace JSX { interface IntrinsicElements { [name: string]: any } type Element = any; }\n",
                    ),
                    ("src/a.tsx", "export const A = () => <div />;\n"),
                    ("src/b.ts", "export const b = 1;\n"),
                    ("src/c.ts", "export const c = 1;\n"),
                ],
            );
            let session = os_session(&p.dir());
            let a = p.uri("src/a.tsx");
            open_kind(
                &session,
                &a,
                "export const A = () => <div />;\n",
                lsproto::LanguageKind::TYPE_SCRIPT_REACT,
            );
            let _ = program(&session, &a);
            let mut version = 1;
            let mut edit_a = |session: &Rc<Session>| {
                version += 1;
                edit(session, &a, version, (0, 31), (0, 31), " //");
                let _ = program(session, &a);
            };
            let write = |name: &str, text: &str| std::fs::write(p.0.join(name), text).unwrap();
            // The config, react's package.json, and a closed source file
            // change; a closed source file goes. No event for any of them.
            write(
                "tsconfig.json",
                r#"{"compilerOptions":{"jsx":"react-jsx","module":"esnext","moduleResolution":"bundler","types":[],"strict":true,"noImplicitAny":true},"include":["src"]}"#,
            );
            edit_a(&session);
            write(
                "node_modules/react/package.json",
                r#"{"name":"react","version":"20.0.0","types":"index.d.ts","type":"module"}"#,
            );
            edit_a(&session);
            write("src/b.ts", "export const b: string = 1;\n");
            edit_a(&session);
            std::fs::remove_file(p.0.join("src/c.ts")).unwrap();
            edit_a(&session);
            // A new file under the include glob.
            write("src/d.ts", "export const d = 1;\n");
            edit_a(&session);
            session.close();
        },
    );
    if in_verify_child() {
        return;
    }
    let kinds: Vec<&str> = lines
        .iter()
        .map(|l| l.split_whitespace().nth(2).unwrap_or(""))
        .collect();
    assert_eq!(kinds, ["OK", "OK", "OK", "OK", "ERROR"], "{lines:#?}");
    assert!(
        lines[4].contains("inputs not reconstructed"),
        "{}",
        lines[4]
    );
}
