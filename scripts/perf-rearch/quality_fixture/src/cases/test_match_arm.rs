// A test-only match arm with a block, then a shipped arm.
pub fn f(x: u8) -> bool {
    match x {
        #[cfg(test)]
        0 => {
            let _ = std::env::var_os("GOPORT_TEST_I");
            true
        }
        _ => std::env::var_os("GOPORT_SHIP_I").is_some(),
    }
}
