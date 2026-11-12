//! PORT: not in Go. The questions that a worker thread of a program asks
//! the program's host file system, when that file system belongs to the
//! program's loading thread.
//!
//! Go's checker and emit goroutines ask the program host's file system
//! directly (compiler/program.go:146 `FileExists`, the resolver behind
//! `GetPackageJsonInfo` and `GetNearestAncestorDirectoryWithPackageJson`).
//! Here the host file system is an `Rc` of the loading thread. A worker
//! thread of a program whose host is the OS file system asks its own copy of
//! it (`go_frontend::file_exists`, `WORKER_FS`). A program whose host reads
//! through client calls (the API build over `--callbacks`,
//! `System::client_reads`) gets a channel instead
//! (`serve_worker_host_queries`): a worker sends each question to the
//! loading thread, the owner, and waits for the answer, as a language server
//! search thread does (`ls/search_thread.rs` `HostQuery`).
//!
//! The owner answers while it waits for the jobs of its workers: every wait
//! of the loading thread for a checker, emit pool or symbol id carry result
//! goes through `recv_serving`. A worker job also wakes the owner when it
//! sends its result, so the wait sees the result at once. A thread with no
//! such program waits as before (`recv_serving` is a plain `recv`).
//!
//! When the owner releases a program, its questions get no answer
//! (`close`): a worker that still asks gets None and answers from the OS
//! file system, which changes nothing that a released program shows.

use std::any::Any;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvError, RecvTimeoutError, Sender, TryRecvError};
use std::time::Duration;

use crate::frontend::compiler::NewProgram;
use crate::prelude::*;

/// How long a wait goes without a wake before it looks at its result
/// again, and how long a worker waits before it looks whether the owner
/// released the program. Neither is a time limit: a wake, a question or an
/// answer ends a wait at once; these only bound a missed one.
const POLL: Duration = Duration::from_millis(10);

/// A question: what to run on the owner with the program's frontend
/// program, and where its answer goes (the result, or the payload of its
/// panic).
type Question = Box<dyn FnOnce(&Rc<NewProgram>) -> std::thread::Result<Box<dyn Any + Send>> + Send>;

enum OwnerMessage {
    /// A question of a worker of `program`.
    Ask {
        program: &'static GoProgram,
        question: Question,
        reply: Sender<Option<std::thread::Result<Box<dyn Any + Send>>>>,
    },
    /// A job sent its result (`waker`).
    Wake,
}

/// The channel of one program (`VersionTables::host_queries`).
pub(crate) struct HostQueries {
    program: &'static GoProgram,
    owner: Sender<OwnerMessage>,
    /// Set by `close` when the owner releases the program.
    closed: Arc<AtomicBool>,
}

/// The owner side of this thread: one inbox for the questions of all its
/// programs, and for the wakes of its jobs.
struct Owner {
    sender: Sender<OwnerMessage>,
    inbox: Receiver<OwnerMessage>,
}

thread_local! {
    static OWNER: RefCell<Option<Rc<Owner>>> = const { RefCell::new(None) };
    /// The `closed` flag of each program of this thread that has a channel
    /// and is not released, by `GoProgram::id`.
    static SERVED: RefCell<FxHashMap<u32, Arc<AtomicBool>>> = RefCell::default();
    /// The number of entries of `SERVED`. While it is 0, `recv_serving` is
    /// a plain `recv`.
    static SERVING: Cell<usize> = const { Cell::new(0) };
}

/// The inbox of this thread, made on first use.
fn owner() -> Rc<Owner> {
    OWNER.with(|owner| {
        Rc::clone(owner.borrow_mut().get_or_insert_with(|| {
            let (sender, inbox) = mpsc::channel();
            Rc::new(Owner { sender, inbox })
        }))
    })
}

impl HostQueries {
    /// The channel of `program`, whose owner is this thread.
    pub(super) fn new(program: &'static GoProgram) -> HostQueries {
        let closed = Arc::<AtomicBool>::default();
        SERVED.with(|served| served.borrow_mut().insert(program.id, Arc::clone(&closed)));
        SERVING.set(SERVED.with(|served| served.borrow().len()));
        HostQueries {
            program,
            owner: owner().sender.clone(),
            closed,
        }
    }

    /// Runs `f` on the owner with the frontend program of `program` (as the
    /// current program), and returns its result. A panic of `f` continues on
    /// this thread. None when the owner released the program.
    pub(super) fn ask<R: Send + 'static>(
        &self,
        f: impl FnOnce(&Rc<NewProgram>) -> R + Send + 'static,
    ) -> Option<R> {
        let (reply, answer) = mpsc::channel();
        let question: Question = Box::new(move |frontend| {
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                Box::new(f(frontend)) as Box<dyn Any + Send>
            }))
        });
        if self.closed.load(Ordering::Acquire)
            || self
                .owner
                .send(OwnerMessage::Ask {
                    program: self.program,
                    question,
                    reply,
                })
                .is_err()
        {
            return None;
        }
        let answered = loop {
            match answer.recv_timeout(POLL) {
                Ok(answered) => break answered?,
                Err(RecvTimeoutError::Timeout) if !self.closed.load(Ordering::Acquire) => {}
                Err(_) => return None,
            }
        };
        match answered {
            Ok(value) => Some(*value.downcast::<R>().expect("the answer of a question")),
            Err(payload) => std::panic::resume_unwind(payload),
        }
    }
}

/// Ends the questions of the program `id` of this thread, if it has a
/// channel: the owner releases it.
pub(super) fn close(id: u32) {
    if SERVING.get() == 0 {
        return;
    }
    if let Some(closed) = SERVED.with(|served| served.borrow_mut().remove(&id)) {
        closed.store(true, Ordering::Release);
        SERVING.set(SERVED.with(|served| served.borrow().len()));
    }
}

/// Answers one message of the inbox.
fn handle(message: OwnerMessage) {
    let OwnerMessage::Ask {
        program,
        question,
        reply,
    } = message
    else {
        return;
    };
    // A released program has no frontend program here: no answer.
    let answered = super::frontend_of_this_thread(program).map(|frontend| {
        let _program = crate::core::enter_program(Some(program));
        question(&frontend)
    });
    let _ = reply.send(answered);
}

/// `receiver.recv()` on a thread that answers the questions of its
/// workers while it waits (see the module comment). A plain `recv` on a
/// thread with no program that has a channel.
pub(super) fn recv_serving<T>(receiver: &Receiver<T>) -> Result<T, RecvError> {
    if SERVING.get() == 0 {
        return receiver.recv();
    }
    let owner = owner();
    loop {
        match receiver.try_recv() {
            Ok(value) => return Ok(value),
            Err(TryRecvError::Disconnected) => return Err(RecvError),
            Err(TryRecvError::Empty) => {}
        }
        match owner.inbox.recv_timeout(POLL) {
            Ok(message) => handle(message),
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => unreachable!("the owner keeps a sender"),
        }
    }
}

/// What a job sent from this thread calls after it sends its result, so
/// that `recv_serving` sees the result at once. None on a thread with no
/// program that has a channel.
pub(super) fn waker() -> Option<Waker> {
    (SERVING.get() > 0).then(|| Waker(owner().sender.clone()))
}

/// See `waker`. It wakes the owner when it drops, after the job sent its
/// result or panicked.
pub(super) struct Waker(Sender<OwnerMessage>);

impl Drop for Waker {
    fn drop(&mut self) {
        let _ = self.0.send(OwnerMessage::Wake);
    }
}
