// A name held in a const: the read through the const is the site; `format!("{NAME}")` prints the
// name and is not a read.
const NAME: &str = "GOPORT_SHIP_CONST";
pub fn r() -> Result<String, String> {
    std::env::var(NAME).map_err(|_| format!("{NAME} is not set"))
}
#[cfg(feature = "diagnostics")]
const GATED: &str = "GOPORT_FEAT_CONST";
#[cfg(feature = "diagnostics")]
pub fn g() -> bool {
    std::env::var_os(GATED).is_some()
}
