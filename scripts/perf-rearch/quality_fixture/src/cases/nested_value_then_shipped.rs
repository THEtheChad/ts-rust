// Nesting in the gated field's value: parens, brackets, braces, a string with brackets and commas,
// a closure; the next field is on the next line.
pub struct T {
    pub x: u8,
    pub y: &'static str,
}
pub struct S {
    #[cfg(feature = "diagnostics")]
    a: usize,
    b: Option<std::ffi::OsString>,
    #[cfg(feature = "diagnostics")]
    c: Option<std::ffi::OsString>,
}
fn g(t: T, a: [u8; 2], f: impl Fn(u8) -> u8, c: char) -> usize {
    t.y.len() + a.len() + f(t.x) as usize + c.len_utf8()
}
pub fn f() -> S {
    S {
        #[cfg(feature = "diagnostics")]
        a: g(T { x: 1, y: "},{)(]" }, [1, 2], |v| { v + 1 }, '}'),
        b: std::env::var_os("GOPORT_SHIP_C"),
        #[cfg(feature = "diagnostics")]
        c: std::env::var_os("GOPORT_FEAT_C"),
    }
}
