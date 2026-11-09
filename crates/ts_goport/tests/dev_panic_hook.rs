//! The panic hook of `goport_typesyms` (`DevPanicHook::GOPORT_TYPESYMS`):
//! a plain Rust panic prints `goport_typesyms: panic at <file>:<line>:
//! <message>` with `Location::file` as it is, as the bin's own hook did
//! before the dev bins shared one. The unit test
//! `gopanic::tests::dev_panic_hook_lines_keep_each_bins_site_format` pins a
//! `parts/` crate site; this one runs the bin.
#![cfg(target_os = "linux")]

use std::process::Command;

#[test]
fn goport_typesyms_prints_a_panic_with_its_site() {
    let dir = std::env::temp_dir().join(format!("dev_panic_hook-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("a.ts"), "export const a = 1;\n").unwrap();
    std::fs::write(
        dir.join("tsconfig.json"),
        r#"{"compilerOptions": {"noLib": true, "types": []}, "files": ["a.ts"]}"#,
    )
    .unwrap();
    // The output directory is under a file, so its create panics
    // (`expect("create output directory")`).
    std::fs::write(dir.join("file"), "").unwrap();
    let run = Command::new(env!("CARGO_BIN_EXE_goport_typesyms"))
        .arg("-p")
        .arg(dir.join("tsconfig.json"))
        .arg("-o")
        .arg(dir.join("file/out"))
        .env_remove("GOPORT_TRACE")
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(&dir);
    let stderr = String::from_utf8_lossy(&run.stderr);
    let first = stderr.lines().next().unwrap_or_default();
    let prefix = "goport_typesyms: panic at crates/ts_goport/src/bin/goport_typesyms.rs:";
    assert!(
        first.starts_with(prefix) && first.contains(": create output directory: "),
        "the hook's panic line:\n{stderr}"
    );
    assert_eq!(run.status.code(), Some(2), "{stderr}");
}
