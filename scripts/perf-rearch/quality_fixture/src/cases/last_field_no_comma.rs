// The last field with no comma.
pub struct S {
    a: u8,
    #[cfg(feature = "diagnostics")]
    held: bool
}
pub fn n() -> bool {
    std::env::var_os("GOPORT_SHIP_E").is_some()
}
