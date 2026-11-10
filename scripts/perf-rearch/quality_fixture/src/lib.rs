//! See Cargo.toml.
#![allow(dead_code, unused)]

pub mod cases;
pub mod gating;

// Astra's Q3 probe: one file, a gated and an ungated module. The ungated one ships.
#[cfg(feature = "diagnostics")]
#[path = "shared.rs"]
mod gated_shared;
#[path = "shared.rs"]
mod open_shared;

// `#[path]` before `#[cfg]`, and a child of the #[path] file: rustc looks for it next to the
// file (src/inner.rs), not in src/path_first/.
#[path = "path_first.rs"]
#[cfg(feature = "diagnostics")]
mod path_first;

// a gated module file with a child file
#[cfg(feature = "diagnostics")]
mod gated_dir;

// one module name declared out of line and inline under different cfgs (core.rs `lib_names`);
// rustc reads twin.rs in every build (as `include_bytes!` of the binder sources reads lib_names.rs),
// so the dep-info cannot tell which twin is compiled, and the expanded body must
const TWIN_SOURCE: &[u8] = include_bytes!("twin.rs");
#[cfg(not(target_family = "wasm"))]
mod twin;
#[cfg(target_family = "wasm")]
mod twin {
    pub const COUNT: usize = 0;
    pub fn twin_read() -> bool {
        false
    }
}

// an inline module with an out-of-line child: src/inline_parent/child.rs
mod inline_parent {
    pub mod child;
}

#[cfg(test)]
mod tests;

/// A build-time name: rustc's dep-info has it, the expanded source only its value.
pub fn build() -> Option<&'static str> {
    option_env!("GOPORT_SHIP_BUILD")
}
