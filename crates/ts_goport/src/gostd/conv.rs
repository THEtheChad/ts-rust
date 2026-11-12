//! Go's numeric conversions where Go and Rust differ.

/// Go `int64(x)` (and `int(x)`; Go `int` is 64 bits) of a `float64`, as the
/// amd64 compiler emits it (CVTTSD2SQ): toward zero in range; out of range
/// and NaN give `math.MinInt64`.
///
/// Go: the spec ("Conversions between numeric types") leaves a value out of
/// range to the implementation; on amd64 it is the "integer indefinite"
/// value 0x8000000000000000. Rust's `as` saturates instead (and gives 0 for
/// NaN).
#[must_use]
pub fn float64_to_int64(x: f64) -> i64 {
    // 2^63 is exact in f64; -2^63 itself converts to i64::MIN. NaN is in no
    // range.
    const LIMIT: f64 = 9_223_372_036_854_775_808.0;
    if (-LIMIT..LIMIT).contains(&x) {
        return x as i64;
    }
    i64::MIN
}

/// Go `int32(x)` of a `float64` (or of a named `~int32` type), as the
/// amd64 compiler emits it (CVTTSD2SL): toward zero in range; out of range
/// and NaN give `math.MinInt32`. Rust's `as` saturates instead.
#[must_use]
pub fn float64_to_int32(x: f64) -> i32 {
    // Truncation toward zero lands in range exactly for -2^31 - 1 < x < 2^31.
    const LIMIT: f64 = 2_147_483_648.0;
    if x > -LIMIT - 1.0 && x < LIMIT {
        return x as i32;
    }
    i32::MIN
}

#[cfg(test)]
mod tests {
    use super::{float64_to_int32, float64_to_int64};

    // Go 1.27.1 linux/amd64 (`T(value.(float64))` with `T ~int32`, as Go
    // tsoptions floatOrInt32ToFlag): 2147483647.9 gives 2147483647,
    // 2147483648 and NaN give -2147483648, -2147483648.5 gives -2147483648.
    #[test]
    fn float64_to_int32_is_go_amd64() {
        assert_eq!(float64_to_int32(9.8), 9);
        assert_eq!(float64_to_int32(-9.8), -9);
        assert_eq!(float64_to_int32(2_147_483_647.9), i32::MAX);
        assert_eq!(float64_to_int32(2_147_483_648.0), i32::MIN);
        assert_eq!(float64_to_int32(-2_147_483_648.5), i32::MIN);
        assert_eq!(float64_to_int32(-2_147_483_649.0), i32::MIN);
        assert_eq!(float64_to_int32(4_294_967_297.0), i32::MIN);
        assert_eq!(float64_to_int32(1e19), i32::MIN);
        assert_eq!(float64_to_int32(f64::NAN), i32::MIN);
        assert_eq!(float64_to_int32(f64::INFINITY), i32::MIN);
        assert_eq!(float64_to_int32(f64::NEG_INFINITY), i32::MIN);
    }

    #[test]
    fn float64_to_int64_is_go_amd64() {
        assert_eq!(float64_to_int64(9.8), 9);
        assert_eq!(float64_to_int64(-9.8), -9);
        assert_eq!(float64_to_int64(-9_223_372_036_854_775_808.0), i64::MIN);
        assert_eq!(float64_to_int64(9_223_372_036_854_775_808.0), i64::MIN);
        assert_eq!(float64_to_int64(1e19), i64::MIN);
        assert_eq!(float64_to_int64(-1e300), i64::MIN);
        assert_eq!(float64_to_int64(f64::NAN), i64::MIN);
        assert_eq!(float64_to_int64(f64::INFINITY), i64::MIN);
    }
}
