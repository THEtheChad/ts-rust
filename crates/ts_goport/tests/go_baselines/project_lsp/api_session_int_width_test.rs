//! PORT: no Go file. Session tests for Go `int` and `uint32` request fields
//! whose values do not fit the port's `i32` positions (lane B9, audit A1 A3
//! and A4). Each expected answer is the pinned Go server's
//! (`scripts/perf-rearch/b9/api_probe.py` with `probe-api.json`).
//!
//! The requests go through `handle_batch_requests` with their JSON params,
//! as a client sends them.

use ts_goport::api::{self, BatchRequest, BatchRequestsParams, Method};
use ts_goport::frontend::json::json_marshal;
use ts_goport::frontend::json_ext::{JsonValue, LspAny};

use super::api_util::{nil_error, no_lib, program_params};
use super::projecttestutil::{self, TypingsInstallerOptions, files};
use super::util::bg;

/// One request of a batch, and the answer's JSON (`{"method", "result"?,
/// "error"?}`).
fn ask(session: &api::Session, method: &str, params: &str) -> LspAny {
    let response = nil_error(session.handle_batch_requests(
        &bg(),
        &BatchRequestsParams {
            requests: vec![BatchRequest {
                method: Method(method.to_string().into()),
                params: JsonValue(params.as_bytes().to_vec()),
            }],
            ..Default::default()
        },
    ));
    let encoded = json_marshal(&response, &[]).unwrap_or_else(|err| panic!("marshal: {err:?}"));
    let mut wire = LspAny::default();
    ts_goport::frontend::json::json_unmarshal(encoded.as_bytes(), &mut wire, &[])
        .unwrap_or_else(|err| panic!("unmarshal: {err:?}"));
    let LspAny::Object(mut page) = wire else {
        panic!("not an object: {encoded}");
    };
    match page.shift_remove("responses") {
        Some(LspAny::Array(mut responses)) if responses.len() == 1 => responses.remove(0),
        other => panic!("not one response: {other:?}"),
    }
}

fn field<'a>(response: &'a LspAny, name: &str) -> Option<&'a LspAny> {
    match response {
        LspAny::Object(object) => object.get(name),
        _ => None,
    }
}

// Audit A3: Go `UTF16ToUTF8(int(params.Position))` (session.go:4167) keeps a
// uint32 position at or above 2^31 as a 64-bit int past the text, so
// GetIndentation takes its past-EOF branch and the request succeeds: Go
// formats the node (`"export {};"`) at 1000, 2147483648 and 4294967295. The
// port read the position as a negative `i32` and panicked on a line index.
child_test! {
    fn format_node_for_insertion_position_past_i32() {
        let (init, _) = projecttestutil::get_session_init_options(
            files(&[
                ("/home/projects/p/src/a.ts", "export const x = 1;\nimport './b';\n"),
                ("/home/projects/p/src/b.ts", "export {};\n"),
            ]),
            None,
            TypingsInstallerOptions::default(),
        );
        let session = api::new_standalone_session(&init, None);
        let created = nil_error(session.handle_create_snapshot(
            &bg(),
            &api::CreateSnapshotParams {
                snapshot_request_changes_params: api::SnapshotRequestChangesParams {
                    create_programs: Some(vec![Some(program_params(
                        &["/home/projects/p/src/a.ts"],
                        no_lib(),
                    ))]),
                    ..Default::default()
                },
                ..Default::default()
            },
        ));
        let project = json_marshal(&created.projects[0].id, &[])
            .unwrap_or_else(|err| panic!("marshal: {err:?}"));
        let snapshot = created.snapshot.0;

        let source_file = ask(
            &session,
            "getSourceFile",
            &format!(
                r#"{{"snapshot":{snapshot},"project":{project},"file":"/home/projects/p/src/b.ts"}}"#
            ),
        );
        let Some(LspAny::String(data)) = field(&source_file, "result").and_then(|r| field(r, "data"))
        else {
            panic!("no source file data: {source_file:?}");
        };

        for position in [1000_u64, 2_147_483_648, 4_294_967_295] {
            let response = ask(
                &session,
                "formatNodeForInsertion",
                &format!(
                    r#"{{"snapshot":{snapshot},"project":{project},"file":"/home/projects/p/src/a.ts","position":{position},"data":"{data}"}}"#
                ),
            );
            assert_eq!(field(&response, "error"), None, "position {position}");
            assert_eq!(
                field(&response, "result"),
                Some(&LspAny::String("export {};".to_string())),
                "position {position}"
            );
        }
        session.close();
    }
}

// Audit A4: Go `GetReferencedSymbolsForNodeParams.Position` is a Go `int`
// (proto.go:1553), so a position outside int32 decodes and the request
// runs: for the source file's handle Go answers `[]` at 0, 2^32 and -2^32.
// The port's `i32` field failed the decode. The handle is the declaration
// of the module symbol that getSymbolAtPosition gives past the text (Go
// and the port both give it at 2147483648: audit A3b).
child_test! {
    fn referenced_symbols_position_is_a_go_int() {
        let (init, _) = projecttestutil::get_session_init_options(
            files(&[("/home/projects/p/src/a.ts", "export const x = 1;\n")]),
            None,
            TypingsInstallerOptions::default(),
        );
        let session = api::new_standalone_session(&init, None);
        let created = nil_error(session.handle_create_snapshot(
            &bg(),
            &api::CreateSnapshotParams {
                snapshot_request_changes_params: api::SnapshotRequestChangesParams {
                    create_programs: Some(vec![Some(program_params(
                        &["/home/projects/p/src/a.ts"],
                        no_lib(),
                    ))]),
                    ..Default::default()
                },
                ..Default::default()
            },
        ));
        let project = json_marshal(&created.projects[0].id, &[])
            .unwrap_or_else(|err| panic!("marshal: {err:?}"));
        let snapshot = created.snapshot.0;

        let symbol = ask(
            &session,
            "getSymbolAtPosition",
            &format!(
                r#"{{"snapshot":{snapshot},"project":{project},"file":"/home/projects/p/src/a.ts","position":2147483648}}"#
            ),
        );
        let Some(LspAny::String(handle)) = field(&symbol, "result")
            .and_then(|r| field(r, "valueDeclaration"))
        else {
            panic!("no module declaration: {symbol:?}");
        };

        for position in ["0", "4294967296", "-4294967296"] {
            let response = ask(
                &session,
                "getReferencedSymbolsForNode",
                &format!(
                    r#"{{"snapshot":{snapshot},"project":{project},"node":"{handle}","position":{position}}}"#
                ),
            );
            assert_eq!(field(&response, "error"), None, "position {position}");
            assert_eq!(
                field(&response, "result"),
                Some(&LspAny::Array(Vec::new())),
                "position {position}"
            );
        }
        session.close();
    }
}
