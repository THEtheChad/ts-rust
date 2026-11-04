//! The scope of a language server program load with the per-load OS view
//! (`vfs::loadview`, loader v2 L1), and the reconciliation after the watch
//! registration.
//!
//! `begin` attaches a new view under the session's host for the load on this
//! thread; the scope detaches it when it ends, before any later load or
//! auto-import work, and keeps the view until the session that made the
//! load takes it for its reconciliation (`take_finished_views`,
//! `reconcile::Reconciler`).
//!
//! Diagnostics (cargo feature `diagnostics`, `diag.rs`): the counts and
//! timing lines, the V-replay oracle and the test hooks.
// PORT: not in Go (perf).

#[cfg(feature = "diagnostics")]
pub mod diag;
mod pool;
pub mod reconcile;

pub use reconcile::{Deliver, ReconcileEvent, Reconciler};

use crate::frontend::prelude::*;
use crate::frontend::vfs::loadview::{self, LoadView};
use std::sync::Arc;

/// The scope of one load with the OS view (`begin`).
pub struct LoadViewScope {
    view: Option<Arc<LoadView>>,
    #[cfg(feature = "diagnostics")]
    t0: f64,
}

thread_local! {
    /// The views of the loads that ended on this thread, until the session
    /// that made them takes them (`take_finished_views`).
    static FINISHED: RefCell<Vec<Arc<LoadView>>> = const { RefCell::new(Vec::new()) };
}

/// The views of the loads that ended on this thread since the last call,
/// for the session whose snapshot clone made them (`Reconciler::add`).
#[must_use]
pub fn take_finished_views() -> Vec<Arc<LoadView>> {
    FINISHED.with(|finished| std::mem::take(&mut *finished.borrow_mut()))
}

/// A new view, or with the `diagnostics` feature the next recorded one to
/// replay.
fn new_view() -> Arc<LoadView> {
    #[cfg(feature = "diagnostics")]
    if let Some(view) = diag::next_replay() {
        return view;
    }
    LoadView::new()
}

/// Attaches a new OS view for the load that starts on this thread, when the
/// view is on and the session's host has one (`loadview::attach`).
#[must_use]
pub fn begin() -> Option<LoadViewScope> {
    let view = loadview::attach(new_view)?;
    Some(LoadViewScope {
        view: Some(view),
        #[cfg(feature = "diagnostics")]
        t0: super::load_times::monotonic_ms(),
    })
}

impl Drop for LoadViewScope {
    fn drop(&mut self) {
        loadview::detach();
        let Some(view) = self.view.take() else {
            return;
        };
        #[cfg(feature = "diagnostics")]
        diag::end_scope(&view, self.t0);
        // The reconciliation after the watch registration checks the view's
        // observations (`reconcile::Reconciler`); it frees the view then. A
        // replayed view (the V-replay oracle) holds no live observation.
        #[cfg(feature = "diagnostics")]
        let reconcile = diag::reconciles(&view);
        #[cfg(not(feature = "diagnostics"))]
        let reconcile = true;
        if reconcile {
            FINISHED.with(|finished| finished.borrow_mut().push(view));
        } else {
            crate::execute::build::build_task::drop_in_background(view);
        }
        #[cfg(feature = "diagnostics")]
        diag::after_load();
    }
}
