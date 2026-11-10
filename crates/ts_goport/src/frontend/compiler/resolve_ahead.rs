//! Resolve ahead: worker threads resolve again, in the same order, the
//! module names that the previous program load of a language server project
//! resolved, while the loader of the new load runs (lspp95 plan, step 1).
//! They also find again the package scope of each directory whose files the
//! previous load read (Go `loadSourceFileMetaData`), and the loader takes a
//! scope as it takes a module answer (`Caches::package_scope_ahead`).
//!
//! The loader stays serial. It takes a worker answer only when the answer
//! passes its host's check (`ResolveAheadHost::accept`). The worker logs
//! the file system calls of its resolution with their answers
//! (`AheadCall`); a call that the check cannot do again (a directory
//! listing, a stat, a read of an open file) makes the answer unshareable.
//! The check gives each call to the host's snapshot file system as the
//! loader's own call would go there, at the moment the loader's own
//! resolution would make it:
//!
//! - a read is the loader's read, and its text must have the worker's hash
//!   (a worker read that failed fails);
//! - a `file_exists`, `directory_exists` or `realpath` must agree with the
//!   snapshot's answer for the name, if it has one. The snapshot's lookup
//!   cache takes each answer that it does not have from the workers' lookups
//!   of the load (`WorkerStats`, `AheadLookups`) before it asks the OS, as
//!   Go's parse tasks fill one per-snapshot cache. So a worker's answer,
//!   which the worker got during this load, is the snapshot's answer when
//!   the snapshot had none. A `file_exists` answer that the workers know
//!   from an earlier load is asked of the snapshot's file system.
//!
//! So the load has one answer for each path, also when the disk changes
//! during the load. The check also replays the side effects of the calls
//! (seen files, missing directories). If an answer does not pass, the
//! loader resolves the key itself. So the program, its file order and the
//! watched files are those of a load with no workers whose calls found the
//! disk as the workers found it.
// PORT: not in Go (perf). Go resolves in each parse task, on all threads;
// the port's loader resolves on one thread (contract 10).

use crate::frontend::prelude::*;
use std::cell::Cell;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex};
use xxhash_rust::xxh3::xxh3_128;

/// Tests and proof tools (diagnostics): how the program loads on a thread
/// resolve ahead (`set_mode`, `GOPORT_RESOLVE_AHEAD`). Shipped builds always
/// resolve ahead (`On`).
#[cfg(feature = "diagnostics")]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// No resolve ahead: the loader resolves every key itself, as a load
    /// whose host has no resolve-ahead view does (the serial oracle;
    /// `GOPORT_RESOLVE_AHEAD=0`).
    Off,
    /// The shipped behavior: the workers resolve while the loader runs.
    On,
    /// The workers resolve every key before the loader starts, so the
    /// loader finds an answer for each key of the previous load.
    Force,
}

#[cfg(feature = "diagnostics")]
thread_local! {
    /// `set_mode` on this thread.
    static MODE: Cell<Option<Mode>> = const { Cell::new(None) };
    /// `set_first_load` on this thread.
    static FIRST_LOAD: Cell<bool> = const { Cell::new(true) };
    /// The counts of the last resolve-ahead load on this thread.
    static LAST_STATS: Cell<Option<LoadStats>> = const { Cell::new(None) };
    /// `inject_worker_panic` on this thread.
    static INJECT_PANIC: Cell<bool> = const { Cell::new(false) };
    /// `set_after_workers` on this thread.
    static AFTER_WORKERS: RefCell<Option<Rc<dyn Fn()>>> = const { RefCell::new(None) };
}

/// Tests (diagnostics): sets the mode of the program loads on this thread,
/// in place of `GOPORT_RESOLVE_AHEAD` (`None`: the variable again).
#[cfg(feature = "diagnostics")]
pub fn set_mode(mode: Option<Mode>) {
    MODE.with(|m| m.set(mode));
}

/// Diagnostics: the mode of a program load on this thread: the test's
/// choice, else `Mode::Off` when `GOPORT_RESOLVE_AHEAD` is `0` (the serial
/// loader of the load-dump proofs, scripts/perf-rearch/loader/l1-dumps.sh
/// and l1-replay-oracle.sh), else `Mode::On`.
#[cfg(feature = "diagnostics")]
#[must_use]
pub fn mode() -> Mode {
    static ENV: std::sync::OnceLock<Mode> = std::sync::OnceLock::new();
    MODE.with(Cell::get).unwrap_or_else(|| {
        *ENV.get_or_init(|| match std::env::var("GOPORT_RESOLVE_AHEAD").as_deref() {
            Ok("0") => Mode::Off,
            _ => Mode::On,
        })
    })
}

/// Whether the program loads on this thread resolve ahead: always, unless
/// a test chose `Mode::Off` (diagnostics).
#[inline]
#[must_use]
pub(crate) fn enabled() -> bool {
    #[cfg(feature = "diagnostics")]
    if mode() == Mode::Off {
        return false;
    }
    true
}

/// Whether a language server's first program load resolves ahead: its
/// parse workers resolve through the view of a resolve-ahead job as they
/// parse, and the loader checks each of their answers as it checks a
/// resolve-ahead answer (`FirstLoadJob`, stage S1a of
/// docs/perf-rearch/design/loader.md). Always, unless a test turned it off
/// (`set_first_load`, diagnostics): the first load then has no job, as a
/// first load with no parse workers.
#[inline]
#[must_use]
pub(crate) fn first_load_enabled() -> bool {
    #[cfg(feature = "diagnostics")]
    if !FIRST_LOAD.with(Cell::get) {
        return false;
    }
    true
}

/// Tests (diagnostics): sets whether the first loads on this thread resolve
/// ahead (`None`: they do).
#[cfg(feature = "diagnostics")]
pub fn set_first_load(on: Option<bool>) {
    FIRST_LOAD.with(|first| first.set(on.unwrap_or(true)));
}

/// The counts of one resolve-ahead load (diagnostics).
#[cfg(feature = "diagnostics")]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct LoadStats {
    /// Module keys of the previous load that the workers had.
    pub keys: usize,
    /// Module keys that this load resolved or took.
    pub new_keys: usize,
    /// Package scope keys of the previous load that the workers had
    /// (`KeyList::push_scope`).
    pub scopes: usize,
    /// Package scope keys of this load.
    pub new_scopes: usize,
    pub loader: AheadStats,
    /// The known files that the workers of this load started with
    /// (`WorkerState::known_files`). 0 when the load had no workers.
    pub known_files: usize,
    /// A worker panicked outside a resolution during this load, and the
    /// loader resolved the rest of the keys itself (`run_task`).
    pub worker_panic: bool,
    /// The load had no keys of a previous load, and its parse workers made
    /// the answers (`FirstLoadJob`).
    pub first_load: bool,
}

/// Tests (diagnostics): the counts of the last resolve-ahead load on this
/// thread.
#[cfg(feature = "diagnostics")]
#[must_use]
pub fn last_stats() -> Option<LoadStats> {
    LAST_STATS.with(Cell::get)
}

/// Makes the workers of the loads on this thread panic outside a
/// resolution, as a port bug would (`run_task`), after they resolved their
/// keys: the answers are published, and the load must take none of them
/// (`AheadQueue::fail`). Tests (diagnostics).
#[cfg(feature = "diagnostics")]
pub fn inject_worker_panic(on: bool) {
    INJECT_PANIC.with(|inject| inject.set(on));
}

/// Takes the workers, as a load on another thread does, until the guard
/// drops: the loads find them taken and resolve every key themselves.
/// Tests (diagnostics).
#[cfg(feature = "diagnostics")]
#[must_use]
pub fn hold_workers() -> HeldWorkers {
    if let Some(workers) = Workers::get() {
        lock_state(workers).held = true;
    }
    HeldWorkers(())
}

/// The guard of `hold_workers`.
#[cfg(feature = "diagnostics")]
pub struct HeldWorkers(());

#[cfg(feature = "diagnostics")]
impl Drop for HeldWorkers {
    fn drop(&mut self) {
        if let Some(workers) = Workers::started() {
            lock_state(workers).held = false;
        }
    }
}

/// Waits until the workers have freed the jobs of the ended loads, so the
/// next load starts with the files that they found (`EndedJob::free`).
/// Tests (diagnostics).
#[cfg(feature = "diagnostics")]
pub fn wait_for_frees() {
    let Some(workers) = Workers::started() else {
        return;
    };
    let mut state = lock_state(workers);
    while state.frees > 0 {
        state = workers
            .left
            .wait(state)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
    }
}

/// Runs `hook` in each `Mode::Force` load on this thread after its workers
/// left the job and before its loader starts (`None`: no hook). A test
/// changes the disk there, during the load. Tests (diagnostics).
#[cfg(feature = "diagnostics")]
pub fn set_after_workers(hook: Option<Rc<dyn Fn()>>) {
    AFTER_WORKERS.with(|after| *after.borrow_mut() = hook);
}

/// Tests (diagnostics): runs the `set_after_workers` hook of this thread before the
/// loader of a load starts (`FilesParser::parse`): in `Mode::Force`, in a
/// first-load load after its parse workers have parsed and resolved all
/// they could; in `Mode::Off`, at the same point of any load. A test changes
/// the disk there, between the workers' answers and the loader's check of
/// them, and compares with a serial load whose disk changed at that point.
#[cfg(feature = "diagnostics")]
pub fn after_first_load_workers() {
    if let Some(hook) = AFTER_WORKERS.with(|after| after.borrow().clone()) {
        hook();
    }
}

/// The workers' view of a host's file system: the OS file system with the
/// open files of the host over it, as the language server's overlay file
/// system shows it (project/overlayfs.rs).
pub struct WorkerView {
    /// `current_directory` and `use_case_sensitive_file_names` make the
    /// paths, as the host's `to_path`.
    pub current_directory: String,
    pub use_case_sensitive_file_names: bool,
    /// The paths of the open files.
    pub open_files: FxHashSet<Path>,
    /// The paths of the directories that have open files in them.
    pub open_directories: FxHashSet<Path>,
}

/// A compiler host's part in resolve ahead
/// (`CompilerHost::resolve_ahead`).
pub struct ResolveAheadHost {
    /// The keys of the host's previous program load, in its order. `None`:
    /// no workers; the load only records its keys.
    pub previous_keys: Option<Arc<KeyList>>,
    pub view: WorkerView,
    /// Checks the calls of a worker answer on the host's file system and
    /// replays their side effects (`AheadLink::accept`).
    pub accept: AheadAccept,
    /// Gives the lookups of the load's job to the host's snapshot file
    /// system, which takes an answer from them before it asks the OS
    /// (`AheadLookups`). Called when the load gives the workers a job.
    pub attach: Box<dyn FnOnce(Arc<dyn AheadLookups>)>,
    /// Called in place of `attach` when the load's job is a first-load job
    /// (`FirstLoadJob`): the snapshot does not keep the job's lookups. Its
    /// check stores the lookups of each answer that it takes in the
    /// snapshot's lookup cache, as the loader's own calls would, so the
    /// lookups of answers that the loader did not take leave no trace.
    pub first_load: Box<dyn FnOnce()>,
    /// The text of a package.json that the check read in this load
    /// (`AheadLink::package_json_text`).
    pub package_json_text: AheadText,
    /// Keeps the keys of this load for the next load.
    pub keep_keys: Box<dyn FnOnce(Arc<KeyList>)>,
    /// The project's share in what the workers keep from job to job.
    pub share: Rc<KeptShare>,
    /// Debug and `diagnostics` builds: makes a new tracking view of the
    /// host's file system, to check that the calls of each taken answer
    /// list every side effect of its resolution (`resolve_ahead_verify`).
    #[cfg(any(debug_assertions, feature = "diagnostics"))]
    pub scratch: Rc<dyn Fn() -> resolve_ahead_verify::ScratchFs>,
}

/// A project's share in what the workers keep from job to job: the known
/// files and the package.json parses. Each host of the project's loads
/// gives it to the next one (project/compilerhost.rs). A project that a
/// snapshot clone made and deleted gives it to the session's stash, and the
/// project of the same config that a later clone makes takes it
/// (`ResolveAheadStash`). When the last one drops it (the project's
/// programs are released, or the stash drops the project) after a load of
/// the project gave the workers a job, the workers drop what they keep
/// (`Workers::forget`). So the kept state holds only what the loads of
/// live and stashed projects found, and the next loads find it again.
// PORT: not in Go (perf).
#[derive(Default)]
pub struct KeptShare {
    /// The epoch of the kept state (`WorkerState::epoch`) when a load of
    /// the project last gave the workers a job.
    epoch: Cell<Option<u64>>,
}

impl Drop for KeptShare {
    fn drop(&mut self) {
        if let Some(epoch) = self.epoch.get()
            && let Some(workers) = Workers::started()
        {
            workers.forget(epoch);
        }
    }
}

/// What a worker resolver needs: the loader resolver's options.
struct ResolverConfig {
    options: CompilerOptions,
    typings_location: String,
    project_name: String,
    extra_extensions: Vec<String>,
    /// The current directory of the loader resolver's host.
    current_directory: String,
}

impl ResolverConfig {
    /// A resolver with the loader resolver's options on `fs`, with
    /// `package_json_cache` or a new one.
    fn new_resolver(
        &self,
        fs: Rc<dyn Fs>,
        package_json_cache: Option<Rc<InfoCache>>,
    ) -> DefaultResolver {
        new_resolver(ResolverOptions {
            host: Some(Rc::new(AheadResolutionHost {
                fs,
                current_directory: self.current_directory.clone(),
            })),
            compiler_options: Some(Rc::new(self.options.clone())),
            typings_location: self.typings_location.clone(),
            project_name: self.project_name.clone(),
            extra_extensions: self.extra_extensions.clone(),
            package_json_cache,
        })
    }
}

/// Resolve ahead in one program load (`process_all_program_files`).
pub struct ResolveAhead {
    /// The workers' job of this load, until `finish`.
    job: Option<Arc<Job>>,
    /// The job is a first-load job (`FirstLoadJob`).
    first_load: bool,
    /// The loader checks every lookup of an answer through the snapshot's
    /// lookup cache (a first load, or any load under the OS view).
    validated: bool,
    keep_keys: Option<Box<dyn FnOnce(Arc<KeyList>)>>,
    /// The module keys and the package scope keys of the previous load
    /// (`LoadStats`, diagnostics).
    #[cfg(feature = "diagnostics")]
    previous_keys: (usize, usize),
}

impl ResolveAhead {
    /// Starts resolve ahead for the load of `resolver`, the loader's
    /// resolver, with `host`. The caller checked the conditions of the load
    /// (`process_all_program_files`). With `first_load` (the load's parse
    /// workers run), a load with no keys of a previous load gives the
    /// workers a job with no keys, whose answers the parse workers make
    /// (`first_load_job`).
    pub fn start(host: ResolveAheadHost, resolver: &DefaultResolver, first_load: bool) -> Self {
        let config = Arc::new(ResolverConfig {
            options: resolver.compiler_options.as_ref().clone(),
            typings_location: resolver.typings_location.clone(),
            project_name: resolver.project_name.clone(),
            extra_extensions: resolver.extra_extensions.clone(),
            current_directory: resolver.host.get_current_directory().to_string(),
        });
        #[cfg(feature = "diagnostics")]
        let previous_keys = host
            .previous_keys
            .as_ref()
            .map_or((0, 0), |keys| (keys.len() - keys.scopes(), keys.scopes()));
        let keys = host
            .previous_keys
            .as_deref()
            .map(KeyList::with_capacity_of)
            .unwrap_or_default();
        let previous = host.previous_keys.filter(|keys| !keys.is_empty());
        let first_load = first_load && previous.is_none();
        let job = previous
            .or_else(|| first_load.then(|| Arc::new(KeyList::default())))
            .and_then(|keys| {
                Workers::get()?.post(Job {
                    queue: Arc::new(AheadQueue::new(keys)),
                    // `post` sets the epoch and the known files.
                    epoch: 0,
                    known_files: Arc::default(),
                    closed: AtomicBool::new(false),
                    left: AtomicUsize::new(0),
                    answers: Arc::new(SharedResolutionCache::default()),
                    view: host.view,
                    stats: Arc::default(),
                    config: config.clone(),
                    #[cfg(feature = "diagnostics")]
                    inject_panic: INJECT_PANIC.with(Cell::get),
                    #[cfg(all(test, not(feature = "diagnostics")))]
                    inject_panic: false,
                    os_view: std::sync::Mutex::new(crate::frontend::vfs::loadview::current()),
                })
            });
        let first_load = first_load && job.is_some();
        // Loader v2 L1: under the load's OS view, a keyed load checks its
        // answers as a first load does: every lookup that the snapshot's
        // lookup cache has no answer for is made through it (a view hit),
        // and the job's lookups are not attached. The cache then holds a
        // serial load's lookups, whichever answers arrived in time.
        let validated =
            job.is_some() && (first_load || crate::frontend::vfs::loadview::current().is_some());
        if let Some(job) = &job {
            if validated {
                (host.first_load)();
            } else {
                (host.attach)(job.stats.clone());
            }
            host.share.epoch.set(Some(job.epoch));
        }
        #[cfg(any(debug_assertions, feature = "diagnostics"))]
        let accept = resolve_ahead_verify::checked_accept(host.accept, host.scratch, config);
        #[cfg(not(any(debug_assertions, feature = "diagnostics")))]
        let accept = host.accept;
        *resolver.caches.ahead.borrow_mut() = Some(AheadLink {
            answers: job.as_ref().map(|job| job.answers.clone()),
            accept,
            keys: RefCell::new(keys),
            queue: job.as_ref().map(|job| job.queue.clone()),
            cursor: Cell::new(0),
            rejected: Cell::new(false),
            stats: Cell::new(AheadStats::default()),
            scopes: RefCell::default(),
            #[cfg(feature = "diagnostics")]
            missing: RefCell::default(),
            #[cfg(feature = "diagnostics")]
            log_missing: diag::debug_log_enabled(),
            // Every load adopts the package.json lookups of the answers it
            // takes (keyed loads too): the loader's package.json cache then
            // holds a serial load's entries whichever answers arrived in
            // time.
            package_json_text: Some(host.package_json_text),
            adopted: RefCell::default(),
        });
        // Tests (diagnostics): a first-load job has no keys: its parse
        // workers make the answers, and `FilesParser::parse` waits for them
        // (`after_first_load_workers`).
        #[cfg(feature = "diagnostics")]
        if mode() == Mode::Force
            && !first_load
            && let Some((job, workers)) = job.as_ref().zip(Workers::get())
        {
            workers.wait_left(job);
            if let Some(hook) = AFTER_WORKERS.with(|after| after.borrow().clone()) {
                hook();
            }
        }
        ResolveAhead {
            job,
            first_load,
            validated,
            keep_keys: Some(host.keep_keys),
            #[cfg(feature = "diagnostics")]
            previous_keys,
        }
    }

    /// The first-load job of this load, for its parse workers. `None` when
    /// the load has keys of a previous load or no job.
    #[must_use]
    pub fn first_load_job(&self) -> Option<FirstLoadJob> {
        self.job
            .as_ref()
            .filter(|_| self.first_load)
            .map(|job| FirstLoadJob(job.clone()))
    }

    /// Ends resolve ahead after the load of `resolver`: stops the workers,
    /// unlinks `resolver` from their answers and gives the keys of the
    /// load to the host. The workers free the answers and their caches.
    pub fn finish(mut self, resolver: &DefaultResolver) {
        let link = resolver.caches.ahead.borrow_mut().take();
        let Some(mut link) = link else {
            self.end_job(None, true);
            return;
        };
        #[cfg(feature = "diagnostics")]
        let loader = link.stats.get();
        #[cfg(feature = "diagnostics")]
        let (known_files, worker_panic) = diag::before_end(&link, self.job.as_deref());
        // A rejected answer can come from a kept package.json parse or a
        // known file that changed: the workers drop both now, so the next
        // job starts with neither, even when a load on another thread has
        // the workers first.
        let rejected = link.rejected.get();
        if rejected && let Some((job, workers)) = self.job.as_ref().zip(Workers::started()) {
            workers.forget(job.epoch);
        }
        // The workers free the answers and the queue with the job.
        // S1a: a first-load job keeps no known files for the next job. The
        // check of a known answer asks the snapshot's lookup cache, which
        // stores the answer; the next load then stores lookups that the
        // same load after a first load with no job would not store
        // (`GOPORT_LOAD_DUMP`, umami edit session). The next load starts as
        // it would after a first load with no job.
        self.end_job(
            Some(Box::new((link.answers.take(), link.queue.take()))),
            rejected || self.validated,
        );
        #[cfg(feature = "diagnostics")]
        if worker_panic {
            diag::debug_log(format_args!(
                "resolve-ahead: a worker panicked; the load resolved its other keys itself"
            ));
        }
        let Some(keep_keys) = self.keep_keys.take() else {
            return;
        };
        let keys = Arc::new(link.keys.into_inner());
        #[cfg(feature = "diagnostics")]
        diag::note_load(LoadStats {
            keys: self.previous_keys.0,
            new_keys: keys.len() - keys.scopes(),
            scopes: self.previous_keys.1,
            new_scopes: keys.scopes(),
            loader,
            known_files,
            worker_panic,
            first_load: self.first_load,
        });
        keep_keys(keys);
    }

    /// Stops the workers of this load and gives its job and `shared`, the
    /// loader's links to it, to them to free. Unless the loader `rejected`
    /// an answer, the workers keep the files that the job's answers found
    /// for the next job (`WorkerState::known_files`).
    fn end_job(&mut self, shared: Option<Box<dyn Send>>, rejected: bool) {
        let Some(job) = self.job.take() else {
            return;
        };
        if let Some(workers) = Workers::get() {
            workers.end(&job);
            workers.free(EndedJob {
                job,
                shared,
                keep_known_files: !rejected,
            });
        }
    }
}

impl Drop for ResolveAhead {
    /// A load that ends with a panic stops its workers too.
    fn drop(&mut self) {
        self.end_job(None, true);
    }
}

/// S1a: the resolve-ahead job of a language server's first program load,
/// which has no keys of a previous load. Its keys are not queued: the
/// load's parse workers (compiler/files_parser.rs) resolve the imports of
/// each file they parse with a resolver of the job (`worker_resolver`), on
/// the job's view of the host's file system (`AheadFs`), and publish each
/// answer with the calls that made it. The loader finds an answer by its
/// key and takes it only after its host's check passes
/// (`ResolveAheadHost::accept`), as for any resolve-ahead answer: the check
/// makes the snapshot's own read of each file the answer read, at the
/// moment the loader's own resolution would make it, compares the worker's
/// hash, and replays each call's effects (seen files, missing directories,
/// the lookup cache). An answer that the loader does not take leaves no
/// trace in the snapshot: the snapshot does not keep the job's lookups
/// (`ResolveAheadHost::first_load`).
// PORT: not in Go (perf). Go resolves in each parse task.
#[derive(Clone)]
pub struct FirstLoadJob(Arc<Job>);

impl FirstLoadJob {
    /// True when both are the same job.
    #[must_use]
    pub fn same(&self, other: &FirstLoadJob) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }

    /// Makes this thread (a parse worker) a resolve-ahead worker of the job
    /// (`begin_ahead_thread`), and gives its resolver: the loader
    /// resolver's options on the job's view, publishing to the job's
    /// answers. The worker calls `end_worker` when it ends.
    #[must_use]
    pub fn worker_resolver(&self) -> DefaultResolver {
        let job = &self.0;
        let view = &job.view;
        let fs = begin_ahead_fs(job, FxHashMap::default(), FxHashSet::default());
        let package_jsons = Rc::new(new_info_cache(
            &view.current_directory,
            view.use_case_sensitive_file_names,
        ));
        let fs: Rc<dyn Fs> = fs;
        let mut resolver = job.config.new_resolver(fs, Some(package_jsons));
        resolver.caches.shared = Some(SharedResolutionLink {
            cache: job.answers.clone(),
            publish: true,
        });
        resolver
    }

    /// Ends `worker_resolver` on this thread.
    pub fn end_worker() {
        drop(end_ahead_thread());
    }
}

/// The number of workers: the parse threads of a program that is not
/// large, less the loading thread (`GOPORT_RESOLVE_AHEAD_THREADS` sets it).
/// 0 on wasm, which has one thread.
fn worker_count() -> usize {
    if cfg!(target_family = "wasm") {
        return 0;
    }
    crate::tuning::resolve_ahead_threads().unwrap_or_else(|| {
        crate::program::ThreadBudget::current()
            .parse_threads(false)
            .saturating_sub(1)
    })
}

/// The workers of the process, once a load started them
/// (`Workers::get`).
static WORKERS: std::sync::OnceLock<Option<&'static Workers>> = std::sync::OnceLock::new();

/// Drops `value` on a resolve-ahead worker when a load started the workers
/// (they wait between loads), else here. The language server's loads take
/// most answers from the workers; the workers then also free a released
/// program's resolutions (`module::Caches::release`), so the dispatch
/// thread does not before its next request.
// PORT: not in Go (Go's garbage collector frees in the background).
pub fn drop_on_worker(value: Box<dyn Send>) {
    match Workers::started() {
        Some(workers) => {
            lock_state(workers).drops.push(value);
            workers.wake.notify_one();
        }
        None => drop(value),
    }
}

/// The resolve-ahead workers of the process. They start at the first load
/// that resolves ahead and stay: between loads they wait on `wake` and
/// use no CPU. One load at a time has them (`post`). After a load they free
/// its data (`free`), which they allocated, so the dispatch thread does not
/// free it before its next request.
struct Workers {
    state: Mutex<WorkerState>,
    /// Wakes the workers for a new job, for data to free or to drop what
    /// they keep (`forget`).
    wake: Condvar,
    /// Wakes a loader that waits for the workers to leave its job
    /// (`wait_left`), and `wait_for_frees`.
    left: Condvar,
    /// The workers that started.
    count: AtomicUsize,
}

#[derive(Default)]
struct WorkerState {
    /// The job of the load that has the workers now.
    job: Option<Arc<Job>>,
    /// Counts the posted jobs, so that a worker runs each job once.
    generation: u64,
    /// The jobs of ended loads, for the workers to free.
    ended: Vec<EndedJob>,
    /// The jobs of ended loads that no worker has freed yet.
    frees: usize,
    /// Other values for the workers to free (`drop_on_worker`).
    drops: Vec<Box<dyn Send>>,
    /// Counts the times the workers dropped what they keep from job to job
    /// (`forget`). A job, the known files and each worker's package.json
    /// parses (`KeptPackageJsons`) belong to one epoch: a job uses only
    /// what its epoch kept.
    epoch: u64,
    /// The files that the answers of the earlier jobs of this epoch found
    /// (`EndedJob::free`): a worker answers `file_exists` for them with no
    /// OS call, and the loader checks those answers
    /// (`AheadCall::FileExists`).
    known_files: Arc<FxHashSet<Path>>,
    /// `hold_workers` (tests, diagnostics): the loads find the workers
    /// taken.
    #[cfg(feature = "diagnostics")]
    held: bool,
}

/// What a worker does next.
enum Task {
    Run(Arc<Job>),
    Free(EndedJob),
    Drop(Box<dyn Send>),
    /// Drop this worker's package.json parses of an earlier epoch.
    Forget,
}

/// The job of an ended load and the loader's links to it, which a worker
/// frees (`Workers::free`).
struct EndedJob {
    job: Arc<Job>,
    shared: Option<Box<dyn Send>>,
    /// Keep the job's known files and the files that its answers found
    /// (`WorkerState::known_files`).
    keep_known_files: bool,
}

impl EndedJob {
    /// Frees the job on this worker, after it keeps the files that the
    /// job's answers found: the known files of the next job, unless the
    /// workers dropped what they keep since the job started (`forget`).
    fn free(self, workers: &Workers) {
        let EndedJob {
            job,
            shared,
            keep_known_files,
        } = self;
        drop(shared);
        // The files of the job's known set and the files that its answers
        // found. A file that the answers did not use stays known: the loader
        // checks each known answer, and a rejection starts a new epoch
        // (`Workers::forget`). Most loads find no new file, and the set is
        // then kept as it is.
        if keep_known_files {
            let mut more: Option<FxHashSet<Path>> = None;
            job.answers.for_each_ahead_calls(|calls| {
                AheadCall::each(calls, &mut |call| {
                    if let AheadCall::FileExists {
                        path, exists: true, ..
                    } = call
                        && !job.known_files.contains(path)
                        && !job.view.open_files.contains(path)
                        && !more.as_ref().is_some_and(|more| more.contains(path))
                    {
                        more.get_or_insert_with(|| (*job.known_files).clone())
                            .insert(path.clone());
                    }
                });
            });
            let known = more.map_or_else(|| job.known_files.clone(), Arc::new);
            let mut state = lock_state(workers);
            if state.epoch == job.epoch {
                // The set it replaces is the job's set or an older one,
                // which the job or a later free drops.
                let older = std::mem::replace(&mut state.known_files, known);
                drop(state);
                drop(older);
            }
        }
        drop(job);
    }
}

fn lock_state(workers: &Workers) -> std::sync::MutexGuard<'_, WorkerState> {
    workers
        .state
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl Workers {
    /// The workers, started at the first call. `None` when none could
    /// start or the count is 0.
    fn get() -> Option<&'static Workers> {
        *WORKERS.get_or_init(|| {
            let count = worker_count();
            if count == 0 {
                return None;
            }
            let workers: &'static Workers = Box::leak(Box::new(Workers {
                state: Mutex::default(),
                wake: Condvar::new(),
                left: Condvar::new(),
                count: AtomicUsize::new(0),
            }));
            for _ in 0..count {
                // A worker that cannot start only makes fewer answers.
                let started = std::thread::Builder::new()
                    .name("goport-resolve".to_string())
                    .stack_size(crate::gostd::stack::max_stack_size())
                    .spawn(move || run_worker(workers));
                if started.is_ok() {
                    workers.count.fetch_add(1, Ordering::Relaxed);
                }
            }
            (workers.count.load(Ordering::Relaxed) > 0).then_some(workers)
        })
    }

    /// The workers when a load started them; never starts them.
    fn started() -> Option<&'static Workers> {
        WORKERS.get().copied().flatten()
    }

    /// Gives `job` to the workers, with the epoch and the known files of
    /// what they keep now. `None` when another load has them now (a load
    /// on another thread): this load then resolves every key itself.
    fn post(&self, mut job: Job) -> Option<Arc<Job>> {
        let mut state = lock_state(self);
        if state.job.is_some() {
            return None;
        }
        #[cfg(feature = "diagnostics")]
        if state.held {
            return None;
        }
        job.epoch = state.epoch;
        job.known_files = state.known_files.clone();
        let job = Arc::new(job);
        state.job = Some(job.clone());
        state.generation += 1;
        drop(state);
        self.wake.notify_all();
        Some(job)
    }

    /// Stops `job`: each worker ends it after its current key. After this, no
    /// worker of the job stores a lookup (`cached`): the snapshot keeps the
    /// job's lookups as answers of the load (`ResolveAheadHost::attach`).
    fn end(&self, job: &Arc<Job>) {
        job.closed.store(true, Ordering::Relaxed);
        // A worker tests `closed` under the shard lock before it stores. A
        // store in a shard now ends before this takes the lock, and a later
        // one sees `closed`.
        job.stats.wait_for_stores();
        // The load's view goes with the load. A worker that still runs a
        // resolution of the job holds its own reference until it returns.
        drop(
            job.os_view
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .take(),
        );
        let mut state = lock_state(self);
        if state
            .job
            .as_ref()
            .is_some_and(|posted| Arc::ptr_eq(posted, job))
        {
            state.job = None;
        }
    }

    /// Gives `ended` to a worker to free.
    fn free(&self, ended: EndedJob) {
        let mut state = lock_state(self);
        state.ended.push(ended);
        state.frees += 1;
        drop(state);
        self.wake.notify_one();
    }

    /// Drops what the workers keep from job to job, if it is still of
    /// `epoch`: the known files now (a worker frees them), and each
    /// worker's package.json parses when it wakes (`Task::Forget`). The
    /// next job starts a new epoch with neither. It acts at once, also
    /// while a load on another thread has the workers.
    fn forget(&self, epoch: u64) {
        let mut state = lock_state(self);
        if state.epoch != epoch {
            return;
        }
        state.epoch += 1;
        let known_files = std::mem::take(&mut state.known_files);
        state.drops.push(Box::new(known_files));
        drop(state);
        self.wake.notify_all();
    }

    /// Waits until every worker has left `job` (`Mode::Force`, diagnostics).
    #[cfg(feature = "diagnostics")]
    fn wait_left(&self, job: &Job) {
        let count = self.count.load(Ordering::Relaxed);
        let mut state = lock_state(self);
        while job.left.load(Ordering::Acquire) < count {
            state = self
                .left
                .wait(state)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
        }
    }
}

/// Tests (diagnostics): while held (`hold_in_job`), a worker that starts a
/// job waits there, holding the job and its view, before it takes a key.
#[cfg(feature = "diagnostics")]
static HOLD_IN_JOB: (std::sync::Mutex<bool>, std::sync::Condvar) =
    (std::sync::Mutex::new(false), std::sync::Condvar::new());

/// Tests (diagnostics): holds the workers that start a job (`on`), or
/// lets them go.
#[cfg(feature = "diagnostics")]
pub fn hold_in_job(on: bool) {
    *HOLD_IN_JOB
        .0
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = on;
    HOLD_IN_JOB.1.notify_all();
}

/// Tests (diagnostics): the jobs that held workers wait in (one entry per
/// waiting worker, by the job's address).
#[cfg(feature = "diagnostics")]
static HELD_JOBS: std::sync::Mutex<Vec<usize>> = std::sync::Mutex::new(Vec::new());

/// Tests (diagnostics): the distinct jobs with a worker held in them
/// (`hold_in_job`); each such worker holds its load's view.
#[cfg(feature = "diagnostics")]
#[must_use]
pub fn jobs_held() -> usize {
    let mut jobs = HELD_JOBS
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone();
    jobs.sort_unstable();
    jobs.dedup();
    jobs.len()
}

#[cfg(feature = "diagnostics")]
fn diag_hold_in_job(job: &Arc<Job>) {
    let id = Arc::as_ptr(job) as usize;
    let mut held = HOLD_IN_JOB
        .0
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if !*held {
        return;
    }
    HELD_JOBS
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .push(id);
    while *held {
        held = HOLD_IN_JOB
            .1
            .wait(held)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
    }
    let mut jobs = HELD_JOBS
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(at) = jobs.iter().position(|&held| held == id) {
        jobs.swap_remove(at);
    }
}

/// The ended loads whose data may wait behind new jobs (`run_worker`).
const MAX_ENDED: usize = 4;

/// A worker: runs each posted job once and frees the data of ended loads.
/// A new job goes before the data to free, unless `MAX_ENDED` ended loads
/// wait.
fn run_worker(workers: &'static Workers) {
    let mut ran = 0;
    loop {
        let task = {
            let mut state = lock_state(workers);
            loop {
                // Bounded retention: past `MAX_ENDED` ended loads, their
                // frees go before a new job.
                if state.ended.len() >= MAX_ENDED
                    && let Some(ended) = state.ended.pop()
                {
                    break Task::Free(ended);
                }
                if state.generation != ran
                    && let Some(job) = &state.job
                {
                    ran = state.generation;
                    break Task::Run(job.clone());
                }
                if KeptPackageJsons::epoch().is_some_and(|epoch| epoch != state.epoch) {
                    break Task::Forget;
                }
                if let Some(ended) = state.ended.pop() {
                    break Task::Free(ended);
                }
                if let Some(value) = state.drops.pop() {
                    break Task::Drop(value);
                }
                state = workers
                    .wake
                    .wait(state)
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
            }
        };
        run_task(workers, task);
    }
}

/// Runs `task` on a worker. A panic outside the `go_recover` of the
/// resolutions (a port bug) would end the thread: a loader that waits for
/// the workers to leave its job (`Mode::Force`) would then wait forever,
/// and the pool would have one worker less with no word. The worker
/// catches it and stays in the pool. A job that panicked fails: its loader
/// resolves the rest of its keys itself, and the other workers take no more
/// keys (`AheadQueue::fail`). The panic hook of the bin prints the panic as
/// for any other, and the debug log (`GOPORT_RESOLVE_AHEAD_STATS`) has a
/// line for it.
///
/// The panic does not close the job (`Job::closed`): only `Workers::end`
/// does, after the load. The loader can still take an answer that a worker
/// published before it saw the failure, and the check of that answer
/// passes a lookup that the snapshot has no answer for, since the snapshot
/// takes the job's stored lookup (`ResolveAheadHost::attach`). A closed
/// job stores no lookup, so a close here let the loader take an answer
/// whose lookups were not stored, and then ask the OS again for them.
// PORT: not in Go (perf).
fn run_task(workers: &Workers, task: Task) {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    match task {
        Task::Run(job) => {
            if let Err(payload) = catch_unwind(AssertUnwindSafe(|| run_job(&job))) {
                job.queue.fail();
                // The thread's resolve-ahead state holds the job.
                drop(end_ahead_thread());
                log_panic("job", payload.as_ref());
            }
            job.left.fetch_add(1, Ordering::Release);
            drop(job);
            let _state = lock_state(workers);
            workers.left.notify_all();
        }
        Task::Free(ended) => {
            if let Err(payload) = catch_unwind(AssertUnwindSafe(|| ended.free(workers))) {
                log_panic("free", payload.as_ref());
            }
            let mut state = lock_state(workers);
            state.frees -= 1;
            if state.frees == 0 {
                workers.left.notify_all();
            }
        }
        Task::Drop(value) => {
            if let Err(payload) = catch_unwind(AssertUnwindSafe(|| drop(value))) {
                log_panic("drop", payload.as_ref());
            }
        }
        Task::Forget => {
            let kept = KEPT.with(|kept| kept.borrow_mut().take());
            if let Err(payload) = catch_unwind(AssertUnwindSafe(|| drop(kept))) {
                log_panic("forget", payload.as_ref());
            }
        }
    }
}

/// A worker panic that `run_task` caught: the bin's panic hook printed it;
/// the debug log (diagnostics, `GOPORT_RESOLVE_AHEAD_STATS`) has a line
/// for it.
#[inline]
fn log_panic(task: &str, payload: &(dyn std::any::Any + Send)) {
    #[cfg(feature = "diagnostics")]
    diag::log_panic(task, payload);
    #[cfg(not(feature = "diagnostics"))]
    let _ = (task, payload);
}

/// The workers' part of one load.
struct Job {
    /// The keys of the previous load, in its order.
    queue: Arc<AheadQueue>,
    /// The epoch of what the workers keep when the job was posted
    /// (`WorkerState::epoch`).
    epoch: u64,
    /// The files that earlier jobs of the epoch found
    /// (`WorkerState::known_files`).
    known_files: Arc<FxHashSet<Path>>,
    /// The load ended (`Workers::end`, the only writer): the workers take no
    /// more keys and store no more lookups (`cached`).
    closed: AtomicBool,
    /// The workers that left this job.
    left: AtomicUsize,
    answers: Arc<SharedResolutionCache>,
    view: WorkerView,
    /// The OS lookups of the workers, as the host's per-snapshot cache
    /// keeps the loader's.
    stats: Arc<WorkerStats>,
    config: Arc<ResolverConfig>,
    /// `inject_worker_panic` (tests): each worker panics in the job,
    /// outside its resolutions, after it resolved its keys.
    #[cfg(any(test, feature = "diagnostics"))]
    inject_panic: bool,
    /// Loader v2 L1: the per-load OS view of the job's load
    /// (`vfs::loadview`), which the workers read through and fill.
    /// The load ends with it (`Workers::end`): a worker that still runs a
    /// resolution holds its own reference until that returns.
    os_view: std::sync::Mutex<Option<Arc<crate::frontend::vfs::loadview::LoadView>>>,
}

impl Job {
    /// A worker's OS file system for this job: the OS file system of its
    /// thread, under the load's OS view when there is one.
    fn worker_os(&self) -> Rc<dyn Fs> {
        let view = self
            .os_view
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        crate::frontend::vfs::loadview::worker_fs(view.as_ref())
    }
}

/// A worker's file system for `job` (`AheadFs`), after it makes this thread
/// an ahead thread of the job (`begin_ahead_thread`) with the kept
/// package.json `reads` and `kept` files.
fn begin_ahead_fs(
    job: &Arc<Job>,
    reads: FxHashMap<String, Option<u128>>,
    kept: FxHashSet<String>,
) -> Rc<AheadFs> {
    let view = &job.view;
    let fs = Rc::new(AheadFs {
        os: job.worker_os(),
        job: job.clone(),
    });
    let directory_exists = {
        let fs = fs.clone();
        Rc::new(move |path: &str| fs.directory_exists_unlogged(path)) as Rc<dyn Fn(&str) -> bool>
    };
    begin_ahead_thread(
        &view.current_directory,
        view.use_case_sensitive_file_names,
        reads,
        kept,
        directory_exists,
    );
    fs
}

/// A worker's part of `job`: resolves the next key until none is left, the
/// job ends or a worker failed it (`run_task`). A resolution that panics (a Go panic) panics on the loader
/// too when it resolves the same key; the worker ends that key with no
/// answer and leaves the job. A panic outside the resolutions fails the
/// job (`run_task`). The worker's resolver and its caches are freed here,
/// on the worker.
fn run_job(job: &Arc<Job>) {
    let view = &job.view;
    let (package_jsons, reads, kept) = KeptPackageJsons::take(job);
    let fs = begin_ahead_fs(job, reads, kept);
    #[cfg(feature = "diagnostics")]
    diag_hold_in_job(job);
    let fs: Rc<dyn Fs> = fs;
    let mut resolver = job.config.new_resolver(fs, Some(package_jsons.clone()));
    resolver.caches.shared = Some(SharedResolutionLink {
        cache: job.answers.clone(),
        publish: true,
    });
    let current = Cell::new(None);
    let _ = crate::core::go_recover(|| {
        while !job.closed.load(Ordering::Relaxed) && !job.queue.failed() {
            let Some((index, (containing_directory, module_name, mode))) = job.queue.take_next()
            else {
                break;
            };
            current.set(Some(index));
            if module_name.is_empty() {
                // A package scope key (`KeyList`).
                resolver.publish_package_scope(containing_directory);
            } else {
                let _ = resolver.resolve_module_name_from_directory(
                    module_name,
                    containing_directory,
                    mode,
                );
            }
            job.queue.done(index);
            current.set(None);
        }
    });
    if let Some(index) = current.get() {
        job.queue.done(index);
    }
    #[cfg(any(test, feature = "diagnostics"))]
    if job.inject_panic {
        panic!("resolve ahead: a worker panic outside a resolution (inject_worker_panic)");
    }
    let reads = end_ahead_thread();
    drop(resolver);
    KEPT.with(|kept| {
        *kept.borrow_mut() = Some(KeptPackageJsons {
            epoch: job.epoch,
            current_directory: view.current_directory.clone(),
            use_case_sensitive_file_names: view.use_case_sensitive_file_names,
            cache: package_jsons,
            reads,
        });
    });
}

thread_local! {
    /// A worker's package.json cache from its last job.
    static KEPT: RefCell<Option<KeptPackageJsons>> = const { RefCell::new(None) };
}

/// A worker's package.json cache, kept from job to job, so a worker does
/// not parse the same package.json files at every load. The loader checks
/// each logged read by the hash of its text on the snapshot file system,
/// so it never takes an answer from a parse whose file changed: it
/// rejects the answer, and the workers drop their kept parses
/// (`Workers::forget`). Only parses of files that the worker
/// read are kept: a directory or package.json that was missing can be
/// there now, and the loader does not check that.
// PORT: not in Go (perf).
struct KeptPackageJsons {
    /// The epoch of the job that kept it (`WorkerState::epoch`). A job of
    /// another epoch does not use it.
    epoch: u64,
    current_directory: String,
    use_case_sensitive_file_names: bool,
    cache: Rc<InfoCache>,
    /// The hash of each file that the worker read, by name
    /// (`begin_ahead_thread`).
    reads: FxHashMap<String, Option<u128>>,
}

impl KeptPackageJsons {
    /// The epoch of this worker's kept parses, if it has any.
    fn epoch() -> Option<u64> {
        KEPT.with(|kept| kept.borrow().as_ref().map(|kept| kept.epoch))
    }

    /// The package.json cache, read hashes and kept file names for `job`
    /// on this worker: the kept entries of files that the worker read, or
    /// none.
    fn take(
        job: &Job,
    ) -> (
        Rc<InfoCache>,
        FxHashMap<String, Option<u128>>,
        FxHashSet<String>,
    ) {
        let view = &job.view;
        let cache = Rc::new(new_info_cache(
            &view.current_directory,
            view.use_case_sensitive_file_names,
        ));
        let mut reads = FxHashMap::default();
        let mut kept_files = FxHashSet::default();
        let kept = KEPT.with(|kept| kept.borrow_mut().take()).filter(|kept| {
            kept.epoch == job.epoch
                && kept.current_directory == view.current_directory
                && kept.use_case_sensitive_file_names == view.use_case_sensitive_file_names
        });
        if let Some(mut kept) = kept {
            kept.cache.range(|_, entry| {
                if entry.directory_exists && entry.contents.is_some() {
                    let file_name = combine_paths(&entry.package_directory, &["package.json"]);
                    if let Some((file_name, Some(hash))) = kept.reads.remove_entry(&file_name) {
                        cache.set(&file_name, entry.clone());
                        kept_files.insert(file_name.clone());
                        reads.insert(file_name, Some(hash));
                    }
                }
                true
            });
        }
        (cache, reads, kept_files)
    }
}

/// The OS lookups of a job's workers (`Job::stats`), as `StatCache`
/// (files_parser.rs) keeps them, in shards by name, so the workers seldom
/// wait for each other's lock. The workers of the job have one answer for
/// each name. A `directory_exists` or `realpath` answer comes with its call
/// (`AheadCall::Shared`), which the answers of the job share: the loader
/// checks it once per load. The host's snapshot keeps the lookups after the
/// load (`AheadLookups`).
#[derive(Default)]
struct WorkerStats {
    file_exists: [Mutex<FxHashMap<String, bool>>; STAT_SHARDS],
    /// The answer, and its call when the name is its path (`AheadCall`).
    directory_exists: [Mutex<FxHashMap<String, (bool, Option<Arc<AheadCall>>)>>; STAT_SHARDS],
    realpath: [Mutex<FxHashMap<String, (String, Arc<AheadCall>)>>; STAT_SHARDS],
}

const STAT_SHARDS: usize = 16;

impl WorkerStats {
    /// `closed` is the job's flag (`Job::closed`): a closed job stores no
    /// lookup (`cached`).
    fn file_exists(&self, closed: &AtomicBool, path: &str, load: impl FnOnce() -> bool) -> bool {
        cached(&self.file_exists, closed, path, load)
    }

    fn directory_exists(
        &self,
        closed: &AtomicBool,
        path: &str,
        load: impl FnOnce() -> (bool, Option<Arc<AheadCall>>),
    ) -> (bool, Option<Arc<AheadCall>>) {
        cached(&self.directory_exists, closed, path, load)
    }

    fn realpath(
        &self,
        closed: &AtomicBool,
        path: &str,
        load: impl FnOnce() -> (String, Arc<AheadCall>),
    ) -> (String, Arc<AheadCall>) {
        cached(&self.realpath, closed, path, load)
    }

    /// Takes and frees each shard lock once (`Workers::end`).
    fn wait_for_stores(&self) {
        fn each<V>(shards: &[Mutex<FxHashMap<String, V>>; STAT_SHARDS]) {
            for shard in shards {
                drop(shard.lock());
            }
        }
        each(&self.file_exists);
        each(&self.directory_exists);
        each(&self.realpath);
    }
}

/// The lookup answers of a resolve-ahead job (`WorkerStats`), which the
/// workers got during the job's load. The host's snapshot file system takes
/// an answer from them before it asks the OS
/// (`ResolveAheadHost::attach`).
pub trait AheadLookups: Send + Sync {
    fn file_exists(&self, name: &str) -> Option<bool>;
    fn directory_exists(&self, name: &str) -> Option<bool>;
    fn realpath(&self, name: &str) -> Option<String>;
}

impl AheadLookups for WorkerStats {
    fn file_exists(&self, name: &str) -> Option<bool> {
        lock_shard(&self.file_exists, name).get(name).copied()
    }

    fn directory_exists(&self, name: &str) -> Option<bool> {
        lock_shard(&self.directory_exists, name)
            .get(name)
            .map(|(exists, _)| *exists)
    }

    fn realpath(&self, name: &str) -> Option<String> {
        lock_shard(&self.realpath, name)
            .get(name)
            .map(|(real, _)| real.clone())
    }
}

/// The locked shard of `path`.
fn lock_shard<'s, V>(
    shards: &'s [Mutex<FxHashMap<String, V>>; STAT_SHARDS],
    path: &str,
) -> std::sync::MutexGuard<'s, FxHashMap<String, V>> {
    use std::hash::BuildHasher;
    let hash = rustc_hash::FxBuildHasher.hash_one(path);
    shards[(hash >> 32) as usize % STAT_SHARDS]
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// The cached value of `path` in its shard, or `load()` stored as it. The
/// lock is not held while `load` runs; the first stored value wins. When the
/// job has ended (`closed`), `load()` is not stored: the snapshot keeps the
/// job's lookups as answers of its load, and an answer that a worker gets
/// after the load is not one of them.
fn cached<V: Clone>(
    shards: &[Mutex<FxHashMap<String, V>>; STAT_SHARDS],
    closed: &AtomicBool,
    path: &str,
    load: impl FnOnce() -> V,
) -> V {
    if let Some(value) = lock_shard(shards, path).get(path) {
        return value.clone();
    }
    let value = load();
    let mut shard = lock_shard(shards, path);
    // Under the shard lock (`Workers::end`).
    if closed.load(Ordering::Relaxed) {
        return shard.get(path).cloned().unwrap_or(value);
    }
    shard.entry(path.to_string()).or_insert(value).clone()
}

/// Go `module.ResolutionHost` of a worker resolver.
struct AheadResolutionHost {
    fs: Rc<dyn Fs>,
    current_directory: String,
}

impl ResolutionHost for AheadResolutionHost {
    fn fs(&self) -> &dyn Fs {
        &*self.fs
    }

    fn get_current_directory(&self) -> &str {
        &self.current_directory
    }
}

/// A worker's file system: the OS file system of its thread with the open
/// files of the host over it (project/overlayfs.rs `OverlayFS`), and the
/// workers' stat cache. It logs each call (`note_ahead_call`). A call that
/// the loader cannot check makes the answer unshareable: a directory
/// listing, a stat, a write, a read of an open file, or a `file_exists` or
/// `directory_exists` of a name that is not its path (`AheadCall`).
struct AheadFs {
    os: Rc<dyn Fs>,
    job: Arc<Job>,
}

impl AheadFs {
    /// `directory_exists` with no log: whether a kept package.json cache
    /// entry's directory is still there (`begin_ahead_thread`).
    fn directory_exists_unlogged(&self, path: &str) -> bool {
        self.directory_lookup(path).0
    }

    /// Whether directory `path` exists, as `overlayFS.DirectoryExists`
    /// answers it, and the call to log: `None` when the name is not its
    /// path (`AheadCall`). An answer of the OS is the job's shared call.
    fn directory_lookup(&self, path: &str) -> (bool, Option<AheadCall>) {
        let canonical = self.path(path);
        let view = &self.job.view;
        let open = view.open_directories.contains(&canonical);
        if open || view.open_files.contains(&canonical) {
            let call = (canonical.as_str() == path).then_some(AheadCall::DirectoryExists {
                path: canonical,
                exists: open,
            });
            return (open, call);
        }
        let (exists, call) = self.job.stats.directory_exists(&self.job.closed, path, || {
            let exists = self.os.directory_exists(path);
            let call = (canonical.as_str() == path).then(|| {
                Arc::new(AheadCall::DirectoryExists {
                    path: canonical,
                    exists,
                })
            });
            (exists, call)
        });
        (exists, call.map(AheadCall::Shared))
    }

    fn path(&self, name: &str) -> Path {
        let view = &self.job.view;
        to_path(
            name,
            &view.current_directory,
            view.use_case_sensitive_file_names,
        )
    }

    /// Logs `call`, a call of `name` whose path is `path`, or makes the
    /// answer unshareable when they differ: the loader's caches find a
    /// call by its name and its side effects by its path.
    fn note_call(name: &str, path: Path, call: impl FnOnce(Path) -> AheadCall) {
        if path.as_str() == name {
            note_ahead_call(call(path));
        } else {
            note_ahead_unshareable(None);
        }
    }
}

impl Fs for AheadFs {
    fn use_case_sensitive_file_names(&self) -> bool {
        self.os.use_case_sensitive_file_names()
    }

    // Go: project/overlayfs.go:271 overlayFS.FileExists
    // PORT: a file that earlier jobs found is known to exist with no OS
    // call; the loader checks that answer (`AheadCall::FileExists`).
    fn file_exists(&self, path: &str) -> bool {
        let canonical = self.path(path);
        let view = &self.job.view;
        let (exists, known) = if view.open_files.contains(&canonical) {
            (true, false)
        } else if view.open_directories.contains(&canonical) {
            (false, false)
        } else if self.job.known_files.contains(&canonical) {
            (true, true)
        } else {
            let exists = self
                .job
                .stats
                .file_exists(&self.job.closed, path, || self.os.file_exists(path));
            (exists, false)
        };
        AheadFs::note_call(path, canonical, |path| AheadCall::FileExists {
            path,
            exists,
            known,
        });
        exists
    }

    // Go: project/overlayfs.go:280 overlayFS.ReadFile
    // PORT: a failed read of a file that the workers do not know from an
    // earlier job (no read permission, or a file that went away after its
    // `file_exists`) makes the answer unshareable: the loader resolves the
    // key itself and gets Go's answer. Nothing that the workers keep made
    // the answer, so they keep it (a rejection drops it, `Workers::forget`),
    // and a package.json that no read can open does not drop it at each
    // load. A failed read of a known file is logged, and the check rejects
    // it (`AheadCall::Read`): the file may be gone, and the workers then
    // drop the known files.
    fn read_file(&self, path: &str) -> (String, bool) {
        let canonical = self.path(path);
        let view = &self.job.view;
        if view.open_files.contains(&canonical) {
            // The workers do not have the text of an open file.
            note_ahead_unshareable(Some(path));
            return (String::new(), false);
        }
        let (text, ok) = if view.open_directories.contains(&canonical) {
            (String::new(), false)
        } else {
            self.os.read_file(path)
        };
        if !ok && !self.job.known_files.contains(&canonical) {
            note_ahead_unshareable(Some(path));
            return (text, ok);
        }
        note_ahead_read(path, ok.then(|| xxh3_128(text.as_bytes())));
        (text, ok)
    }

    fn write_file(&self, path: &str, data: &str) -> Result<(), FsError> {
        note_ahead_unshareable(None);
        self.os.write_file(path, data)
    }

    fn append_file(&self, path: &str, data: &str) -> Result<(), FsError> {
        note_ahead_unshareable(None);
        self.os.append_file(path, data)
    }

    fn remove(&self, path: &str) -> Result<(), FsError> {
        note_ahead_unshareable(None);
        self.os.remove(path)
    }

    fn chtimes(
        &self,
        path: &str,
        a_time: Option<std::time::SystemTime>,
        m_time: Option<std::time::SystemTime>,
    ) -> Result<(), FsError> {
        note_ahead_unshareable(None);
        self.os.chtimes(path, a_time, m_time)
    }

    // Go: project/overlayfs.go:299 overlayFS.DirectoryExists
    fn directory_exists(&self, path: &str) -> bool {
        let (exists, call) = self.directory_lookup(path);
        match call {
            Some(call) => note_ahead_call(call),
            None => note_ahead_unshareable(None),
        }
        exists
    }

    // The loader's directory entries merge cached and open files
    // (project/snapshotfs.rs `GetAccessibleEntries`).
    fn get_accessible_entries(&self, path: &str) -> Entries {
        note_ahead_unshareable(None);
        self.os.get_accessible_entries(path)
    }

    fn stat(&self, path: &str) -> Option<FileInfo> {
        note_ahead_unshareable(None);
        self.os.stat(path)
    }

    // Go: project/overlayfs.go:362 overlayFS.Realpath
    fn realpath(&self, path: &str) -> String {
        let (real, call) = self.job.stats.realpath(&self.job.closed, path, || {
            let real = self.os.realpath(path);
            let call = Arc::new(AheadCall::Realpath {
                name: path.to_string(),
                real: real.clone(),
            });
            (real, call)
        });
        note_ahead_call(AheadCall::Shared(call));
        real
    }
}

/// Debug and `diagnostics` builds: checks each answer that a load takes
/// against a resolution of its key on a new tracking view of the host's
/// file system (`ResolveAheadHost::scratch`, which project/compilerhost.rs
/// makes). A release build without `diagnostics` has none of it.
#[cfg(any(debug_assertions, feature = "diagnostics"))]
pub mod resolve_ahead_verify {
    use super::*;

    /// A view of a host's file system that tracks its seen files and missing
    /// directories in new sets (`ResolveAheadHost::scratch`).
    pub struct ScratchFs {
        pub fs: Rc<dyn Fs>,
        /// The seen files and missing directories of `fs` so far.
        pub tracked: Box<dyn Fn() -> (FxHashSet<Path>, FxHashSet<Path>)>,
        /// The host's `to_path`.
        pub to_path: Rc<dyn Fn(&str) -> Path>,
    }

    /// `accept`, which also checks each taken answer on a `scratch` view
    /// (`check_answer`).
    pub(super) fn checked_accept(
        accept: AheadAccept,
        scratch: Rc<dyn Fn() -> ScratchFs>,
        config: Arc<ResolverConfig>,
    ) -> AheadAccept {
        Rc::new(move |answer: AheadAnswer<'_>, calls: &[AheadCall]| {
            let accepted = accept(answer, calls);
            if accepted {
                check_answer(&config, &scratch(), answer, calls);
            }
            accepted
        }) as AheadAccept
    }

    /// Checks a taken answer against a resolution of its key (a
    /// module key or the package scope of a directory) by a new resolver on a
    /// new tracking view of the host's file system. The answer must be the
    /// same, and the calls must list exactly the files that the view saw and
    /// the directories that it found missing. A new resolver reads every
    /// package.json itself, so this also checks that each answer lists the
    /// calls of the package.json cache entries it read.
    fn check_answer(
        config: &ResolverConfig,
        scratch: &ScratchFs,
        answer: AheadAnswer<'_>,
        calls: &[AheadCall],
    ) {
        let resolver = config.new_resolver(scratch.fs.clone(), None);
        match answer {
            AheadAnswer::Module((containing_directory, module_name, mode, _), value) => {
                let (own, _, _) = resolver.resolve_module_name_from_directory(
                    module_name,
                    containing_directory,
                    mode,
                );
                assert_eq!(
                    describe(&own),
                    describe(value),
                    "resolve ahead: the answer for {answer:?} differs"
                );
            }
            AheadAnswer::Scope(directory, scope) => {
                let own =
                    PackageScope::of(resolver.get_package_scope_for_path(directory).as_deref());
                assert_eq!(
                    own.as_ref(),
                    scope,
                    "resolve ahead: the answer for {answer:?} differs"
                );
            }
        }
        let mut seen = FxHashSet::default();
        let mut missing = FxHashSet::default();
        AheadCall::each(calls, &mut |call| match call {
            AheadCall::FileExists { path, .. } => {
                seen.insert(path.clone());
            }
            AheadCall::DirectoryExists {
                path,
                exists: false,
            } => {
                missing.insert(path.clone());
            }
            AheadCall::Read { file_name, .. } => {
                seen.insert((scratch.to_path)(file_name));
            }
            // `each` gives the calls of a `PackageJson` and a `Shared`.
            AheadCall::DirectoryExists { exists: true, .. }
            | AheadCall::Realpath { .. }
            | AheadCall::PackageJson(_)
            | AheadCall::Shared(_) => {}
        });
        let (own_seen, own_missing) = (scratch.tracked)();
        assert!(
            own_seen == seen && own_missing == missing,
            "resolve ahead: the calls for {answer:?} differ: seen {:?}, logged {:?}; missing {:?}, logged {:?}",
            sorted(&own_seen),
            sorted(&seen),
            sorted(&own_missing),
            sorted(&missing),
        );
    }

    fn sorted(paths: &FxHashSet<Path>) -> Vec<&str> {
        let mut paths: Vec<&str> = paths.iter().map(Path::as_str).collect();
        crate::gostd::slices::stable_sort_by(&mut paths, Ord::cmp);
        paths
    }

    /// The fields of a resolved module, for `check_answer`.
    fn describe(module: &ResolvedModule) -> String {
        format!(
            "{} {} {} {} {} {:?} {} {} {:?}",
            module.resolved_file_name,
            module.original_path,
            module.extension,
            module.resolved_using_ts_extension,
            module.resolved_using_extra_extensions,
            module.package_id,
            module.is_external_library_import,
            module.alternate_result,
            module.resolution_diagnostics,
        )
    }
}

/// Where this crate compiles `resolve_ahead_verify`, the code of another
/// crate that uses it (project/compilerhost.rs makes
/// `ResolveAheadHost::scratch`). That crate cannot test the cfg itself:
/// `debug_assertions` is a setting of each crate, and rustdoc compiles
/// ts_goport's doctests with it on against a release goport_core. So the
/// boundary is here, in the crate that owns the module: `verify_only!`
/// keeps its items or statements where the module is compiled and drops
/// them where it is not.
#[cfg(any(debug_assertions, feature = "diagnostics"))]
#[doc(hidden)]
#[macro_export]
macro_rules! __resolve_ahead_verify_only {
    ($($code:tt)*) => { $($code)* };
}

#[cfg(not(any(debug_assertions, feature = "diagnostics")))]
#[doc(hidden)]
#[macro_export]
macro_rules! __resolve_ahead_verify_only {
    ($($code:tt)*) => {};
}

pub use crate::__resolve_ahead_verify_only as verify_only;

/// A `ResolveAheadHost` literal for another crate (see `verify_only!`): its
/// first field is `scratch`, which the host has where this crate compiles
/// `resolve_ahead_verify`, and which is dropped where it does not.
#[cfg(any(debug_assertions, feature = "diagnostics"))]
#[doc(hidden)]
#[macro_export]
macro_rules! __resolve_ahead_host {
    (scratch: $scratch:expr, $($field:ident $(: $value:expr)?),* $(,)?) => {
        $crate::frontend::compiler::resolve_ahead::ResolveAheadHost {
            scratch: $scratch,
            $($field $(: $value)?),*
        }
    };
}

#[cfg(not(any(debug_assertions, feature = "diagnostics")))]
#[doc(hidden)]
#[macro_export]
macro_rules! __resolve_ahead_host {
    (scratch: $scratch:expr, $($field:ident $(: $value:expr)?),* $(,)?) => {
        $crate::frontend::compiler::resolve_ahead::ResolveAheadHost { $($field $(: $value)?),* }
    };
}

pub use crate::__resolve_ahead_host as host;

/// The debug log of resolve ahead (`GOPORT_RESOLVE_AHEAD_STATS`) and the
/// counts of each load (`last_stats`). Diagnostics only: shipped builds
/// keep neither.
#[cfg(feature = "diagnostics")]
mod diag {
    use super::{AheadLink, Cell, Job, LAST_STATS, LoadStats};

    /// Before the end of a load's job: the known files that its workers
    /// started with and whether a worker panicked, and the debug log of
    /// its missing keys.
    pub(super) fn before_end(link: &AheadLink, job: Option<&Job>) -> (usize, bool) {
        let known_files = job.map_or(0, |job| job.known_files.len());
        let worker_panic = link.queue.as_ref().is_some_and(|queue| queue.failed());
        if debug_log_enabled() {
            log_missing(link, job);
        }
        (known_files, worker_panic)
    }

    /// Keeps the counts of a load for `last_stats` and logs them.
    pub(super) fn note_load(stats: LoadStats) {
        LAST_STATS.with(|last| last.set(Some(stats)));
        debug_log(format_args!("resolve-ahead: {stats:?}"));
    }

    /// Writes `line` (the counts of a load, a worker panic) when
    /// `GOPORT_RESOLVE_AHEAD_STATS` is set: to stderr for `1`, else appended to
    /// the file that it names (`write_debug_line`).
    pub(super) fn debug_log(line: std::fmt::Arguments<'_>) {
        let Some(to) = debug_log_to() else {
            return;
        };
        write_debug_line(to, line);
    }

    fn debug_log_to() -> Option<&'static str> {
        static TO: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();
        TO.get_or_init(|| std::env::var("GOPORT_RESOLVE_AHEAD_STATS").ok())
            .as_deref()
    }

    pub(super) fn debug_log_enabled() -> bool {
        debug_log_to().is_some()
    }

    /// The debug log of the keys that had no worker answer when the loader
    /// asked (`AheadLink::missing`), by why: `late` (a worker published a
    /// shareable answer after the loader asked), `unshareable` (the worker's
    /// resolution made a call that the check cannot make again: a directory
    /// listing, a stat, a read of an open file), `none` (no worker resolved
    /// the key: no worker parsed the file, or it resolved the name with
    /// another mode). A few keys of each with their mode.
    fn log_missing(link: &AheadLink, job: Option<&Job>) {
        let missing = link.missing.borrow();
        if missing.is_empty() {
            return;
        }
        let mut kinds: [(&str, usize, Vec<String>); 3] = [
            ("late", 0, Vec::new()),
            ("unshareable", 0, Vec::new()),
            ("none", 0, Vec::new()),
        ];
        for (directory, name, mode) in missing.iter() {
            let found = job.and_then(|job| {
                job.answers
                    .get_module(&(directory.as_str(), name.as_str(), *mode, ""))
            });
            let kind = match found {
                Some(found) if found.ahead.is_some() => 0,
                Some(_) => 1,
                None => 2,
            };
            let entry = &mut kinds[kind];
            entry.1 += 1;
            if entry.2.len() < 4 {
                entry.2.push(format!("{name:?} {mode:?} from {directory}"));
            }
        }
        let parts: Vec<String> = kinds
            .iter()
            .map(|(kind, count, samples)| format!("{kind} {count} [{}]", samples.join("; ")))
            .collect();
        debug_log(format_args!(
            "resolve-ahead: missing keys: {}",
            parts.join(", ")
        ));
    }

    /// Writes `line` and a newline to stderr (`to` is `1`) or to the end of
    /// file `to`. The workers (`log_panic`) and the loader write at the same
    /// time, so the line is built first and goes out in one write: a write
    /// per part of the format let the lines of two threads interleave. A
    /// failed write is dropped, never a panic: a worker logs a caught panic
    /// before it counts itself out of the job (`run_task`), so a panic here
    /// (`eprintln!` on a closed stderr) made a force load wait forever.
    pub(super) fn write_debug_line(to: &str, line: std::fmt::Arguments<'_>) {
        use std::io::Write;
        let line = format!("{line}\n");
        if to == "1" {
            let _ = std::io::stderr().write_all(line.as_bytes());
        } else if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(to)
        {
            let _ = file.write_all(line.as_bytes());
        }
    }

    /// Writes a worker panic that `run_task` caught to the debug log.
    pub(super) fn log_panic(task: &str, payload: &(dyn std::any::Any + Send)) {
        let message = payload
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
            .unwrap_or_default();
        debug_log(format_args!(
            "resolve-ahead: a worker panicked in a {task} task: {message}"
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::{
        AheadLookups, AheadQueue, Job, KeyList, ResolverConfig, Task, WorkerStats, WorkerView,
        Workers, run_task,
    };
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    /// A pool with no threads, for `run_task` on the test thread.
    fn test_workers() -> Workers {
        Workers {
            state: std::sync::Mutex::default(),
            wake: std::sync::Condvar::new(),
            left: std::sync::Condvar::new(),
            count: AtomicUsize::new(1),
        }
    }

    /// A job of `keys` in a missing directory.
    fn test_job(keys: KeyList, inject_panic: bool) -> Arc<Job> {
        let root = "/goport-resolve-ahead-test-missing";
        Arc::new(Job {
            queue: Arc::new(AheadQueue::new(Arc::new(keys))),
            epoch: 0,
            known_files: Arc::default(),
            closed: AtomicBool::new(false),
            left: AtomicUsize::new(0),
            answers: Arc::default(),
            view: WorkerView {
                current_directory: root.to_string(),
                use_case_sensitive_file_names: true,
                open_files: Default::default(),
                open_directories: Default::default(),
            },
            stats: Arc::default(),
            config: Arc::new(ResolverConfig {
                options: crate::options::CompilerOptions::default(),
                typings_location: String::new(),
                project_name: String::new(),
                extra_extensions: Vec::new(),
                current_directory: root.to_string(),
            }),
            inject_panic,
            os_view: std::sync::Mutex::default(),
        })
    }

    // PORT: not in Go (resolve ahead). R169 reviewer (followups19): a worker
    // panic fails the job but does not close it. The loader can still take
    // an answer that another worker published before it saw the failure,
    // and the check passes the lookups that the snapshot takes from the job
    // (`ResolveAheadHost::attach`), so those lookups must be stored. When
    // the panic closed the job, they were not, and the snapshot asked the
    // OS for them again later in the load.
    #[test]
    fn a_worker_panic_fails_the_job_but_keeps_its_lookups() {
        let workers = test_workers();
        let job = test_job(KeyList::default(), true);
        run_task(&workers, Task::Run(job.clone()));
        assert!(job.queue.failed());
        assert_eq!(job.left.load(Ordering::Relaxed), 1);
        assert!(!job.closed.load(Ordering::Relaxed));
        // Another worker's lookup during the load.
        assert!(job.stats.file_exists(&job.closed, "/p/a.ts", || true));
        assert_eq!(
            AheadLookups::file_exists(&*job.stats, "/p/a.ts"),
            Some(true)
        );
    }

    // PORT: not in Go (resolve ahead). The workers stop at a failed job
    // (`AheadQueue::fail`) without a close: the loader resolves its keys.
    #[test]
    fn a_worker_takes_no_key_of_a_failed_job() {
        let workers = test_workers();
        let mut keys = KeyList::default();
        keys.push("/p", "./a", crate::options::RESOLUTION_MODE_NONE);
        let job = test_job(keys, false);
        job.queue.fail();
        run_task(&workers, Task::Run(job.clone()));
        assert_eq!(job.left.load(Ordering::Relaxed), 1);
        assert_eq!(job.queue.next.load(Ordering::Relaxed), 0);
    }

    // PORT: not in Go (resolve ahead). R167 reviewer (followups15a): a
    // worker of an ended job must not store a lookup, since the snapshot
    // keeps the job's lookups as answers of the load (`Workers::end`). A
    // lookup that another worker stored before the end still answers.
    #[test]
    fn an_ended_job_stores_no_lookup() {
        let stats = WorkerStats::default();
        let closed = AtomicBool::new(false);
        assert!(stats.file_exists(&closed, "/p/a.ts", || true));
        assert!(stats.directory_exists(&closed, "/p", || (true, None)).0);
        closed.store(true, Ordering::Relaxed);
        stats.wait_for_stores();
        assert!(stats.file_exists(&closed, "/p/a.ts", || false));
        assert!(!stats.file_exists(&closed, "/p/b.ts", || false));
        assert!(!stats.directory_exists(&closed, "/q", || (false, None)).0);
        let real = stats.realpath(&closed, "/p/l", || {
            let call = super::AheadCall::Realpath {
                name: "/p/l".to_string(),
                real: "/p/r".to_string(),
            };
            ("/p/r".to_string(), std::sync::Arc::new(call))
        });
        assert_eq!(real.0, "/p/r");
        assert_eq!(AheadLookups::file_exists(&stats, "/p/a.ts"), Some(true));
        assert_eq!(AheadLookups::directory_exists(&stats, "/p"), Some(true));
        assert_eq!(AheadLookups::file_exists(&stats, "/p/b.ts"), None);
        assert_eq!(AheadLookups::directory_exists(&stats, "/q"), None);
        assert_eq!(AheadLookups::realpath(&stats, "/p/l"), None);
    }

    // PORT: not in Go (the resolve-ahead debug log). The workers' panic
    // lines and the loader's counts go to one file at the same time
    // (followups13b skeptic: 1,917 of 12,288 lines garbled).
    #[cfg(feature = "diagnostics")]
    #[test]
    fn debug_log_lines_of_parallel_writers_do_not_interleave() {
        let to = std::env::temp_dir().join(format!(
            "goport-resolve-ahead-log-{}.txt",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&to);
        let to_text = to.to_str().expect("a UTF-8 temp path").to_string();
        std::thread::scope(|scope| {
            for thread in 0..8 {
                let to_text = &to_text;
                scope.spawn(move || {
                    for line in 0..500 {
                        super::diag::write_debug_line(
                            to_text,
                            format_args!(
                                "resolve-ahead: a worker panicked in a {} task: {} {}",
                                "job", thread, line
                            ),
                        );
                    }
                });
            }
        });
        let text = std::fs::read_to_string(&to).expect("the log");
        std::fs::remove_file(&to).expect("remove the log");
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), 8 * 500);
        let bad = lines
            .iter()
            .filter(|line| {
                let Some(rest) =
                    line.strip_prefix("resolve-ahead: a worker panicked in a job task: ")
                else {
                    return true;
                };
                let mut parts = rest.split(' ');
                !(parts
                    .next()
                    .is_some_and(|t| t.parse::<u32>().is_ok_and(|t| t < 8))
                    && parts
                        .next()
                        .is_some_and(|l| l.parse::<u32>().is_ok_and(|l| l < 500))
                    && parts.next().is_none())
            })
            .count();
        assert_eq!(bad, 0, "{bad} lines garbled");
    }
}
