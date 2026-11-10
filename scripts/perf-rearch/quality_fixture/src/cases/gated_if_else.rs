// A gated `if` with an `else` branch: both branches are gated.
fn a() -> bool {
    true
}
fn b() {}
pub fn f() {
    #[cfg(feature = "diagnostics")]
    if a() {
        b();
    } else {
        let _ = std::env::var_os("GOPORT_FEAT_G");
    }
    let _ = std::env::var_os("GOPORT_SHIP_G");
}
