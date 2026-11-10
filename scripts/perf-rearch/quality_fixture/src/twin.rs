pub const COUNT: usize = 7;
pub fn twin_read() -> bool {
    std::env::var_os("GOPORT_SHIP_TWIN").is_some()
}
