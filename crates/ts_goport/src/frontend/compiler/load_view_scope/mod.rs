//! The scope of a language server program load with the per-load OS view
//! (`vfs::loadview`, loader v2 L1).
//!
//! `begin` attaches a new view under the session's host for the load on this
//! thread; the scope detaches it when it ends, before any later load or
//! auto-import work, and frees it then (on the free thread). Nothing keeps
//! the view after the load: as in Go, a change that happens after the load
//! read a file and before the watch registration is seen when a later watch
//! event reports that path (Go `project/session.go` `updateWatch` registers
//! after the load).
//!
//! Diagnostics (cargo feature `diagnostics`, `diag.rs`): the counts and
//! observation-time lines, the V-replay oracle and the test hooks.
// PORT: not in Go (perf).

#[cfg(feature = "diagnostics")]
pub mod diag;

use crate::frontend::vfs::loadview::{self, LoadView};
use std::sync::Arc;

/// The scope of one load with the OS view (`begin`).
pub struct LoadViewScope {
    view: Option<Arc<LoadView>>,
    /// When the load started: ms of `load_times::monotonic_ms` and ns of the
    /// observation log's clock.
    #[cfg(feature = "diagnostics")]
    t0: (f64, u64),
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
/// session's host has one (`loadview::attach`).
#[must_use]
pub fn begin() -> Option<LoadViewScope> {
    let view = loadview::attach(new_view)?;
    Some(LoadViewScope {
        view: Some(view),
        #[cfg(feature = "diagnostics")]
        t0: (
            super::load_times::monotonic_ms(),
            loadview::diag::clock_ns(),
        ),
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
        // The view's texts and lookups go with the load; the free thread
        // frees them (the snapshot keeps the texts it cached).
        crate::execute::build::build_task::drop_in_background(view);
        #[cfg(feature = "diagnostics")]
        diag::after_load();
    }
}
