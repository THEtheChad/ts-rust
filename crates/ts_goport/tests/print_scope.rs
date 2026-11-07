//! Print scopes (n1free): the checker frees the nodes of each to-string call
//! when the call ends (`ast::synthetic::enter_print_scope`, `PrintScope` in
//! `checker/printer_impl.rs`), as Go `getNodeBuilder`'s `release` lets the GC
//! take them. A read of a freed node panics, so a run that ends with exit
//! code 2 and Go's text read none.
//!
//! The fixture `fixtures/print_scope` (the n1note scratch project) has 27
//! errors that print object, mapped, conditional, tuple, generic signature,
//! type predicate, class, enum, late-bound key and long-union types. Its
//! to-string calls pin (a serialized type stored under an enclosing
//! declaration), hit that cache, and make fake scopes (n1note probe: 159
//! calls, 7 pinned, 10 cache hits, 4 fake scopes). The expected text and
//! d.ts files are the output of `tsgo-oracle-fed0bf24149f` (Go N') on the
//! same files: `-p tsconfig.json --pretty false`, and
//! `-p tsconfig.decl.json --pretty false --outDir <dir>`, run in the
//! fixture dir. A `--generateTrace` types dump (`types_0.json`) must be
//! `tsgo-oracle-3e9236361fa9`'s, with the fixture dir as `{fixture}`.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

const FIXTURE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/print_scope");

/// What one `tsgo` run printed.
#[derive(Debug, PartialEq)]
struct Run {
    stdout: String,
    stderr: String,
    status: Option<i32>,
}

/// Runs `tsgo` in the fixture dir with `args`.
fn tsgo(args: &[&str]) -> Run {
    let output = Command::new(env!("CARGO_BIN_EXE_tsgo"))
        .current_dir(FIXTURE)
        .args(args)
        .output()
        .expect("run tsgo");
    Run {
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        status: output.status.code(),
    }
}

/// A new directory under the system temp dir.
fn scratch_dir() -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock after 1970")
        .as_nanos();
    let dir =
        std::env::temp_dir().join(format!("goport-print-scope-{}-{nanos}", std::process::id()));
    fs::create_dir(&dir).unwrap_or_else(|error| panic!("create {}: {error}", dir.display()));
    dir
}

fn expected(name: &str) -> String {
    let path = Path::new(FIXTURE).join("expected").join(name);
    fs::read_to_string(&path).unwrap_or_else(|error| panic!("read {}: {error}", path.display()))
}

/// The run must print Go's diagnostics, exit with 2 and not panic.
fn assert_go_diagnostics(run: &Run, case: &str) {
    assert_eq!(
        run.stderr, "",
        "{case}: stderr (a read of a freed node panics)"
    );
    assert_eq!(run.status, Some(2), "{case}: exit code");
    assert_eq!(
        run.stdout,
        expected("diagnostics.txt"),
        "{case}: diagnostics"
    );
}

#[test]
fn print_scope_diagnostics_are_go_text() {
    for checkers in [&["--singleThreaded"][..], &["--checkers", "4"]] {
        let mut args = vec!["-p", "tsconfig.json", "--pretty", "false"];
        args.extend_from_slice(checkers);
        assert_go_diagnostics(&tsgo(&args), &format!("{checkers:?}"));
        // The same text without truncation (the long union fits).
        args.push("--noErrorTruncation");
        assert_go_diagnostics(&tsgo(&args), &format!("{checkers:?} noErrorTruncation"));
    }
}

#[test]
fn print_scope_declaration_emit_is_go_text() {
    for checkers in [&["--singleThreaded"][..], &["--checkers", "4"]] {
        let out = scratch_dir();
        let out_dir = out.to_str().expect("utf-8 temp dir");
        let mut args = vec![
            "-p",
            "tsconfig.decl.json",
            "--pretty",
            "false",
            "--outDir",
            out_dir,
        ];
        args.extend_from_slice(checkers);
        assert_go_diagnostics(&tsgo(&args), &format!("d.ts {checkers:?}"));
        for name in ["a.d.ts", "b.d.ts"] {
            let path = out.join(name);
            let text = fs::read_to_string(&path)
                .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
            assert_eq!(text, expected(name), "{checkers:?}: {name}");
        }
        fs::remove_dir_all(&out)
            .unwrap_or_else(|error| panic!("remove {}: {error}", out.display()));
    }
}

#[test]
fn print_scope_trace_types_are_go_types() {
    let out = scratch_dir();
    let out_dir = out.to_str().expect("utf-8 temp dir");
    let args = [
        "-p",
        "tsconfig.json",
        "--pretty",
        "false",
        "--singleThreaded",
        "--generateTrace",
        out_dir,
    ];
    assert_go_diagnostics(&tsgo(&args), "trace");
    let path = out.join("types_0.json");
    let text = fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    fs::remove_dir_all(&out).unwrap_or_else(|error| panic!("remove {}: {error}", out.display()));
    // The trace prints Go path keys: `/` separators, lowercased where the
    // file system ignores case (Windows: `c:/...`).
    let fixture = FIXTURE.replace('\\', "/");
    assert_eq!(
        text.replace(&fixture, "{fixture}")
            .replace(&fixture.to_lowercase(), "{fixture}"),
        expected("types_0.json"),
        "types_0.json"
    );
}
