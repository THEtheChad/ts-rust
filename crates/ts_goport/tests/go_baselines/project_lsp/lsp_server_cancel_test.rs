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
//! waits in the queue for the whole run of the requests ahead of it. A
//! cancel for it is kept when Go's loop would have taken it: the dispatch
//! thread is not in a sync part, and no message ahead of it has a sync part
//! that waits for a client (`ServerShared::go_took_queued_request`). The
//! tests hold the dispatch thread on one of the client's replies:
//!
//! - in the sync part of `initialized` (its `workspace/configuration`):
//!   Go's dispatch loop is busy there too, so a request behind it is in
//!   Go's queue and Go drops its cancel;
//! - in the watch registration that the sync part of `didOpen` queues
//!   (`client/registerCapability`, a background task): Go runs it on a
//!   goroutine and has taken the requests behind it, so their cancels land
//!   and each answers RequestCancelled, the request held at that point too;
//! - in that registration, with a `custom/setContentMapperContributions`
//!   queued ahead of the request: Go's dispatch loop waits in its sync part
//!   for the content-mapper registration, so Go drops the cancel.
//!
//! With `test-hooks` the tests also read the number of kept cancels
//! (`ServerShared::queued_cancel_count`), so a mark that outlives its
//! request shows.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, SyncSender, sync_channel};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ts_goport::frontend::json_ext::{AnyValue, LspAny};
use ts_goport::jsonrpc;
use ts_goport::ls::lsconv;
use ts_goport::lsp::lsproto;

use super::lsptestutil::{self, LspClient, result_response};
use super::projecttestutil::{FileMap, files};
use crate::support::contentmappertest;

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
    start_with(
        on_server_request,
        files(&[
            ("/home/projects/tsconfig.json", "{}"),
            ("/home/projects/a.ts", SOURCE),
        ]),
        false,
    )
}

/// `external`: RunExternalCode and a client that registers text document
/// sync dynamically, with the in-process content mappers.
fn start_with(
    on_server_request: lsptestutil::ServerRequestHandler,
    file_map: FileMap,
    external: bool,
) -> LspClient {
    let mut setup = lsptestutil::server_setup("/home/projects", file_map);
    if external {
        setup.spawner = Some(contentmappertest::new_spawner);
    }
    let client = lsptestutil::new_lsp_client(setup, Some(on_server_request), None);
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
                text_document: external.then(|| lsproto::TextDocumentClientCapabilities {
                    synchronization: Some(lsproto::TextDocumentSyncClientCapabilities {
                        dynamic_registration: Some(true),
                        ..Default::default()
                    }),
                    ..Default::default()
                }),
                ..Default::default()
            }),
            initialization_options: external.then(|| lsproto::InitializationOptionsOrNull {
                initialization_options: Some(lsproto::InitializationOptions {
                    run_external_code: Some(true),
                    ..Default::default()
                }),
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

/// A request of each kind the editor cancels, by method, with id `id`.
fn request(method: &str, id: i32) -> lsproto::RequestMessage {
    fn msg<P: AnyValue, R>(
        info: &lsproto::RequestInfo<P, R>,
        id: i32,
        params: P,
    ) -> lsproto::RequestMessage {
        info.new_request_message(Some(jsonrpc::new_id_int(id)), params)
    }
    match method {
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
    }
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
    let id = client.next_id();
    (id, send_with_id(client, method, id))
}

/// Sends `method` with an id the test picks (a reused one).
fn send_with_id(client: &LspClient, method: &str, id: i32) -> Receiver<lsproto::ResponseMessage> {
    client.send_request_message(request(method, id), jsonrpc::new_id_int(id))
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

/// With `test-hooks`: waits until the server keeps `n` cancels of queued
/// requests (the reader thread handles the cancels after the test sent
/// them), then checks that it keeps no more.
fn assert_kept_cancels(client: &LspClient, n: usize) {
    #[cfg(feature = "test-hooks")]
    {
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        while client.shared.queued_cancel_count() < n && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(client.shared.queued_cancel_count(), n, "kept cancels");
    }
    #[cfg(not(feature = "test-hooks"))]
    let _ = (client, n);
}

/// The unknown id that the tests cancel.
const UNKNOWN_ID: i32 = 999;

/// Cancels for finished and unknown requests change nothing, and no mark
/// stays behind: requests that reuse the finished ids and the unknown id
/// answer their results, and the server keeps no cancel.
fn assert_late_cancels_dropped(client: &LspClient, finished: &[i32]) {
    for &id in finished {
        cancel(client, id);
    }
    cancel(client, UNKNOWN_ID);
    let reused = finished.iter().copied().chain([UNKNOWN_ID]);
    for (method, id) in KINDS.iter().cycle().zip(reused) {
        assert_result(method, &answer(&send_with_id(client, method, id)));
    }
    assert_kept_cancels(client, 0);
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
        cancel(&client, UNKNOWN_ID);
        assert_kept_cancels(&client, KINDS.len());
        hold.release.send(()).unwrap();

        for (method, (_, rx)) in &canceled {
            assert_cancelled(method, &answer(rx));
        }
        assert_result("kept hover", &answer(&kept));
        assert_kept_cancels(&client, 0);
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
        assert_kept_cancels(&client, 1);
        hold.release.send(()).unwrap();

        assert_cancelled("running hover", &answer(&running));
        assert_cancelled("queued documentSymbol", &answer(&queued));
        assert_late_cancels_dropped(&client, &[running_id, queued_id]);
    }
}

child_test! {
    // The reviewer's counterexample (round 2). The dispatch thread runs
    // didOpen's watch registration, which the client holds. Meanwhile the
    // client sends custom/setContentMapperContributions, a hover and the
    // hover's cancel. Go runs the watch registration on a goroutine, so its
    // dispatch loop takes setContentMapperContributions at once, and its
    // sync part registers the project's content-mapper extensions with
    // the client (session.go:359, server.go:731) and waits for the answer.
    // The hover is still in Go's queue when the cancel comes: Go drops it
    // and answers the hover's result. The port answers the same.
    fn a_cancel_behind_a_sync_part_that_waits_for_the_client_is_dropped() {
        let mapper = contentmappertest::package_json(contentmappertest::COMPONENT_MAPPER);
        let registrations: Arc<Mutex<Vec<String>>> = Arc::default();
        let (on_server_request, hold) = held_server_requests(is_watch_registration, true);
        let recorded = registrations.clone();
        let on_server_request: lsptestutil::ServerRequestHandler = Arc::new(move |req| {
            if req.method == lsproto::Method::CLIENT_REGISTER_CAPABILITY
                && let Ok(params) = lsproto::unmarshal_params::<lsproto::RegistrationParams>(req)
            {
                recorded
                    .lock()
                    .unwrap()
                    .extend(params.registrations.into_iter().map(|r| r.id));
            }
            on_server_request(req)
        });
        let client = start_with(
            on_server_request,
            files(&[
                (
                    "/home/projects/tsconfig.json",
                    r#"{ "contentMappers": [ { "package": "mapper", "extensions": [".vue"] } ] }"#,
                ),
                ("/home/projects/node_modules/mapper/package.json", mapper.as_str()),
                ("/home/projects/a.ts", SOURCE),
            ]),
            true,
        );
        client.send_notification(&lsproto::INITIALIZED_INFO, lsproto::InitializedParams::default());
        did_open(&client);
        hold.asked
            .recv_timeout(Duration::from_mins(2))
            .expect("didOpen registers the file watchers");

        let contributions = client.send_request_async(
            &lsproto::CUSTOM_SET_CONTENT_MAPPER_CONTRIBUTIONS_INFO,
            lsproto::SetContentMapperContributionsParams::default(),
        );
        let (hover_id, hover) = send(&client, "textDocument/hover");
        cancel(&client, hover_id);
        hold.release.send(()).unwrap();

        let (msg, _) = contributions();
        assert!(msg.error.is_none(), "{:?}", msg.error);
        assert_result("hover behind setContentMapperContributions", &answer(&hover));
        assert!(
            registrations
                .lock()
                .unwrap()
                .iter()
                .any(|id| id == "content-mapper-did-open"),
            "the content-mapper extensions were registered"
        );
        assert_late_cancels_dropped(&client, &[hover_id]);
    }
}

child_test! {
    // A cancelled shutdown that waited in the queue: Go's shutdown handler
    // (registerRequestHandler, server.go:1354) runs to its end and answers
    // RequestCancelled at its `ctx.Err()` check (:1371). Exit then ends the
    // server.
    fn a_shutdown_canceled_while_queued_answers_request_cancelled() {
        let (on_server_request, hold) = held_server_requests(is_watch_registration, true);
        let client = start(on_server_request);
        client.send_notification(&lsproto::INITIALIZED_INFO, lsproto::InitializedParams::default());
        did_open(&client);
        hold.asked
            .recv_timeout(Duration::from_mins(2))
            .expect("didOpen registers the file watchers");

        let shutdown_id = client.next_id();
        let shutdown = client.send_request_message(
            lsproto::RequestMessage {
                id: Some(jsonrpc::new_id_int(shutdown_id)),
                method: lsproto::Method::SHUTDOWN,
                ..Default::default()
            },
            jsonrpc::new_id_int(shutdown_id),
        );
        cancel(&client, shutdown_id);
        assert_kept_cancels(&client, 1);
        hold.release.send(()).unwrap();

        assert_cancelled("shutdown", &answer(&shutdown));
        assert_kept_cancels(&client, 0);
        client.write_msg(
            lsproto::RequestMessage {
                method: lsproto::Method::EXIT,
                ..Default::default()
            }
            .message(),
        );
        assert!(client.wait_server_end(Duration::from_mins(1)), "exit ends the server");
    }
}

child_test! {
    // A cancel that comes during the async part of a request: a diagnostic
    // pull of a large file that is not open, so its sync part loads the
    // project and queues the watch registration, held until the cancel is
    // sent. The cancel reaches the registered request
    // (`pending_client_requests`, as in Go) during or just before its async
    // part; both give RequestCancelled at the checker's or the wrapper's
    // context check.
    fn a_request_canceled_during_its_async_part_answers_request_cancelled() {
        let big: String = (0..20_000)
            .map(|i| format!("export const v{i} = [1, 2, 3].map((x) => x + {i}).filter((y) => y > 1);\n"))
            .collect();
        let (on_server_request, hold) = held_server_requests(is_watch_registration, true);
        let client = start_with(
            on_server_request,
            files(&[
                ("/home/projects/tsconfig.json", "{}"),
                ("/home/projects/a.ts", big.as_str()),
            ]),
            false,
        );
        client.send_notification(&lsproto::INITIALIZED_INFO, lsproto::InitializedParams::default());
        let (pull_id, pull) = send(&client, "textDocument/diagnostic");
        hold.asked
            .recv_timeout(Duration::from_secs(30))
            .expect("the pull's snapshot registers the file watchers");
        hold.release.send(()).unwrap();
        std::thread::sleep(Duration::from_millis(50));
        cancel(&client, pull_id);

        assert_cancelled("diagnostic pull", &answer(&pull));
        assert_kept_cancels(&client, 0);
    }
}
