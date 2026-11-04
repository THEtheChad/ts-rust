//! The early emit of plain `tsc -p` (no incremental or composite program,
//! `execute::tsc::EarlyEmitCompilerProgram`). Each checker gets its check,
//! then its global diagnostics read, then its emit, so it emits when its
//! own check ends. The plain program reads the global diagnostics after the
//! check (Go program.go:2088); the read queued before each checker's emit
//! keeps the globals that an emit makes out of it. The emit makes no write
//! before every check ended: its writes wait in memory.
//!
//! Each test compares a run with the early emit against a run with
//! `GOPORT_PLAIN_EARLY_EMIT=0` (the emit after the whole check, as in Go).
//! A plain run (no instrumentation) gives the outputs, stdout, stderr and
//! the exit code, which must be equal. A second run of each side with
//! `GOPORT_EMIT_PHASE_TIMES=1` and `GOPORT_CHECKER_CALL_LOG` gives the emit
//! routing (which side started the emit early) and the checker call log
//! (each checker's jobs with its type, symbol and instantiation counts),
//! which must be equal too; its stderr without the recognized report must
//! be the plain run's. Every run sets each `GOPORT_*` variable it depends
//! on, and writes to the same new directory under the system temp dir, so
//! the paths in outputs and messages are the same. A passing test deletes
//! it.
//!
//! The panic and stall tests use the test hooks `GOPORT_TEST_CHECK_PANIC`
//! and `GOPORT_TEST_CHECK_STALL` (`program::test_check_hook`): a check that
//! panics, or one that blocks until the process is killed, must leave no
//! output file, while the other checkers have emitted.

use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const FIXTURE_SRC: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/emit_pool/src");

/// What one `tsgo` run wrote and printed.
#[derive(Debug, PartialEq)]
struct Run {
    /// The bytes of each file under the out dir, by relative path.
    files: BTreeMap<String, Vec<u8>>,
    stdout: String,
    stderr: String,
    status: Option<i32>,
}

/// How a run sent its emit, from the routing counts.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum Start {
    Early,
    Late,
    None,
}

/// What an instrumented run reported.
struct Instrumented {
    start: Start,
    call_log: String,
    /// stderr without the phase report.
    stderr: String,
}

/// The `GOPORT_*` variables that change how a run emits, removed from
/// every run before it sets its own.
const RUN_ENV: [&str; 7] = [
    "GOPORT_EARLY_EMIT",
    "GOPORT_PLAIN_EARLY_EMIT",
    "GOPORT_EMIT_PHASE_TIMES",
    "GOPORT_CHECKER_CALL_LOG",
    "GOPORT_TEST_CHECK_PANIC",
    "GOPORT_TEST_CHECK_STALL",
    "GOPORT_EMIT_THREADS",
];

/// `tsgo -p tsconfig.json --outDir out --pretty false <args>` in `root`,
/// with each `GOPORT_*` variable of `RUN_ENV` unset, then `env` set.
fn tsgo_command(root: &Path, args: &[&str], early: bool, env: &[(&str, &str)]) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_tsgo"));
    command
        .current_dir(root)
        .args([
            "-p",
            "tsconfig.json",
            "--outDir",
            "out",
            "--pretty",
            "false",
        ])
        .args(args);
    for name in RUN_ENV {
        command.env_remove(name);
    }
    command
        .env("GOPORT_EARLY_EMIT", "1")
        .env("GOPORT_EMIT_THREADS", "2")
        .env("GOPORT_PLAIN_EARLY_EMIT", if early { "1" } else { "0" });
    for (name, value) in env {
        command.env(name, value);
    }
    command
}

/// Removes `root/out`, then makes it with `prepare_out`.
fn reset_out(root: &Path, prepare_out: Option<&dyn Fn(&Path)>) -> PathBuf {
    let out = root.join("out");
    if out.is_dir() {
        fs::remove_dir_all(&out).expect("remove out");
    } else if out.exists() {
        fs::remove_file(&out).expect("remove out file");
    }
    if let Some(prepare) = prepare_out {
        prepare(&out);
    }
    out
}

/// A plain run, no instrumentation.
fn tsgo(
    root: &Path,
    args: &[&str],
    early: bool,
    prepare_out: Option<&dyn Fn(&Path)>,
    env: &[(&str, &str)],
) -> Run {
    let out = reset_out(root, prepare_out);
    let output = tsgo_command(root, args, early, env)
        .output()
        .expect("run tsgo");
    let mut files = BTreeMap::new();
    if out.is_dir() {
        read_files(&out, &out, &mut files);
    }
    Run {
        files,
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        status: output.status.code(),
    }
}

/// An instrumented run: the routing, the call log (a new file each run)
/// and stderr without the phase report.
fn tsgo_instrumented(
    root: &Path,
    args: &[&str],
    early: bool,
    prepare_out: Option<&dyn Fn(&Path)>,
) -> Instrumented {
    reset_out(root, prepare_out);
    let log = root.join("calls.log");
    if log.exists() {
        fs::remove_file(&log).expect("remove the call log");
    }
    let log_name = log.to_string_lossy().into_owned();
    let output = tsgo_command(
        root,
        args,
        early,
        &[
            ("GOPORT_EMIT_PHASE_TIMES", "1"),
            ("GOPORT_CHECKER_CALL_LOG", &log_name),
        ],
    )
    .output()
    .expect("run tsgo");
    let (stderr, routes) = split_phase_report(&String::from_utf8_lossy(&output.stderr));
    let count = |name: &str| routes.get(name).copied().unwrap_or(0);
    let start = match (
        count("emit sent with check (plain tsc -p)"),
        count("emit sent after check"),
    ) {
        (0, 0) => Start::None,
        (_, 0) => Start::Early,
        (0, _) => Start::Late,
        _ => panic!("a run sent its emit twice: {routes:?}"),
    };
    let call_log = fs::read_to_string(&log).expect("each instrumented run writes its call log");
    Instrumented {
        start,
        call_log,
        stderr,
    }
}

/// Splits the `GOPORT_EMIT_PHASE_TIMES` report off `stderr`: returns the
/// rest, and the routing counts by name. The report must be there once,
/// in its known form (a header, indented table lines, the routing header
/// and indented `<name> <count>` lines); any other line stays in the rest.
fn split_phase_report(stderr: &str) -> (String, BTreeMap<String, usize>) {
    const HEADER: &str = "emit phase times (GOPORT_EMIT_PHASE_TIMES)";
    const ROUTING: &str = "emit routing (files)";
    let lines: Vec<&str> = stderr.split_inclusive('\n').collect();
    let at = lines
        .iter()
        .position(|line| line.trim_end_matches('\n') == HEADER)
        .unwrap_or_else(|| panic!("no phase report in stderr: {stderr}"));
    let mut end = at + 1;
    while end < lines.len() && lines[end].starts_with("  ") {
        end += 1;
    }
    assert_eq!(
        lines.get(end).map(|line| line.trim_end_matches('\n')),
        Some(ROUTING),
        "the phase report has its routing part: {stderr}"
    );
    end += 1;
    let mut routes = BTreeMap::new();
    while end < lines.len() && lines[end].starts_with("  ") {
        let line = lines[end].trim();
        let (name, count) = line.rsplit_once(' ').expect("a routing line has a count");
        routes.insert(
            name.trim().to_owned(),
            count.parse().expect("a routing count"),
        );
        end += 1;
    }
    let rest: String = lines[..at].iter().chain(&lines[end..]).copied().collect();
    assert!(!rest.contains(HEADER), "one phase report: {stderr}");
    (rest, routes)
}

/// Runs `args` early and after the barrier, checks that both are equal and
/// that the early run started as `expected`, and returns the early run.
fn compare(root: &Path, case: &str, args: &[&str], expected: Start) -> Run {
    compare_with(root, case, args, expected, None)
}

fn compare_with(
    root: &Path,
    case: &str,
    args: &[&str],
    expected: Start,
    prepare_out: Option<&dyn Fn(&Path)>,
) -> Run {
    let barrier = tsgo(root, args, false, prepare_out, &[]);
    let early = tsgo(root, args, true, prepare_out, &[]);
    assert_eq!(early, barrier, "{case}: the early emit against the barrier");
    let barrier_report = tsgo_instrumented(root, args, false, prepare_out);
    let early_report = tsgo_instrumented(root, args, true, prepare_out);
    assert_eq!(
        early_report.start, expected,
        "{case}: how the early run sent its emit"
    );
    assert_ne!(
        barrier_report.start,
        Start::Early,
        "{case}: GOPORT_PLAIN_EARLY_EMIT=0 keeps the barrier"
    );
    assert_eq!(
        early_report.call_log, barrier_report.call_log,
        "{case}: each checker's calls, early against the barrier"
    );
    assert_eq!(
        (&early_report.stderr, &barrier_report.stderr),
        (&early.stderr, &barrier.stderr),
        "{case}: stderr without the phase report is the plain run's"
    );
    early
}

/// The `emit_pool` fixture (4 program files, es2020 libs) in a scratch dir,
/// with `options` added to its compiler options.
fn pool_project(options: &str) -> PathBuf {
    let root = scratch_dir();
    copy_dir(Path::new(FIXTURE_SRC), &root.join("src"));
    fs::write(
        root.join("tsconfig.json"),
        format!(
            r#"{{"compilerOptions": {{"target": "es2020", "lib": ["es2020"], "module": "esnext",
  "moduleResolution": "bundler", "strict": true, "allowJs": true, "rootDir": "src",
  "types": []{options}}}, "include": ["src"]}}"#
        ),
    )
    .expect("write the config");
    root
}

#[test]
fn plain_early_emit_writes_what_the_barrier_writes() {
    let root = pool_project("");
    let cases: [(&str, &[&str]); 9] = [
        ("js on the checkers", &[]),
        ("js on the pool", &["--verbatimModuleSyntax"]),
        ("js and d.ts", &["--declaration", "--declarationMap"]),
        (
            "js and d.ts on the pool",
            &["--declaration", "--sourceMap", "--verbatimModuleSyntax"],
        ),
        ("d.ts only", &["--declaration", "--emitDeclarationOnly"]),
        ("commonjs", &["--module", "commonjs", "--declaration"]),
        ("4 checkers", &["--checkers", "4", "--declaration"]),
        ("8 checkers", &["--checkers", "8", "--declaration"]),
        ("16 checkers", &["--checkers", "16", "--declaration"]),
    ];
    for (case, args) in cases {
        let run = compare(&root, case, args, Start::Early);
        assert_eq!(run.status, Some(0), "{case}: {}", run.stdout);
        assert!(
            run.files
                .keys()
                .any(|name| name.ends_with(".js") || name.ends_with(".d.ts")),
            "{case}: the run writes outputs"
        );
    }
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// The rules that keep Go's order start no early emit (`noEmitOnError`,
/// `--singleThreaded`); `--listFilesOnly` emits nothing. The other rules
/// (F1 to F4, `outFile`) are `emit_can_start_with_check`'s, tested in
/// `early_emit.rs`.
#[test]
fn plain_early_emit_keeps_the_barrier_where_the_rules_say() {
    let root = pool_project("");
    for (case, args) in [
        ("noEmitOnError", &["--noEmitOnError", "--declaration"][..]),
        ("singleThreaded", &["--singleThreaded", "--declaration"]),
        ("listFilesOnly", &["--listFilesOnly"]),
    ] {
        compare(
            &root,
            case,
            args,
            if case == "listFilesOnly" {
                Start::None
            } else {
                Start::Late
            },
        );
    }
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// Statistics: the counts of `--diagnostics` and `--extendedDiagnostics` (not
/// the times and memory) are the barrier's.
#[test]
fn plain_early_emit_keeps_the_statistics_counts() {
    let root = pool_project("");
    for flag in ["--diagnostics", "--extendedDiagnostics"] {
        let args = [flag, "--declaration"];
        let barrier = tsgo(&root, &args, false, None, &[]);
        let early = tsgo(&root, &args, true, None, &[]);
        let barrier_report = tsgo_instrumented(&root, &args, false, None);
        let early_report = tsgo_instrumented(&root, &args, true, None);
        assert_eq!(early_report.start, Start::Early, "{flag}");
        let counts = |run: &Run| -> Vec<String> {
            run.stdout
                .lines()
                .filter(|line| {
                    let lower = line.to_lowercase();
                    line.contains(':') && !lower.contains("time") && !lower.contains("memory")
                })
                .map(str::to_owned)
                .collect()
        };
        assert!(
            counts(&early).iter().any(|line| line.starts_with("Types:")),
            "{flag}: {}",
            early.stdout
        );
        assert_eq!(counts(&early), counts(&barrier), "{flag}: the counts");
        assert_eq!(early.files, barrier.files, "{flag}: the outputs");
        assert_eq!(
            (early.status, &early.stderr),
            (barrier.status, &barrier.stderr),
            "{flag}: exit and stderr"
        );
        assert_eq!(
            early_report.call_log, barrier_report.call_log,
            "{flag}: the call log"
        );
    }
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// A minimal lib with no `IterableIterator` and no `Generator`: the check
/// of a generator's return type, or the d.ts emit of it, adds TS2318 to the
/// checker's global diagnostics.
const MINI_LIB: &str = "interface Boolean {}\ninterface Function {}\ninterface CallableFunction {}\n\
interface NewableFunction {}\ninterface IArguments {}\ninterface Number { toExponential: any; }\n\
interface Object {}\ninterface RegExp {}\ninterface String { charAt: any; }\n\
interface Array<T> { length: number; [n: number]: T; }\ninterface ReadonlyArray<T> {}\n";

fn global_project() -> PathBuf {
    let root = scratch_dir();
    fs::create_dir(root.join("src")).expect("create src");
    fs::write(
        root.join("tsconfig.json"),
        r#"{"compilerOptions": {"noLib": true, "target": "es2022", "module": "esnext", "rootDir": "src"}, "include": ["src"]}"#,
    )
    .expect("write the config");
    fs::write(root.join("src/lib.d.ts"), MINI_LIB).expect("write lib.d.ts");
    fs::write(
        root.join("src/gen.ts"),
        "export function* values() { yield 1; }\n",
    )
    .expect("write gen.ts");
    for i in 0..12 {
        fs::write(
            root.join(format!("src/m{i}.ts")),
            format!(
                "import {{ values }} from \"./gen\";\nexport const v{i} = values();\nexport function g{i}(x: number) {{ return x * {i}; }}\n"
            ),
        )
        .expect("write a module");
    }
    root
}

/// The global diagnostics boundary. With `noCheck` the d.ts emit of the
/// generator makes TS2318 'IterableIterator' in the checker's globals after
/// Go's read: Go reports nothing and exits 0. The early emit sends the read
/// before each checker's emit, so it reports nothing either (a read after
/// the emit would report TS2318). With the check, the check makes TS2318
/// before the read, and both report it once.
#[test]
fn plain_early_emit_reads_the_global_diagnostics_before_the_emit() {
    let root = global_project();
    let run = compare(
        &root,
        "noCheck",
        &["--noCheck", "--declaration"],
        Start::Early,
    );
    assert_eq!(
        (run.status, run.stdout.as_str()),
        (Some(0), ""),
        "noCheck: no TS2318 from the emit"
    );
    for checkers in ["4", "8", "16"] {
        let run = compare(
            &root,
            "check",
            &["--declaration", "--checkers", checkers],
            Start::Early,
        );
        assert_eq!(run.status, Some(2), "check: {}", run.stdout);
        assert_eq!(
            run.stdout.matches("TS2318").count(),
            1,
            "check: the global from the check, once: {}",
            run.stdout
        );
    }
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// Syntax errors and option errors: Go skips the check (and the second
/// global diagnostics read) and emits; no early emit starts.
#[test]
fn plain_early_emit_skips_syntax_and_option_errors() {
    let root = pool_project("");
    fs::write(
        root.join("src/broken.ts"),
        "export const broken: number = ;\n",
    )
    .expect("write");
    let run = compare(&root, "syntax", &["--declaration"], Start::Late);
    assert_eq!(run.status, Some(2), "syntax: {}", run.stdout);
    fs::remove_file(root.join("src/broken.ts")).expect("remove broken.ts");
    // TS5069: declarationDir without declaration.
    let run = compare(&root, "option", &["--declarationDir", "decl"], Start::Late);
    assert_eq!(run.status, Some(2), "option: {}", run.stdout);
    assert!(run.stdout.contains("TS5069"), "option: {}", run.stdout);
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// Semantic errors in many files: the emit still starts early and writes
/// the same outputs and diagnostics.
#[test]
fn plain_early_emit_with_semantic_errors() {
    let root = pool_project("");
    for i in 0..16 {
        fs::write(
            root.join(format!("src/err{i}.ts")),
            format!("export const s{i}: string = {i};\nexport enum E{i} {{ A = {i}, B }}\nexport const e{i} = E{i}.B;\n"),
        )
        .expect("write");
    }
    let run = compare(
        &root,
        "semantic",
        &["--declaration", "--sourceMap"],
        Start::Early,
    );
    assert_eq!(run.status, Some(2), "semantic: {}", run.stdout);
    assert_eq!(
        run.stdout.matches("TS2322").count(),
        16,
        "semantic: {}",
        run.stdout
    );
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// Output path collisions are program diagnostics (TS5055, TS5056): no
/// early emit, the barrier's outputs.
#[test]
fn plain_early_emit_with_output_collisions() {
    let root = pool_project("");
    fs::write(
        root.join("tsconfig.json"),
        r#"{"compilerOptions": {"allowJs": true, "jsx": "preserve", "rootDir": "src", "types": []},
  "files": ["src/a.ts", "src/a.js", "src/b.ts", "src/b.tsx"]}"#,
    )
    .expect("write the config");
    for (name, text) in [
        ("a.ts", "export const a = 1;\n"),
        ("a.js", "export const a = 2;\n"),
        ("b.ts", "export const b = 1;\n"),
        ("b.tsx", "export const b = <div/>;\n"),
    ] {
        fs::write(root.join("src").join(name), text).expect("write");
    }
    let run = compare(&root, "collision", &["--declaration"], Start::Late);
    assert!(run.stdout.contains("TS5056"), "collision: {}", run.stdout);
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// Failed writes during the early emit (a directory at an output path, or
/// an out dir that is a file) give the barrier's TS5033 diagnostics.
#[cfg(unix)]
#[test]
fn plain_early_emit_reports_failed_writes_as_the_barrier() {
    let root = pool_project("");
    let dir_at_output = |out: &Path| {
        fs::create_dir_all(out.join("index.js")).expect("dir at output");
        fs::create_dir_all(out.join("shapes.d.ts")).expect("dir at output");
    };
    let run = compare_with(
        &root,
        "dir at output",
        &["--declaration", "--sourceMap"],
        Start::Early,
        Some(&dir_at_output),
    );
    assert!(
        run.stdout.contains("TS5033"),
        "dir at output: {}",
        run.stdout
    );
    let out_is_file = |out: &Path| fs::write(out, "not a dir\n").expect("out file");
    let run = compare_with(
        &root,
        "out is a file",
        &["--declaration"],
        Start::Early,
        Some(&out_is_file),
    );
    assert!(
        run.stdout.contains("TS5033"),
        "out is a file: {}",
        run.stdout
    );
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// A project of many small files and one slow file (`slow.ts`, a deep
/// conditional type), on 4 checkers, so the other checkers end their check
/// and emit long before the slow file's checker.
fn panic_project() -> PathBuf {
    let root = pool_project("");
    for i in 0..24 {
        fs::write(
            root.join(format!("src/m{i}.ts")),
            format!("export const m{i}: number = {i};\nexport function f{i}(x: string) {{ return x.length + {i}; }}\n"),
        )
        .expect("write a module");
    }
    fs::write(
        root.join("src/slow.ts"),
        "export type Deep<T, N extends unknown[] = []> = N['length'] extends 40 ? T : Deep<{ v: T }, [...N, 0]>;\nexport const slow: Deep<number> = null!;\n",
    )
    .expect("write slow.ts");
    root
}

/// A check that panics (`GOPORT_TEST_CHECK_PANIC`): the early emit cancels
/// and drains its emit and writes nothing, as the barrier and Go write
/// nothing before the check ends. stdout, stderr and the exit code are the
/// barrier's.
#[test]
fn plain_early_emit_writes_nothing_after_a_check_panic() {
    let root = panic_project();
    let args = ["--declaration", "--sourceMap", "--checkers", "4"];
    let hook = [("GOPORT_TEST_CHECK_PANIC", "slow.ts")];
    let barrier = tsgo(&root, &args, false, None, &hook);
    let early = tsgo(&root, &args, true, None, &hook);
    assert!(
        barrier.stderr.contains("GOPORT_TEST_CHECK_PANIC slow.ts"),
        "the hook panics: {}",
        barrier.stderr
    );
    assert_ne!(barrier.status, Some(0), "a panic fails the run");
    assert!(
        barrier.files.is_empty(),
        "the barrier writes nothing: {:?}",
        barrier.files.keys()
    );
    assert!(
        early.files.is_empty(),
        "the early emit writes nothing: {:?}",
        early.files.keys()
    );
    assert!(!root.join("out").exists(), "no out dir is made");
    assert_eq!(early, barrier, "a check panic, early against the barrier");
    // Without the hook the project emits early.
    let report = tsgo_instrumented(&root, &args, true, None);
    assert_eq!(report.start, Start::Early, "the project emits early");
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

/// SIGKILL while one check blocks (`GOPORT_TEST_CHECK_STALL`) and the
/// other checkers have checked and emitted: no output file exists. The
/// test waits for the hook's line on stderr, then a moment for the other
/// checkers' emits, then kills the process.
#[cfg(unix)]
#[test]
fn plain_early_emit_writes_nothing_before_the_check_ends() {
    let root = panic_project();
    for early in [true, false] {
        let out = reset_out(&root, None);
        let mut child = tsgo_command(
            &root,
            &["--declaration", "--sourceMap", "--checkers", "4"],
            early,
            &[
                ("GOPORT_TEST_CHECK_STALL", "slow.ts"),
                ("GOPORT_LAUNCH", "0"),
            ],
        )
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("run tsgo");
        let stderr = child.stderr.take().expect("stderr");
        let (sender, receiver) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                if line.starts_with("GOPORT_TEST_CHECK_STALL") {
                    let _ = sender.send(());
                }
            }
        });
        let stalled = receiver.recv_timeout(Duration::from_secs(60)).is_ok();
        if stalled {
            // The other checkers' checks and emits are short; give them time.
            let until = Instant::now() + Duration::from_millis(1500);
            while Instant::now() < until {
                std::thread::sleep(Duration::from_millis(50));
            }
        }
        child.kill().expect("SIGKILL tsgo");
        let _ = child.wait();
        assert!(stalled, "early={early}: the check reaches the stall hook");
        let mut files = BTreeMap::new();
        if out.is_dir() {
            read_files(&out, &out, &mut files);
        }
        assert!(
            files.is_empty() && !out.exists(),
            "early={early}: no output before the check ends: {:?}",
            files.keys()
        );
    }
    fs::remove_dir_all(&root).expect("remove the scratch dir");
}

fn copy_dir(from: &Path, to: &Path) {
    fs::create_dir_all(to).expect("create dir");
    for entry in fs::read_dir(from).expect("read fixture dir") {
        let path = entry.expect("fixture entry").path();
        let dest = to.join(path.file_name().expect("file name"));
        if path.is_dir() {
            copy_dir(&path, &dest);
        } else {
            fs::copy(&path, &dest).expect("copy fixture file");
        }
    }
}

/// Reads every file under `dir` into `files`, by path relative to `root`.
fn read_files(root: &Path, dir: &Path, files: &mut BTreeMap<String, Vec<u8>>) {
    for entry in fs::read_dir(dir).expect("read out dir") {
        let path = entry.expect("out dir entry").path();
        let name = path
            .strip_prefix(root)
            .expect("a path under the out dir")
            .components()
            .map(|c| c.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/");
        if path.is_dir() {
            files.insert(format!("{name}/"), Vec::new());
            read_files(root, &path, files);
        } else {
            files.insert(name, fs::read(&path).expect("read output"));
        }
    }
}

/// A new directory under the system temp dir, by its real path.
fn scratch_dir() -> PathBuf {
    static MADE: AtomicUsize = AtomicUsize::new(0);
    let count = MADE.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock after 1970")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "goport-plain-early-emit-{}-{nanos}-{count}",
        std::process::id()
    ));
    fs::create_dir(&dir).unwrap_or_else(|error| panic!("create {}: {error}", dir.display()));
    fs::canonicalize(&dir).expect("canonical scratch dir")
}
