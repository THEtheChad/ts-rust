#!/usr/bin/env python3
"""Self-test of quality.py's cfg spans: which env reads are shipped, test-only or under a cargo
feature (`item_end`, `test_spans`, `feature_spans`, `analyze_file`).

usage: python3 -I scripts/perf-rearch/quality_test.py

Each case is a Rust snippet with env reads named GOPORT_SHIP_* (must be a shipped read: in
`env_sites` with no feature), GOPORT_FEAT_* (in `env_sites` under feature "diagnostics") and
GOPORT_TEST_* (inside a test span, so not in `env_sites`). Prints one line per case and
`SELFTEST OK` or `SELFTEST FAIL` (exit 1).
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("quality", HERE / "quality.py")
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)

CASES = {
    # The regression: a gated field of a struct literal, then an ungated field.
    "literal-field-then-shipped": """
fn f() -> S {
    S {
        #[cfg(feature = "diagnostics")]
        stats: Stats::new(1, (2, 3), [4, 5]),
        flag: std::env::var_os("GOPORT_SHIP_A").is_some(),
    }
}
""",
    "test-literal-field-then-shipped": """
fn f() -> S {
    S {
        #[cfg(all(test, not(feature = "diagnostics")))]
        inject: false,
        flag: std::env::var_os("GOPORT_SHIP_B").is_some(),
    }
}
""",
    # Nesting in the gated field's value: parens, brackets, braces, a string with brackets and
    # commas, a closure; the next field is on the next line.
    "nested-value-then-shipped": """
fn f() -> S {
    S {
        #[cfg(feature = "diagnostics")]
        a: g(T { x: 1, y: "},{)(]" }, [1, 2], |v| { v + 1 }, '}'),
        b: std::env::var_os("GOPORT_SHIP_C"),
        #[cfg(feature = "diagnostics")]
        c: std::env::var_os("GOPORT_FEAT_C"),
    }
}
""",
    # A gated struct field, then a field and an impl with a shipped read.
    "struct-field-then-impl": """
struct S {
    #[cfg(feature = "diagnostics")]
    held: bool,
    keys: (usize, usize),
}
impl S {
    fn n() -> usize {
        std::env::var("GOPORT_SHIP_D").map_or(0, |v| v.len())
    }
}
""",
    # The last field with no comma.
    "last-field-no-comma": """
struct S {
    a: u8,
    #[cfg(feature = "diagnostics")]
    held: bool
}
fn n() -> bool {
    std::env::var_os("GOPORT_SHIP_E").is_some()
}
""",
    "gated-fn": """
#[cfg(feature = "diagnostics")]
fn on() -> bool {
    std::env::var_os("GOPORT_FEAT_F").is_some()
}
fn shipped() -> bool {
    std::env::var_os("GOPORT_SHIP_F").is_some()
}
""",
    # A gated `if` with an `else` branch: both branches are gated.
    "gated-if-else": """
fn f() {
    #[cfg(feature = "diagnostics")]
    if a() {
        b();
    } else {
        let _ = std::env::var_os("GOPORT_FEAT_G");
    }
    let _ = std::env::var_os("GOPORT_SHIP_G");
}
""",
    # A test-only macro item, then a shipped fn.
    "test-macro-then-fn": """
#[cfg(test)]
thread_local! {
    static X: Cell<bool> = const { Cell::new(false) };
}
fn f() -> bool {
    std::env::var_os("GOPORT_SHIP_H").is_some()
}
""",
    # A test-only match arm with a block, then a shipped arm.
    "test-match-arm": """
fn f(x: u8) -> bool {
    match x {
        #[cfg(test)]
        0 => {
            let _ = std::env::var_os("GOPORT_TEST_I");
            true
        }
        _ => std::env::var_os("GOPORT_SHIP_I").is_some(),
    }
}
""",
    # A gated statement and a gated `let`, then a shipped read.
    "gated-statements": """
fn f() {
    #[cfg(feature = "diagnostics")]
    let t = T { a: std::env::var_os("GOPORT_FEAT_J") };
    #[cfg(feature = "diagnostics")]
    diag::note(&t, |out| out.push(','));
    let _ = std::env::var_os("GOPORT_SHIP_J");
}
""",
    "test-mod": """
#[cfg(test)]
mod tests {
    fn t() { let _ = std::env::var_os("GOPORT_TEST_K"); }
}
fn f() { let _ = std::env::var_os("GOPORT_SHIP_K"); }
""",
}

fails = []
for name, src in CASES.items():
    an = quality.analyze_file(f"crates/x/src/{name}.rs", src, "prod")
    sites = {e["name"]: e.get("feature") for e in an["env_sites"]}
    expected = set(quality.re.findall(r'"(GOPORT_[A-Z]+_[A-Z])"', src))
    bad = []
    for env in sorted(expected):
        kind = env.split("_")[1]
        if kind == "SHIP" and not (env in sites and sites[env] is None):
            bad.append(f"{env} not shipped ({'feature ' + str(sites[env]) if env in sites else 'test span'})")
        if kind == "FEAT" and sites.get(env) != "diagnostics":
            bad.append(f"{env} not under diagnostics ({'absent' if env not in sites else sites[env]})")
        if kind == "TEST" and env in sites:
            bad.append(f"{env} not in a test span")
    print(f"{'ok  ' if not bad else 'FAIL'} {name}" + (": " + "; ".join(bad) if bad else ""))
    if bad:
        fails.append(name)

# `#[cfg(feature = "diagnostics")] mod verify;`: the reads of verify.rs and of its child module are
# feature-only, those of the shipped sibling are not.
files = ["crates/x/src/lib.rs", "crates/x/src/verify.rs", "crates/x/src/verify/child.rs", "crates/x/src/plain.rs"]
srcs = {
    files[0]: '#[cfg(feature = "diagnostics")]\n#[allow(dead_code)]\npub mod verify;\npub mod plain;\n',
    files[1]: 'mod child;\nfn f() { let _ = std::env::var_os("GOPORT_FEAT_M"); }\n',
    files[2]: 'fn g() { let _ = std::env::var_os("GOPORT_FEAT_N"); }\n',
    files[3]: 'fn h() { let _ = std::env::var_os("GOPORT_SHIP_M"); }\n',
}
an = {f: quality.analyze_file(f, s, "prod") for f, s in srcs.items()}
quality.propagate_feature_mods(an, files)
got = {f: an[f].get("file_feature") for f in files}
want = {files[0]: None, files[1]: "diagnostics", files[2]: "diagnostics", files[3]: None}
ok = got == want
print(f"{'ok  ' if ok else 'FAIL'} feature-mod" + ("" if ok else f": {got}"))
if not ok:
    fails.append("feature-mod")

print("SELFTEST OK" if not fails else "SELFTEST FAIL")
sys.exit(1 if fails else 0)
