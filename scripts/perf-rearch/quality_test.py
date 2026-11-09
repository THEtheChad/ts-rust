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

# Module gating (`propagate_feature_mods`): a file is feature-only only when every declaration that
# reaches it is gated, or sits in a feature-only file. Each case: {file: source}, and the files expected
# feature-only (all others shipped). Paths are under crates/x/src/.
MOD_CASES = {
    # A gated module and its child are feature-only; a shipped sibling is not.
    "gated-mod": ({
        "lib.rs": '/// Doc.\n#[cfg(feature = "diagnostics")]\n#[allow(dead_code)]\npub mod verify;\npub mod plain;\n',
        "verify.rs": "mod child;\n",
        "verify/child.rs": "",
        "plain.rs": "",
    }, {"verify.rs", "verify/child.rs"}),
    # Astra's repro: a gated and an ungated declaration of one #[path] file. It is shipped.
    "shared-path": ({
        "lib.rs": '#[cfg(feature = "diagnostics")]\n#[path = "shared.rs"]\nmod a;\n#[path = "shared.rs"]\nmod b;\n',
        "shared.rs": "",
    }, set()),
    # #[path] before #[cfg], with a visibility.
    "path-before-cfg": ({
        "lib.rs": '#[path = "p.rs"]\n#[cfg(all(feature = "diagnostics", unix))]\npub(crate) mod gated;\n',
        "p.rs": "",
    }, {"p.rs"}),
    # A child of a gated module outside its directory (verify/): #[path] is from verify.rs's directory.
    "child-elsewhere": ({
        "lib.rs": '#[cfg(feature = "diagnostics")]\nmod verify;\n',
        "verify.rs": '#[path = "elsewhere/child.rs"]\nmod child;\n',
        "elsewhere/child.rs": "",
    }, {"verify.rs", "elsewhere/child.rs"}),
    # The child of a gated module that a shipped file also declares stays shipped.
    "also-shipped": ({
        "lib.rs": '#[cfg(feature = "diagnostics")]\nmod verify;\nmod both;\n',
        "verify.rs": '#[path = "both.rs"]\nmod again;\n',
        "both.rs": "",
    }, {"verify.rs"}),
    # A declaration inside a gated inline module, and a not(feature) one.
    "inline-and-not": ({
        "lib.rs": '#[cfg(feature = "diagnostics")]\nmod inl {\n    mod deep;\n}\n#[cfg(not(feature = "diagnostics"))]\nmod off;\n',
        "inl/deep.rs": "",
        "off.rs": "",
    }, {"inl/deep.rs"}),
    # A file that no declaration reaches counts as shipped.
    "unreached": ({
        "lib.rs": '#[cfg(feature = "diagnostics")]\nmod verify;\n',
        "verify.rs": "",
        "loose.rs": "",
    }, {"verify.rs"}),
}
for case, (srcs, want) in MOD_CASES.items():
    files = [f"crates/x/src/{f}" for f in srcs]
    an = {f: quality.analyze_file(f, s, "prod") for f, s in zip(files, srcs.values())}
    for a in an.values():
        a["fkind"] = "prod"
    quality.propagate_feature_mods(an, files)
    got = {f.removeprefix("crates/x/src/") for f in files if an[f].get("file_feature")}
    bad_feat = [f for f in files if an[f].get("file_feature") not in (None, "diagnostics")]
    ok = got == want and not bad_feat
    print(f"{'ok  ' if ok else 'FAIL'} mod-{case}" + ("" if ok else f": feature-only {sorted(got)}, want {sorted(want)}"))
    if not ok:
        fails.append(f"mod-{case}")

print("SELFTEST OK" if not fails else "SELFTEST FAIL")
sys.exit(1 if fails else 0)
