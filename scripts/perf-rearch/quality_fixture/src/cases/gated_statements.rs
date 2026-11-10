// A gated `let` and a gated statement, then a shipped read.
pub struct T {
    pub a: Option<std::ffi::OsString>,
}
mod note {
    pub fn note(_t: &super::T, f: impl Fn(&mut String)) {
        f(&mut String::new());
    }
}
pub fn f() {
    #[cfg(feature = "diagnostics")]
    let t = T { a: std::env::var_os("GOPORT_FEAT_J") };
    #[cfg(feature = "diagnostics")]
    note::note(&t, |out| out.push(','));
    let _ = std::env::var_os("GOPORT_SHIP_J");
}
