//! Effect code actions on a file whose module specifier is unterminated
//! (lane W2-EF, round 2).
//!
//! The Effect module identifier lookups (`effect::typeparser::
//! find_module_identifier_for_package` and the Layer lookup of
//! `effect::layergraph`) read an import's module specifier without its
//! quotes. They used to slice the text as a `str`, so a specifier that ends
//! inside a multi-byte character (`import { X } from "é` with no closing
//! quote, or an invalid UTF-8 or UTF-16 file decoded to such text) panicked:
//! `textDocument/codeAction` answered `-32603` (`end byte index 2 is not a
//! char boundary`). The lookups now slice bytes lossily, as the Go reference
//! (Effect-TS/tsgo) does, and answer the refactors. This is an intended
//! behaviour change of the Effect extension; plain tsgo (without
//! `TSC_RS_EFFECT=1`) never runs these lookups.
//!
//! Each test writes a project with the `@effect/language-service` plugin and
//! a `main.ts` given as raw bytes, runs `TSC_RS_EFFECT=1 tsgo --lsp --stdio`
//! in it, and requests code actions on `load` (the file is read from disk,
//! not opened, so its encoding is the file's).
#![cfg(unix)]

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::time::Duration;

/// The function the code actions are requested on (line 1, `load` at
/// character 22).
const LOAD: &str = "export async function load() { return await Promise.resolve(1) }\n";

/// A temporary project dir, removed on drop.
struct Project(PathBuf);

impl Project {
    fn new(main_ts: &[u8]) -> Project {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "effect-lsp-module-specifier-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("create the project dir");
        std::fs::write(
            dir.join("tsconfig.json"),
            r#"{"compilerOptions":{"strict":true,"plugins":[{"name":"@effect/language-service"}]},"include":["*.ts"]}"#,
        )
        .expect("write tsconfig.json");
        std::fs::write(dir.join("main.ts"), main_ts).expect("write main.ts");
        Project(dir)
    }
}

impl Drop for Project {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// One LSP message body from `stdout`, or `None` at its end.
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

/// The `id` of a server request (a message with a `method` and an `id`).
fn server_request_id(body: &str) -> Option<&str> {
    let head = body.split(r#""params""#).next()?;
    if !head.contains(r#""method":"#) {
        return None;
    }
    let start = head.find(r#""id":"#)? + 5;
    let rest = &head[start..];
    Some(&rest[..rest.find([',', '}'])?])
}

/// Whether `body` is the answer to the client request with id 2.
fn is_answer_2(body: &str) -> bool {
    let end = [r#""result""#, r#""error""#]
        .iter()
        .filter_map(|key| body.find(key))
        .min()
        .unwrap_or(body.len());
    let head = &body[..end];
    head.contains(r#""id":2"#) && !head.contains(r#""method":"#)
}

/// The answer of `textDocument/codeAction` on `load` in a project whose
/// `main.ts` is `main_ts`.
fn code_actions_on_load(main_ts: &[u8]) -> String {
    let project = Project::new(main_ts);
    let root = format!("file://{}", project.0.display());
    let mut child = Command::new(env!("CARGO_BIN_EXE_tsgo"))
        .args(["--lsp", "--stdio"])
        .current_dir(&project.0)
        .env("TSC_RS_EFFECT", "1")
        .env_remove("GOPORT_TRACE")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("run tsgo --lsp");
    let mut stdin = child.stdin.take().expect("stdin");
    let send = |stdin: &mut std::process::ChildStdin, body: &str| {
        let _ = write!(stdin, "Content-Length: {}\r\n\r\n{body}", body.len());
        let _ = stdin.flush();
    };
    send(
        &mut stdin,
        &format!(
            r#"{{"jsonrpc":"2.0","id":1,"method":"initialize","params":{{"processId":null,"rootUri":"{root}","capabilities":{{}},"workspaceFolders":[{{"uri":"{root}","name":"p"}}]}}}}"#
        ),
    );
    send(
        &mut stdin,
        r#"{"jsonrpc":"2.0","method":"initialized","params":{}}"#,
    );
    send(
        &mut stdin,
        &format!(
            r#"{{"jsonrpc":"2.0","id":2,"method":"textDocument/codeAction","params":{{"textDocument":{{"uri":"{root}/main.ts"}},"range":{{"start":{{"line":1,"character":22}},"end":{{"line":1,"character":26}}}},"context":{{"diagnostics":[]}}}}}}"#
        ),
    );
    let (tx, rx) = mpsc::channel();
    let mut stdout = BufReader::new(child.stdout.take().expect("stdout"));
    std::thread::spawn(move || {
        while let Some(body) = read_message(&mut stdout) {
            if tx.send(body).is_err() {
                break;
            }
        }
    });
    let answer = loop {
        let body = rx
            .recv_timeout(Duration::from_secs(60))
            .expect("an answer to textDocument/codeAction within 60 s");
        if is_answer_2(&body) {
            break body;
        }
        if let Some(id) = server_request_id(&body) {
            send(
                &mut stdin,
                &format!(r#"{{"jsonrpc":"2.0","id":{id},"result":null}}"#),
            );
        }
    };
    let _ = child.kill();
    let _ = child.wait();
    answer
}

/// Asserts that the answer lists the Effect refactors of an async function
/// and is no error.
fn assert_refactors(case: &str, answer: &str) {
    assert!(
        !answer.contains(r#""error""#),
        "{case}: codeAction answered an error: {answer}"
    );
    assert!(
        answer.contains("refactor.rewrite.effect.asyncAwaitToGen"),
        "{case}: no asyncAwaitToGen refactor: {answer}"
    );
}

/// UTF-8 `main.ts` text: `import_line` then `LOAD`.
fn utf8(import_line: &str) -> Vec<u8> {
    format!("{import_line}\n{LOAD}").into_bytes()
}

#[test]
fn terminated_specifier_control() {
    assert_refactors(
        "terminated",
        &code_actions_on_load(&utf8(r#"import { X } from "é";"#)),
    );
}

#[test]
fn unterminated_named_import() {
    assert_refactors(
        "named",
        &code_actions_on_load(&utf8(r#"import { X } from "é"#)),
    );
}

#[test]
fn unterminated_namespace_import() {
    assert_refactors(
        "namespace",
        &code_actions_on_load(&utf8(r#"import * as E from "é"#)),
    );
}

#[test]
fn unterminated_side_effect_import() {
    assert_refactors("side effect", &code_actions_on_load(&utf8(r#"import "é"#)));
}

#[test]
fn unterminated_import_in_utf16_file() {
    let text = format!("import {{ X }} from \"é\n{LOAD}");
    let mut bytes = vec![0xff, 0xfe];
    bytes.extend(text.encode_utf16().flat_map(u16::to_le_bytes));
    assert_refactors("utf-16", &code_actions_on_load(&bytes));
}

#[test]
fn unterminated_import_in_invalid_utf8_file() {
    let mut bytes = b"import { X } from \"\xff\n".to_vec();
    bytes.extend_from_slice(LOAD.as_bytes());
    assert_refactors("invalid utf-8", &code_actions_on_load(&bytes));
}
