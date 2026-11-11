//! Port-only test of the seen files of `tsc --watch` (lane B8): the paths
//! that the checkers ask the program's host file system about.
//!
//! PORT: Go's checkers ask `Program.FileExists` (compiler/program.go:146),
//! which reads the program host's file system. In a full watch build that is
//! the watcher's tracking file system (execute/watcher.go:480), so each path
//! a checker probes is a seen file, and a change to it starts a full build
//! (execute/watcher.go:338). Module specifier generation probes the file
//! next to an `index` file's directory (modulespecifiers/util.go:184
//! tryGetAnyFileFromPath). The port's checker threads ask the OS file system
//! through the program's cache, and note each path for the watcher
//! (`program::track_worker_file_probes`).
//!
//! The expected builds are those of `tsgo-oracle-3e9236361fa9 -w -p
//! tsconfig.json` on the same files with the OS watcher: creating
//! `src/zzz.ts` starts no build, creating `src/lib.ts` starts one.

use ts_goport::execute;
use ts_goport::execute::tsc::Watcher;
use ts_goport::fswatch::{Event, EventKind};
use ts_goport::gostd::context;

use crate::support::child::{command_line_in_process, new_in_process_test_sys, run_test_in_child};
use crate::support::runner::TscInput;

const PROJECT: &str = "/home/src/workspaces/project";

/// The full and fast path builds of `w` (Go `w.FullBuilds()`,
/// `w.FastPathBuilds()`).
fn builds(w: &dyn Watcher) -> (i32, i32) {
    let w = w
        .as_any()
        .downcast_ref::<execute::watcher::Watcher>()
        .expect("the watcher is an *execute.Watcher");
    (w.full_builds(), w.fast_path_builds())
}

#[test]
fn a_file_that_only_a_module_specifier_probes_is_watched() {
    run_test_in_child(
        "tsctests::watch_checker_file_probes::a_file_that_only_a_module_specifier_probes_is_watched",
        || {
            let file = |name: &str, text: &str| (format!("{PROJECT}/{name}"), text.into());
            let input = TscInput {
                files: [
                    // `files`, not `include`: a new file in src/ is not a
                    // possible root file (execute/watcher.go:318).
                    file(
                        "tsconfig.json",
                        r#"{"compilerOptions":{"declaration":true,"outDir":"out","rootDir":"src","module":"esnext","moduleResolution":"bundler","types":[]},"files":["src/main.ts"]}"#,
                    ),
                    // `Foo` is not imported into main.ts, so its declaration
                    // gets a new module specifier for src/lib/index.ts, which
                    // probes src/lib.ts (and .tsx, .d.ts, ...).
                    file(
                        "src/main.ts",
                        "import { make } from \"./reexport\";\nexport const v = make();\n",
                    ),
                    file("src/reexport.ts", "export { make } from \"./lib/index\";\n"),
                    file(
                        "src/lib/index.ts",
                        "export interface Foo { a: number }\nexport function make(): Foo { return { a: 1 }; }\n",
                    ),
                ]
                .into_iter()
                .collect(),
                command_line_args: vec!["--watch".to_string()],
                ..Default::default()
            };
            let sys = new_in_process_test_sys(&input);
            let args = ["--watch", "--pretty", "false"].map(String::from);
            let result = command_line_in_process(&context::background(), &sys, &args);
            let mut w = result
                .watcher
                .expect("expected Watcher to be non-nil in watch mode");
            let fs = sys.fs_from_file_map();
            let (declaration, _) = fs.read_file(&format!("{PROJECT}/out/main.d.ts"));
            assert!(
                declaration.contains(r#"import("./lib").Foo"#),
                "the first build names Foo through the lib directory: {declaration}"
            );

            let create = |path: &str| {
                let _ = fs.write_file(&format!("{PROJECT}/{path}"), "export const z = 0;\n");
                sys.mock_watch_backend().send_events(vec![Event {
                    kind: EventKind::Update,
                    path: format!("{PROJECT}/{path}"),
                }]);
            };

            // A file that nothing asked about: no build.
            let before = builds(w.as_ref());
            create("src/zzz.ts");
            w.do_cycle();
            assert_eq!(builds(w.as_ref()), before, "src/zzz.ts is not a seen file");

            // The file that the module specifier probed: a full build.
            let (full, fast) = builds(w.as_ref());
            create("src/lib.ts");
            w.do_cycle();
            assert_eq!(
                builds(w.as_ref()),
                (full + 1, fast),
                "a file that a checker probed is a seen file"
            );
        },
    );
}
