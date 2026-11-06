//! A project on the OS file system, for the tests of the port's parse and
//! resolve workers (parseahead_test.rs, resolveahead_test.rs), which read
//! the OS file system. Not a Go file.

use std::rc::Rc;

use ts_goport::frontend::bundled;
use ts_goport::frontend::vfs::osvfs_fs;
use ts_goport::lsp::lsproto;
use ts_goport::project::{self, Session, SessionInit, SessionOptions};

use super::projecttestutil;
use super::util::bg;

/// Writes `text` to `name` in `root`, with its directories.
pub fn write(root: &str, name: &str, text: &str) {
    let path = std::path::Path::new(root).join(name);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

/// A session on the OS file system in `root`, with no client, watch or
/// typings installer.
pub fn os_session(root: &str) -> Rc<Session> {
    project::new_session(&SessionInit {
        background_ctx: bg(),
        options: Rc::new(SessionOptions {
            current_directory: root.to_string(),
            default_library_path: bundled::lib_path(),
            typings_location: String::new(),
            position_encoding: lsproto::PositionEncodingKind::UTF8,
            watch_enabled: false,
            logging_enabled: false,
            ..projecttestutil::session_options(root)
        }),
        fs: bundled::wrap_fs(osvfs_fs()),
        client: None,
        logger: None,
        npm_executor: None,
        spawner: None,
        content_mapper_logger: None,
        parse_cache: None,
        content_mapped_parse_cache: None,
    })
}
