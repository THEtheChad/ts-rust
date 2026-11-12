#!/usr/bin/env python3
"""Self-test of the Go-relative complexity: the Go-side metric (gocog) and the Rust-to-Go mapper (gorel.py).

usage: python3 -I scripts/perf-rearch/gorel_test.py

- Go metric: hand-computed Go functions. Where rust-code-analysis-cli is installed, the same functions
  written in Rust are scored by rca too, and each pair must agree (the two sides follow one definition).
- Mapper: markers, prose, the name matcher, method families, splits, lifted closures and nested
  functions, on a small synthetic Go index; markers naming struct fields and package vars; markers
  naming Go outside the pin (GOROOT `src` and x/text, as their own index).
- Residue: Go functions with tagless switches and their Rust else-if chains; rca's figure less
  gocog's must equal gocog's residue, and the rows carry it.

Builds gocog into a temporary cache (needs ~/.local/go/bin/go or PERF_QUALITY_GO; downloads nothing).
Prints one line per case and `SELFTEST OK` or `SELFTEST FAIL` (exit 1).
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gorel", HERE / "gorel.py")
gorel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gorel)
RCA = Path.home() / ".cache/perf-rearch/tools/rca/bin/rust-code-analysis-cli"

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got!r}" + ("" if ok else f", want {want!r}"))
    if not ok:
        FAILS.append(name)


# --------------------------------------------------------------------------
# Go metric. Each case: Go source, Rust equivalent (or None), expected (cog, cyc), and why.

GO_CASES = {
    # if +1; && +1; for (nesting 1) +2; if (nesting 2) +3; else-if +1 (no nesting increment); else +1
    "a": ("""
func a(x int, ok bool) int {
	if x > 0 && ok {
		for i := 0; i < x; i++ {
			if i%2 == 0 {
				continue
			}
		}
	} else if x < 0 {
		return -1
	} else {
		return 0
	}
	return x
}""", """
fn a(x: i32, ok: bool) -> i32 {
    if x > 0 && ok {
        for i in 0..x {
            if i % 2 == 0 {
                continue;
            }
        }
    } else if x < 0 {
        return -1;
    } else {
        return 0;
    }
    x
}""", (9, 6)),
    # rca's boolean sequence: one state per function, reset only by a nesting increment. `a && b && c`
    # +1 (state &&); `a || b` +1 (differs; state stays &&); `!a && b` +0 (same as &&), then `!` sets
    # the state to unary; `c || d` +1; `(x && y) || (z && w)`: +1 +1 +1. cyc: 1 + 8 operators.
    "b": ("""
func b(a, b, c, d bool) bool {
	x := a && b && c
	y := a || b
	z := !a && b
	w := c || d
	return x && y || z && w
}""", """
fn b(a: bool, b: bool, c: bool, d: bool) -> bool {
    let x = a && b && c;
    let y = a || b;
    let z = !a && b;
    let w = c || d;
    x && y || z && w
}""", (6, 9)),
    # a closure adds 1 nesting (its if: +2) and counts in its function; a bare `for {}` is Rust's
    # `loop` (+0, no nesting); switch +1; labeled break +1; range in a case (nesting 1) +2.
    # cyc: 1 + closure 1 + if + for + 2 case clauses + range.
    "c": ("""
func c(xs []int) int {
	n := 0
	visit := func(v int) {
		if v > 0 {
			n++
		}
	}
outer:
	for {
		switch len(xs) {
		case 0:
			break outer
		default:
			for _, x := range xs {
				visit(x)
			}
			xs = xs[1:]
		}
	}
	return n
}""", """
fn c(mut xs: &[i32]) -> i32 {
    let mut n = 0;
    let mut visit = |v: i32| {
        if v > 0 {
            n += 1;
        }
    };
    'outer: loop {
        match xs.len() {
            0 => break 'outer,
            _ => {
                for x in xs {
                    visit(*x);
                }
                xs = &xs[1..];
            }
        }
    }
    n
}""", (6, 7)),
    # type switch +1; select (nesting 1) +2; goto +1. cyc: 1 + 1 case clause + 2 comm clauses.
    "d": ("""
func d(v any, ch chan int) {
	switch v.(type) {
	case int:
		select {
		case <-ch:
		default:
		}
	}
	goto end
end:
}""", None, (4, 4)),
    # a method on a generic receiver; `*T` in a type position does not touch the boolean state, a
    # deref does: `p != nil && *p > 0` +1, then the deref sets unary, so `|| q` +1. cyc 1 + 1 + && + ||.
    # tagless-switch residue. A tagless switch of 3 cases and a default (nesting 0) is +1, as the
    # chain's `if` is; the chain pays one `else` per clause after the first: 3, the residue. The
    # tagged switch (nesting 1) costs +2 and a `match` the same, no residue. The tagless switch in the
    # closure (2 clauses; nesting 2: the default clause, then the closure) is +3, as the Rust if/else
    # is, plus its one else.
    # cog Go: 1 + 2 + 3 = 6; Rust (1 + 3 elses) + 2 + (3 + 1 else) = 10; residue 3 + 1 = 4.
    # cyc: Go 1 + 4 case clauses + 2 case clauses (tagged) + closure 1 + 2 clauses = 10.
    "r": ("""
func r(x, y int) int {
	switch {
	case x > 0:
		switch y {
		case 1:
			return 1
		default:
			return 2
		}
	case x < 0:
		return -1
	case y > 0:
		return 3
	default:
		f := func() int {
			switch {
			case y < 0:
				return 4
			default:
				return 5
			}
		}
		return f()
	}
}""", """
fn r(x: i32, y: i32) -> i32 {
    if x > 0 {
        match y {
            1 => 1,
            _ => 2,
        }
    } else if x < 0 {
        -1
    } else if y > 0 {
        3
    } else {
        let f = || if y < 0 { 4 } else { 5 };
        f()
    }
}""", (6, 10)),
    "e": ("""
func (m *Map[K, V]) e(p *int, q bool) bool {
	var r *int = p
	if r != nil && *r > 0 || q {
		return true
	}
	return false
}""", None, (3, 4)),
}


def go_metric_cases(cache: Path) -> None:
    exe = gorel.gocog_binary(cache)
    work = Path(tempfile.mkdtemp(prefix="gocog-", dir=cache))
    (work / "internal" / "x").mkdir(parents=True)
    src = "package x\n\ntype Map[K comparable, V any] struct{}\n" + "\n".join(g for g, _, _ in GO_CASES.values()) + "\n"
    (work / "internal" / "x" / "x.go").write_text(src)
    (work / "internal" / "x" / "gen.go").write_text("// Code generated by hand. DO NOT EDIT.\n\npackage x\n\nfunc g() {}\n")
    (work / "internal" / "x" / "x_test.go").write_text("package x\n\nfunc t() {}\n")
    (work / "internal" / "x" / "members.go").write_text(MEMBERS_GO)
    (work / "internal" / "x" / "vendor" / "v").mkdir(parents=True)
    (work / "internal" / "x" / "vendor" / "v" / "v.go").write_text("package v\n\nfunc vendored() {}\n")
    out = json.loads(subprocess.run([str(exe), str(work), "internal"], check=True, capture_output=True, text=True).stdout)
    members = {(f.get("kind"), f["recv"], f["name"]): f for f in out["funcs"] if f.get("kind")}
    funcs = {f["name"]: f for f in out["funcs"] if not f.get("kind")}
    for name, (_, _, want) in GO_CASES.items():
        f = funcs[name]
        check(f"go {name} (cog, cyc)", (f["cog"], f["cyc"]), want)
    check("go e receiver", funcs["e"]["recv"], "Map")
    check("go c closure (name, standalone cog, in-place cog, cyc)",
          [(c["name"], c["cog"], c["cog_inplace"], c["cyc"]) for c in funcs["c"]["closures"]], [("visit", 1, 2, 2)])
    check("go a lines", funcs["a"]["lines"], 14)
    check("go generated file kept and flagged", funcs["g"].get("generated"), True)
    check("go _test.go skipped", "t" in funcs, False)
    check("go vendor directory skipped", "vendored" in funcs, False)
    check("go residue: tagless switches, closures included", funcs["r"]["residue"], 4)
    check("go residue: none without a tagless switch", (funcs["a"]["residue"], funcs["c"]["residue"]), (0, 0))
    check("go closure residue (standalone)", [(c["name"], c["residue"]) for c in funcs["r"]["closures"]], [("f", 1)])
    # fields: named fields of a package-level struct, the embedded one skipped
    check("go struct fields", sorted((k[1], k[2], f["line"]) for k, f in members.items() if k[0] == "field"),
          [("Box", "Body", 7), ("Box", "Name", 6), ("Box", "Size", 6)])
    # vars and consts: a func literal scored as a function (if +1, else +1), a map literal 0, a const 0
    check("go var func literal (cog, cyc, residue)", [(f["cog"], f["cyc"], f["residue"]) for k, f in members.items() if k[2] == "pick"], [(2, 2, 0)])
    check("go var and const kinds", sorted((k[0], k[2]) for k in members if k[0] != "field"),
          [("const", "limit"), ("const", "other"), ("var", "names"), ("var", "pick")])
    check("go var lines span the spec", members[("var", "", "names")]["lines"], 3)
    if RCA.exists():
        rs = work / "rs"
        rs.mkdir()
        (work / "out").mkdir()
        (rs / "a.rs").write_text("\n".join(r for _, r, _ in GO_CASES.values() if r) + "\n")
        subprocess.run([str(RCA), "-m", "-p", "rs", "-I", "*.rs", "-O", "json", "-o", "out"], cwd=work, check=True, capture_output=True)
        d = json.loads(next((work / "out").rglob("*.json")).read_text())
        rca = {s["name"]: (int(s["metrics"]["cognitive"]["sum"]), int(s["metrics"]["cyclomatic"]["sum"])) for s in d["spaces"]}
        for name, (_, r, _) in GO_CASES.items():
            if r and name != "r":
                check(f"rca {name} equals gocog", rca[name], (funcs[name]["cog"], funcs[name]["cyc"]))
        # the residue rule: an else-if chain costs the Go tagless switch's residue more, nothing else
        check("rca r less gocog r is the residue", rca["r"][0] - funcs["r"]["cog"], funcs["r"]["residue"])
    else:
        print(f"skip rca cross-check: {RCA} missing (quality.sh install)")
    shutil.rmtree(work, ignore_errors=True)


MEMBERS_GO = """package x

type Inner struct{}

type Box struct {
	Name, Size string
	Body       *Inner
	Inner
}

var (
	names = map[string]int{
		"a": 1,
	}
	pick = func(b bool) int {
		if b {
			return 1
		} else {
			return 2
		}
	}
)

const limit, other = 10, 20
"""


# --------------------------------------------------------------------------
# mapper

def gofn(pkg, file, line, recv, name, cog, end=None, closures=(), lines=10):
    return {"pkg": pkg, "file": file, "line": line, "end": end or line + lines - 1, "recv": recv, "name": name,
            "cog": cog, "cyc": cog + 1, "lines": lines, "closures": list(closures)}


def rust(file, name, start, end, cog):
    return {"key": f"{file}::{name}", "file": file, "name": name, "start": start, "end": end, "cog": cog, "cyc": cog + 1,
            "sloc": end - start + 1}


def mapper_cases() -> None:
    check("norm", gorel.norm("r#type_"), "type")
    check("split name", gorel.split_rust_name("FilesParser::get_processed_files::Collector<'_>::collect_files"),
          ("collect_files", "Collector"))
    check("split name free fn in mod", gorel.split_rust_name("project::helpers::run"), ("run", None))
    m = gorel.parse_marker("    // Go: checker/relater.go:3294 structuredTypeRelatedToWorker")
    check("marker path/line/name", (m["path"], m["line"], m["recv"], m["name"]), ("checker/relater.go", 3294, None, "structuredTypeRelatedToWorker"))
    m = gorel.parse_marker("// Go: build/orchestrator.go:921 (*Orchestrator).rangeTasks over")
    check("marker (*T).name", (m["recv"], m["name"]), ("Orchestrator", "rangeTasks"))
    m = gorel.parse_marker("// Go: (r *Relater) isRelatedTo (ts#123)")
    check("marker (r *T) name", (m["path"], m["recv"], m["name"]), (None, "Relater", "isRelatedTo"))
    check("statement note is no marker", gorel.parse_marker("// Go: w.wg.Wait()"), None)

    C = "crates/ts_goport/src/checker/"
    L = "crates/ts_goport/src/ls/"
    go = [
        gofn("internal/checker", "relater.go", 100, "Relater", "isRelatedTo", 40),
        gofn("internal/checker", "relater.go", 200, "Checker", "isSimpleTypeRelatedTo", 20),
        gofn("internal/checker", "checker.go", 300, "Checker", "GetTypeOfSymbol", 1),
        gofn("internal/checker", "checker.go", 310, "Checker", "getTypeOfSymbol", 30),
        gofn("internal/checker", "checker.go", 400, "Checker", "checkBinaryLikeExpression", 50, lines=200),
        gofn("internal/checker", "checker.go", 700, "Checker", "getCompletionThing", 60,
             closures=[{"name": "addItem", "line": 710, "end": 720, "cog": 5, "cyc": 3, "lines": 11, "cog_inplace": 8}]),
        gofn("internal/checker", "checker.go", 800, "Checker", "checkThing", 10),
        gofn("internal/checker", "checker.go", 900, "Checker", "relateWithKinds", 12),
        gofn("internal/checker", "scope.go", 10, "Scope", "Lookup", 3),
        gofn("internal/checker", "scope.go", 20, "Scope", "relateWithKinds", 12),
        gofn("internal/ast", "ast_generated.go", 10, "IfStatement", "ForEachChild", 2),
        gofn("internal/ast", "ast_generated.go", 20, "Block", "ForEachChild", 1),
        gofn("internal/ast", "ast_generated.go", 30, "ForStatement", "ForEachChild", 3),
        gofn("internal/ls", "inlay_hints.go", 72, "inlayHintState", "visit", 21),
        gofn("internal/ls", "symbols.go", 100, "", "getDocumentSymbols", 9,
             closures=[{"name": "visit", "line": 147, "end": 190, "cog": 12, "cyc": 9, "lines": 44, "cog_inplace": 15}]),
        gofn("internal/scanner", "scanner.go", 50, "", "NewScanner", 1),
        gofn("internal/ipc", "transport.go", 55, "PipeTransport", "close", 3),
        gofn("internal/checker", "printer.go", 55, "Checker", "TypeToStringEx", 0, lines=3),
        gofn("internal/checker", "printer.go", 59, "Checker", "typeToStringEx", 17),
        gofn("internal/ls/lsutil", "userpreferences_generated.go", 193, "", "parsePreferenceIndentStyle", 4),
        # a tagless switch of 4 clauses: residue 3
        {**gofn("internal/checker", "checker.go", 950, "Checker", "getApparentKind", 5), "residue": 3},
        # fields and vars, which only markers name
        {**gofn("internal/ast", "ast_generated.go", 160, "FunctionLikeBase", "FullSignature", 0, lines=1), "kind": "field"},
        {**gofn("internal/ast", "ast_generated.go", 300, "Other", "FullSignature", 0, lines=1), "kind": "field"},
        {**gofn("internal/ls", "codeactions.go", 71, "", "codeFixProviders", 0, lines=40), "kind": "var"},
        {**gofn("internal/checker", "relater.go", 50, "", "relateWithKinds", 0, lines=1), "kind": "var"},
    ]
    gi = gorel.GoIndex(go)
    ext = [
        gofn("std/regexp/syntax", "parse.go", 891, "", "parse", 101),
        gofn("std/time", "format_rfc3339.go", 20, "", "daysIn", 2),
        gofn("std/flag", "flag.go", 1075, "FlagSet", "parseOne", 35),
        gofn("x/text/internal/language", "parse.go", 49, "", "NewValueError", 0),
        gofn("x/text/language", "parse.go", 49, "", "NewValueError", 7),
        gofn("x/text/internal/language", "match.go", 71, "", "addTags", 89),
    ]
    xi = gorel.GoIndex(ext)
    files = {
        C + "relater_p1.rs": [
            "impl Checker {",                                          # 1
            "    // Go: checker/relater.go:98 isRelatedTo",            # 2
            "    /// docs",                                            # 3
            "    #[inline]",                                           # 4
            "    pub fn is_related_to(&mut self) {}",                  # 5
            "    // Go: checker/relater.go:200 isSimpleTypeRelatedTo",  # 6
            "    pub fn is_simple_type_related_to(&mut self) {}",      # 7
            "    // PORT: perf. Go isSimpleTypeRelatedTo with the relation as a",  # 8
            "    // `RelationKind`.",                                  # 9
            "    pub fn is_simple_type_related_to_kind(&mut self) {}",  # 10
            "    pub fn get_type_of_symbol(&mut self) {}",             # 11
            "    pub fn get_type_of_symbol_exported(&mut self) {}",    # 12
            "    pub fn relate_with_kinds(&mut self) {}",              # 13
            "}",
        ] + [""] * 20,
        C + "checker_p14.rs": [
            "impl Checker {",                                            # 1
            "    // Go: checker.go:400 checkBinaryLikeExpression",        # 2
            "    pub fn check_binary_like_expression(&mut self) {}",      # 3
            "    /// The operator checks (Go",                            # 4
            "    /// checker.go:410-590).",                               # 5
            "    pub fn check_binary_like_expression_worker(&mut self) {}",  # 6
            "    // Go: checker.go:700 getCompletionThing",              # 7
            "    pub fn get_completion_thing(&mut self) {}",             # 8
            "    pub fn add_item(&mut self) {}",                         # 9
            "    // Go: checker.go:800 checkThing",                      # 10
            "    pub fn check_thing(&mut self) {",                       # 11
            "        fn walk() {}",                                      # 12
            "        // Go: scope.go:10 Lookup",                         # 13
            "        fn lookup() {}",                                    # 14
            "    }",                                                     # 15
            "    // Go: x/text/language.go:12 parseTag",                 # 16
            "    pub fn helper(&mut self) {}",                           # 17
            "}",
        ],
        "crates/ts_goport/src/ast/node.rs": [
            "impl Node {",
            "    // Go: ast_generated.go:8769 ForEachChild",
            "    pub fn for_each_child(self) {}",
            "}",
        ],
        L + "symbols.rs": [
            "impl DocumentSymbolsVisitor {",
            "    // Go: ls/symbols.go:147 visit",
            "    fn visit(&mut self) {}",
            "}",
        ],
        L + "semantictokens.rs": [
            "impl SemanticTokenCollector {",
            "    fn visit(&mut self) {}",
            "}",
        ],
        "crates/ts_goport/src/frontend/scanner/scanner_p1.rs": [
            "impl Scanner {",
            "    pub fn new() -> Self {}",
            "}",
        ],
        "crates/ts_goport/src/ipc/transport.rs": [
            "impl PipeTransport {",
            "    #[cfg(unix)]",
            "    // Go: ipc/transport.go:55 close",
            "    fn close(&self) {}",
            "    #[cfg(windows)]",
            "    // Go: ipc/transport.go:55 close",
            "    fn close(&self) {}",
            "}",
        ],
        C + "printer_impl.rs": [
            "impl Checker {",
            "    // Go: checker/printer.go:55 TypeToStringEx and checker/printer.go:59 typeToStringEx",
            "    pub fn type_to_string_ex(&mut self) {}",
            "}",
        ],
        "crates/ts_goport/src/ls/lsutil/formatcodeoptions.rs": [
            "// Go: ls/lsutil/formatcodeoptions.go:19 parseIndentStyle (at 673a5f17d713; ts#64554 generates it:",
            "// ls/lsutil/userpreferences_generated.go:193 parsePreferenceIndentStyle)",
            "fn parse_indent_style() {}",
        ],
        "crates/ts_goport/src/locale.rs": [
            "// Go: language/parse.go:1 parseTag",
            "fn parse_tag() {}",
            "// Go: language/parse.go:2 parseExtension",
            "fn parse_extension() {}",
            "// Go: language/match.go:3 addTags",
            "fn add_tags() {}",
            "fn is_simple_type_related_to() {}",
            "// Go: internal/language/parse.go:49 NewValueError",
            "fn new_value_error() {}",
        ],
        "crates/ts_goport/src/gostd/regexp.rs": [
            "// Go: regexp/syntax/parse.go:891 parse",
            "fn parse() {}",
            "// Go: time.daysIn with time.isLeap",
            "fn days_in() {}",
            "// Go: strings.daysIn",
            "fn days_in_wrong_qualifier() {}",
        ],
        "crates/ts_goport/src/cmd/tsgo/main.rs": [
            "impl FlagSet {",
            "    // Go: flag.go:1075 (*FlagSet).parseOne",
            "    fn parse_one(&self) {}",
            "}",
        ],
        "crates/ts_goport/src/effect/rules.rs": [
            "// Go: regexp/syntax/parse.go:891 parse",
            "fn effect_parse() {}",
        ],
        "crates/ts_goport/src/ast/fields.rs": [
            "impl Node {",
            "    // Go: ast/ast_generated.go:160 FullSignature",
            "    pub fn full_signature(self) {}",
            "    pub fn code_fix_providers(self) {}",
            "}",
        ],
        "crates/ts_goport/src/ls/codeactions.rs": [
            "// Go: ls/codeactions.go:71 codeFixProviders",
            "fn code_fix_providers() {}",
        ],
        C + "checker_p24.rs": [
            "impl Checker {",
            "    // Go: checker/checker.go:950 getApparentKind",
            "    pub fn get_apparent_kind(&mut self) {}",
            "}",
        ],
    }
    funcs = [
        rust(C + "relater_p1.rs", "Checker::is_related_to", 5, 5, 45),
        rust(C + "relater_p1.rs", "Checker::is_simple_type_related_to", 7, 7, 2),
        rust(C + "relater_p1.rs", "Checker::is_simple_type_related_to_kind", 10, 10, 25),
        rust(C + "relater_p1.rs", "Checker::get_type_of_symbol", 11, 11, 31),
        rust(C + "relater_p1.rs", "Checker::get_type_of_symbol_exported", 12, 12, 1),
        rust(C + "relater_p1.rs", "Checker::relate_with_kinds", 13, 13, 12),
        rust(C + "checker_p14.rs", "Checker::check_binary_like_expression", 3, 3, 10),
        rust(C + "checker_p14.rs", "Checker::check_binary_like_expression_worker", 6, 6, 50),
        rust(C + "checker_p14.rs", "Checker::get_completion_thing", 8, 8, 50),
        rust(C + "checker_p14.rs", "Checker::add_item", 9, 9, 6),
        rust(C + "checker_p14.rs", "Checker::check_thing", 11, 15, 20),
        rust(C + "checker_p14.rs", "Checker::check_thing::walk", 12, 12, 4),
        rust(C + "checker_p14.rs", "Checker::check_thing::lookup", 14, 14, 3),
        rust(C + "checker_p14.rs", "Checker::helper", 17, 17, 30),
        rust("crates/ts_goport/src/ast/node.rs", "Node::for_each_child", 3, 3, 40),
        rust(L + "symbols.rs", "DocumentSymbolsVisitor::visit", 3, 3, 14),
        rust(L + "semantictokens.rs", "SemanticTokenCollector::visit", 2, 2, 88),
        rust("crates/ts_goport/src/frontend/scanner/scanner_p1.rs", "Scanner::new", 2, 2, 0),
        rust(C + "printer_impl.rs", "Checker::type_to_string_ex", 3, 3, 17),
        {**rust("crates/ts_goport/src/ipc/transport.rs", "PipeTransport::close", 4, 4, 4)},
        {**rust("crates/ts_goport/src/ipc/transport.rs", "PipeTransport::close", 7, 7, 6), "key": "crates/ts_goport/src/ipc/transport.rs::PipeTransport::close#2"},
        rust("crates/ts_goport/src/ls/lsutil/formatcodeoptions.rs", "parse_indent_style", 3, 3, 4),
        rust("crates/ts_goport/src/locale.rs", "parse_tag", 2, 2, 5),
        rust("crates/ts_goport/src/locale.rs", "parse_extension", 4, 4, 5),
        rust("crates/ts_goport/src/locale.rs", "add_tags", 6, 6, 5),
        rust("crates/ts_goport/src/locale.rs", "is_simple_type_related_to", 7, 7, 5),
        rust("crates/ts_goport/src/locale.rs", "new_value_error", 9, 9, 1),
        rust("crates/ts_goport/src/gostd/regexp.rs", "parse", 2, 2, 98),
        rust("crates/ts_goport/src/gostd/regexp.rs", "days_in", 4, 4, 3),
        rust("crates/ts_goport/src/gostd/regexp.rs", "days_in_wrong_qualifier", 6, 6, 3),
        rust("crates/ts_goport/src/cmd/tsgo/main.rs", "FlagSet::parse_one", 3, 3, 27),
        rust("crates/ts_goport/src/effect/rules.rs", "effect_parse", 2, 2, 30),
        rust("crates/ts_goport/src/ast/fields.rs", "Node::full_signature", 3, 3, 1),
        rust("crates/ts_goport/src/ast/fields.rs", "Node::code_fix_providers", 4, 4, 1),
        rust("crates/ts_goport/src/ls/codeactions.rs", "code_fix_providers", 2, 2, 2),
        rust(C + "checker_p24.rs", "Checker::get_apparent_kind", 3, 3, 15),
    ]
    rfs = gorel.map_functions(funcs, files, gi, xi)
    by = {rf["name"]: rf for rf in rfs}

    def placed(name):
        rf = by[name]
        return (rf["conf"], rf["go"]["recv"] + "." + rf["go"]["name"] if rf["go"] else None)

    check("marker over a doc and an attribute", placed("Checker::is_related_to"), ("marker", "Relater.isRelatedTo"))
    check("prose `Go fooBar` names a split", placed("Checker::is_simple_type_related_to_kind"), ("prose+split", "Checker.isSimpleTypeRelatedTo"))
    check("prose line range names a split", placed("Checker::check_binary_like_expression_worker"),
          ("prose+split", "Checker.checkBinaryLikeExpression"))
    check("name: unexported of an exported pair", placed("Checker::get_type_of_symbol"), ("high", "Checker.getTypeOfSymbol"))
    check("name: foo_exported is Go's Foo", placed("Checker::get_type_of_symbol_exported"), ("high", "Checker.GetTypeOfSymbol"))
    check("name: same receiver beats another Go type's", placed("Checker::relate_with_kinds"), ("high", "Checker.relateWithKinds"))
    check("name: T::new is NewT", placed("Scanner::new"), ("high", ".NewScanner"))
    check("name: no match across unrelated receivers", placed("SemanticTokenCollector::visit"), ("none", None))
    check("marker naming a Go closure", placed("DocumentSymbolsVisitor::visit"), ("marker", ".getDocumentSymbols"))
    check("marker naming a method family", (by["Node::for_each_child"]["go"]["id"], by["Node::for_each_child"]["go"]["cog"]),
          ("internal/ast/ast_generated.go:*ForEachChild", 6))
    check("lifted closure by name", (placed("Checker::add_item"), by["Checker::add_item"]["part_of"]),
          (("lifted", "Checker.getCompletionThing"), C + "checker_p14.rs::Checker::get_completion_thing"))
    check("compound marker: two Go functions joined by `and`", (by["Checker::type_to_string_ex"]["go"]["name"], by["Checker::type_to_string_ex"]["go"]["cog"]),
          ("TypeToStringEx + typeToStringEx", 17))
    check("marker paragraph: a later reference after a rename", placed("parse_indent_style"), ("marker", ".parsePreferenceIndentStyle"))
    check("nested fn with a marker has its own row", placed("Checker::check_thing::lookup"), ("marker", "Scope.Lookup"))
    check("nested fn without one stays in its parent", placed("Checker::check_thing::walk"), ("none", None))
    check("marker outside the pin: own code", placed("Checker::helper"), ("none", None))
    check("file whose markers name other Go: no name matching", placed("is_simple_type_related_to"), ("none", None))
    check("marker naming a struct field (the line picks the struct)", (by["Node::full_signature"]["conf"], by["Node::full_signature"]["go"]["id"]),
          ("marker", "internal/ast/ast_generated.go:160:FullSignature"))
    check("marker naming a package var", (by["code_fix_providers"]["conf"], by["code_fix_providers"]["go"]["kind"]), ("marker", "var"))
    check("name matcher ignores vars and fields", placed("Node::code_fix_providers"), ("none", None))
    check("name matcher: a function, not a var of the same name", placed("Checker::relate_with_kinds"), ("high", "Checker.relateWithKinds"))

    def ext_placed(name):
        rf = by[name]
        return (rf["conf"], rf["go"]["id"] if rf["go"] else None)

    check("std marker resolves in GOROOT", ext_placed("parse"), ("ext", "std/regexp/syntax/parse.go:891"))
    check("x/text marker keeps its internal/ path", ext_placed("new_value_error"), ("ext", "x/text/internal/language/parse.go:49"))
    check("x/text marker without internal/: the one file of that name holding the function", ext_placed("add_tags"), ("ext", "x/text/internal/language/match.go:71"))
    check("std marker without a path: a qualifier the hit agrees with", ext_placed("days_in"), ("ext", "std/time/format_rfc3339.go:20"))
    check("std marker without a path: a qualifier it does not agree with", ext_placed("days_in_wrong_qualifier"), ("none", None))
    check("flag marker by file name", ext_placed("FlagSet::parse_one"), ("ext", "std/flag/flag.go:1075"))
    check("effect/ markers are not looked up outside the pin", ext_placed("effect_parse"), ("none", None))
    check("locale.rs stays out of name matching", placed("is_simple_type_related_to"), ("none", None))

    ports, own = gorel.build_rows(rfs)
    rows = {r["name"]: r for r in ports}
    r = rows["Checker::is_simple_type_related_to"]
    check("split summed into the head row", (r["cog"], r["go_cog"], [p["name"] for p in r["parts"]]),
          (27, 20, ["Checker::is_simple_type_related_to_kind"]))
    r = rows["Checker::get_completion_thing"]
    # Rust 50 + lifted 6; Go 60 with the closure in place (8) replaced by its standalone figure (5)
    check("lifted closure: Rust sum, Go standalone", (r["cog"], r["go_cog"], r["diff"]), (56, 57, -1))
    r = next(r for r in ports if r["name"] == "PipeTransport::close")
    check("cfg variants of one Go function: the larger, not the sum", (r["cog"], r["go_cog"], [p["kind"] for p in r["parts"]]),
          (6, 3, ["variant"]))
    r = rows["Checker::check_thing"]
    check("parent less its nested row", r["cog"], 17)
    check("own rows", sorted(x["name"] for x in own),
          ["Checker::helper", "Node::code_fix_providers", "SemanticTokenCollector::visit", "days_in_wrong_qualifier", "effect_parse",
           "is_simple_type_related_to", "parse_extension", "parse_tag"])
    r = rows["parse"]
    check("std row: tree, Δ against GOROOT", (r["tree"], r["diff"], r["go_cog"]), ("std", -3, 101))
    r = rows["Checker::get_apparent_kind"]
    check("residue row: Δ, residue, Δ less residue", (r["diff"], r["residue"], r["diff_less_residue"]), (10, 3, 7))
    r = rows["Checker::get_type_of_symbol"]
    check("no residue: Δ less residue is Δ", (r["diff"], r["residue"], r["diff_less_residue"]), (1, 0, 1))
    neg = gorel.build_rows([{**rf, "cog": 4} for rf in rfs if rf["name"] == "Checker::get_apparent_kind"])[0][0]
    check("residue explains only a positive Δ: Δ −1 stays −1", (neg["diff"], neg["residue"], neg["diff_less_residue"]), (-1, 3, -1))
    s = gorel.summarize_rows(rfs, ports, own)
    check("summary: own over 25 (Checker::helper, SemanticTokenCollector::visit, effect_parse)", s["own_gt25"], 3)
    check("summary: rows against Go outside the pin", s["port_ext_rows"], {"std": 3, "x/text": 2})
    # Δ ≥ 10: check_binary_like_expression (10), Node::for_each_child (34), get_apparent_kind (10, 7 less residue)
    check("summary: Δ ≥10 rows still ≥10 less residue", (s["port_diff_ge10"], s["port_diff_ge10_less_residue_ge10"]), (3, 2))
    check("summary: residue of the Δ ≥10 rows", s["port_target_residue"], 3)


def main() -> None:
    cache = Path(tempfile.mkdtemp(prefix="gorel-test-"))
    try:
        go_metric_cases(cache)
        mapper_cases()
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    print("SELFTEST FAIL: " + ", ".join(FAILS) if FAILS else "SELFTEST OK")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
