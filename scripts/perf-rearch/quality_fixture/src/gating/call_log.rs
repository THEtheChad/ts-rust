// env: a diagnostic env name read in the default build. topic: state named after a gated name.
pub fn leak() -> bool {
    std::env::var_os("GOPORT_LEAK_STATS").is_some()
}
#[cfg(feature = "diagnostics")]
pub fn on() -> bool {
    std::env::var_os("GOPORT_FIXTURE_CALL_LOG").is_some()
}
pub static FIXTURE_CALL_LOG: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());
pub fn note(s: &str) {
    FIXTURE_CALL_LOG.lock().unwrap().push(s.to_string());
}
