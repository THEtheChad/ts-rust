//! The process's reconciliation threads: one bounded pool (`size` threads,
//! started on first use) that runs the tasks of every session's
//! reconciliation jobs (`reconcile::Job`). A session has at most one job in
//! flight (`reconcile::Reconciler`), so the threads and the views that wait
//! for them are bounded too. A task catches its own panics.
// PORT: not in Go (perf, loader v2 L1).

use std::collections::VecDeque;
use std::sync::{Condvar, Mutex, OnceLock};

/// One unit of work for a reconcile thread.
pub(super) type Task = Box<dyn FnOnce() + Send>;

struct Pool {
    queue: Mutex<VecDeque<Task>>,
    ready: Condvar,
}

/// The number of reconcile threads of the process: the cores, at most 8.
pub(super) fn size() -> usize {
    std::thread::available_parallelism()
        .map_or(1, std::num::NonZero::get)
        .clamp(1, 8)
}

fn pool() -> &'static Pool {
    static POOL: OnceLock<&'static Pool> = OnceLock::new();
    POOL.get_or_init(|| {
        let pool: &'static Pool = Box::leak(Box::new(Pool {
            queue: Mutex::default(),
            ready: Condvar::new(),
        }));
        for index in 0..size() {
            // Out of threads, as for the parse workers: no silent loss of
            // the reconciliation.
            if let Err(err) = std::thread::Builder::new()
                .name(format!("goport-reconcile-{index}"))
                .spawn(move || run(pool))
            {
                panic!("load view reconciliation: cannot start a thread: {err}");
            }
        }
        pool
    })
}

fn lock(pool: &Pool) -> std::sync::MutexGuard<'_, VecDeque<Task>> {
    pool.queue
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Queues `tasks` for the reconcile threads.
pub(super) fn submit(tasks: impl IntoIterator<Item = Task>) {
    let pool = pool();
    lock(pool).extend(tasks);
    pool.ready.notify_all();
}

/// A reconcile thread: runs queued tasks in order, for the process's life.
fn run(pool: &'static Pool) {
    loop {
        let task = {
            let mut queue = lock(pool);
            loop {
                if let Some(task) = queue.pop_front() {
                    break task;
                }
                queue = pool
                    .ready
                    .wait(queue)
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
            }
        };
        task();
    }
}
