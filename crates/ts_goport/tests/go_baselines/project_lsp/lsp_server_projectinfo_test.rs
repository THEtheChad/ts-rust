//! Port of Go `internal/lsp/server_projectinfo_test.go`.

use std::sync::Arc;

use ts_goport::lsp::lsproto;

use super::lsptestutil::{self, LspClient, result_response};
use super::projecttestutil::files;
use super::util::uri;

// Go: server_projectinfo_test.go:17 expectedCodeActionKinds (ts#63951)
// PORT: with the Effect extension on (`TSC_RS_EFFECT=1`), plus
// `refactor.rewrite` after `quickfix`, as effect-tsgo 0.51.1 patch 017 adds
// it to Go's `supportedCodeActionKinds` (decision D1 option B), also without
// an Effect plugin: this test and the content mapper test have none. Off
// (the default, perf-rearch E0), Go's list.
pub(super) fn expected_code_action_kinds() -> Vec<lsproto::CodeActionKind> {
    let mut kinds = vec![lsproto::CodeActionKind::QUICK_FIX];
    if ts_goport::effect::switch_on() {
        kinds.push(lsproto::CodeActionKind("refactor.rewrite".into()));
    }
    kinds.extend([
        lsproto::CodeActionKind("source.organizeImports.ts".into()),
        lsproto::CodeActionKind("source.removeUnusedImports.ts".into()),
        lsproto::CodeActionKind("source.sortImports.ts".into()),
        lsproto::CodeActionKind("source.fixAll.ts".into()),
    ]);
    kinds
}

// Go: server_projectinfo_test.go:26 initProjectInfoClient
pub(super) fn init_project_info_client(entries: &[(&str, &str)]) -> LspClient {
    let on_server_request: lsptestutil::ServerRequestHandler = Arc::new(|req| {
        if req.method == lsproto::Method::CLIENT_REGISTER_CAPABILITY
            || req.method == lsproto::Method::CLIENT_UNREGISTER_CAPABILITY
            || req.method == lsproto::Method::WINDOW_WORK_DONE_PROGRESS_CREATE
        {
            return Some(result_response(req, Box::new(lsproto::Null)));
        }
        None
    });

    let client = lsptestutil::new_lsp_client(
        lsptestutil::server_setup("/home/projects", files(entries)),
        Some(on_server_request),
        None,
    );

    let (init_msg, _) = client.send_request(
        &lsproto::INITIALIZE_INFO,
        lsproto::InitializeParams {
            capabilities: Some(lsproto::ClientCapabilities::default()),
            ..Default::default()
        },
    );
    assert!(init_msg.error.is_none(), "Initialize failed");
    client.send_notification(
        &lsproto::INITIALIZED_INFO,
        lsproto::InitializedParams::default(),
    );

    client
}

/// The code action kinds of the `initialize` answer.
fn initialize_code_action_kinds_answer() -> Vec<lsproto::CodeActionKind> {
    let client = lsptestutil::new_lsp_client(
        lsptestutil::server_setup("/home/projects", files(&[])),
        None,
        None,
    );

    let (message, result) = client.send_request(
        &lsproto::INITIALIZE_INFO,
        lsproto::InitializeParams {
            capabilities: Some(lsproto::ClientCapabilities::default()),
            ..Default::default()
        },
    );
    assert!(message.error.is_none(), "Initialize failed");
    // Go dereferences the `*InitializeResult`.
    let result = result.flatten().expect("Initialize failed");
    result
        .capabilities
        .and_then(|capabilities| capabilities.code_action_provider)
        .and_then(|provider| provider.code_action_options)
        .and_then(|options| options.code_action_kinds)
        .expect("expected code action kinds")
}

child_test! {
    // Go: server_projectinfo_test.go:62 TestInitializeCodeActionKinds (ts#63951)
    fn initialize_code_action_kinds() {
        let kinds = initialize_code_action_kinds_answer();
        assert_eq!(kinds, expected_code_action_kinds());
        // PORT: the default (no `TSC_RS_EFFECT`) is Go's list.
        if std::env::var_os(ts_goport::effect::SWITCH_ENV).is_none() {
            assert!(!kinds.contains(&lsproto::CodeActionKind::REFACTOR_REWRITE));
        }
    }
}

effect_child_test! {
    // PORT: not in Go. With the Effect extension on, the list has
    // `refactor.rewrite` (effect-tsgo 0.51.1 patch 017).
    fn initialize_code_action_kinds_with_the_effect_extension() {
        let kinds = initialize_code_action_kinds_answer();
        assert_eq!(kinds, expected_code_action_kinds());
        assert_eq!(kinds[1], lsproto::CodeActionKind::REFACTOR_REWRITE);
    }
}

fn open_and_project_info(client: &LspClient, text: &str) -> lsproto::ProjectInfoResult {
    let u = uri("file:///home/projects/index.ts");
    client.send_notification(
        &lsproto::TEXT_DOCUMENT_DID_OPEN_INFO,
        lsproto::DidOpenTextDocumentParams {
            text_document: Some(lsproto::TextDocumentItem {
                uri: u.clone(),
                language_id: lsproto::LanguageKind::TYPE_SCRIPT,
                text: text.to_string(),
                ..Default::default()
            }),
        },
    );

    let (msg, resp) = client.send_request(
        &lsproto::CUSTOM_PROJECT_INFO_INFO,
        lsproto::ProjectInfoParams {
            text_document: lsproto::TextDocumentIdentifier { uri: u },
        },
    );
    assert!(msg.error.is_none(), "{:?}", msg.error);
    resp.expect("expected a response")
        .expect("project info result")
}

child_test! {
    // Go: server_projectinfo_test.go:52 TestProjectInfoConfiguredProject
    fn project_info_configured_project() {
        let client = init_project_info_client(&[
            ("/home/projects/tsconfig.json", "{}"),
            ("/home/projects/index.ts", "export const x = 1;"),
        ]);
        let resp = open_and_project_info(&client, "export const x = 1;");
        assert_eq!(resp.config_file_path, "/home/projects/tsconfig.json");
    }
}

child_test! {
    // Go: server_projectinfo_test.go:77 TestProjectInfoInferredProject
    fn project_info_inferred_project() {
        let client = init_project_info_client(&[("/home/projects/index.ts", "export const x = 1;")]);
        let resp = open_and_project_info(&client, "export const x = 1;");
        assert_eq!(resp.config_file_path, "");
    }
}
