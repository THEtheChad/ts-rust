//! Go `strings.Repeat` (go1.27.1), with every panic it raises before it allocates.

/// Go `maxAlloc` on linux/amd64: `1 << heapAddrBits`, with `heapAddrBits` 48 on 64-bit targets that
/// are not wasm or iOS.
///
/// Go: runtime/malloc.go:213 heapAddrBits, :220 maxAlloc (go1.27.1).
const MAX_ALLOC: u128 = 1 << 48;

/// The length of `strings.Repeat(s, count)` for a string `len` bytes long, after the checks Go makes
/// before it allocates. It panics as Go does, with Go's panic texts:
/// - a negative count: `strings: negative Repeat count` (strings/strings.go:608);
/// - a length past `math.MaxInt`: `strings: Repeat output length overflow` (:611);
/// - a length past `maxAlloc`: Repeat grows a `strings.Builder` by the length (:657, builder.go:75
///   Grow, :67 grow), which asks `bytealg.MakeNoZero`; runtime/slice.go:432 bytealg_MakeNoZero
///   panics with `panicmakeslicelen` (:29), a runtime error whose text is `runtime error:
///   makeslice: len out of range` (runtime/error.go:91). Go's LSP recovers it like any panic.
///
/// Go: strings/strings.go:597 Repeat. A length up to `maxAlloc` that the host can't allocate is a
/// fatal out-of-memory error in Go, and an allocation failure here: neither is an answer.
#[must_use]
pub fn repeat_len(len: usize, count: i64) -> usize {
    match count {
        0 => return 0,
        1 => return len,
        _ => {}
    }
    if count < 0 {
        panic!("strings: negative Repeat count");
    }
    let n = len as u128 * count as u128;
    if n > i64::MAX as u128 {
        panic!("strings: Repeat output length overflow");
    }
    if len == 0 {
        return 0;
    }
    // Go answers a length up to 128 from a static string (strings.go:621), which is under maxAlloc.
    if n > MAX_ALLOC {
        panic!("runtime error: makeslice: len out of range");
    }
    n as usize
}

/// Go `strings.Repeat(s, count)`, with its panics (`repeat_len`).
// Go: strings/strings.go:597 Repeat
#[must_use]
pub fn repeat(s: &str, count: i64) -> String {
    let n = repeat_len(s.len(), count);
    if n == 0 {
        return String::new();
    }
    s.repeat(n / s.len())
}

#[cfg(test)]
mod tests {
    use super::{repeat, repeat_len};

    fn panic_text(f: impl FnOnce() + std::panic::UnwindSafe) -> String {
        let err = std::panic::catch_unwind(f).expect_err("a panic");
        err.downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| err.downcast_ref::<String>().cloned())
            .unwrap_or_default()
    }

    #[test]
    fn repeat_is_go() {
        assert_eq!(repeat(" ", 0), "");
        assert_eq!(repeat("ab", 1), "ab");
        assert_eq!(repeat("\t", 3), "\t\t\t");
        assert_eq!(repeat("", 1 << 62), "");
        assert_eq!(
            panic_text(|| drop(repeat(" ", -1))),
            "strings: negative Repeat count"
        );
        assert_eq!(
            panic_text(|| drop(repeat("ab", i64::MAX))),
            "strings: Repeat output length overflow"
        );
        assert_eq!(
            panic_text(|| drop(repeat(" ", 1 << 62))),
            "runtime error: makeslice: len out of range"
        );
        assert_eq!(
            panic_text(|| {
                let _ = repeat_len(1, (1 << 48) + 1);
            }),
            "runtime error: makeslice: len out of range"
        );
    }

    /// A length of exactly `maxAlloc` passes Go's checks. Its `usize` only exists on 64-bit
    /// targets, which is also where `MAX_ALLOC` is Go's figure (wasm32 builds lint the tests).
    #[cfg(target_pointer_width = "64")]
    #[test]
    fn repeat_len_up_to_max_alloc_is_go() {
        assert_eq!(repeat_len(1, 1 << 48), 1 << 48);
    }
}
