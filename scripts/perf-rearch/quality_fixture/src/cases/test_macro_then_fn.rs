// A test-only macro item, then a shipped fn.
#[cfg(test)]
thread_local! {
    static X: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}
pub fn f() -> bool {
    std::env::var_os("GOPORT_SHIP_H").is_some()
}
