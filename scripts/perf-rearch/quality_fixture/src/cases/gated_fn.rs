#[cfg(feature = "diagnostics")]
pub fn on() -> bool {
    std::env::var_os("GOPORT_FEAT_F").is_some()
}
pub fn shipped() -> bool {
    std::env::var_os("GOPORT_SHIP_F").is_some()
}
