// A reserved module that ships only a stateless no-op stub: not a finding.
#[cfg(feature = "diagnostics")]
mod on {
    pub struct Timer(std::time::Instant);
    impl Timer {
        pub fn start() -> Self {
            Timer(std::time::Instant::now())
        }
        pub fn report(self) {
            eprintln!("{:?}", self.0.elapsed());
        }
    }
}
#[cfg(not(feature = "diagnostics"))]
mod off {
    #[derive(Clone, Copy, Debug, Default)]
    pub struct Timer;
    impl Timer {
        #[inline(always)]
        pub fn start() -> Self {
            Timer
        }
        #[inline(always)]
        pub fn report(self) {}
    }
}
#[cfg(feature = "diagnostics")]
pub use on::Timer;
#[cfg(not(feature = "diagnostics"))]
pub use off::Timer;
