#[cfg(test)]
mod tests {
    fn t() {
        let _ = std::env::var_os("GOPORT_TEST_K");
    }
}
pub fn f() {
    let _ = std::env::var_os("GOPORT_SHIP_K");
}
