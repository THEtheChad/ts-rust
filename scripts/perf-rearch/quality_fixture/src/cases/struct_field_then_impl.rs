// A gated struct field, then a field and an impl with a shipped read.
pub struct S {
    #[cfg(feature = "diagnostics")]
    held: bool,
    keys: (usize, usize),
}
impl S {
    pub fn n() -> usize {
        std::env::var("GOPORT_SHIP_D").map_or(0, |v| v.len())
    }
}
