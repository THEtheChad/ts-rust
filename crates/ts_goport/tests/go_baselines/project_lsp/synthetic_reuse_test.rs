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
