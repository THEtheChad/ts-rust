//! An API session whose file system case setting differs from the OS's
//! (`tsgo --api --useCaseSensitiveFileNames=false` on Linux) answers as Go
//! at the pin does. Lane B2 (B3 audit D1, D2 and their call sites):
//! - D2: the emit side took its case setting from the OS file system, not
//!   from the program host's (`d2_*`), which also moved the source map's
//!   `sources` (`d2_source_map_sources`);
//! - D1: the common source directory of encoded dynamic names
//!   (`^/~ts-uri~/...`) folded their case (`d1_common_dir`);
//! - the JSON part of `sourceFileMayBeEmitted` compares with
//!   `CompareFilePaths`, which keeps the case of dynamic names
//!   (`json_dynamic_out_dir_case`);
//! - config file name keys, the glob walk's visited set and the emit
//!   collision check key dynamic names with `PathKey`, which keeps their
//!   case (`config_glob_dynamic`);
//! - the extension check of a `/// <reference path>` uses `PathKey`
//!   (`reference_extension_dynamic`).
//!
//! Each case of `fixtures/api_case_sensitivity` holds the createSnapshot
//! params (`request.json`) and Go's answers (`expected.json`), which
//! `scripts/perf-rearch/b2_api_expected.py` writes with the Go oracle at
//! the pin. The file systems are in memory.

use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use ts_goport::frontend::json_ext::{LspAny, unmarshal_root};

/// The project requests after createSnapshot, in the generator's order.
const PROJECT_METHODS: [&str; 5] = [
    "getSourceFileNames",
    "getProgramDiagnostics",
    "getSemanticDiagnostics",
    "getConfigFileParsingDiagnostics",
    "emitToString",
];

#[test]
fn d2_out_dir() {
    check_case("d2_out_dir");
}

#[test]
fn d2_root_dir() {
    check_case("d2_root_dir");
}

#[test]
fn d2_source_map_sources() {
    check_case("d2_source_map_sources");
}

#[test]
fn d2_json_own_input() {
    check_case("d2_json_own_input");
}

#[test]
fn d1_common_dir() {
    check_case("d1_common_dir");
}

#[test]
fn json_dynamic_out_dir_case() {
    check_case("json_dynamic_out_dir_case");
}

#[test]
fn config_glob_dynamic() {
    check_case("config_glob_dynamic");
}

#[test]
fn reference_extension_dynamic() {
    check_case("reference_extension_dynamic");
}

fn case_dir(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/api_case_sensitivity")
        .join(name)
}

/// Sends the generator's request sequence for case `name` and compares each
/// answer with Go's.
fn check_case(name: &str) {
    let dir = case_dir(name);
    let request = std::fs::read_to_string(dir.join("request.json")).expect("read request.json");
    let expected =
        parse(&std::fs::read_to_string(dir.join("expected.json")).expect("read expected.json"));
    let mut api = Api::start();
    api.call("initialize", None);
    let snapshot = api.call("createSnapshot", Some(request.trim()));
    assert_answer(name, "createSnapshot", &snapshot, &expected);
    let snapshot_id = match get(&snapshot, "snapshot") {
        // A JSON number prints as Go's integer id.
        LspAny::Number(id) => id.to_string(),
        other => panic!("{name}: snapshot id {other:?}"),
    };
    let LspAny::Array(projects) = get(&snapshot, "projects") else {
        panic!("{name}: no projects in {snapshot:?}");
    };
    for project in projects {
        let LspAny::String(id) = get(project, "id") else {
            panic!("{name}: project id in {project:?}");
        };
        let params = format!(
            r#"{{"snapshot":{snapshot_id},"project":{}}}"#,
            json_string(id)
        );
        for method in PROJECT_METHODS {
            let answer = api.call(method, Some(&params));
            assert_answer(name, &format!("{method} {id}"), &answer, &expected);
        }
    }
    api.stop();
}

fn assert_answer(name: &str, key: &str, answer: &LspAny, expected: &LspAny) {
    assert_eq!(answer, get(expected, key), "{name}: {key}");
}

fn get<'a>(value: &'a LspAny, key: &str) -> &'a LspAny {
    match value {
        LspAny::Object(fields) => fields.get(key).unwrap_or(&LspAny::Null),
        _ => &LspAny::Null,
    }
}

fn parse(text: &str) -> LspAny {
    let mut value = LspAny::Null;
    unmarshal_root(text.as_bytes(), &mut value).expect("valid JSON");
    value
}

/// `text` as a JSON string (the project ids are paths).
fn json_string(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        if matches!(c, '"' | '\\') {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}

/// A `tsgo --api --async` server on stdio whose file system is case
/// insensitive, in `/` (as the generator runs Go).
struct Api {
    child: std::process::Child,
    stdout: BufReader<std::process::ChildStdout>,
    id: u32,
}

impl Api {
    fn start() -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_tsgo"))
            .args([
                "--api",
                "--async",
                "--useCaseSensitiveFileNames=false",
                "--cwd",
                "/",
            ])
            .current_dir("/")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("start tsgo --api");
        let stdout = BufReader::new(child.stdout.take().expect("stdout"));
        Api {
            child,
            stdout,
            id: 0,
        }
    }

    /// Sends a request with `params` (JSON text) and returns its result.
    fn call(&mut self, method: &str, params: Option<&str>) -> LspAny {
        self.id += 1;
        let params = params.map_or_else(String::new, |params| format!(r#","params":{params}"#));
        let body = format!(
            r#"{{"jsonrpc":"2.0","id":{},"method":"{method}"{params}}}"#,
            self.id
        );
        let stdin = self.child.stdin.as_mut().expect("stdin");
        write!(stdin, "Content-Length: {}\r\n\r\n{body}", body.len()).expect("write a request");
        stdin.flush().expect("flush a request");
        loop {
            let mut length = 0;
            loop {
                let mut line = String::new();
                self.stdout.read_line(&mut line).expect("read a header");
                assert!(!line.is_empty(), "tsgo --api closed during {method}");
                let line = line.trim_end();
                if line.is_empty() {
                    break;
                }
                if let Some(value) = line.strip_prefix("Content-Length:") {
                    length = value.trim().parse().expect("a content length");
                }
            }
            let mut body = vec![0; length];
            self.stdout.read_exact(&mut body).expect("read a message");
            let message = parse(&String::from_utf8(body).expect("UTF-8 message"));
            let is_response = matches!(get(&message, "id"), LspAny::Number(id) if id.to_string() == self.id.to_string())
                && matches!(get(&message, "method"), LspAny::Null);
            if is_response {
                assert!(
                    matches!(get(&message, "error"), LspAny::Null),
                    "{method}: {message:?}"
                );
                return get(&message, "result").clone();
            }
        }
    }

    fn stop(mut self) {
        drop(self.child.stdin.take());
        self.child.wait().expect("wait for tsgo --api");
    }
}
