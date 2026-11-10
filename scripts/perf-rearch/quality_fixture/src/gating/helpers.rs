// dead: shipped, named only by gated code. name: a verifier name.
pub fn only_for_the_check(x: u8) -> u8 {
    x + 1
}
#[cfg(feature = "diagnostics")]
pub fn check() -> u8 {
    only_for_the_check(1)
}
pub fn assert_same_fixture(a: u8, b: u8) {
    assert_eq!(a, b);
}
pub fn used() -> u8 {
    assert_same_fixture(1, 1);
    0
}
