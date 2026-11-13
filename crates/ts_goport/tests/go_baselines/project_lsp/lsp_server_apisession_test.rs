//! Port-only tests of the API session of the LSP server
//! (`custom/initializeAPISession`, Go `server.go:2333
//! handleInitializeAPISession`).

use std::io::{BufRead, BufReader, Read, Write};
#[cfg(unix)]
use std::os::unix::net::UnixStream as ApiStream;
// The API pipe is a named pipe on Windows; a client opens it as a file.
#[cfg(windows)]
use std::fs::File as ApiStream;
use std::path::{Path, PathBuf};
use std::sync::mpsc::TryRecvError;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ts_goport::jsonrpc;
use ts_goport::lsp::lsproto;

use super::lsptestutil::{self, LspClient, result_response};
use super::projecttestutil::files;
use super::util::uri;

const A_TS: &str = "export const a = 1;\n";

/// An initialized server with `/home/projects/a.ts` open.
fn start_server(
    on_server_notification: Option<lsptestutil::ServerNotificationHandler>,
) -> LspClient {
    let on_server_request: lsptestutil::ServerRequestHandler = Arc::new(|req| {
        if req.method == lsproto::Method::CLIENT_REGISTER_CAPABILITY
            || req.method == lsproto::Method::CLIENT_UNREGISTER_CAPABILITY
        {
            return Some(result_response(req, Box::new(lsproto::Null)));
        }
        None
    });
    let client = lsptestutil::new_lsp_client(
        lsptestutil::server_setup(
            "/home/projects",
            files(&[
                ("/home/projects/tsconfig.json", "{}"),
                ("/home/projects/a.ts", A_TS),
                (
                    "/home/projects/b.ts",
                    "import { a } from \"./a.js\";\nexport const b = a;\n",
                ),
            ]),
        ),
        Some(on_server_request),
        on_server_notification,
    );
    let (init_msg, _) = client.send_request(
        &lsproto::INITIALIZE_INFO,
        lsproto::InitializeParams {
            capabilities: Some(lsproto::ClientCapabilities::default()),
            ..Default::default()
        },
    );
    assert!(init_msg.error.is_none(), "initialize failed");
    client.send_notification(
        &lsproto::INITIALIZED_INFO,
        lsproto::InitializedParams::default(),
    );
    client.send_notification(
        &lsproto::TEXT_DOCUMENT_DID_OPEN_INFO,
        lsproto::DidOpenTextDocumentParams {
            text_document: Some(lsproto::TextDocumentItem {
                uri: uri("file:///home/projects/a.ts"),
                language_id: lsproto::LanguageKind::TYPE_SCRIPT,
                text: A_TS.to_string(),
                ..Default::default()
            }),
        },
    );
    client
}

/// A hover on `a` in `a.ts`.
fn hover_params() -> lsproto::HoverParams {
    lsproto::HoverParams {
        text_document: lsproto::TextDocumentIdentifier {
            uri: uri("file:///home/projects/a.ts"),
        },
        position: lsproto::Position {
            line: 0,
            character: 13,
        },
        ..Default::default()
    }
}

fn hover(client: &LspClient) {
    let (msg, hover) = client.send_request(&lsproto::TEXT_DOCUMENT_HOVER_INFO, hover_params());
    assert!(msg.error.is_none(), "hover failed");
    assert!(hover.is_some_and(|hover| hover.hover.is_some()), "no hover");
}

/// Sends `custom/initializeAPISession` with a pipe in the temp dir and
/// returns the pipe that the server answers. On Unix the path must not
/// exist: the server makes it a private directory and listens on
/// `<path>/socket`, which it answers (ts#64818, Go server.go:2347 and
/// :2405). On Windows it is a name under `\\.\pipe\` (no files), which the
/// server answers as it is.
fn init_api_session(client: &LspClient) -> PathBuf {
    #[cfg(unix)]
    let pipe = std::env::temp_dir().join(format!("goport-apisess-{}", std::process::id()));
    #[cfg(windows)]
    let pipe = PathBuf::from(format!(r"\\.\pipe\goport-apisess-{}", std::process::id()));
    remove_api_pipe(&pipe.join("socket"));
    request_api_session(client, &pipe)
        .unwrap_or_else(|err| panic!("initializeAPISession failed: {err}"))
}

/// Sends `custom/initializeAPISession` with `pipe` and returns the pipe that
/// the server answers, or the message of its error.
fn request_api_session(client: &LspClient, pipe: &Path) -> Result<PathBuf, String> {
    let (session_msg, session) = client.send_request(
        &lsproto::CUSTOM_INITIALIZE_API_SESSION_INFO,
        lsproto::InitializeAPISessionParams {
            pipe: Some(pipe.to_string_lossy().into_owned()),
        },
    );
    if let Some(err) = session_msg.error {
        return Err(err.message);
    }
    let session = session.flatten().expect("an API session");
    Ok(PathBuf::from(session.pipe))
}

/// Removes what a session left of the API pipe `pipe`: the socket and its
/// private directory on Unix (Go removes both when the transport closes).
fn remove_api_pipe(pipe: &Path) {
    let _ = std::fs::remove_file(pipe);
    #[cfg(unix)]
    if let Some(dir) = pipe.parent() {
        let _ = std::fs::remove_dir(dir);
    }
}

/// How long a read of the API pipe waits for the server.
const READ_TIMEOUT: Duration = Duration::from_mins(1);

#[cfg(unix)]
type ApiReader = ApiStream;
#[cfg(windows)]
type ApiReader = PipeReader;

/// The read half of the API pipe on Windows. A pipe handle has no read
/// timeout, so each read runs on its own thread and `read` waits for it for
/// `READ_TIMEOUT`. The thread reads only while `read` waits: a read that is
/// pending on the handle would block the writes of `send`.
#[cfg(windows)]
struct PipeReader(Arc<ApiStream>);

#[cfg(windows)]
impl Read for PipeReader {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let (done, result) = std::sync::mpsc::channel();
        let pipe = self.0.clone();
        let len = buf.len();
        std::thread::spawn(move || {
            let mut chunk = vec![0u8; len];
            let read = (&*pipe).read(&mut chunk).map(|n| {
                chunk.truncate(n);
                chunk
            });
            let _ = done.send(read);
        });
        match result.recv_timeout(READ_TIMEOUT) {
            Ok(read) => read.map(|chunk| {
                buf[..chunk.len()].copy_from_slice(&chunk);
                chunk.len()
            }),
            Err(_) => Err(std::io::ErrorKind::TimedOut.into()),
        }
    }
}

/// A connected API client.
struct ApiClient {
    stream: ApiStream,
    reader: BufReader<ApiReader>,
}

impl ApiClient {
    fn connect(pipe: &PathBuf) -> Self {
        #[cfg(unix)]
        let stream = {
            let stream = ApiStream::connect(pipe).expect("connect to the API pipe");
            stream
                .set_read_timeout(Some(READ_TIMEOUT))
                .expect("set a read timeout");
            stream
        };
        #[cfg(windows)]
        let stream = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(pipe)
            .expect("connect to the API pipe");
        let clone = stream.try_clone().expect("clone the API socket");
        #[cfg(unix)]
        let reader = BufReader::new(clone);
        #[cfg(windows)]
        let reader = BufReader::new(PipeReader(Arc::new(clone)));
        Self { stream, reader }
    }

    /// Writes one Content-Length framed JSON-RPC message.
    fn send(&mut self, body: &str) {
        let frame = format!("Content-Length: {}\r\n\r\n{body}", body.len());
        self.stream
            .write_all(frame.as_bytes())
            .expect("write to the API socket");
    }

    /// Reads one Content-Length framed JSON-RPC message.
    fn recv(&mut self) -> String {
        let mut len = None;
        loop {
            let mut line = String::new();
            self.reader
                .read_line(&mut line)
                .expect("read from the API socket");
            let line = line.trim_end();
            if line.is_empty() {
                break;
            }
            if let Some(value) = line.strip_prefix("Content-Length: ") {
                len = value.parse::<usize>().ok();
            }
        }
        let mut body = vec![0; len.expect("a Content-Length header")];
        self.reader
            .read_exact(&mut body)
            .expect("read the API message");
        String::from_utf8(body).expect("UTF-8 message")
    }
}

/// Sends `shutdown`, which must be answered, and `exit`, which must end
/// the server. Both have no params (`NoParams` would write `{}`).
fn shutdown_and_exit(client: &LspClient) {
    shutdown(client);
    exit(client);
}

/// Sends `shutdown`, which must be answered.
fn shutdown(client: &LspClient) {
    let id = jsonrpc::new_id_int(client.next_id());
    let shutdown = lsproto::RequestMessage {
        id: Some(id.clone()),
        method: lsproto::Method::SHUTDOWN,
        ..Default::default()
    };
    let shutdown_msg = client
        .send_request_worker(shutdown, id)
        .expect("an answer to shutdown");
    assert!(
        shutdown_msg.error.is_none(),
        "shutdown failed: {:?}",
        shutdown_msg.error.map(|err| err.message)
    );
}

/// Sends `exit`, which must end the server.
fn exit(client: &LspClient) {
    client.write_msg(
        lsproto::RequestMessage {
            method: lsproto::Method::EXIT,
            ..Default::default()
        }
        .message(),
    );
    assert!(
        client.wait_server_end(Duration::from_mins(1)),
        "the server did not stop after exit"
    );
}

child_test! {
    // R151 L11 (apisess1): after custom/initializeAPISession the server
    // served only the API pipe, so LSP requests got no answer and exit did
    // not stop it. Go runs Accept and the connection on goroutines. The
    // server must answer LSP requests before and while an API client is
    // connected, answer API requests, and stop on shutdown and exit.
    fn api_session_keeps_lsp_served() {
        let client = start_server(None);
        let pipe = init_api_session(&client);

        // No API client yet.
        hover(&client);

        // An API client is connected and idle.
        let mut api = ApiClient::connect(&pipe);
        api.send(r#"{"jsonrpc":"2.0","id":1,"method":"initialize"}"#);
        let answer = api.recv();
        assert!(answer.contains(r#""id":1"#) && answer.contains(r#""result""#), "{answer}");
        hover(&client);

        shutdown_and_exit(&client);
        drop(api);
        remove_api_pipe(&pipe);
    }
}

child_test! {
    // followups2 (apisess1 item 6): while an API request waits for a
    // client callback that never comes, Go serves LSP messages in order on
    // its dispatch goroutine (server.go:1004). The port serves the ones that
    // do not need the session (didChange here) in order, keeps the others
    // (hover), and serves shutdown and exit wherever they are in the queue.
    // The server logs each message it handled (`window/logMessage`, Go
    // server.go:1218 "handled method"), so the log shows that didChange
    // was served before shutdown and the hover was not.
    fn api_callback_wait_serves_shutdown_and_exit_behind_other_messages() {
        let handled = Arc::new(Mutex::new(Vec::<String>::new()));
        let log = Arc::clone(&handled);
        let client = start_server(Some(Arc::new(move |msg| {
            if msg.method != lsproto::Method::WINDOW_LOG_MESSAGE {
                return;
            }
            if let Ok(params) = lsproto::unmarshal_params::<lsproto::LogMessageParams>(msg) {
                if params.message.starts_with("handled method") {
                    log.lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .push(params.message);
                }
            }
        })));
        let pipe = init_api_session(&client);
        let mut api = ApiClient::connect(&pipe);
        api.send(r#"{"jsonrpc":"2.0","id":1,"method":"initialize"}"#);
        api.recv();
        api.send(
            r#"{"jsonrpc":"2.0","id":2,"method":"createModuleResolver","params":{"compilerOptions":{"module":199,"moduleResolution":99},"resolveModuleNameCallback":"resolveCb"}}"#,
        );
        let answer = api.recv();
        let resolver = answer
            .split_once(r#""result":"#)
            .and_then(|(_, rest)| rest.split(|c: char| !c.is_ascii_digit()).next())
            .unwrap_or_else(|| panic!("no resolver id: {answer}"));
        api.send(&format!(
            r#"{{"jsonrpc":"2.0","id":3,"method":"createSnapshot","params":{{"createPrograms":[{{"rootFiles":["/home/projects/b.ts"],"compilerOptions":{{"module":199,"moduleResolution":99,"noEmit":true}},"options":{{"moduleResolver":{resolver}}}}}]}}}}"#
        ));
        let callback = api.recv();
        assert!(callback.contains(r#""method":"resolveCb""#), "{callback}");

        // The callback is never answered.
        client.send_notification(
            &lsproto::TEXT_DOCUMENT_DID_CHANGE_INFO,
            lsproto::DidChangeTextDocumentParams {
                text_document: lsproto::VersionedTextDocumentIdentifier {
                    uri: uri("file:///home/projects/a.ts"),
                    version: 2,
                },
                content_changes: vec![lsproto::TextDocumentContentChangePartialOrWholeDocument {
                    partial: None,
                    whole_document: Some(lsproto::TextDocumentContentChangeWholeDocument {
                        text: format!("{A_TS}export const c = 2;\n"),
                    }),
                }],
            },
        );
        let hover_id = jsonrpc::new_id_int(client.next_id());
        let kept_hover = client.send_request_message(
            lsproto::TEXT_DOCUMENT_HOVER_INFO.new_request_message(Some(hover_id.clone()), hover_params()),
            hover_id,
        );
        shutdown_and_exit(&client);

        let handled = handled
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let position = |method: &str| {
            handled
                .iter()
                .position(|line| line.starts_with(&format!("handled method '{method}'")))
        };
        let did_change = position("textDocument/didChange");
        let shutdown = position("shutdown");
        assert!(
            did_change.is_some() && did_change < shutdown,
            "didChange was not served before shutdown: {handled:?}"
        );
        assert_eq!(position("textDocument/hover"), None, "{handled:?}");
        assert!(
            matches!(kept_hover.try_recv(), Err(TryRecvError::Empty)),
            "the hover was answered"
        );
        drop(api);
        remove_api_pipe(&pipe);
    }
}

child_test! {
    // Bump E L5 round 2 (B2, ts#64818): Go's apiSessionState.stop
    // (server.go:278) closes the transport. That wakes the waiting Accept
    // and removes the socket and its private directory, and stop waits for
    // the goroutine, which logs the accept error. So after shutdown the
    // pipe path is free, and a second session at it starts (Go N' probe:
    // w2/l5-skeptic/probe/lsp_reuse_probe.py). Before, the directory
    // stayed, and the second session failed with "mkdir P: file exists".
    // On Windows the pipe is a name with no files.
    #[cfg(unix)]
    fn api_session_without_client_frees_its_pipe_on_shutdown() {
        let accept_errors = Arc::new(Mutex::new(Vec::<String>::new()));
        let log = Arc::clone(&accept_errors);
        let client = start_server(Some(Arc::new(move |msg| {
            if msg.method != lsproto::Method::WINDOW_LOG_MESSAGE {
                return;
            }
            if let Ok(params) = lsproto::unmarshal_params::<lsproto::LogMessageParams>(msg)
                && params.message.contains("failed to accept connection")
            {
                log.lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .push(params.message);
            }
        })));
        let socket = init_api_session(&client);
        let dir = socket.parent().expect("a private directory").to_path_buf();
        assert!(dir.is_dir(), "{} is not a directory", dir.display());

        shutdown(&client);
        assert!(
            !socket.exists() && !dir.exists(),
            "{} stays after shutdown",
            dir.display()
        );
        // The client reads messages in order, so the log came before the
        // shutdown answer.
        let accept_errors = accept_errors
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let want = format!(
            ": failed to accept connection: accept unix {}: use of closed network connection",
            socket.to_string_lossy()
        );
        assert!(
            accept_errors.len() == 1 && accept_errors[0].ends_with(&want),
            "{accept_errors:?}"
        );
        exit(&client);

        let client = start_server(None);
        let second = request_api_session(&client, &dir)
            .unwrap_or_else(|err| panic!("a second session at {}: {err}", dir.display()));
        assert_eq!(second, socket);
        shutdown_and_exit(&client);
        assert!(!dir.exists(), "{} stays after shutdown", dir.display());
    }
}

child_test! {
    // lsp-queued-cancel round 2: cancels while an API request waits for a
    // client callback. Go's API request holds `snapshotUpdateMu`
    // (project/api.go:18 APIUpdate) while it waits, so Go's dispatch loop
    // serves didChange and setTrace, takes the first hover and blocks in
    // its sync part on that lock; the second hover is still in Go's queue.
    // So Go's first hover answers RequestCancelled and the second its
    // result. The port serves didChange in its wait (nested
    // dispatch), keeps the first hover's cancel and drops the second's.
    // It reads the kept cancels, so it needs `test-hooks`.
    #[cfg(feature = "test-hooks")]
    fn api_callback_wait_keeps_the_cancel_of_the_first_waiting_request_only() {
        let handled = Arc::new(Mutex::new(Vec::<String>::new()));
        let log = Arc::clone(&handled);
        let client = start_server(Some(Arc::new(move |msg| {
            if msg.method != lsproto::Method::WINDOW_LOG_MESSAGE {
                return;
            }
            if let Ok(params) = lsproto::unmarshal_params::<lsproto::LogMessageParams>(msg)
                && params.message.starts_with("handled method")
            {
                log.lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .push(params.message);
            }
        })));
        let pipe = init_api_session(&client);
        let mut api = ApiClient::connect(&pipe);
        api.send(r#"{"jsonrpc":"2.0","id":1,"method":"initialize"}"#);
        api.recv();
        api.send(
            r#"{"jsonrpc":"2.0","id":2,"method":"createModuleResolver","params":{"compilerOptions":{"module":199,"moduleResolution":99},"resolveModuleNameCallback":"resolveCb"}}"#,
        );
        let answer = api.recv();
        let resolver = answer
            .split_once(r#""result":"#)
            .and_then(|(_, rest)| rest.split(|c: char| !c.is_ascii_digit()).next())
            .unwrap_or_else(|| panic!("no resolver id: {answer}"))
            .to_string();
        api.send(&format!(
            r#"{{"jsonrpc":"2.0","id":3,"method":"createSnapshot","params":{{"createPrograms":[{{"rootFiles":["/home/projects/b.ts"],"compilerOptions":{{"module":199,"moduleResolution":99,"noEmit":true}},"options":{{"moduleResolver":{resolver}}}}}]}}}}"#
        ));
        let mut callback = api.recv();
        assert!(callback.contains(r#""method":"resolveCb""#), "{callback}");

        client.send_notification(
            &lsproto::TEXT_DOCUMENT_DID_CHANGE_INFO,
            lsproto::DidChangeTextDocumentParams {
                text_document: lsproto::VersionedTextDocumentIdentifier {
                    uri: uri("file:///home/projects/a.ts"),
                    version: 2,
                },
                content_changes: vec![lsproto::TextDocumentContentChangePartialOrWholeDocument {
                    partial: None,
                    whole_document: Some(lsproto::TextDocumentContentChangeWholeDocument {
                        text: format!("{A_TS}export const c = 2;\n"),
                    }),
                }],
            },
        );
        // The wait serves didChange (nested dispatch) before the hovers come.
        let deadline = std::time::Instant::now() + Duration::from_mins(1);
        while !handled
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .iter()
            .any(|line| line.starts_with("handled method 'textDocument/didChange'"))
        {
            assert!(std::time::Instant::now() < deadline, "didChange was not served");
            std::thread::sleep(Duration::from_millis(5));
        }
        let send_hover = || {
            let id = client.next_id();
            let rx = client.send_request_message(
                lsproto::TEXT_DOCUMENT_HOVER_INFO
                    .new_request_message(Some(jsonrpc::new_id_int(id)), hover_params()),
                jsonrpc::new_id_int(id),
            );
            (id, rx)
        };
        let (first_id, first) = send_hover();
        let (second_id, second) = send_hover();
        // The second hover's cancel goes first: once the server keeps the
        // first one's, it has handled both.
        for id in [second_id, first_id] {
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
        let deadline = std::time::Instant::now() + Duration::from_mins(1);
        while client.shared.queued_cancel_count() == 0 {
            assert!(std::time::Instant::now() < deadline, "no cancel was kept");
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(client.shared.queued_cancel_count(), 1, "kept cancels");

        // Answer the callbacks until createSnapshot answers.
        loop {
            if callback.contains(r#""id":3"#) && !callback.contains(r#""method""#) {
                break;
            }
            if callback.contains(r#""method""#) {
                let id = callback
                    .split_once(r#""id":"#)
                    .map(|(_, rest)| rest.split([',', '}']).next().unwrap_or_default().to_string())
                    .unwrap_or_else(|| panic!("no callback id: {callback}"));
                api.send(&format!(r#"{{"jsonrpc":"2.0","id":{id},"result":null}}"#));
            }
            callback = api.recv();
        }

        let wait = |rx: &std::sync::mpsc::Receiver<lsproto::ResponseMessage>| {
            rx.recv_timeout(Duration::from_mins(2)).expect("an answer to the hover")
        };
        let first = wait(&first);
        assert_eq!(
            first.error.as_ref().map(|err| err.code),
            Some(lsproto::ErrorCode::REQUEST_CANCELLED.0),
            "first hover: {:?}",
            first.error
        );
        let second = wait(&second);
        assert!(second.error.is_none(), "second hover: {:?}", second.error);
        assert_eq!(client.shared.queued_cancel_count(), 0, "kept cancels");
        let handled = handled
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let position = |method: &str| {
            handled
                .iter()
                .position(|line| line.starts_with(&format!("handled method '{method}'")))
        };
        assert!(
            position("textDocument/didChange").is_some()
                && position("textDocument/didChange") < position("textDocument/hover"),
            "didChange was not served in the wait: {handled:?}"
        );
        shutdown_and_exit(&client);
        drop(api);
        remove_api_pipe(&pipe);
    }
}
