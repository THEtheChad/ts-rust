//! PORT: no Go counterpart. `$/cancelRequest` against the port's request
//! queue (`ServerShared::cancel_request`, `Server::dispatch_request`).
//!
//! Go (lsp/server.go): the read loop handles `$/cancelRequest` itself
//! (920 readLoop) and cancels only a request in `pendingClientRequests`
//! (991 cancelRequest). The dispatch loop puts a request there when it takes
//! it from the queue (1005 dispatchLoop), runs the handler's sync part and
//! starts the async part on a goroutine, so it takes the next request as
//! soon as that sync part ends. A canceled request answers RequestCancelled
//! at its wrapper's `ctx.Err()` check (1371, 1397, 1420-1432, 1460); a
//! cancel for a request still in Go's queue, finished or unknown is dropped.
//!
//! The port's dispatch thread runs each request to its end, so a request
//! waits in the queue for the whole run of the requests ahead of it. The
//! tests hold the dispatch thread on a client reply at two places:
//!
//! - in the sync part of `initialized` (its `workspace/configuration`):
//!   Go's dispatch loop is busy there too, so a request behind it is in
//!   Go's queue and Go drops its cancel;
//! - in the watch registration that the sync part of `didOpen` queues
//!   (`client/registerCapability`, a background task): Go runs it on a
//!   goroutine and has taken the requests behind it, so their cancels land
//!   and each answers RequestCancelled, the request held at that point too.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, SyncSender, sync_channel};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ts_goport::frontend::json_ext::{AnyValue, LspAny};
use ts_goport::jsonrpc;
use ts_goport::ls::lsconv;
use ts_goport::lsp::lsproto;

use super::lsptestutil::{self, LspClient, result_response};
use super::projecttestutil::files;

const SOURCE: &str = "export const a = 1;\nexport function f(x: number) { return x + a; }\nf(a);\n";

/// One client reply that the test holds: the server's first request that
/// `matches` after the test arms the hold waits until the test sends on
/// `release`.
struct Hold {
    armed: Arc<AtomicBool>,
    asked: Receiver<()>,
    release: SyncSender<()>,
}

/// The client's answers to the server's requests, with one of them held
/// (`Hold`). `armed`: whether the hold starts armed.
fn held_server_requests(
    matches: fn(&lsproto::RequestMessage) -> bool,
    armed: bool,
) -> (lsptestutil::ServerRequestHandler, Hold) {
    let (asked_tx, asked) = sync_channel::<()>(1);
    let (release, go) = sync_channel::<()>(1);
    let gate: Mutex<Option<(SyncSender<()>, Receiver<()>)>> = Mutex::new(Some((asked_tx, go)));
    let armed = Arc::new(AtomicBool::new(armed));
    let is_armed = armed.clone();
    let handler: lsptestutil::ServerRequestHandler = Arc::new(move |req| {
        if is_armed.load(Ordering::SeqCst) && matches(req) {
            let held = gate.lock().unwrap().take();
            if let Some((asked_tx, go)) = held {
                asked_tx.send(()).unwrap();
                go.recv().unwrap();
            }
        }
        if req.method == lsproto::Method::WORKSPACE_CONFIGURATION {
            return Some(result_response(req, Box::new(vec![LspAny::Null; 4])));
        }
        if req.method == lsproto::Method::CLIENT_REGISTER_CAPABILITY
            || req.method == lsproto::Method::CLIENT_UNREGISTER_CAPABILITY
        {
            return Some(result_response(req, Box::new(lsproto::Null)));
        }
        None
    });
    (
        handler,
        Hold {
            armed,
            asked,
            release,
        },
    )
}

fn is_configuration(req: &lsproto::RequestMessage) -> bool {
    req.method == lsproto::Method::WORKSPACE_CONFIGURATION
}

fn is_watch_registration(req: &lsproto::RequestMessage) -> bool {
    req.method == lsproto::Method::CLIENT_REGISTER_CAPABILITY
        && lsproto::unmarshal_params::<lsproto::RegistrationParams>(req).is_ok_and(|params| {
            params.registrations.iter().any(|r| {
                r.register_options
                    .as_ref()
                    .is_some_and(|o| o.workspace_did_change_watched_files.is_some())
            })
        })
}

fn start(on_server_request: lsptestutil::ServerRequestHandler) -> LspClient {
    let client = lsptestutil::new_lsp_client(
        lsptestutil::server_setup(
            "/home/projects",
            files(&[
                ("/home/projects/tsconfig.json", "{}"),
                ("/home/projects/a.ts", SOURCE),
            ]),
        ),
        Some(on_server_request),
        None,
    );
    let (init_msg, _) = client.send_request(
        &lsproto::INITIALIZE_INFO,
        lsproto::InitializeParams {
            capabilities: Some(lsproto::ClientCapabilities {
                workspace: Some(lsproto::WorkspaceClientCapabilities {
                    configuration: Some(true),
                    did_change_watched_files: Some(
                        lsproto::DidChangeWatchedFilesClientCapabilities {
                            dynamic_registration: Some(true),
                            ..Default::default()
                        },
                    ),
                    ..Default::default()
                }),
                ..Default::default()
            }),
            ..Default::default()
        },
    );
    assert!(init_msg.error.is_none(), "Initialize failed");
    client
}

fn uri() -> lsproto::DocumentUri {
    lsconv::file_name_to_document_uri("/home/projects/a.ts")
}

fn did_open(client: &LspClient) {
    client.send_notification(
        &lsproto::TEXT_DOCUMENT_DID_OPEN_INFO,
        lsproto::DidOpenTextDocumentParams {
            text_document: Some(lsproto::TextDocumentItem {
                uri: uri(),
                language_id: lsproto::LanguageKind::TYPE_SCRIPT,
                text: SOURCE.to_string(),
                ..Default::default()
            }),
        },
    );
}

fn did_change(client: &LspClient, version: i32, text: String) {
    client.send_notification(
        &lsproto::TEXT_DOCUMENT_DID_CHANGE_INFO,
        lsproto::DidChangeTextDocumentParams {
            text_document: lsproto::VersionedTextDocumentIdentifier {
                uri: uri(),
                version,
            },
            content_changes: vec![lsproto::TextDocumentContentChangePartialOrWholeDocument {
                partial: None,
                whole_document: Some(lsproto::TextDocumentContentChangeWholeDocument { text }),
            }],
        },
    );
}

fn doc() -> lsproto::TextDocumentIdentifier {
    lsproto::TextDocumentIdentifier { uri: uri() }
}

/// The `f` in `f(a)` on line 2.
fn pos() -> lsproto::Position {
    lsproto::Position {
        line: 2,
        character: 0,
    }
}

/// A request of each kind the editor cancels, by method.
fn request(client: &LspClient, method: &str) -> (i32, lsproto::RequestMessage) {
    fn msg<P: AnyValue, R>(
        info: &lsproto::RequestInfo<P, R>,
        id: i32,
        params: P,
    ) -> lsproto::RequestMessage {
        info.new_request_message(Some(jsonrpc::new_id_int(id)), params)
    }
    let id = client.next_id();
    let req = match method {
        "textDocument/hover" => msg(
            &lsproto::TEXT_DOCUMENT_HOVER_INFO,
            id,
            lsproto::HoverParams {
                text_document: doc(),
                position: pos(),
                ..Default::default()
            },
        ),
        "textDocument/documentSymbol" => msg(
            &lsproto::TEXT_DOCUMENT_DOCUMENT_SYMBOL_INFO,
            id,
            lsproto::DocumentSymbolParams {
                text_document: doc(),
                ..Default::default()
            },
        ),
        "textDocument/references" => msg(
            &lsproto::TEXT_DOCUMENT_REFERENCES_INFO,
            id,
            lsproto::ReferenceParams {
                text_document: doc(),
                position: pos(),
                context: Some(lsproto::ReferenceContext {
                    include_declaration: true,
                }),
                ..Default::default()
            },
        ),
        "textDocument/rename" => msg(
            &lsproto::TEXT_DOCUMENT_RENAME_INFO,
            id,
            lsproto::RenameParams {
                text_document: doc(),
                position: pos(),
                new_name: "g".to_string(),
                ..Default::default()
            },
        ),
        "workspace/symbol" => msg(
            &lsproto::WORKSPACE_SYMBOL_INFO,
            id,
            lsproto::WorkspaceSymbolParams {
                query: "f".to_string(),
                ..Default::default()
            },
        ),
        "textDocument/completion" => msg(
            &lsproto::TEXT_DOCUMENT_COMPLETION_INFO,
            id,
            lsproto::CompletionParams {
                text_document: doc(),
                position: pos(),
                ..Default::default()
            },
        ),
        "textDocument/diagnostic" => msg(
            &lsproto::TEXT_DOCUMENT_DIAGNOSTIC_INFO,
            id,
            lsproto::DocumentDiagnosticParams {
                text_document: doc(),
                ..Default::default()
            },
        ),
        "textDocument/semanticTokens/full" => msg(
            &lsproto::TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL_INFO,
            id,
            lsproto::SemanticTokensParams {
                text_document: doc(),
                ..Default::default()
            },
        ),
        "textDocument/definition" => msg(
            &lsproto::TEXT_DOCUMENT_DEFINITION_INFO,
            id,
            lsproto::DefinitionParams {
                text_document: doc(),
                position: pos(),
                ..Default::default()
            },
        ),
        other => panic!("no request for {other}"),
    };
    (id, req)
}

const KINDS: &[&str] = &[
    "textDocument/hover",
    "textDocument/documentSymbol",
    "textDocument/references",
    "textDocument/rename",
    "workspace/symbol",
    "textDocument/completion",
    "textDocument/diagnostic",
    "textDocument/semanticTokens/full",
    "textDocument/definition",
];

fn send(client: &LspClient, method: &str) -> (i32, Receiver<lsproto::ResponseMessage>) {
    let (id, req) = request(client, method);
    (
        id,
        client.send_request_message(req, jsonrpc::new_id_int(id)),
    )
}

fn cancel(client: &LspClient, id: i32) {
    client.send_notification(
        &lsproto::CANCEL_REQUEST_INFO,
        lsproto::CancelParams {
            id: lsproto::IntegerOrString {
                integer: Some(id),
                ..Default::default()
            },
        },
    );
}

fn answer(rx: &Receiver<lsproto::ResponseMessage>) -> lsproto::ResponseMessage {
    rx.recv_timeout(Duration::from_mins(2))
        .expect("the server answers the request")
}

fn assert_cancelled(method: &str, resp: &lsproto::ResponseMessage) {
    assert_eq!(
        resp.error.as_ref().map(|err| err.code),
        Some(lsproto::ErrorCode::REQUEST_CANCELLED.0),
        "{method}: {:?}",
        resp.error
    );
}

fn assert_result(method: &str, resp: &lsproto::ResponseMessage) {
    assert!(resp.error.is_none(), "{method}: {:?}", resp.error);
    assert!(resp.result.is_some(), "{method}: no result");
}

/// Cancels for finished and unknown requests change nothing: the next
/// request of each kind answers its result.
fn assert_late_cancels_dropped(client: &LspClient, finished: &[i32]) {
    for &id in finished {
        cancel(client, id);
    }
    cancel(client, 999);
    for method in KINDS {
        let (_, rx) = send(client, method);
        assert_result(method, &answer(&rx));
    }
}

child_test! {
    // The dispatch thread runs the background watch registration of
    // didOpen, outside any sync part. Go has taken the requests sent then,
    // so a cancel for one of them reaches it: each kind answers
    // RequestCancelled. A request that is not canceled answers its result.
    fn a_request_canceled_while_queued_outside_a_sync_part_answers_request_cancelled() {
        let (on_server_request, hold) = held_server_requests(is_watch_registration, true);
        let client = start(on_server_request);
        client.send_notification(&lsproto::INITIALIZED_INFO, lsproto::InitializedParams::default());
        did_open(&client);
        hold.asked
            .recv_timeout(Duration::from_mins(2))
            .expect("didOpen registers the file watchers");

        let canceled: Vec<_> = KINDS.iter().map(|method| (*method, send(&client, method))).collect();
        let (kept_id, kept) = send(&client, "textDocument/hover");
        for (_, (id, _)) in &canceled {
            cancel(&client, *id);
        }
        cancel(&client, 999);
        hold.release.send(()).unwrap();

        for (method, (_, rx)) in &canceled {
            assert_cancelled(method, &answer(rx));
        }
        assert_result("kept hover", &answer(&kept));
        let mut finished: Vec<i32> = canceled.iter().map(|(_, (id, _))| *id).collect();
        finished.push(kept_id);
        assert_late_cancels_dropped(&client, &finished);
    }
}

child_test! {
    // The dispatch thread is in the sync part of `initialized`, waiting for
    // `workspace/configuration`. Go's dispatch loop is busy there too, so
    // the requests sent then wait in Go's queue and Go drops their
    // cancels: each kind answers its result.
    fn a_cancel_while_the_dispatch_thread_runs_a_sync_part_is_dropped() {
        let (on_server_request, hold) = held_server_requests(is_configuration, true);
        let client = start(on_server_request);
        client.send_notification(&lsproto::INITIALIZED_INFO, lsproto::InitializedParams::default());
        hold.asked
            .recv_timeout(Duration::from_mins(2))
            .expect("initialized asks for the configuration");

        did_open(&client);
        let queued: Vec<_> = KINDS.iter().map(|method| (*method, send(&client, method))).collect();
        for (_, (id, _)) in &queued {
            cancel(&client, *id);
        }
        hold.release.send(()).unwrap();

        for (method, (_, rx)) in &queued {
            assert_result(method, &answer(rx));
        }
        let finished: Vec<i32> = queued.iter().map(|(_, (id, _))| *id).collect();
        assert_late_cancels_dropped(&client, &finished);
    }
}

child_test! {
    // A cancel that comes while the request runs lands at once, as in Go
    // (the request is in `pending_client_requests`). The test holds the
    // hover between its sync and async parts, on the watch registration
    // that its snapshot update queued (a.ts is not open, so the hover's
    // snapshot loads its project). The hover and the request queued behind
    // it answer RequestCancelled.
    fn a_request_canceled_while_it_runs_answers_request_cancelled() {
        let (on_server_request, hold) = held_server_requests(is_watch_registration, true);
        let client = start(on_server_request);
        client.send_notification(&lsproto::INITIALIZED_INFO, lsproto::InitializedParams::default());
        let (running_id, running) = send(&client, "textDocument/hover");
        hold.asked
            .recv_timeout(Duration::from_secs(30))
            .expect("the hover's snapshot registers the file watchers");
        let (queued_id, queued) = send(&client, "textDocument/documentSymbol");
        cancel(&client, running_id);
        cancel(&client, queued_id);
        hold.release.send(()).unwrap();

        assert_cancelled("running hover", &answer(&running));
        assert_cancelled("queued documentSymbol", &answer(&queued));
        assert_late_cancels_dropped(&client, &[running_id, queued_id]);
    }
}
