mod inner;
pub fn p() -> bool { std::env::var_os("GOPORT_FEAT_PATHFIRST").is_some() }
