// The C2 regression: a gated field of a struct literal, then an ungated field.
pub struct S {
    #[cfg(feature = "diagnostics")]
    stats: (u8, (u8, u8), [u8; 2]),
    flag: bool,
}
pub fn f() -> S {
    S {
        #[cfg(feature = "diagnostics")]
        stats: (1, (2, 3), [4, 5]),
        flag: std::env::var_os("GOPORT_SHIP_A").is_some(),
    }
}
