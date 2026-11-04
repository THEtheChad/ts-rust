//! Verify mode of the language server's program reuse beyond Go
//! (perf/first-principles A1, `NewProgram::update_program_reusing_synthetic`).
//!
//! Not in Go. Go builds a new program when the changed file has a synthetic
//! import (JSX runtime or `importHelpers`); the port reuses the old program
//! when the synthetic imports stay the same. With `GOPORT_VERIFY_REUSE=1`
//! each such reuse is checked against a full build of the same inputs.
//! This module has the mode, the report and the canonical dump; the
//! project system makes the request and runs the child
//! (`project::reuse_verify`):
//!
//! - the language server dumps the reused program (`dump`). The dump reads
//!   a shadow of the program (`NewProgram::shadow`) whose caches start
//!   empty, so it changes nothing that the session reads later.
//! - a child process (this binary with `GOPORT_VERIFY_REUSE_CHILD=1`) gets
//!   the session's inputs: its options and open files, and its snapshot
//!   where the disk has changed since (see `project::reuse_verify`). It
//!   starts a new language server session on them, opens the files, and
//!   dumps the program of the same configured or inferred project, content
//!   mappers included: a full build, as Go's rebuild. A separate process
//!   cannot change the global ids or the session of the server.
//! - the two dumps must be equal: OK, else MISMATCH. When the child cannot
//!   rebuild the server's inputs the result is ERROR (not checked), never
//!   MISMATCH (see `project::reuse_verify`). Each result goes to stderr and
//!   `GOPORT_VERIFY_REUSE_LOG`, and both dumps of a MISMATCH are kept in
//!   `GOPORT_VERIFY_REUSE_DIR`. `GOPORT_VERIFY_REUSE=panic` panics on a
//!   MISMATCH or an ERROR. `GOPORT_VERIFY_REUSE=all` checks the reuses that
//!   Go makes too.
//!
//! The dump has the options and root files, the file order and keys, the
//! file metadata, the module and type reference resolutions, both
//! synthetic specifier maps (text, parents and resolution mode), the
//! synthetic specifiers that the include reasons keep, `--explainFiles`,
//! the program, config, content mapper and include processor diagnostics,
//! the files found by searching node_modules, the missing files, the
//! redirects and the emit blocking files.
//!
//! **What an OK covers: the program structure only** (the items above).
//! It does not compare
//! checker output (semantic, suggestion or global diagnostics) or LSP
//! answers and notifications: computing them in the server would create
//! checker state (type and symbol ids) that changes later answers. Checker
//! output of reused programs is compared with full builds in the unit
//! tests below (in-process), and LSP answers and publishDiagnostics with Go
//! by `scripts/lspdiff` and `scripts/perf-rearch/a1/pubdiag-stream.mjs`.

use crate::frontend::bundled;
use crate::frontend::compiler::{NewProgram, ProgramOptions};
use crate::frontend::prelude::*;
use std::fmt::Write as _;
use std::io::Write;

/// Which reuses the verify mode checks (`GOPORT_VERIFY_REUSE`).
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Off,
    /// The reuses beyond Go (`ReuseOutcome::ReusedSynthetic`).
    Synthetic,
    /// Every reuse.
    All,
}

static MODE: std::sync::OnceLock<(Mode, bool)> = std::sync::OnceLock::new();

/// The verify mode, read once.
pub fn mode() -> Mode {
    MODE.get_or_init(read_mode).0
}

/// A difference or an error panics (`GOPORT_VERIFY_REUSE=panic|all-panic`).
pub fn panics() -> bool {
    MODE.get_or_init(read_mode).1
}

fn read_mode() -> (Mode, bool) {
    match std::env::var("GOPORT_VERIFY_REUSE").as_deref() {
        Ok("1") => (Mode::Synthetic, false),
        Ok("panic") => (Mode::Synthetic, true),
        Ok("all") => (Mode::All, false),
        Ok("all-panic") => (Mode::All, true),
        _ => (Mode::Off, false),
    }
}

/// Writes `line` to stderr and appends it to `GOPORT_VERIFY_REUSE_LOG`.
pub fn report(line: &str) {
    let _ = writeln!(std::io::stderr(), "{line}");
    if let Ok(log) = std::env::var("GOPORT_VERIFY_REUSE_LOG")
        && let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(log)
    {
        let _ = writeln!(file, "{line}");
    }
}

/// `None` when `reused` and `full` are equal, else where they first differ
/// and the files that keep both (`GOPORT_VERIFY_REUSE_DIR`).
pub fn compare(reused: &str, full: &str) -> Option<String> {
    if reused == full {
        return None;
    }
    let dir = std::env::var("GOPORT_VERIFY_REUSE_DIR").map_or_else(
        |_| std::env::temp_dir().join("goport-verify-reuse"),
        std::path::PathBuf::from,
    );
    let _ = std::fs::create_dir_all(&dir);
    static COUNT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
    let n = COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let base = dir.join(format!("{}-{n}", std::process::id()));
    let reused_path = base.with_extension("reused.txt");
    let full_path = base.with_extension("full.txt");
    let _ = std::fs::write(&reused_path, reused);
    let _ = std::fs::write(&full_path, full);
    let first = reused
        .lines()
        .zip(full.lines())
        .position(|(a, b)| a != b)
        .unwrap_or_else(|| reused.lines().count().min(full.lines().count()));
    Some(format!(
        "first difference at line {}: reused {:?}, full {:?}; dumps {} and {}",
        first + 1,
        reused.lines().nth(first).unwrap_or("<end>"),
        full.lines().nth(first).unwrap_or("<end>"),
        reused_path.display(),
        full_path.display()
    ))
}

/// The canonical dump of `program` (see the module comment). It reads a
/// shadow of the program, so the program's own caches stay as they are.
pub fn dump(program: &NewProgram) -> String {
    let p = program.shadow();
    let locale = &crate::locale::DEFAULT;
    let mut out = String::new();
    let names: FxHashMap<Node, String> = p
        .source_files()
        .iter()
        .map(|file| (file.root, file.file_name().to_string()))
        .collect();

    let options = p.options();
    let _ = writeln!(out, "== options\n{options:?}");
    let _ = writeln!(out, "== roots");
    for name in p.command_line().file_names() {
        let _ = writeln!(out, "{name}");
    }

    let _ = writeln!(out, "== files");
    for (index, file) in p.source_files().iter().enumerate() {
        let path = file.path();
        let _ = writeln!(
            out,
            "{index} {} path={} kind={:?} hash={:032x} meta={:?} default_lib={} node_modules={}",
            file.file_name(),
            path.0,
            file.script_kind,
            xxhash_rust::xxh3::xxh3_128(file.text().as_bytes()),
            p.get_source_file_meta_data(path),
            p.is_source_file_default_library(path),
            p.source_files_found_searching_node_modules.contains(path),
        );
    }
    let _ = writeln!(out, "== files by path");
    let mut by_path: Vec<String> = p
        .files_by_path()
        .iter()
        .map(|(path, file)| format!("{} -> {}", path.0, file.file_name()))
        .collect();
    by_path.sort();
    for line in by_path {
        let _ = writeln!(out, "{line}");
    }
    let _ = writeln!(out, "== missing\n{:?}", p.missing_files);
    let _ = writeln!(out, "== node_modules");
    let mut found: Vec<&str> = p
        .source_files_found_searching_node_modules
        .iter()
        .map(|path| path.0.as_str())
        .collect();
    found.sort_unstable();
    for path in found {
        let _ = writeln!(out, "{path}");
    }
    let _ = writeln!(out, "== redirects");
    let mut redirects: Vec<String> = p
        .redirect_files_by_path
        .iter()
        .flat_map(|map| map.iter())
        .map(|(path, file)| format!("{} {} {}", path.0, file.file_name, file.index))
        .chain(
            p.redirect_targets_map
                .iter()
                .flat_map(|map| map.iter())
                .map(|(path, targets)| format!("target {} {targets:?}", path.0)),
        )
        .collect();
    redirects.sort();
    for line in redirects {
        let _ = writeln!(out, "{line}");
    }

    let _ = writeln!(out, "== resolutions");
    for file in p.source_files() {
        let path = file.path();
        let mut lines: Vec<String> = p
            .resolved_modules
            .get(path)
            .into_iter()
            .flat_map(|cache| cache.iter())
            .map(|(key, resolved)| {
                format!(
                    "  module {:?} {:?} -> {:?} {:?} ext={:?} lib={} pkg={:?} alt={:?} diags={}",
                    key.name,
                    key.mode,
                    resolved.resolved_file_name,
                    resolved.resolved_path.0,
                    resolved.extension,
                    resolved.is_external_library_import,
                    resolved.package_id,
                    resolved.alternate_result,
                    render_diagnostics(&resolved.resolution_diagnostics, &names, locale),
                )
            })
            .chain(
                p.type_resolutions_in_file
                    .get(path)
                    .into_iter()
                    .flat_map(|cache| cache.iter())
                    .map(|(key, resolved)| {
                        format!(
                            "  type {:?} {:?} -> {:?} primary={} lib={} pkg={:?} diags={}",
                            key.name,
                            key.mode,
                            resolved.resolved_file_name,
                            resolved.primary,
                            resolved.is_external_library_import,
                            resolved.package_id,
                            render_diagnostics(&resolved.resolution_diagnostics, &names, locale),
                        )
                    }),
            )
            .collect();
        if lines.is_empty() {
            continue;
        }
        lines.sort();
        let _ = writeln!(out, "{}", path.0);
        for line in lines {
            let _ = writeln!(out, "{line}");
        }
    }

    // Both specifier maps: the text and the parents of each specifier,
    // which must be an import declaration in the file's own root.
    let specifier = |path: &Path, node: Node| -> String {
        let file = p.get_source_file_by_path(path);
        let in_root = file
            .as_ref()
            .is_some_and(|file| node.parent().is_some() && node.parent().parent() == file.root);
        let mode = file
            .as_ref()
            .map(|file| format!("{:?}", p.get_mode_for_usage_location(&**file, node)));
        format!(
            "text={:?} parent={:?} in_root={in_root} mode={}",
            node.text(),
            node.parent().kind(),
            mode.unwrap_or_default()
        )
    };
    let _ = writeln!(out, "== jsx runtime specifiers");
    let mut jsx: Vec<String> = p
        .jsx_runtime_import_specifiers
        .iter()
        .flat_map(|map| map.iter())
        .map(|(path, s)| {
            format!(
                "{} ref={:?} {}",
                path.0,
                s.module_reference,
                specifier(path, s.specifier)
            )
        })
        .collect();
    jsx.sort();
    for line in jsx {
        let _ = writeln!(out, "{line}");
    }
    let _ = writeln!(out, "== import helpers specifiers");
    let mut helpers: Vec<String> = p
        .import_helpers_import_specifiers
        .iter()
        .flat_map(|map| map.iter())
        .map(|(path, &node)| format!("{} {}", path.0, specifier(path, node)))
        .collect();
    helpers.sort();
    for line in helpers {
        let _ = writeln!(out, "{line}");
    }
    // The synthetic specifier of each reason must be the one in the map of
    // its file (Go keeps one node, and the explanation compares them).
    let _ = writeln!(out, "== reason specifiers");
    let mut reason_specifiers: Vec<String> = Vec::new();
    p.include_processor
        .file_include_reasons
        .for_each_synthetic(&mut |path, node| {
            let in_jsx = p
                .jsx_runtime_import_specifiers
                .as_ref()
                .and_then(|map| map.get(path))
                .is_some_and(|s| s.specifier == node);
            let in_helpers = p
                .import_helpers_import_specifiers
                .as_ref()
                .and_then(|map| map.get(path))
                .is_some_and(|&s| s == node);
            reason_specifiers.push(format!(
                "{} {} jsx_map={in_jsx} helpers_map={in_helpers}",
                path.0,
                specifier(path, node)
            ));
        });
    reason_specifiers.sort();
    for line in reason_specifiers {
        let _ = writeln!(out, "{line}");
    }

    let _ = writeln!(out, "== explainFiles");
    let mut explained = Vec::new();
    p.explain_files(&mut explained, locale, &p.host().get_current_directory());
    out.push_str(&String::from_utf8_lossy(&explained));

    let _ = writeln!(out, "== program diagnostics");
    out.push_str(&render_diagnostics(&p.program_diagnostics, &names, locale));
    let _ = writeln!(out, "\n== config diagnostics");
    out.push_str(&render_diagnostics(
        &p.get_config_file_parsing_diagnostics(),
        &names,
        locale,
    ));
    let _ = writeln!(out, "\n== content mapper diagnostics");
    out.push_str(&render_diagnostics(
        &p.content_mapper_diagnostics,
        &names,
        locale,
    ));
    let _ = writeln!(out, "\n== include processor diagnostics");
    let include = p
        .include_processor
        .get_diagnostics(&p)
        .borrow()
        .get_diagnostics();
    let mut include: Vec<String> = include
        .iter()
        .map(|d| render_diagnostics(std::slice::from_ref(d), &names, locale))
        .collect();
    include.sort();
    for line in include {
        let _ = writeln!(out, "{line}");
    }
    let _ = writeln!(out, "== emit blocking");
    let mut blocking: Vec<&str> = p
        .has_emit_blocking_diagnostics
        .iter()
        .map(|path| path.0.as_str())
        .collect();
    blocking.sort_unstable();
    let _ = writeln!(out, "{blocking:?}");
    out
}

/// The diagnostics as text: file, span, category, code and message, with
/// their chains and related information.
fn render_diagnostics(
    diagnostics: &[Diagnostic],
    names: &FxHashMap<Node, String>,
    locale: &crate::locale::Locale,
) -> String {
    fn one(
        d: &Diagnostic,
        names: &FxHashMap<Node, String>,
        locale: &crate::locale::Locale,
        depth: usize,
        out: &mut String,
    ) {
        let file = if d.file.is_nil() {
            String::new()
        } else {
            names
                .get(&d.file)
                .cloned()
                .unwrap_or_else(|| "<not a program file>".to_string())
        };
        let _ = write!(
            out,
            "[{depth} {file}:{}-{} {:?} {}{} {:?}]",
            d.pos,
            d.end,
            d.category,
            d.source,
            d.code,
            d.localize(locale)
        );
        for chained in &d.message_chain {
            one(chained, names, locale, depth + 1, out);
        }
        for related in &d.related_information {
            one(related, names, locale, depth + 1, out);
        }
    }
    let mut out = String::new();
    for d in diagnostics {
        one(d, names, locale, 0, &mut out);
    }
    out
}

#[cfg(test)]
mod tests {
    //! Reuses beyond Go (`ReuseOutcome::ReusedSynthetic`) against a full
    //! build of the same inputs: the dump and the checker's diagnostics
    //! must be equal. The full build runs in this process (a test may move
    //! the global ids), as `ls_program` builds a new language server
    //! program.
    use super::*;
    use crate::frontend::compiler::ReuseOutcome;
    use crate::program::ls_program;

    /// `ParseConfigHost` of the test projects.
    struct ConfigHost {
        fs: Rc<dyn Fs>,
        current_directory: String,
    }

    impl ParseConfigHost for ConfigHost {
        fn fs(&self) -> Rc<dyn Fs> {
            self.fs.clone()
        }
        fn get_current_directory(&self) -> String {
            self.current_directory.clone()
        }
    }

    struct Project {
        dir: std::path::PathBuf,
        cwd: String,
        config: Rc<ParsedCommandLine>,
    }

    impl Drop for Project {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    fn project(label: &str, tsconfig: &str, files: &[(&str, &str)]) -> Project {
        let dir = std::env::temp_dir().join(format!(
            "ts_goport_reuse_verify_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        for (path, text) in files {
            let path = dir.join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        std::fs::write(dir.join("tsconfig.json"), tsconfig).unwrap();
        let cwd = dir.to_string_lossy().replace('\\', "/");
        let sys = ConfigHost {
            fs: bundled::wrap_fs(osvfs_fs()),
            current_directory: cwd.clone(),
        };
        let (config, errors) = get_parsed_command_line_of_config_file(
            &format!("{cwd}/tsconfig.json"),
            None,
            None,
            &sys,
            None,
        );
        assert!(errors.is_empty(), "{label}: config errors");
        Project {
            dir,
            cwd,
            config: Rc::new(config.unwrap()),
        }
    }

    impl Project {
        fn host(&self) -> Rc<dyn CompilerHost> {
            new_cached_fs_compiler_host(
                &self.cwd,
                bundled::wrap_fs(osvfs_fs()),
                &bundled::lib_path(),
                None,
                None,
                None,
            )
        }

        /// A new language server program of the files on disk, bound.
        fn load(&self) -> Rc<NewProgram> {
            let p = ls_program::new_program(
                ProgramOptions {
                    host: self.host(),
                    config: self.config.clone(),
                    use_source_of_project_reference: true,
                    single_threaded: Tristate::True,
                    typings_location: String::new(),
                    project_name: String::new(),
                    create_module_resolver: None,
                    skip_module_resolution: false,
                },
                None,
            );
            ls_program::bind_source_files(&p);
            p
        }

        /// Writes `text` to `file`, updates `old` for it and binds the new
        /// program. A reused program must equal a full build: its dump, and
        /// the semantic and global diagnostics of its checkers.
        fn edit(&self, old: &NewProgram, file: &str, text: &str) -> (Rc<NewProgram>, ReuseOutcome) {
            std::fs::write(self.dir.join(file), text).unwrap();
            let path = old
                .get_source_file(&format!("{}/{file}", self.cwd))
                .unwrap_or_else(|| panic!("{file} is not in the program"))
                .path()
                .clone();
            let (p, _, outcome) = ls_program::update_program(old, &path, self.host(), None, None);
            ls_program::bind_source_files(&p);
            if outcome.reused_storage() {
                let full = self.load();
                assert_same(&dump(&p), &dump(&full), file);
                assert_eq!(
                    checker_output(&p),
                    checker_output(&full),
                    "{file}: diagnostics"
                );
                ls_program::release_program(&full);
            }
            (p, outcome)
        }
    }

    /// The semantic diagnostics of each program file and the global
    /// diagnostics, as text.
    fn checker_output(p: &NewProgram) -> Vec<String> {
        let ctx = crate::gostd::context::background();
        let names: FxHashMap<Node, String> = p
            .source_files()
            .iter()
            .map(|file| (file.root, file.file_name().to_string()))
            .collect();
        let locale = &crate::locale::DEFAULT;
        let mut out: Vec<String> = p
            .source_files()
            .iter()
            .filter(|file| !file.file_name().starts_with("bundled:"))
            .map(|file| {
                let diagnostics = ls_program::get_semantic_diagnostics(p, &ctx, file.root);
                format!(
                    "{}: {}",
                    file.file_name(),
                    render_diagnostics(&diagnostics, &names, locale)
                )
            })
            .collect();
        out.push(format!(
            "global: {}",
            render_diagnostics(&ls_program::get_global_diagnostics(p, &ctx), &names, locale)
        ));
        out
    }

    fn assert_same(reused: &str, full: &str, what: &str) {
        if reused != full {
            let line = reused
                .lines()
                .zip(full.lines())
                .position(|(a, b)| a != b)
                .unwrap_or_else(|| reused.lines().count().min(full.lines().count()));
            panic!(
                "{what}: the reused program differs from a full build at line {}:\n  reused {:?}\n  full   {:?}",
                line + 1,
                reused.lines().nth(line),
                full.lines().nth(line)
            );
        }
    }

    /// Runs `f` on a thread of its own: each language server program
    /// version lives on the thread that made it.
    fn on_thread(f: impl FnOnce() + Send + 'static) {
        std::thread::Builder::new()
            .stack_size(256 << 20)
            .spawn(f)
            .unwrap()
            .join()
            .unwrap();
    }

    const REACT: &[(&str, &str)] = &[
        (
            "node_modules/react/package.json",
            r#"{"name":"react","version":"19.0.0","types":"index.d.ts"}"#,
        ),
        (
            "node_modules/react/index.d.ts",
            "export type ReactNode = string | number | null;\n",
        ),
        (
            "node_modules/react/jsx-runtime.d.ts",
            "export declare function jsx(type: any, props: any): any;\nexport declare function jsxs(type: any, props: any): any;\nexport declare namespace JSX { interface IntrinsicElements { [name: string]: any } type Element = any; }\n",
        ),
    ];

    fn with(
        base: &[(&'static str, &'static str)],
        more: &[(&'static str, &'static str)],
    ) -> Vec<(&'static str, &'static str)> {
        base.iter().chain(more).copied().collect()
    }

    const JSX_CONFIG: &str = r#"{"compilerOptions":{"jsx":"react-jsx","module":"esnext","moduleResolution":"bundler","strict":true,"types":[]}}"#;

    // A body edit of a `.tsx` file keeps its JSX runtime import: the
    // program is reused, the update is Go's (a new program), and the old
    // program keeps its own specifier.
    #[test]
    fn jsx_body_edits_reuse_and_keep_older_programs() {
        on_thread(|| {
            let p = project(
                "jsx_body",
                JSX_CONFIG,
                &with(
                    REACT,
                    &[
                        ("src/a.tsx", "export const A = () => <div>a</div>;\n"),
                        (
                            "src/b.tsx",
                            "import { A } from \"./a\";\nexport const B = () => <A />;\n",
                        ),
                        ("src/c.ts", "export const c: number = 1;\n"),
                    ],
                ),
            );
            let v0 = p.load();
            let before = dump(&v0);
            let (v1, outcome) = p.edit(
                &v0,
                "src/b.tsx",
                "import { A } from \"./a\";\nexport const B = () => <A />; // 1\n",
            );
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            assert_eq!(dump(&v0), before, "the reuse changed the old program");
            let (v2, outcome) = p.edit(
                &v1,
                "src/b.tsx",
                "import { A } from \"./a\";\nexport const B = () => <A>{1}</A>;\n",
            );
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            // A type error in the body: the checker reads the new version.
            let (v3, outcome) = p.edit(&v2, "src/a.tsx", "export const A = () => <div>{undefined as unknown as symbol}</div>;\nconst n: string = 1;\n");
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            // A `.ts` file has no synthetic import: Go reuses.
            let (v4, outcome) = p.edit(&v3, "src/c.ts", "export const c: number = 2;\n");
            assert_eq!(outcome, ReuseOutcome::Reused);
            // An import change builds again.
            let (v5, outcome) = p.edit(&v4, "src/b.tsx", "export const B = () => <div />;\n");
            assert_eq!(outcome, ReuseOutcome::Rebuilt);
            for v in [v0, v1, v2, v3, v4, v5] {
                ls_program::release_program(&v);
            }
        });
    }

    // `@jsxImportSource`: the same pragma reuses, a new or removed pragma
    // changes the runtime import and builds again.
    #[test]
    fn jsx_import_source_pragma() {
        on_thread(|| {
            let p = project(
                "jsx_pragma",
                JSX_CONFIG,
                &with(
                    REACT,
                    &[
                        (
                            "node_modules/preact/package.json",
                            r#"{"name":"preact","version":"10.0.0","types":"index.d.ts"}"#,
                        ),
                        ("node_modules/preact/index.d.ts", "export {};\n"),
                        (
                            "node_modules/preact/jsx-runtime.d.ts",
                            "export declare function jsx(type: any, props: any): any;\nexport declare namespace JSX { interface IntrinsicElements { [name: string]: any } type Element = any; }\n",
                        ),
                        (
                            "src/a.tsx",
                            "/** @jsxImportSource preact */\nexport const A = () => <div />;\n",
                        ),
                    ],
                ),
            );
            let v0 = p.load();
            let (v1, outcome) = p.edit(
                &v0,
                "src/a.tsx",
                "/** @jsxImportSource preact */\nexport const A = () => <span />;\n",
            );
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            let (v2, outcome) = p.edit(
                &v1,
                "src/a.tsx",
                "/** @jsxImportSource react */\nexport const A = () => <span />;\n",
            );
            assert_eq!(outcome, ReuseOutcome::Rebuilt);
            let (v3, outcome) = p.edit(&v2, "src/a.tsx", "export const A = () => <span />;\n");
            // react from the pragma and react from the options: the same import.
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            let (v4, outcome) = p.edit(
                &v3,
                "src/a.tsx",
                "/** @jsxImportSource preact */\nexport const A = () => <span />;\n",
            );
            assert_eq!(outcome, ReuseOutcome::Rebuilt);
            for v in [v0, v1, v2, v3, v4] {
                ls_program::release_program(&v);
            }
        });
    }

    // `@jsxRuntime classic` drops the runtime import: a classic file reuses
    // as in Go, and a switch between the runtimes builds again.
    #[test]
    fn automatic_and_classic_runtime_switches() {
        on_thread(|| {
            let p = project(
                "jsx_classic",
                JSX_CONFIG,
                &with(
                    REACT,
                    &[(
                        "src/a.tsx",
                        "/** @jsxRuntime classic */\nimport * as React from \"react\";\nexport const A = () => <div />;\n",
                    )],
                ),
            );
            let v0 = p.load();
            let classic = |body: &str| {
                format!("/** @jsxRuntime classic */\nimport * as React from \"react\";\n{body}")
            };
            let automatic = |body: &str| format!("import * as React from \"react\";\n{body}");
            let (v1, outcome) = p.edit(
                &v0,
                "src/a.tsx",
                &classic("export const A = () => <b />;\n"),
            );
            assert_eq!(outcome, ReuseOutcome::Reused);
            let (v2, outcome) = p.edit(
                &v1,
                "src/a.tsx",
                &automatic("export const A = () => <b />;\n"),
            );
            assert_eq!(outcome, ReuseOutcome::Rebuilt);
            let (v3, outcome) = p.edit(
                &v2,
                "src/a.tsx",
                &automatic("export const A = () => <i />;\n"),
            );
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            let (v4, outcome) = p.edit(
                &v3,
                "src/a.tsx",
                &classic("export const A = () => <i />;\n"),
            );
            assert_eq!(outcome, ReuseOutcome::Rebuilt);
            for v in [v0, v1, v2, v3, v4] {
                ls_program::release_program(&v);
            }
        });
    }

    // A runtime module that does not resolve: the import stays unresolved,
    // and the checker reports it at the JSX tag of the new version.
    #[test]
    fn missing_runtime_module() {
        on_thread(|| {
            let p = project(
                "jsx_missing",
                JSX_CONFIG,
                &[("src/a.tsx", "export const A = () => <div />;\n")],
            );
            let v0 = p.load();
            let (v1, outcome) = p.edit(&v0, "src/a.tsx", "\nexport const A = () => <span />;\n");
            assert_eq!(outcome, ReuseOutcome::ReusedSynthetic);
            let output = checker_output(&v1).join("\n");
            assert!(output.contains("2875"), "{output}");
            for v in [v0, v1] {
                ls_program::release_program(&v);
            }
        });
    }

    // `importHelpers`: tslib missing, tslib without the helper, and a
    // complete tslib (whose explanation names `importHelpers`, which the
    // include reason gives only with the map's own specifier).
    #[test]
    fn import_helpers_with_tslib_errors() {
        const CONFIG: &str = r#"{"compilerOptions":{"importHelpers":true,"target":"es2015","module":"commonjs","strict":true,"types":[]}}"#;
        const FILE: &str = "export const f = async () => 1;\n";
        const EDITED: &str = "export const f = async () => 2;\n";
        for (label, tslib, code) in [
            ("helpers_missing", None, Some("2354")),
            (
                "helpers_incomplete",
                Some("export declare function __extends(a: any, b: any): void;\n"),
                Some("2343"),
            ),
            (
                "helpers_complete",
                Some("export declare function __awaiter(a: any, b: any, c: any, d: any): any;\n"),
                None,
            ),
        ] {
            on_thread(move || {
                let mut files: Vec<(&str, &str)> =
                    vec![("src/a.ts", FILE), ("src/b.ts", "export {};\n")];
                if let Some(tslib) = tslib {
                    files.push((
                        "node_modules/tslib/package.json",
                        r#"{"name":"tslib","version":"2.8.0","types":"tslib.d.ts"}"#,
                    ));
                    files.push(("node_modules/tslib/tslib.d.ts", tslib));
                }
                let p = project(label, CONFIG, &files);
                let v0 = p.load();
                let (v1, outcome) = p.edit(&v0, "src/a.ts", EDITED);
                assert_eq!(outcome, ReuseOutcome::ReusedSynthetic, "{label}");
                let output = checker_output(&v1).join("\n");
                match code {
                    Some(code) => assert!(output.contains(code), "{label}: {output}"),
                    None => assert!(!output.contains("[0 "), "{label}: {output}"),
                }
                if tslib.is_some() {
                    let text = dump(&v1);
                    assert!(
                        text.contains("to import 'importHelpers' as specified in compilerOptions"),
                        "{label}: {text}"
                    );
                }
                // A file that stops being a module loses its helpers import
                // only without isolatedModules: here it keeps none and
                // builds again.
                let (v2, outcome) = p.edit(&v1, "src/b.ts", "const b = 1;\n");
                assert_eq!(outcome, ReuseOutcome::Rebuilt, "{label}");
                for v in [v0, v1, v2] {
                    ls_program::release_program(&v);
                }
            });
        }
    }

    // `allowJs` with `jsx`: a `.js` file gets the runtime import (Go
    // `IsSourceFileJS`), so it reuses like a `.tsx` file; `allowJs` alone
    // (no `jsx`) gives none and reuses as in Go.
    #[test]
    fn js_files_with_allow_js_and_jsx() {
        for (label, config, expected) in [
            (
                "js_jsx",
                r#"{"compilerOptions":{"allowJs":true,"checkJs":true,"jsx":"react-jsx","module":"esnext","moduleResolution":"bundler","types":[]}}"#,
                ReuseOutcome::ReusedSynthetic,
            ),
            (
                "js_plain",
                r#"{"compilerOptions":{"allowJs":true,"checkJs":true,"module":"esnext","moduleResolution":"bundler","types":[]}}"#,
                ReuseOutcome::Reused,
            ),
        ] {
            on_thread(move || {
                let p = project(
                    label,
                    config,
                    &with(
                        REACT,
                        &[
                            ("src/a.js", "export const a = 1;\n"),
                            (
                                "src/b.jsx",
                                "import { a } from \"./a.js\";\nexport const B = () => <div>{a}</div>;\n",
                            ),
                        ],
                    ),
                );
                let v0 = p.load();
                let (v1, outcome) = p.edit(&v0, "src/a.js", "export const a = 2;\n");
                assert_eq!(outcome, expected, "{label}");
                let (v2, outcome) = p.edit(
                    &v1,
                    "src/b.jsx",
                    "import { a } from \"./a.js\";\nexport const B = () => <p>{a}</p>;\n",
                );
                assert_eq!(outcome, expected, "{label}");
                for v in [v0, v1, v2] {
                    ls_program::release_program(&v);
                }
            });
        }
    }
}
