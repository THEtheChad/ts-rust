//! A language-server client for the Effect tests: runs
//! `TSC_RS_EFFECT=1 tsgo --lsp --stdio` in a project dir, sends requests one
//! at a time and answers the server's own requests with `null`.

#![allow(dead_code)]

pub mod json;

use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

/// How long one answer may take.
const ANSWER_TIMEOUT: Duration = Duration::from_secs(120);

/// A running `tsgo --lsp --stdio`, killed on drop.
pub struct Server {
    child: Child,
    stdin: ChildStdin,
    messages: mpsc::Receiver<String>,
    next_id: u64,
}

impl Server {
    /// Starts the server in `dir` with the Effect extension on and sends
    /// `initialize` (with `capabilities`, a JSON object) and `initialized`.
    pub fn start(dir: &Path, capabilities: &str) -> Server {
        let mut child = Command::new(env!("CARGO_BIN_EXE_tsgo"))
            .args(["--lsp", "--stdio"])
            .current_dir(dir)
            .env("TSC_RS_EFFECT", "1")
            .env_remove("GOPORT_TRACE")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("run tsgo --lsp");
        let stdin = child.stdin.take().expect("stdin");
        let (tx, messages) = mpsc::channel();
        let mut stdout = BufReader::new(child.stdout.take().expect("stdout"));
        std::thread::spawn(move || {
            while let Some(body) = read_message(&mut stdout) {
                if tx.send(body).is_err() {
                    break;
                }
            }
        });
        let mut server = Server {
            child,
            stdin,
            messages,
            next_id: 1,
        };
        let root = file_uri(dir);
        server.request(
            "initialize",
            &format!(
                r#"{{"processId":null,"rootUri":"{root}","capabilities":{capabilities},"workspaceFolders":[{{"uri":"{root}","name":"p"}}]}}"#
            ),
        );
        server.notify("initialized", "{}");
        server
    }

    fn send(&mut self, body: &str) {
        let _ = write!(self.stdin, "Content-Length: {}\r\n\r\n{body}", body.len());
        let _ = self.stdin.flush();
    }

    pub fn notify(&mut self, method: &str, params: &str) {
        self.send(&format!(
            r#"{{"jsonrpc":"2.0","method":"{method}","params":{params}}}"#
        ));
    }

    /// Sends a request and returns its answer: the whole message body.
    pub fn request(&mut self, method: &str, params: &str) -> String {
        let id = self.next_id;
        self.next_id += 1;
        self.send(&format!(
            r#"{{"jsonrpc":"2.0","id":{id},"method":"{method}","params":{params}}}"#
        ));
        loop {
            let body = self
                .messages
                .recv_timeout(ANSWER_TIMEOUT)
                .unwrap_or_else(|_| panic!("an answer to {method} within {ANSWER_TIMEOUT:?}"));
            if is_answer(&body, id) {
                return body;
            }
            if let Some(server_id) = server_request_id(&body) {
                let reply = format!(r#"{{"jsonrpc":"2.0","id":{server_id},"result":null}}"#);
                self.send(&reply);
            }
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// The `file://` URI of an absolute path.
pub fn file_uri(path: &Path) -> String {
    format!("file://{}", path.display())
}

/// One message body from `stdout`, or `None` at its end.
fn read_message(stdout: &mut impl BufRead) -> Option<String> {
    let mut length = 0;
    loop {
        let mut line = String::new();
        if stdout.read_line(&mut line).ok()? == 0 {
            return None;
        }
        let line = line.trim_end();
        if line.is_empty() {
            break;
        }
        if let Some(value) = line.strip_prefix("Content-Length:") {
            length = value.trim().parse().ok()?;
        }
    }
    let mut body = vec![0; length];
    stdout.read_exact(&mut body).ok()?;
    String::from_utf8(body).ok()
}

/// The part of a message before its `params`, `result` or `error`.
fn head(body: &str) -> &str {
    let end = [r#""params""#, r#""result""#, r#""error""#]
        .iter()
        .filter_map(|key| body.find(key))
        .min()
        .unwrap_or(body.len());
    &body[..end]
}

/// The `id` of a server request (a message with a `method` and an `id`).
fn server_request_id(body: &str) -> Option<&str> {
    let head = head(body);
    if !head.contains(r#""method":"#) {
        return None;
    }
    let start = head.find(r#""id":"#)? + 5;
    let rest = &head[start..];
    Some(&rest[..rest.find([',', '}'])?])
}

/// Whether `body` is the answer to the client request `id`.
fn is_answer(body: &str, id: u64) -> bool {
    let head = head(body);
    !head.contains(r#""method":"#)
        && (head.contains(&format!(r#""id":{id},"#)) || head.ends_with(&format!(r#""id":{id}"#)))
}
