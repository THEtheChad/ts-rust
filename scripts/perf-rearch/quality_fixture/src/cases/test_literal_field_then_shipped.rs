pub struct S {
    #[cfg(all(test, not(feature = "diagnostics")))]
    inject: bool,
    flag: bool,
}
pub fn f() -> S {
    S {
        #[cfg(all(test, not(feature = "diagnostics")))]
        inject: false,
        flag: std::env::var_os("GOPORT_SHIP_B").is_some(),
    }
}
