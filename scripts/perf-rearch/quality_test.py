#!/usr/bin/env python3
"""Self-test of the shipped-source path of quality.sh: rustc's cfg-expanded source (qexpand.py),
the escape-hatch inventory on it (quality.py `inventory_of`) and the gating rules (qgating.py).

usage: python3 -I scripts/perf-rearch/quality_test.py

The fixture crate scripts/perf-rearch/quality_fixture is expanded twice, as the real tree is:
`cargo check --release` (default) and `--features diagnostics`, on CI's toolchain, through
qexpand_wrapper.py (a scratch target dir under ~/.cache/perf-rearch/quality/tmp; about 5 s).
Then:

- inventory: every GOPORT_SHIP_* name is read in the default build, every GOPORT_FEAT_* name only
  in the diagnostics build, every GOPORT_TEST_* name in neither; each read is attributed to the
  source file and line that holds it (the fixture's names are unique, so every line is exact).
  The cases are the old cfg-parser cases (src/cases/), Astra's Q3 probe (one #[path] file behind a
  gated and an ungated module), `#[path]` before `#[cfg]`, a #[path] file's child, a gated module
  directory, a name declared out of line and inline under different cfgs, an inline module's
  out-of-line child, a const-held name and an `option_env!` name.
- attribution: the pieces name the files rustc read, and nothing else.
- gating: the planted findings of src/gating/ and no others.

Prints one line per check and `SELFTEST OK` or `SELFTEST FAIL` (exit 1).
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qexpand  # noqa: E402
import qgating  # noqa: E402
import quality  # noqa: E402

FIXTURE = HERE / "quality_fixture"
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def source_lines(name: str) -> list[tuple[str, int]]:
    """(file, line) of each `"name"` literal in the fixture's sources (outside comments)."""
    out = []
    for p in sorted((FIXTURE / "src").rglob("*.rs")):
        rel = str(p.relative_to(FIXTURE))
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if f'"{name}"' in line and not line.lstrip().startswith("//"):
                out.append((rel, n))
    return out


class Src(dict):
    """The fixture's lexed sources, as quality.py's source analysis gives them."""

    def __init__(self):
        super().__init__()
        for p in (FIXTURE / "src").rglob("*.rs"):
            kinds, texts, lines, _ = quality.lex(p.read_text())
            self[str(p.relative_to(FIXTURE))] = {"kinds": kinds, "texts": texts, "lines": lines,
                                                 "funcs": quality.fn_spans(kinds, texts, lines)}


def main() -> int:
    tc = qexpand.ci_toolchain("HEAD")
    default = qexpand.expand_crate(FIXTURE, [], tc, "fixture-default")
    feats = qexpand.expand_crate(FIXTURE, ["diagnostics"], tc, "fixture-features")
    src = Src()

    # attribution: the module files of each build are exactly the files rustc read
    for exp in (default, feats):
        c = exp["crates"][0]
        files = {p["file"] for p in exp["pieces"]}
        check(f"{exp['variant']}: pieces are the files rustc read", files == set(c["files"]) and not c["unclaimed"],
              f"pieces {sorted(files)}, unclaimed {c['unclaimed']}")
    want_default = {"src/lib.rs", "src/shared.rs", "src/twin.rs", "src/inline_parent/child.rs"}
    got_default = {p["file"] for p in default["pieces"] if not p["file"].startswith(("src/cases", "src/gating"))}
    check("default: gated module files are not compiled", got_default == want_default, f"{sorted(got_default)}")
    got_feats = {p["file"] for p in feats["pieces"]}
    for f in ("src/path_first.rs", "src/inner.rs", "src/gated_dir/mod.rs", "src/gated_dir/child.rs"):
        check(f"features: {f} is a module file", f in got_feats, f"{sorted(got_feats)}")
    mods = {p["modpath"]: p["file"] for p in feats["pieces"]}
    check("features: the shared #[path] file gives two pieces",
          mods.get("crate::gated_shared") == "src/shared.rs" and mods.get("crate::open_shared") == "src/shared.rs", f"{mods}")
    twin = [p for p in default["pieces"] if p["modpath"] == "crate::twin"]
    check("twin, native: the out-of-line file, by content (rustc read the file either way)",
          len(twin) == 1 and twin[0]["file"] == "src/twin.rs" and twin[0]["how"] == "content", f"{twin and twin[0]['how']}")
    wasm = qexpand.expand_crate(FIXTURE, [], tc, "fixture-wasm", "wasm32-wasip1")
    twin_w = [p for p in wasm["pieces"] if p["modpath"] == "crate::twin"]
    check("twin, wasm32-wasip1: the inline module, by content", not twin_w and
          any(m["modpath"] == "crate::twin" and m["inline"] for m in wasm["modules"]), f"{twin_w}")
    hw, _ = quality.inventory_of(wasm, src)
    check("twin, wasm32-wasip1: the file's read is not in the build", "GOPORT_SHIP_TWIN" not in hw, f"{hw.get('GOPORT_SHIP_TWIN')}")

    # inventory
    hd, _ = quality.inventory_of(default, src)
    hf, _ = quality.inventory_of(feats, src)
    names = set()
    for p in (FIXTURE / "src").rglob("*.rs"):
        names |= set(re.findall(r'"(GOPORT_[A-Z_]+)"', p.read_text()))
    expect = {n: n.split("_")[1] for n in names}
    expect.update({"GOPORT_LEAK_STATS": "SHIP", "GOPORT_FIXTURE_CALL_LOG": "FEAT"})
    for n in sorted(expect):
        kind = expect[n]
        in_d = n in hd and hd[n]["reads"] > 0
        in_f = n in hf and hf[n]["reads"] > 0
        if kind == "SHIP":
            ok = in_d and in_f
        elif kind == "FEAT":
            ok = in_f and not in_d
        else:
            ok = not in_d and not in_f
        where = hd.get(n) or hf.get(n)
        detail = f"default {in_d}, features {in_f}"
        if ok and where and kind != "TEST":
            # attribution of each read: the literal's own file and line
            want = {f"{f}:{ln}" for f, ln in source_lines(n)}
            got = {s.rsplit(":", 2)[0] + ":" + s.rsplit(":", 2)[1] for s in where["sites"] if ":read" in s or ":compile" in s}
            # read through a const: the site is the read, not the const
            want = {"GOPORT_SHIP_CONST": {"src/cases/via_const.rs:5"},
                    "GOPORT_FEAT_CONST": {"src/cases/via_const.rs:11"}}.get(n, want)
            ok = got == want
            detail = f"sites {sorted(got)}, want {sorted(want)}"
        check(f"inventory: {n} {kind}", ok, detail)
    c = hd.get("GOPORT_SHIP_CONST", {})
    check("inventory: format!(\"{NAME}\") is not a read", c.get("reads") == 1, f"reads {c.get('reads')}")
    b = hd.get("GOPORT_SHIP_BUILD", {})
    check("inventory: option_env! name from the dep-info", any(":compile" in s for s in b.get("sites", [])), f"{b.get('sites')}")

    # gating
    hatches = []
    for n in sorted(set(hd) | set(hf)):
        shipped = n in hd and hd[n]["reads"] > 0
        h = {**(hd[n] if shipped or n not in hf else hf[n]), "read_in_prod": shipped,
             "features": [] if shipped else ["diagnostics"], "fallback": False}
        h["class"] = quality.hatch_class(h)
        h["class_by_name"] = bool(quality.TESTHOOK_RE.search(n) or quality.DIAG_RE.search(n) or quality.VERIFY_RE.search(n))
        hatches.append(h)
    found = {(f["rule"], f["subject"]) for f in qgating.build_findings("default", default, hatches, src)
             + qgating.dead_findings(default, feats, src)}
    found = {(r, s.replace("src/", "")) for r, s in found if "gating" in s or r == "env"}
    want = {("env", "GOPORT_LEAK_STATS"),
            ("module", "quality_fixture::gating::load_stats"),
            ("topic", "gating/call_log.rs::FIXTURE_CALL_LOG"),
            ("dead", "gating/helpers.rs::only_for_the_check"),
            ("name", "gating/helpers.rs::assert_same_fixture")}
    check("gating: the planted findings and no others", found == want,
          f"missing {sorted(want - found)}, extra {sorted(found - want)}")

    print("SELFTEST OK" if not fails else "SELFTEST FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
