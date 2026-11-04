//! The project side of the reuse verify mode (perf/first-principles A1;
//! see `program::reuse_verify` for the mode, the dump and what an OK
//! covers). Only in a `diagnostics` feature build.
//!
//! Not in Go. For each program that the language server reused beyond Go,
//! `verify` sends the inputs of the session (`Request`) to a child process,
//! which starts a new session on them and dumps the program of the same
//! project: a full build, as Go's rebuild. The child runs the project
//! system itself, so configured and inferred projects, the effective
//! options and content mappers (with the server's spawner) are built as the
//! server builds them. The inputs:
//! - the session options, the custom config file name, the options of
//!   inferred projects and the open files with their editor text (a
//!   content-mapped file keeps its original text; the child runs its
//!   mapper);
//! - the server's snapshot where it answers differently from the disk now
//!   (`SnapshotDiff`): the texts of its file cache (source files,
//!   package.json files, the original texts of content-mapped files) and
//!   the files it has as deleted, its cached directory entries, and the
//!   texts of the config files that the program's command line was parsed
//!   from (the config file registry and the extended config cache keep
//!   them parsed). Every other lookup of the snapshot asks the disk now,
//!   as the child does.
//!
//! When the child cannot rebuild the server's inputs, the result is an
//! ERROR (not checked), never a MISMATCH:
//! - the child's options or root files differ from the server's (a reuse
//!   keeps the old command line, as Go's rebuild does): for example a new
//!   file under an include glob with no watch event, which the child's
//!   config parse finds and the server's kept parse does not have;
//! - the project has ATA typings files, or a custom module resolver (API);
//! - the snapshot caches a deleted file or a directory on a
//!   case-insensitive file system (the cache has its path, not its name).

use crate::frontend::compiler::NewProgram;
use crate::frontend::json::{json_marshal, json_unmarshal};
use crate::frontend::tspath;
use crate::frontend::vfs::{self, Entries, FileInfo, Fs, FsError};
use crate::lsp::lsproto;
use crate::options::CompilerOptions;
use crate::options_json::CompilerOptionsJSON;
use crate::program::reuse_verify::{compare, dump, panics, report};
use rustc_hash::FxHashMap;
use std::io::{Read, Write};
use std::rc::Rc;
use std::time::{Duration, Instant};

use super::compilerhost::CompilerHost;
use super::project::{Kind, Project};
use super::session::{SessionInit, SessionOptions};

/// The environment variable that makes this binary a verify child.
pub const CHILD_ENV: &str = "GOPORT_VERIFY_REUSE_CHILD";
/// The file that the child writes its dump to.
const CHILD_OUT_ENV: &str = "GOPORT_VERIFY_REUSE_CHILD_OUT";
/// Arguments for the child command, separated by U+001F (tests run a test
/// of their own binary as the child).
const CHILD_ARGS_ENV: &str = "GOPORT_VERIFY_REUSE_CHILD_ARGS";

/// After `project.create_program` made `program` (`outcome` for an update):
/// with `GOPORT_VERIFY_REUSE`, checks a reuse beyond Go (or, in the `all`
/// modes, every reuse) against a full build (`verify`), and with
/// `GOPORT_REUSE_STATS=<file>` appends what reuses keep alive
/// (`append_stats`).
pub fn after_update(
    project: &Project,
    host: &CompilerHost,
    outcome: Option<crate::frontend::compiler::ReuseOutcome>,
    program: &NewProgram,
) {
    use crate::frontend::compiler::ReuseOutcome;
    use crate::program::reuse_verify::{Mode, mode};
    let checked = match outcome {
        Some(ReuseOutcome::ReusedSynthetic) => mode() != Mode::Off,
        Some(ReuseOutcome::Reused) => mode() == Mode::All,
        _ => false,
    };
    if checked {
        verify(project, host, program);
    }
    if let Some(stats) = reuse_stats_file() {
        append_stats(stats, outcome, program);
    }
}

/// `GOPORT_REUSE_STATS`, read once.
fn reuse_stats_file() -> Option<&'static str> {
    static FILE: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();
    FILE.get_or_init(|| {
        std::env::var("GOPORT_REUSE_STATS")
            .ok()
            .filter(|f| !f.is_empty())
    })
    .as_deref()
}

/// Checks `program`, the new program of `project` that a reuse made, against
/// a full build in a child process, and reports the result. `host` is the
/// project's compiler host during `create_program`. The program must be
/// bound.
fn verify(project: &Project, host: &CompilerHost, program: &NewProgram) {
    let start = Instant::now();
    let changed = project.dirty_file_path.0.clone();
    let result = Request::of(project, host, program).and_then(|request| {
        let reused = dump(program);
        let full = run_child(&request)?;
        // A reuse keeps the old program's command line, and so does Go's
        // rebuild (`UpdateProgram` builds with `p.opts`): when the child's
        // options or root files differ, the child did not rebuild the
        // server's inputs, which is not a finding about the reuse.
        let config = |dump: &str| -> String {
            dump.split("\n== files\n").next().unwrap_or_default().to_string()
        };
        if config(&reused) != config(&full) {
            return Err(match compare(&config(&reused), &config(&full)) {
                Some(saved) => format!("the child's options or root files are not the server's (inputs not reconstructed): {saved}"),
                None => "the child's options or root files are not the server's".to_string(),
            });
        }
        Ok(compare(&reused, &full))
    });
    let ms = start.elapsed().as_millis();
    let files = program.source_files().len();
    let (line, failed) = match result {
        Ok(None) => (
            format!("goport verify-reuse: OK {changed} ({files} files, {ms} ms)"),
            false,
        ),
        Ok(Some(saved)) => (
            format!("goport verify-reuse: MISMATCH {changed} ({ms} ms): {saved}"),
            true,
        ),
        Err(error) => (
            format!("goport verify-reuse: ERROR {changed} ({ms} ms): {error}"),
            true,
        ),
    };
    report(&line);
    if failed && panics() {
        panic!("{line}");
    }
}

/// Appends one line to `file` after a program update (`GOPORT_REUSE_STATS`):
/// the outcome, the program's files, its per-path owners of reused
/// synthetic specifiers, the live synthetic slots of this thread and the
/// registered program versions.
fn append_stats(
    file: &str,
    outcome: Option<crate::frontend::compiler::ReuseOutcome>,
    program: &NewProgram,
) {
    let owners = program
        .processed_files
        .reused_synthetic_owners
        .as_ref()
        .map_or(0, |owners| owners.len());
    let line = format!(
        "outcome={} files={} owners={owners} live_slots={} programs={}\n",
        outcome.map_or_else(|| "New".to_string(), |o| format!("{o:?}")),
        program.source_files().len(),
        crate::ast::synthetic_live_slot_count(),
        crate::program::ls_program::registered_programs(),
    );
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(file)
    {
        let _ = f.write_all(line.as_bytes());
    }
}

/// An open file of the session.
#[derive(Clone, Debug, PartialEq)]
struct OpenFile {
    file_name: String,
    version: i32,
    language_kind: String,
    text: String,
}

/// What the child needs to start the session again.
#[derive(Debug)]
struct Request {
    current_directory: String,
    default_library_path: String,
    typings_location: String,
    position_encoding: String,
    run_external_code: bool,
    custom_config_file_name: String,
    /// `compilerOptionsForInferredProjects` as JSON, or empty.
    inferred_options: String,
    /// The config file name of a configured project, or empty for the
    /// inferred project.
    config_file_name: String,
    /// The file whose change made the program.
    changed_file: String,
    /// The open files, in the session's order.
    open_files: Vec<OpenFile>,
    /// Where the server's snapshot answers differently from the disk.
    snapshot: SnapshotDiff,
}

/// The answers of the server's snapshot file system that the disk no
/// longer gives (the disk changed with no watch event). The snapshot keeps
/// file texts (`cache_files`: config files, package.json files, the
/// original texts of content-mapped files, source files) and directory
/// entries (`cache_directories`); every other lookup of the snapshot asks
/// the disk now, as the child does. An entry that needs a reload reads the
/// disk on its next use, so it is not part of the diff.
#[derive(Debug, Default)]
struct SnapshotDiff {
    /// Files with their snapshot text.
    texts: Vec<(String, String)>,
    /// Files that the snapshot has as deleted and the disk has.
    missing: Vec<String>,
    /// Directories with their snapshot entries.
    directories: Vec<(String, Entries)>,
}

/// The same names in each list, in any order.
fn same_entries(a: &Entries, b: &Entries) -> bool {
    let sorted = |names: &[String]| {
        let mut names = names.to_vec();
        names.sort();
        names
    };
    let symlinks = |e: &Entries| {
        e.symlinks.as_ref().map(|s| {
            let mut s: Vec<&String> = s.iter().collect();
            s.sort();
            s.into_iter().cloned().collect::<Vec<_>>()
        })
    };
    sorted(&a.files) == sorted(&b.files)
        && sorted(&a.directories) == sorted(&b.directories)
        && symlinks(a) == symlinks(b)
}

impl SnapshotDiff {
    /// Adds the texts of the program's config file and of the configs it
    /// extends where the disk has another text. Go's rebuild builds with
    /// the old program's command line (`p.opts`), which the config file
    /// registry and the extended config cache keep parsed; the snapshot
    /// does not keep these texts in its file cache.
    fn add_config_texts(
        &mut self,
        command_line: &crate::frontend::tsoptions::ParsedCommandLine,
        builder: &super::projectcollectionbuilder::ProjectCollectionBuilder,
    ) {
        let disk = crate::frontend::bundled::wrap_fs_exported(vfs::osvfs::osvfs_fs());
        let mut texts: Vec<(String, String)> = Vec::new();
        if let Some(config_file) = &command_line.config_file {
            texts.push((
                config_file.file_name.clone(),
                crate::ast::source_file_text(config_file.source_file).to_string(),
            ));
        }
        for &name in command_line.extended_source_files() {
            let path = (builder.fs.to_path)(name);
            let entry = builder
                .extended_config_cache
                .entries
                .borrow()
                .get(&path)
                .and_then(|entry| entry.value.borrow().clone());
            if let Some(extended) = entry
                .as_ref()
                .and_then(|entry| entry.extended_config_cache_entry.extended_result.as_ref())
            {
                texts.push((
                    name.to_string(),
                    crate::ast::source_file_text(extended.source_file).to_string(),
                ));
            }
        }
        for (name, text) in texts {
            let (disk_text, ok) = disk.read_file(&name);
            if (!ok || disk_text != text) && !self.texts.iter().any(|(n, _)| *n == name) {
                self.texts.push((name, text));
            }
        }
        self.texts.sort();
    }

    fn of(fs: &super::snapshotfs::SnapshotFSBuilder) -> Result<Self, String> {
        let disk = crate::frontend::bundled::wrap_fs_exported(vfs::osvfs::osvfs_fs());
        let case_sensitive = disk.use_case_sensitive_file_names();
        let mut diff = SnapshotDiff::default();
        let mut error = None;
        let mut cached: rustc_hash::FxHashSet<tspath::Path> = rustc_hash::FxHashSet::default();
        fs.cache_files.range(&mut |entry| {
            let path = entry.key();
            match entry.value() {
                None => {
                    // A deleted file has no file name in the cache: on a
                    // case-insensitive file system its path is not a name.
                    if !case_sensitive {
                        error = Some(format!(
                            "the snapshot has {} as deleted, which the child cannot name on a case-insensitive file system",
                            path.0
                        ));
                        return false;
                    }
                    if disk.file_exists(&path.0) {
                        diff.missing.push(path.0);
                    }
                }
                Some(file) => {
                    let file = file.borrow();
                    if file.needs_reload {
                        return true;
                    }
                    cached.insert(path);
                    let name = &file.file_base.file_name;
                    let (text, ok) = disk.read_file(name);
                    if !ok || text != *file.file_base.content {
                        diff.texts.push((name.clone(), file.file_base.content.to_string()));
                    }
                }
            }
            true
        });
        if let Some(error) = error {
            return Err(error);
        }
        fs.cache_directories.range(&mut |entry| {
            let Some(directory) = entry.value() else {
                return true;
            };
            let path = entry.key();
            if !case_sensitive {
                error = Some(format!(
                    "the snapshot caches the entries of {}, which the child cannot name on a case-insensitive file system",
                    path.0
                ));
                return false;
            }
            let live = disk.get_accessible_entries(&path.0);
            let merged = super::snapshotfs::merge_cached_directory_entries(
                live.clone(),
                &directory.borrow(),
                &|child_path: &tspath::Path, child_name: &str| {
                    cached.contains(child_path)
                        || disk.file_exists(&tspath::combine_paths(&path.0, &[child_name]))
                },
                case_sensitive,
            );
            if !same_entries(&merged, &live) {
                diff.directories.push((path.0, merged));
            }
            true
        });
        if let Some(error) = error {
            return Err(error);
        }
        diff.texts.sort();
        diff.missing.sort();
        diff.directories.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(diff)
    }
}

/// The LSP language id that `language_kind_to_script_kind` maps to `kind`.
fn language_kind_of(kind: crate::project::prelude::ScriptKind) -> &'static str {
    use crate::project::prelude::ScriptKind;
    match kind {
        ScriptKind::TS => "typescript",
        ScriptKind::TSX => "typescriptreact",
        ScriptKind::JS => "javascript",
        ScriptKind::JSX => "javascriptreact",
        ScriptKind::JSON => "json",
        _ => "unknown",
    }
}

impl Request {
    fn of(project: &Project, host: &CompilerHost, program: &NewProgram) -> Result<Self, String> {
        if !project.typings_files.is_empty() {
            return Err(
                "the project has ATA typings files, which the child cannot install: not checked"
                    .to_string(),
            );
        }
        let builder = host
            .builder
            .borrow()
            .clone()
            .ok_or("no project collection builder")?;
        let options = &builder.session_options;
        let open_files: Vec<OpenFile> = builder
            .overlays
            .values()
            .map(|overlay| OpenFile {
                file_name: overlay.file_base.file_name.clone(),
                version: overlay.version.get(),
                language_kind: language_kind_of(overlay.kind).to_string(),
                text: overlay.file_base.content.to_string(),
            })
            .collect();
        if project.module_resolver_factory.is_some() {
            return Err(
                "the project has a custom module resolver (API), which the child cannot have: not checked"
                    .to_string(),
            );
        }
        let mut snapshot = SnapshotDiff::of(&builder.fs)?;
        snapshot.add_config_texts(program.command_line(), &builder);
        let inferred_options = match &builder.compiler_options_for_inferred_projects {
            Some(options) => json_marshal(&CompilerOptionsJSON(options), &[])
                .map_err(|e| format!("inferred options: {e:?}"))?,
            None => String::new(),
        };
        Ok(Request {
            current_directory: options.current_directory.clone(),
            default_library_path: options.default_library_path.clone(),
            typings_location: options.typings_location.clone(),
            position_encoding: options.position_encoding.0.to_string(),
            run_external_code: options.run_external_code,
            custom_config_file_name: builder
                .config_file_registry_builder
                .custom_config_file_name
                .clone(),
            inferred_options,
            config_file_name: if project.kind == Kind::CONFIGURED {
                project.config_file_name.clone()
            } else {
                String::new()
            },
            changed_file: program
                .get_source_file_by_path(&project.dirty_file_path)
                .map_or_else(
                    || project.dirty_file_path.0.clone(),
                    |file| file.file_name().to_string(),
                ),
            open_files,
            snapshot,
        })
    }

    /// Each string is its length in bytes, a newline and its bytes.
    fn encode(&self) -> Vec<u8> {
        let mut out = Vec::new();
        let mut put = |s: &str| {
            out.extend_from_slice(format!("{}\n", s.len()).as_bytes());
            out.extend_from_slice(s.as_bytes());
        };
        put(&self.current_directory);
        put(&self.default_library_path);
        put(&self.typings_location);
        put(&self.position_encoding);
        put(if self.run_external_code { "1" } else { "0" });
        put(&self.custom_config_file_name);
        put(&self.inferred_options);
        put(&self.config_file_name);
        put(&self.changed_file);
        put(&self.open_files.len().to_string());
        for file in &self.open_files {
            put(&file.file_name);
            put(&file.version.to_string());
            put(&file.language_kind);
            put(&file.text);
        }
        let diff = &self.snapshot;
        put(&diff.texts.len().to_string());
        for (name, text) in &diff.texts {
            put(name);
            put(text);
        }
        put(&diff.missing.len().to_string());
        for name in &diff.missing {
            put(name);
        }
        put(&diff.directories.len().to_string());
        for (name, entries) in &diff.directories {
            put(name);
            let mut list = |names: &[String]| {
                put(&names.len().to_string());
                for n in names {
                    put(n);
                }
            };
            list(&entries.files);
            list(&entries.directories);
            // The symlinks: a marker, then the sorted names.
            let symlinks: Vec<String> = match &entries.symlinks {
                None => vec!["-".to_string()],
                Some(symlinks) => {
                    let mut sorted: Vec<String> = symlinks.iter().cloned().collect();
                    sorted.sort();
                    std::iter::once("+".to_string()).chain(sorted).collect()
                }
            };
            list(&symlinks);
        }
        out
    }

    fn decode(bytes: &[u8]) -> Result<Self, String> {
        let mut rest = bytes;
        let mut take = || -> Result<String, String> {
            let newline = rest
                .iter()
                .position(|&b| b == b'\n')
                .ok_or("truncated request")?;
            let len: usize = std::str::from_utf8(&rest[..newline])
                .map_err(|e| e.to_string())?
                .parse()
                .map_err(|e: std::num::ParseIntError| e.to_string())?;
            let body = rest
                .get(newline + 1..newline + 1 + len)
                .ok_or("truncated request")?;
            let s = String::from_utf8(body.to_vec()).map_err(|e| e.to_string())?;
            rest = &rest[newline + 1 + len..];
            Ok(s)
        };
        let number = |s: String| -> Result<usize, String> {
            s.parse()
                .map_err(|e: std::num::ParseIntError| e.to_string())
        };
        let current_directory = take()?;
        let default_library_path = take()?;
        let typings_location = take()?;
        let position_encoding = take()?;
        let run_external_code = take()? == "1";
        let custom_config_file_name = take()?;
        let inferred_options = take()?;
        let config_file_name = take()?;
        let changed_file = take()?;
        let mut open_files = Vec::new();
        for _ in 0..number(take()?)? {
            let file_name = take()?;
            let version = take()?
                .parse()
                .map_err(|e: std::num::ParseIntError| e.to_string())?;
            let language_kind = take()?;
            let text = take()?;
            open_files.push(OpenFile {
                file_name,
                version,
                language_kind,
                text,
            });
        }
        let mut snapshot = SnapshotDiff::default();
        for _ in 0..number(take()?)? {
            let name = take()?;
            let text = take()?;
            snapshot.texts.push((name, text));
        }
        for _ in 0..number(take()?)? {
            snapshot.missing.push(take()?);
        }
        for _ in 0..number(take()?)? {
            let name = take()?;
            let mut list = || -> Result<Vec<String>, String> {
                let n = number(take()?)?;
                (0..n).map(|_| take()).collect()
            };
            let files = list()?;
            let directories = list()?;
            let marked = list()?;
            let symlinks = match marked.split_first() {
                Some((marker, names)) if marker == "+" => Some(names.iter().cloned().collect()),
                _ => None,
            };
            snapshot.directories.push((
                name,
                Entries {
                    files,
                    directories,
                    symlinks,
                },
            ));
        }
        Ok(Request {
            current_directory,
            default_library_path,
            typings_location,
            position_encoding,
            run_external_code,
            custom_config_file_name,
            inferred_options,
            config_file_name,
            changed_file,
            open_files,
            snapshot,
        })
    }
}

/// Runs the verify child on `request` and returns its dump.
fn run_child(request: &Request) -> Result<String, String> {
    // On Linux `/proc/self/exe` runs this binary even when a build has
    // replaced the file since the server started.
    let exe = if cfg!(target_os = "linux") && std::path::Path::new("/proc/self/exe").exists() {
        std::path::PathBuf::from("/proc/self/exe")
    } else {
        std::env::current_exe().map_err(|e| format!("current_exe: {e}"))?
    };
    let out_path = std::env::temp_dir().join(format!(
        "goport-verify-reuse-child-{}-{}.txt",
        std::process::id(),
        CHILD_COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    ));
    let _ = std::fs::remove_file(&out_path);
    let mut command = std::process::Command::new(exe);
    if let Ok(args) = std::env::var(CHILD_ARGS_ENV) {
        command.args(args.split('\u{1f}').filter(|arg| !arg.is_empty()));
    }
    let mut child = command
        .env(CHILD_ENV, "1")
        .env(CHILD_OUT_ENV, &out_path)
        .env_remove("GOPORT_VERIFY_REUSE")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn: {e}"))?;
    let mut stdin = child.stdin.take().ok_or("no child stdin")?;
    let encoded = request.encode();
    let writer = std::thread::spawn(move || {
        let _ = stdin.write_all(&encoded);
    });
    let mut stderr = child.stderr.take().ok_or("no child stderr")?;
    let err_reader = std::thread::spawn(move || {
        let mut out = String::new();
        let _ = stderr.read_to_string(&mut out);
        out
    });
    let deadline = Instant::now() + Duration::from_secs(300);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(5)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("child timed out after 300 s".to_string());
            }
            Err(e) => return Err(format!("wait: {e}")),
        }
    };
    let _ = writer.join();
    let err = err_reader.join().unwrap_or_default();
    let out = std::fs::read_to_string(&out_path);
    let _ = std::fs::remove_file(&out_path);
    match out {
        Ok(out) if status.success() => Ok(out),
        _ => {
            let tail: String = err
                .chars()
                .rev()
                .take(2000)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect();
            Err(format!("child failed ({status}): {tail}"))
        }
    }
}

static CHILD_COUNT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

/// The body of the verify child of the `tsgo` binary (`CHILD_ENV`), with
/// the server's process spawner for content mappers. Returns the exit code.
pub fn child_main() -> i32 {
    let spawner: Rc<dyn crate::contentmapper::Spawner> =
        Rc::new(crate::contentmapper::SpawnerFunc(Box::new(
            |command: &[String], dir: &str, stderr: Option<Box<dyn Write + Send>>| {
                crate::execute::tsc::compile::spawn_process(command, dir, stderr)
            },
        )));
    child_main_with(Some(spawner))
}

/// The body of a verify child with the content mapper spawner `spawner`
/// (tests give their in-process spawner): reads a `Request` on stdin,
/// starts a session on it and writes the dump of the project's program to
/// the file `CHILD_OUT_ENV`. Returns the exit code.
pub fn child_main_with(spawner: Option<Rc<dyn crate::contentmapper::Spawner>>) -> i32 {
    match run_child_session(spawner) {
        Ok(()) => 0,
        Err(e) => {
            let _ = writeln!(std::io::stderr(), "verify child: {e}");
            2
        }
    }
}

fn run_child_session(spawner: Option<Rc<dyn crate::contentmapper::Spawner>>) -> Result<(), String> {
    let out_path =
        std::env::var(CHILD_OUT_ENV).map_err(|_| format!("{CHILD_OUT_ENV} is not set"))?;
    let mut input = Vec::new();
    std::io::stdin()
        .read_to_end(&mut input)
        .map_err(|e| format!("stdin: {e}"))?;
    let request = Request::decode(&input)?;
    let ctx = crate::gostd::context::background();
    let base = crate::frontend::bundled::wrap_fs_exported(vfs::osvfs::osvfs_fs());
    let fs: Rc<dyn Fs> = Rc::new(SnapshotTextFs {
        inner: base,
        texts: request.snapshot.texts.iter().cloned().collect(),
        missing: request.snapshot.missing.iter().cloned().collect(),
        directories: request.snapshot.directories.iter().cloned().collect(),
    });
    let session = super::session::new_session(&SessionInit {
        background_ctx: ctx.clone(),
        options: Rc::new(SessionOptions {
            current_directory: request.current_directory.clone(),
            default_library_path: request.default_library_path.clone(),
            typings_location: request.typings_location.clone(),
            position_encoding: lsproto::PositionEncodingKind(
                request.position_encoding.clone().into(),
            ),
            watch_enabled: false,
            logging_enabled: false,
            telemetry_enabled: false,
            push_diagnostics_enabled: false,
            run_external_code: request.run_external_code,
            debounce_delay: Duration::from_millis(500),
            checker_pool_options: super::checkerpool::CheckerPoolOptions::default(),
        }),
        fs,
        client: None,
        logger: None,
        npm_executor: None,
        spawner: if request.run_external_code {
            spawner
        } else {
            None
        },
        content_mapper_logger: None,
        parse_cache: None,
        content_mapped_parse_cache: None,
    });
    let mut preferences = crate::ls::lsutil::new_default_user_preferences();
    preferences.custom_config_file_name = request.custom_config_file_name.clone();
    session.initialize_with_user_config(preferences);
    if !request.inferred_options.is_empty() {
        let mut options = CompilerOptions::default();
        json_unmarshal(request.inferred_options.as_bytes(), &mut options, &[])
            .map_err(|e| format!("inferred options: {e:?}"))?;
        session.did_change_compiler_options_for_inferred_projects(&ctx, Some(Rc::new(options)));
    }
    let uri = crate::ls::lsconv::file_name_to_document_uri;
    for file in &request.open_files {
        session.did_open_file(
            &ctx,
            &uri(&file.file_name),
            file.version,
            &file.text,
            &lsproto::LanguageKind(file.language_kind.clone().into()),
        );
    }
    // The program of the project: ask for the changed file's language
    // service when it is open, else for each open file, until the project
    // has a program.
    let program_of = |session: &Rc<super::session::Session>| -> Option<Rc<NewProgram>> {
        let snapshot = session.snapshot();
        let project = if request.config_file_name.is_empty() {
            snapshot.project_collection.inferred_project()
        } else {
            let path = tspath::to_path(
                &request.config_file_name,
                &request.current_directory,
                snapshot_case_sensitive(),
            );
            snapshot.project_collection.configured_project(&path)
        }?;
        let program = project.borrow().program.clone();
        program
    };
    let candidates: Vec<&str> = std::iter::once(request.changed_file.as_str())
        .filter(|name| {
            request
                .open_files
                .iter()
                .any(|file| file.file_name == *name)
        })
        .chain(
            request
                .open_files
                .iter()
                .map(|file| file.file_name.as_str()),
        )
        .collect();
    let mut program = None;
    for name in candidates {
        let _ = session.get_language_service(&ctx, &uri(name));
        program = program_of(&session);
        if program.is_some() {
            break;
        }
    }
    let program = program.ok_or("the child session has no program for the project")?;
    let text = dump(&program);
    drop(program);
    session.close();
    std::fs::write(&out_path, text).map_err(|e| format!("write {out_path}: {e}"))
}

/// The OS file system's case sensitivity (the session's paths).
fn snapshot_case_sensitive() -> bool {
    vfs::osvfs::osvfs_fs().use_case_sensitive_file_names()
}

/// The file system of the child: the disk, with the answers of the
/// server's snapshot where they differ (`SnapshotDiff`).
struct SnapshotTextFs {
    inner: Rc<dyn Fs>,
    texts: FxHashMap<String, String>,
    missing: rustc_hash::FxHashSet<String>,
    directories: FxHashMap<String, Entries>,
}

impl Fs for SnapshotTextFs {
    fn use_case_sensitive_file_names(&self) -> bool {
        self.inner.use_case_sensitive_file_names()
    }
    fn file_exists(&self, path: &str) -> bool {
        !self.missing.contains(path)
            && (self.texts.contains_key(path) || self.inner.file_exists(path))
    }
    fn read_file(&self, path: &str) -> (String, bool) {
        if self.missing.contains(path) {
            return (String::new(), false);
        }
        match self.texts.get(path) {
            Some(text) => (text.clone(), true),
            None => self.inner.read_file(path),
        }
    }
    vfs::fs_write_through!(inner);
    fn directory_exists(&self, path: &str) -> bool {
        self.inner.directory_exists(path)
    }
    fn get_accessible_entries(&self, path: &str) -> Entries {
        // The disk lists a directory in name order (Go `os.ReadDir`); the
        // snapshot's merged entries come in cache order (a Go map range),
        // so a replayed listing is sorted as the disk sorts.
        let mut entries = match self.directories.get(path) {
            Some(entries) => {
                let mut entries = entries.clone();
                entries.files.sort();
                entries.directories.sort();
                entries
            }
            None => self.inner.get_accessible_entries(path),
        };
        // The listing agrees with the files that exist here: a file with a
        // snapshot text is listed, a file that the snapshot has as deleted is
        // not, as for the server's snapshot.
        for name in self.texts.keys() {
            if tspath::get_directory_path(name) == path {
                let base = tspath::get_base_file_name(name);
                if !entries.files.iter().any(|file| *file == base) {
                    entries.files.push(base);
                    entries.files.sort();
                }
            }
        }
        entries.files.retain(|file| {
            !self
                .missing
                .contains(&tspath::combine_paths(path, &[file.as_str()]))
        });
        entries
    }
    fn stat(&self, path: &str) -> Option<FileInfo> {
        if self.missing.contains(path) {
            return None;
        }
        self.inner.stat(path)
    }
    fn realpath(&self, path: &str) -> String {
        self.inner.realpath(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The request round trip of the verify child.
    #[test]
    fn request_round_trip() {
        let request = Request {
            current_directory: "/p".to_string(),
            default_library_path: "bundled:///libs".to_string(),
            typings_location: String::new(),
            position_encoding: "utf-16".to_string(),
            run_external_code: true,
            custom_config_file_name: "tsconfig.dev.json".to_string(),
            inferred_options: r#"{"jsx":4}"#.to_string(),
            config_file_name: String::new(),
            changed_file: "/p/a.tsx".to_string(),
            open_files: vec![OpenFile {
                file_name: "/p/é\n.tsx".to_string(),
                version: 3,
                language_kind: "typescriptreact".to_string(),
                text: "const a = \"\n1\";".to_string(),
            }],
            snapshot: SnapshotDiff {
                texts: vec![("/p/b.ts".to_string(), String::new())],
                missing: vec!["/p/gone.ts".to_string()],
                directories: vec![
                    (
                        "/p".to_string(),
                        Entries {
                            files: vec!["a.ts".to_string()],
                            directories: vec!["src".to_string()],
                            symlinks: None,
                        },
                    ),
                    (
                        "/p/src".to_string(),
                        Entries {
                            files: Vec::new(),
                            directories: Vec::new(),
                            symlinks: Some(
                                ["x".to_string(), "a".to_string()].into_iter().collect(),
                            ),
                        },
                    ),
                ],
            },
        };
        assert_eq!(
            Request::decode(&request.encode()).unwrap().encode(),
            request.encode()
        );
    }
}
