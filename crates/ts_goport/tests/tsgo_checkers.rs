//! The checker split from the command line: where Go's output depends on
//! which files share a checker, tsgo gives Go's output for each
//! `--checkers N`, and the same output on every run. Each test runs `tsgo`
//! in a new scratch dir. A passing test deletes its dir.

use std::fmt::Write as _;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

/// Writes `files` to a new scratch dir and returns it.
fn scratch(name: &str, files: &[(String, String)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("tsgo_checkers_{}_{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    for (file, text) in files {
        fs::write(dir.join(file), text).unwrap();
    }
    dir
}

/// `Rec<300>` instantiates too deep. A checker reports TS5114 at the first
/// reference to it that it checks and keeps the result, so a later file on
/// the same checker gets no error. Which of `f1.ts` to `f6.ts` report it
/// depends on which files share a checker.
const REC_TS: &str = r#"export type Rec<N extends number, A extends unknown[] = []> = A["length"] extends N ? A : [0, ...Rec<N, [...A, 0]>];"#;

/// The files that report TS5114 in Go's output for the
/// `same_output_on_every_run` project with `--checkers N`, for N from 1 to
/// 7 (tsgo-oracle-fed0bf24149f; exit code 2 at each count). No `--checkers`
/// is 4. Go's output is different at each count.
const SPLIT_GO: [&[u8]; 7] = [
    &[1],
    &[1, 5],
    &[1, 4],
    &[1, 2, 3],
    &[1, 2, 3, 4],
    &[1, 2, 3, 4, 5],
    &[1, 2, 3, 4, 5, 6],
];

/// Go's output for that project with `--checkers checkers`.
fn split_go(checkers: usize) -> String {
    let mut out = String::new();
    for i in SPLIT_GO[checkers - 1] {
        writeln!(
            out,
            "f{i}.ts(2,18): error TS5114: Instantiations of type 'Rec' appear infinitely circular."
        )
        .unwrap();
    }
    out
}

/// On a project whose output depends on which files share a checker, the
/// default (Go's 4 checkers) gives Go's output in each of 5 runs, and
/// `--checkers N` gives Go's output for N in each of 3 runs, for each other
/// N from 1 to 7. `skipLibCheck` keeps the lib files out of the check work.
#[test]
fn same_output_on_every_run() {
    let mut files = vec![("deep.ts".to_string(), format!("{REC_TS}\n"))];
    files.extend((1..=6).map(|i| {
        (
            format!("f{i}.ts"),
            format!("import type {{ Rec }} from \"./deep\";\nexport type T{i} = Rec<300>;\n"),
        )
    }));
    files.push((
        "tsconfig.json".into(),
        r#"{ "compilerOptions": { "noEmit": true, "strict": true, "skipLibCheck": true, "types": [], "lib": ["es2015"] }, "files": ["deep.ts", "f1.ts", "f2.ts", "f3.ts", "f4.ts", "f5.ts", "f6.ts"] }"#.into(),
    ));
    let dir = scratch("same", &files);
    let check = |args: &[&str], checkers: usize, label: &str| {
        let output = Command::new(env!("CARGO_BIN_EXE_tsgo"))
            .args(args)
            .current_dir(&dir)
            .output()
            .unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n");
        assert_eq!(stdout, split_go(checkers), "{label}");
        assert_eq!(output.status.code(), Some(2), "{label}");
    };
    for run in 0..5 {
        check(&[], 4, &format!("default, run {run}"));
    }
    for checkers in [1, 2, 3, 5, 6, 7] {
        let count = checkers.to_string();
        for run in 0..3 {
            let label = format!("--checkers {checkers}, run {run}");
            check(&["--checkers", &count], checkers, &label);
        }
    }
    fs::remove_dir_all(&dir).unwrap();
}
