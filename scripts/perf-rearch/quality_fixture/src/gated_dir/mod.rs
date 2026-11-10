mod child;
pub fn d() -> bool { std::env::var_os("GOPORT_FEAT_DIR").is_some() }
