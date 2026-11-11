//! Lane B4 (perf-rearch): the server-to-client calls of a watch session's
//! load and of the file events after it, in order: the diagnostics refreshes
//! that ATA's results and the watched-file events schedule, and the ATA
//! progress around them. No Go test; the expected sequences are Go's at the
//! pin, with the LSP server's debounce (lsp/server.go: `DebounceDelay` 500 ms):
//!
//! - project/session.go `updateSnapshot` starts ATA for each project whose
//!   `ShouldTriggerATA` holds (`triggerATAForUpdatedProjects`). A didOpen's
//!   snapshot does not take the pending ATA state (`DidOpenFile` flushes with
//!   `flushChangesLocked`), so until a request's snapshot applies it, every
//!   open into the inferred project runs ATA again, and each result differs
//!   from the project's typings and calls `ScheduleDiagnosticsRefresh`.
//! - `scheduleDiagnosticsRefresh` cancels the scheduled refresh and waits
//!   out the debounce again: results within one debounce window send one
//!   refresh. So the count depends on when the client sends its
//!   notifications, for Go as for tsc-rs: a client that sends the next
//!   didOpen before the server has handled the last one gets fewer (lane
//!   B4, watch-hono-f12's load: 10 to 12 refreshes on a starved CPU, Go and
//!   tsc-rs alike). Each step below waits for the server's background work
//!   (Go `WaitForBackgroundTasks`), as lspdiff's scenarios must.
//! - `DidChangeWatchedFiles` schedules a refresh for an event on a path with
//!   a relevant extension (or a directory), and not for others.
//! - `DidChangeFile` of a file that no content mapper handles cancels the
//!   scheduled refresh.

use std::rc::Rc;
use std::time::Duration;

use ts_goport::diag;
use ts_goport::lsp::lsproto;
use ts_goport::project::{Session, SessionOptions};

use super::projecttestutil::{self, ClientCall, SessionUtils, TypingsInstallerOptions, files};
use super::util::*;

const DIR: &str = "/user/username/projects/project";

fn file_uri(name: &str) -> String {
    format!("file://{DIR}/{name}")
}

fn setup() -> (Rc<Session>, SessionUtils) {
    let files = files(&[
        (
            "/user/username/projects/project/package.json",
            r#"{ "name": "test", "dependencies": { "jquery": "^3.1.0" } }"#,
        ),
        ("/user/username/projects/project/app.js", ""),
        ("/user/username/projects/project/b.js", ""),
        ("/user/username/projects/project/c.js", ""),
        ("/user/username/projects/project/d.js", ""),
        ("/user/username/projects/project/e.js", ""),
    ]);
    let ti_options = TypingsInstallerOptions {
        types_registry: Vec::new(),
        package_to_file: [(
            "jquery".to_string(),
            "declare const $: { x: number }".to_string(),
        )]
        .into(),
    };
    projecttestutil::setup_with_options_and_typings_installer(
        files,
        Some(SessionOptions {
            debounce_delay: Duration::from_millis(500),
            ..projecttestutil::default_session_options()
        }),
        ti_options,
    )
}

fn open_js(session: &Rc<Session>, name: &str) {
    open_kind(
        session,
        &file_uri(name),
        "",
        lsproto::LanguageKind::JAVA_SCRIPT,
    );
}

/// The calls since the last `take`: the diagnostics refreshes and ATA's
/// progress (`START` and `FINISH`, Go `Installing_types_for_0`). The pushed
/// diagnostics and a project load's progress come with each snapshot, not
/// from ATA or a debounce.
struct Calls {
    seen: usize,
}

const START: &str = "ata start";
const FINISH: &str = "ata finish";
const REFRESH: &str = "refresh";

impl Calls {
    fn take(&mut self, utils: &SessionUtils) -> Vec<&'static str> {
        let all = utils.client().sequence();
        let ata = diag::Installing_types_for_0.key();
        let fresh = all[self.seen..]
            .iter()
            .filter_map(|call| match call {
                ClientCall::RefreshDiagnostics => Some(REFRESH),
                ClientCall::ProgressStart(key) if *key == ata => Some(START),
                ClientCall::ProgressFinish(key) if *key == ata => Some(FINISH),
                _ => None,
            })
            .collect();
        self.seen = all.len();
        fresh
    }
}

fn refresh_sequence() {
    let (session, utils) = setup();
    let mut calls = Calls { seen: 0 };

    // Load: each open into the inferred project runs ATA, and each result
    // schedules a refresh of its own (the steps wait for the server).
    open_js(&session, "app.js");
    session.wait_for_background_tasks();
    assert_eq!(calls.take(&utils), [START, FINISH, REFRESH], "first open");
    let npm: Vec<String> = utils
        .npm_executor()
        .npm_install_calls()
        .iter()
        .map(|c| c.args[2].clone())
        .collect();
    assert_eq!(npm, ["types-registry@latest", "@types/jquery@latest"]);
    for name in ["b.js", "c.js"] {
        open_js(&session, name);
        session.wait_for_background_tasks();
        assert_eq!(calls.take(&utils), [START, FINISH, REFRESH], "open {name}");
    }
    assert_eq!(utils.npm_executor().npm_install_calls().len(), 2);

    // Two opens with no wait between them: both ATA results fall in one
    // debounce window, so one refresh (Go coalesces them the same way).
    open_js(&session, "d.js");
    open_js(&session, "e.js");
    session.wait_for_background_tasks();
    assert_eq!(
        calls.take(&utils),
        [START, FINISH, START, FINISH, REFRESH],
        "back-to-back opens"
    );

    // A request's snapshot applies the ATA state; the new program runs ATA
    // again, and its result equals the project's typings: no refresh.
    let p = program(&session, &file_uri("app.js"));
    assert!(has_file(
        &p,
        &format!(
            "{}/node_modules/@types/jquery/index.d.ts",
            projecttestutil::TEST_TYPINGS_LOCATION
        )
    ));
    session.wait_for_background_tasks();
    assert_eq!(
        calls.take(&utils),
        [START, FINISH],
        "request after the load"
    );

    // File events.
    watch(&session, &[(CREATED, &file_uri("src/new.js"))]);
    session.wait_for_background_tasks();
    assert_eq!(calls.take(&utils), [REFRESH], "relevant event");

    watch(&session, &[(CREATED, &file_uri("src/x.js"))]);
    watch(&session, &[(CHANGED, &file_uri("src/y.ts"))]);
    session.wait_for_background_tasks();
    assert_eq!(calls.take(&utils), [REFRESH], "two events in one window");

    watch(&session, &[(CREATED, &file_uri("icon.svg"))]);
    session.wait_for_background_tasks();
    assert_eq!(calls.take(&utils), [] as [&str; 0], "irrelevant event");

    watch(&session, &[(DELETED, &file_uri("src/new.js"))]);
    edit(&session, &file_uri("app.js"), 2, (0, 0), (0, 0), "$;");
    session.wait_for_background_tasks();
    assert_eq!(
        calls.take(&utils),
        [] as [&str; 0],
        "an edit cancels the refresh"
    );

    assert_eq!(utils.client().refresh_diagnostics_calls(), 6);
}

child_test! {
    fn load_and_file_events() {
        refresh_sequence();
    }
}

child_test! {
    env &[("GOPORT_PARSE_THREADS", "1")];
    fn load_and_file_events_one_parse_thread() {
        refresh_sequence();
    }
}
