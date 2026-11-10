// module: a reserved module name (`_stats`) in the default build, with state.
static COUNT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
pub fn count() -> usize {
    COUNT.load(std::sync::atomic::Ordering::Relaxed)
}
