//! Outputs that share one file (`emitter::output_aliases`). Go writes each
//! file's outputs inside a parallel work group, so with output paths that
//! are symlinks or hard links to one inode its result changes from run to
//! run. tsc-rs makes those writes one at a time in program file order: the
//! shared file ends with the output of the last such file in program
//! order, on every run, on the early-emit path (hard links: every output
//! path is a regular file) and on the barrier path (symlinks, or a
//! directory symlink, keep it). With `--singleThreaded` the emit runs in
//! Go's single-threaded order (last queued first), so the shared file ends
//! with the output of the first file, as Go's does.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// Modules in the project; enough that the emit runs on every checker and
/// the emit pool.
const MODULES: usize = 160;
const RUNS: usize = 8;

fn scratch_dir() -> PathBuf {
    static MADE: AtomicUsize = AtomicUsize::new(0);
    let count = MADE.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock after 1970")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "goport-aliased-outputs-{}-{nanos}-{count}",
        std::process::id()
    ));
    fs::create_dir(&dir).unwrap_or_else(|error| panic!("create {}: {error}", dir.display()));
    fs::canonicalize(&dir).expect("canonical scratch dir")
}

/// A project of `MODULES` modules `src/f<i>.ts`, each with a long string
/// so a write takes a while.
fn project() -> PathBuf {
    let root = scratch_dir();
    fs::create_dir(root.join("src")).expect("src");
    fs::write(
        root.join("tsconfig.json"),
        r#"{"compilerOptions": {"outDir": "out", "target": "es2020", "module": "commonjs", "rootDir": "src"}, "include": ["src"]}"#,
    )
    .expect("tsconfig");
    for index in 0..MODULES {
        let digit = char::from(b'0' + u8::try_from(index % 10).expect("a digit"));
        let text: String = std::iter::repeat_n(digit, 4000).collect();
        fs::write(
            root.join(format!("src/f{index}.ts")),
            format!("export const f{index} = \"{text}\";\n"),
        )
        .expect("module");
    }
    root
}

fn tsgo(root: &Path, args: &[&str]) -> std::process::Output {
    let output = Command::new(env!("CARGO_BIN_EXE_tsgo"))
        .current_dir(root)
        .args(["-p", "tsconfig.json", "--pretty", "false"])
        .args(args)
        .env_remove("GOPORT_EMIT_THREADS")
        .output()
        .expect("run tsgo");
    assert_eq!(
        output.status.code(),
        Some(0),
        "tsgo {args:?}: {}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    output
}

/// The module names in program order (`--listFilesOnly`).
fn program_order(root: &Path) -> Vec<String> {
    let output = tsgo(root, &["--listFilesOnly"]);
    String::from_utf8(output.stdout)
        .expect("utf-8 list")
        .lines()
        .filter_map(|line| {
            let name = Path::new(line).file_name()?.to_str()?;
            let stem = name.strip_suffix(".ts")?;
            (line.contains("/src/") && !name.ends_with(".d.ts")).then(|| stem.to_owned())
        })
        .collect()
}

/// The JS output of each module, emitted into a plain out dir.
fn plain_outputs(root: &Path) -> std::collections::BTreeMap<String, Vec<u8>> {
    let _ = fs::remove_dir_all(root.join("out"));
    tsgo(root, &[]);
    (0..MODULES)
        .map(|index| {
            let name = format!("f{index}");
            let text = fs::read(root.join(format!("out/{name}.js"))).expect("plain output");
            (name, text)
        })
        .collect()
}

/// How the out dir aliases every `f<i>.js`.
#[derive(Clone, Copy, Debug)]
enum Alias {
    /// `out/f<i>.js` symlinks to `out/shared`, which exists.
    Symlink,
    /// `out/f<i>.js` symlinks to `out/shared`, which does not exist yet.
    Dangling,
    /// `out/f<i>.js` is a hard link of `out/shared`: every output path is
    /// a regular file, so the early emit runs.
    HardLink,
}

fn prepare(root: &Path, alias: Alias) -> PathBuf {
    let out = root.join("out");
    let _ = fs::remove_dir_all(&out);
    fs::create_dir(&out).expect("out");
    let shared = out.join("shared");
    if !matches!(alias, Alias::Dangling) {
        fs::write(&shared, "").expect("shared");
    }
    for index in 0..MODULES {
        let link = out.join(format!("f{index}.js"));
        match alias {
            Alias::Symlink | Alias::Dangling => {
                std::os::unix::fs::symlink("shared", &link).expect("symlink");
            }
            Alias::HardLink => fs::hard_link(&shared, &link).expect("hard link"),
        }
    }
    shared
}

fn check(alias: Alias, args: &[&str], expected_module: impl Fn(&[String]) -> String) {
    let root = project();
    let order = program_order(&root);
    assert_eq!(order.len(), MODULES, "{order:?}");
    let outputs = plain_outputs(&root);
    let expected = &outputs[&expected_module(&order)];
    for run in 0..RUNS {
        let shared = prepare(&root, alias);
        tsgo(&root, args);
        let text = fs::read(&shared).expect("shared output");
        assert!(
            text == *expected,
            "{alias:?} {args:?} run {run}: the shared file is not the output of {} \
             ({} bytes, expected {})",
            expected_module(&order),
            text.len(),
            expected.len()
        );
    }
    fs::remove_dir_all(root).expect("remove scratch");
}

fn last(order: &[String]) -> String {
    order.last().expect("a module").clone()
}

fn first(order: &[String]) -> String {
    order.first().expect("a module").clone()
}

#[test]
fn symlinked_outputs_end_with_the_last_file_in_program_order() {
    check(Alias::Symlink, &[], last);
}

#[test]
fn dangling_symlinked_outputs_end_with_the_last_file_in_program_order() {
    check(Alias::Dangling, &[], last);
}

#[test]
fn hard_linked_outputs_end_with_the_last_file_in_program_order() {
    check(Alias::HardLink, &[], last);
}

#[test]
fn aliased_outputs_of_an_incremental_emit_end_with_the_last_file() {
    // The incremental program emits through `emit_batch`. Its build info
    // goes into the out dir, which each run makes again, so every run is
    // a full build.
    check(
        Alias::HardLink,
        &[
            "--incremental",
            "--tsBuildInfoFile",
            "out/build.tsbuildinfo",
        ],
        last,
    );
}

#[test]
fn single_threaded_aliased_outputs_end_with_the_first_file() {
    check(Alias::Symlink, &["--singleThreaded"], first);
}

#[test]
fn outputs_through_a_symlinked_directory_alias() {
    // `src/f<i>.ts` emits `out/src/f<i>.js` and `src2/f<i>.ts` (other
    // text) emits `out/src2/f<i>.js`. With `out/src2` a symlink to
    // `out/src`, each pair writes one file, and `src2`'s module comes
    // later in program order.
    let root = project();
    fs::create_dir(root.join("src2")).expect("src2");
    for index in 0..MODULES {
        let text: String = std::iter::repeat_n('x', 4000).collect();
        fs::write(
            root.join(format!("src2/f{index}.ts")),
            format!("export const g{index} = \"{text}\";\n"),
        )
        .expect("module");
    }
    fs::write(
        root.join("tsconfig.json"),
        r#"{"compilerOptions": {"outDir": "out", "target": "es2020", "module": "commonjs", "rootDir": "."}, "include": ["src", "src2"]}"#,
    )
    .expect("tsconfig");
    let order = String::from_utf8(tsgo(&root, &["--listFilesOnly"]).stdout).expect("utf-8");
    let first_src2 = order.find("/src2/").expect("a src2 module");
    assert!(
        order
            .rfind("/src/")
            .is_some_and(|last_src| last_src < first_src2),
        "src comes before src2 in program order"
    );
    let _ = fs::remove_dir_all(root.join("out"));
    tsgo(&root, &[]);
    let expected: Vec<Vec<u8>> = (0..MODULES)
        .map(|index| fs::read(root.join(format!("out/src2/f{index}.js"))).expect("output"))
        .collect();
    for run in 0..RUNS {
        let _ = fs::remove_dir_all(root.join("out"));
        fs::create_dir_all(root.join("out/src")).expect("out/src");
        std::os::unix::fs::symlink("src", root.join("out/src2")).expect("dir symlink");
        tsgo(&root, &[]);
        for (index, expected) in expected.iter().enumerate() {
            let text = fs::read(root.join(format!("out/src/f{index}.js"))).expect("output");
            assert!(
                text == *expected,
                "run {run}: out/src/f{index}.js is not src2's"
            );
        }
    }
    fs::remove_dir_all(root).expect("remove scratch");
}

/// The project of the dangling directory link (Astra D1-I3 finding 1):
/// `src/left/f<i>.ts` and `src/right/f<i>.ts` with other text, emitted to
/// `out/left` and `out/right`. Each run starts from an empty `out` with
/// the dangling link `out/left -> right`, so the two outputs of each name
/// are one file once the emit makes `out/right`.
fn dirlink_project() -> PathBuf {
    let root = scratch_dir();
    for side in ["left", "right"] {
        fs::create_dir_all(root.join("src").join(side)).expect("src dir");
        for index in 0..40 {
            let text: String = std::iter::repeat_n(side, 200).collect();
            fs::write(
                root.join(format!("src/{side}/f{index}.ts")),
                format!("export const f{index} = \"{text}\";\n"),
            )
            .expect("module");
        }
    }
    fs::write(
        root.join("tsconfig.json"),
        r#"{"compilerOptions": {"outDir": "out", "rootDir": "src", "target": "es2020", "module": "commonjs"}, "include": ["src"]}"#,
    )
    .expect("tsconfig");
    root
}

/// One run of `args` on a fresh `out` with `out/left -> right`: the out
/// tree (path, kind, link target, bytes), stdout and the exit code.
fn dirlink_run(root: &Path, args: &[&str]) -> (Vec<(String, Vec<u8>)>, Vec<u8>, Option<i32>) {
    let out = root.join("out");
    let _ = fs::remove_dir_all(&out);
    let _ = fs::remove_file(root.join("x.tsbuildinfo"));
    fs::create_dir(&out).expect("out");
    std::os::unix::fs::symlink("right", out.join("left")).expect("dir link");
    let output = Command::new(env!("CARGO_BIN_EXE_tsgo"))
        .current_dir(root)
        .args(args)
        .env_remove("GOPORT_EMIT_THREADS")
        .output()
        .expect("run tsgo");
    let mut tree = Vec::new();
    let mut stack = vec![out.clone()];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir).expect("read out") {
            let path = entry.expect("entry").path();
            let rel = path
                .strip_prefix(&out)
                .unwrap()
                .to_string_lossy()
                .into_owned();
            let meta = fs::symlink_metadata(&path).expect("stat");
            if meta.file_type().is_symlink() {
                let target = fs::read_link(&path).expect("link");
                tree.push((
                    format!("l {rel}"),
                    target.to_string_lossy().into_owned().into_bytes(),
                ));
            } else if meta.is_dir() {
                tree.push((format!("d {rel}"), Vec::new()));
                stack.push(path);
            } else {
                tree.push((format!("f {rel}"), fs::read(&path).expect("read")));
            }
        }
    }
    tree.sort();
    (tree, output.stdout, output.status.code())
}

/// Astra D1-I3 finding 1: with the dangling directory link, every emit
/// mode gives one result on every run, and the modes that write in
/// program order (the default route, Go's split, incremental, `-b`) give
/// the same one. Before the fix the runs differed (5 results in 10 plain
/// runs, 6 in 6 incremental and `-b --force` runs). `--singleThreaded`
/// writes in Go's single-threaded order, also every run the same.
#[test]
fn a_dangling_directory_link_gives_one_result_in_every_emit_mode() {
    let root = dirlink_project();
    let modes: [&[&str]; 4] = [
        &["-p", "tsconfig.json"],
        &["-p", "tsconfig.json", "--checkers", "4"],
        &[
            "-p",
            "tsconfig.json",
            "--incremental",
            "--tsBuildInfoFile",
            "x.tsbuildinfo",
        ],
        &["-b", "tsconfig.json", "--force"],
    ];
    let mut results = Vec::new();
    for args in modes {
        let first = dirlink_run(&root, args);
        for run in 1..RUNS {
            assert!(
                dirlink_run(&root, args) == first,
                "{args:?} run {run} differs"
            );
        }
        results.push((args, first));
    }
    for (args, result) in &results[1..] {
        assert!(
            *result == results[0].1,
            "{args:?} differs from {:?}",
            results[0].0
        );
    }
    let single = ["-p", "tsconfig.json", "--singleThreaded"];
    let first = dirlink_run(&root, &single);
    for run in 1..RUNS {
        assert!(
            dirlink_run(&root, &single) == first,
            "--singleThreaded run {run} differs"
        );
    }
    fs::remove_dir_all(root).expect("remove scratch");
}
