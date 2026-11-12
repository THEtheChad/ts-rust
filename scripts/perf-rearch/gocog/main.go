// Command gocog prints per-function complexity of a Go source tree as JSON, for the Go-relative
// ranking of scripts/perf-rearch/quality.py (docs/perf-rearch/quality.md, "Go-relative complexity").
//
// usage: gocog <root> <dir>...   (for example gocog ~/.explore/repos/microsoft__TypeScript@<pin>/tsc internal cmd)
//
// It reads every non-test .go file under each <root>/<dir>, skipping testdata and vendor
// directories, and prints one JSON object:
// {"version": N, "funcs": [{pkg, file, line, end, recv, name, cog, cyc, lines, residue, kind,
// generated, closures: [...]}]}.
// "generated" marks a file headed "Code generated ... DO NOT EDIT." (ast_generated.go and the like):
// the Rust port writes some of that code by hand, so it stays in the list.
//
// "kind" is empty for a function or method. The list also holds the other named things a `// Go:`
// marker can name:
//   - "field": a named field of a package-level struct type (recv is the struct's name). An embedded
//     field has no name of its own and is not listed. cog and cyc are 0.
//   - "var" and "const": a name declared by a package-level `var` or `const`. Its figures are those
//     of its value: a function literal is scored like a function (its body at nesting 0, cyc 1 + its
//     own), any other value is walked at nesting 0, so a function literal inside it counts one
//     nesting deeper, as a Rust closure in a `static` initializer does. A name with no value of its
//     own scores 0. Lines span the spec.
//
// "residue" is the tagless-switch residue of a function (its function literals included): for each
// `switch` with no tag expression (`switch {` or `switch x := f(); {`), its number of clauses less
// one, `default` counted as a clause. A Rust port writes such a switch as an if / else-if / else
// chain, and rca charges each `else` (else-if included) +1 where Go's case labels cost nothing, so
// the chain scores exactly this many points more than the switch (the conditions and bodies score
// alike on both sides). docs/perf-rearch/quality.md, "Tagless-switch residue", has the rule.
// Standard library only (go/ast, go/parser); it downloads nothing and writes nothing.
//
// The metrics mirror rust-code-analysis 0.0.25's Rust rules (src/metrics/cognitive.rs and
// cyclomatic.rs, `impl ... for RustCode`), so a Go function and its Rust port are scored alike:
//
// Cognitive (SonarSource, as rca implements it):
//   - `if` (not an else-if), `for` with a condition, init or post, `range`, `switch`, type switch and
//     `select`: +1 + nesting, and nesting+1 for everything under the node (its condition included).
//     A bare `for {}` is Rust's `loop`, which rca does not count: +0 and no nesting.
//   - each `else` (else-if included): +1.
//   - labeled `break`/`continue`, and `goto`: +1.
//   - `&&`/`||`: rca's BoolSequence. The first operator of a sequence counts +1 and is remembered;
//     each later one counts +1 when it differs from the remembered operator (which it does not
//     replace). The state is one value per function (or function literal) walk in source order: it
//     is reset only by a nesting increment, and a unary operator (`!`, `-`, `+`, `^`, deref `*`)
//     sets it to "unary", so every later `&&`/`||` counts until the next reset.
//   - a function literal adds 1 to the nesting of everything inside it (rca's `lambda`), and its
//     increments count toward the enclosing function, as Rust closures do in rca.
//
// Cyclomatic: 1 per function and per function literal, +1 per `if`, `for`, `range`, case clause
// (default included, as rca counts every match arm), select comm clause, `&&` and `||`.
//
// Lines: from the `func` keyword to the closing brace, as rca's sloc spans `fn` to `}`.
//
// Each named function literal (`name := func`, `name = func`, `var name = func`) is also listed under
// its function in "closures", with its standalone figures (walked as if it were a top-level function)
// and its in-place cognitive contribution to the enclosing function, so a Rust port that lifted the
// closure into its own function can be compared fairly.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

const version = 3

var generatedRE = regexp.MustCompile(`(?m)^// Code generated .* DO NOT EDIT\.$`)

type Closure struct {
	Name    string `json:"name"`
	Line    int    `json:"line"`
	End     int    `json:"end"`
	Cog     int    `json:"cog"`
	Cyc     int    `json:"cyc"`
	Lines   int    `json:"lines"`
	InPlace int    `json:"cog_inplace"`
	Residue int    `json:"residue"`
}

type Func struct {
	Pkg      string    `json:"pkg"`
	File     string    `json:"file"`
	Line     int       `json:"line"`
	End      int       `json:"end"`
	Recv     string    `json:"recv"`
	Name     string    `json:"name"`
	Cog      int       `json:"cog"`
	Cyc      int       `json:"cyc"`
	Lines    int       `json:"lines"`
	Residue  int       `json:"residue"`
	Kind     string    `json:"kind,omitempty"`
	Gen      bool      `json:"generated,omitempty"`
	Closures []Closure `json:"closures,omitempty"`
}

// boolState is rca's BoolSequence: the previous boolean operator of the current sequence.
type boolState int

const (
	boolNone boolState = iota
	boolUnary
	boolAnd
	boolOr
)

// walker scores one function body. nesting carries rca's nesting + depth + lambda for the node
// being visited.
type walker struct {
	fset     *token.FileSet
	cog      int
	cyc      int
	residue  int
	seq      boolState
	closures []Closure
	// named closures are recorded only on the top-level walk
	record bool
}

func (w *walker) increaseNesting(nesting int) {
	w.cog += nesting + 1
	w.seq = boolNone
}

// boolOp is rca's eval_based_on_prev: the first operator of a sequence counts and is remembered;
// later ones count when they differ from the remembered one, which they do not replace.
func (w *walker) boolOp(op boolState) {
	if w.seq == boolNone {
		w.seq = op
		w.cog++
	} else if w.seq != op {
		w.cog++
	}
}

// stmts and exprs walk children at a nesting level.
func (w *walker) stmts(list []ast.Stmt, nesting int) {
	for _, s := range list {
		w.node(s, nesting)
	}
}

func (w *walker) exprs(list []ast.Expr, nesting int) {
	for _, e := range list {
		w.node(e, nesting)
	}
}

func (w *walker) ifStmt(s *ast.IfStmt, nesting int, elseIf bool) {
	w.cyc++
	inner := nesting
	if !elseIf {
		w.increaseNesting(nesting)
		inner = nesting + 1
	}
	w.node(s.Init, inner)
	w.node(s.Cond, inner)
	w.node(s.Body, inner)
	if s.Else != nil {
		w.cog++ // else, or else-if
		if e, ok := s.Else.(*ast.IfStmt); ok {
			w.ifStmt(e, inner, true)
		} else {
			w.node(s.Else, inner)
		}
	}
}

// funcLit walks a function literal in place (nesting+1) and, when it is named, records its
// standalone figures.
func (w *walker) funcLit(f *ast.FuncLit, nesting int, name string) {
	// a function literal is its own rca space: a fresh boolean sequence, restored after it
	before, seq := w.cog, w.seq
	w.seq = boolNone
	w.cyc++
	w.node(f.Body, nesting+1)
	w.seq = seq
	if name != "" && w.record {
		sub := &walker{fset: w.fset}
		sub.cyc = 1
		sub.node(f.Body, 0)
		start := w.fset.Position(f.Pos()).Line
		end := w.fset.Position(f.End()).Line
		w.closures = append(w.closures, Closure{
			Name: name, Line: start, End: end, Cog: sub.cog, Cyc: sub.cyc, Lines: end - start + 1,
			InPlace: w.cog - before, Residue: sub.residue,
		})
	}
}

// assignedFuncLits walks `a, b := func..., x` with the closure names known.
func (w *walker) assigned(lhs []ast.Expr, rhs []ast.Expr, nesting int) {
	for i, r := range rhs {
		if fl, ok := r.(*ast.FuncLit); ok {
			name := ""
			if len(lhs) == len(rhs) {
				if id, ok := lhs[i].(*ast.Ident); ok {
					name = id.Name
				}
			}
			w.funcLit(fl, nesting, name)
		} else {
			w.node(r, nesting)
		}
	}
}

func (w *walker) node(n ast.Node, nesting int) {
	switch n := n.(type) {
	case nil:
	case *ast.BlockStmt:
		if n != nil {
			w.stmts(n.List, nesting)
		}
	case *ast.IfStmt:
		w.ifStmt(n, nesting, false)
	case *ast.ForStmt:
		w.cyc++
		inner := nesting
		if n.Init != nil || n.Cond != nil || n.Post != nil {
			w.increaseNesting(nesting)
			inner = nesting + 1
		}
		w.node(n.Init, inner)
		w.node(n.Cond, inner)
		w.node(n.Post, inner)
		w.node(n.Body, inner)
	case *ast.RangeStmt:
		w.cyc++
		w.increaseNesting(nesting)
		w.node(n.Key, nesting+1)
		w.node(n.Value, nesting+1)
		w.node(n.X, nesting+1)
		w.node(n.Body, nesting+1)
	case *ast.SwitchStmt:
		if n.Tag == nil && len(n.Body.List) > 1 {
			w.residue += len(n.Body.List) - 1
		}
		w.increaseNesting(nesting)
		w.node(n.Init, nesting+1)
		w.node(n.Tag, nesting+1)
		w.node(n.Body, nesting+1)
	case *ast.TypeSwitchStmt:
		w.increaseNesting(nesting)
		w.node(n.Init, nesting+1)
		w.node(n.Assign, nesting+1)
		w.node(n.Body, nesting+1)
	case *ast.SelectStmt:
		w.increaseNesting(nesting)
		w.node(n.Body, nesting+1)
	case *ast.CaseClause:
		w.cyc++
		w.exprs(n.List, nesting)
		w.stmts(n.Body, nesting)
	case *ast.CommClause:
		w.cyc++
		w.node(n.Comm, nesting)
		w.stmts(n.Body, nesting)
	case *ast.BranchStmt:
		if n.Tok == token.GOTO || (n.Label != nil && (n.Tok == token.BREAK || n.Tok == token.CONTINUE)) {
			w.cog++
		}
	case *ast.LabeledStmt:
		w.node(n.Stmt, nesting)
	case *ast.BinaryExpr:
		switch n.Op {
		case token.LAND:
			w.cyc++
			w.boolOp(boolAnd)
		case token.LOR:
			w.cyc++
			w.boolOp(boolOr)
		}
		w.node(n.X, nesting)
		w.node(n.Y, nesting)
	case *ast.UnaryExpr:
		if n.Op == token.NOT || n.Op == token.SUB || n.Op == token.ADD || n.Op == token.XOR {
			w.seq = boolUnary
		}
		w.node(n.X, nesting)
	case *ast.StarExpr:
		w.seq = boolUnary
		w.node(n.X, nesting)
	case *ast.FuncLit:
		w.funcLit(n, nesting, "")
	case *ast.AssignStmt:
		w.exprs(n.Lhs, nesting)
		w.assigned(n.Lhs, n.Rhs, nesting)
	case *ast.DeclStmt:
		if g, ok := n.Decl.(*ast.GenDecl); ok {
			for _, sp := range g.Specs {
				if v, ok := sp.(*ast.ValueSpec); ok {
					lhs := make([]ast.Expr, len(v.Names))
					for i, id := range v.Names {
						lhs[i] = id
					}
					w.assigned(lhs, v.Values, nesting)
				}
			}
		}
	// types hold no control flow; skipping them keeps `*T` out of the boolean sequence state
	case *ast.TypeAssertExpr:
		w.node(n.X, nesting)
	case *ast.CompositeLit:
		w.exprs(n.Elts, nesting)
	case *ast.ExprStmt:
		w.node(n.X, nesting)
	case *ast.ReturnStmt:
		w.exprs(n.Results, nesting)
	case *ast.DeferStmt:
		w.node(n.Call, nesting)
	case *ast.GoStmt:
		w.node(n.Call, nesting)
	case *ast.SendStmt:
		w.node(n.Chan, nesting)
		w.node(n.Value, nesting)
	case *ast.IncDecStmt:
		w.node(n.X, nesting)
	case *ast.CallExpr:
		w.node(n.Fun, nesting)
		w.exprs(n.Args, nesting)
	case *ast.ParenExpr:
		w.node(n.X, nesting)
	case *ast.SelectorExpr:
		w.node(n.X, nesting)
	case *ast.IndexExpr:
		w.node(n.X, nesting)
		w.node(n.Index, nesting)
	case *ast.IndexListExpr:
		w.node(n.X, nesting)
		w.exprs(n.Indices, nesting)
	case *ast.SliceExpr:
		w.node(n.X, nesting)
		w.node(n.Low, nesting)
		w.node(n.High, nesting)
		w.node(n.Max, nesting)
	case *ast.KeyValueExpr:
		w.node(n.Key, nesting)
		w.node(n.Value, nesting)
	case *ast.Ident, *ast.BasicLit, *ast.EmptyStmt, *ast.BadStmt, *ast.BadExpr,
		*ast.ArrayType, *ast.MapType, *ast.ChanType, *ast.FuncType, *ast.StructType, *ast.InterfaceType, *ast.Ellipsis:
	default:
		panic(fmt.Sprintf("gocog: unhandled node %T at %s", n, w.fset.Position(n.Pos())))
	}
}

func recvName(fl *ast.FieldList) string {
	if fl == nil || len(fl.List) == 0 {
		return ""
	}
	t := fl.List[0].Type
	for {
		switch x := t.(type) {
		case *ast.StarExpr:
			t = x.X
		case *ast.ParenExpr:
			t = x.X
		case *ast.IndexExpr:
			t = x.X
		case *ast.IndexListExpr:
			t = x.X
		case *ast.Ident:
			return x.Name
		default:
			return ""
		}
	}
}

// Score walks one function declaration.
func Score(fset *token.FileSet, d *ast.FuncDecl) (cog, cyc, residue int, closures []Closure) {
	w := &walker{fset: fset, record: true, cyc: 1}
	w.node(d.Body, 0)
	return w.cog, w.cyc, w.residue, w.closures
}

// scoreValue scores the value of a package-level var or const: a function literal like a function,
// anything else walked in place (nil scores 0).
func scoreValue(fset *token.FileSet, v ast.Expr) (cog, cyc, residue int) {
	w := &walker{fset: fset}
	if fl, ok := v.(*ast.FuncLit); ok {
		w.cyc = 1
		w.node(fl.Body, 0)
	} else {
		w.node(v, 0)
	}
	return w.cog, w.cyc, w.residue
}

// members lists the fields of a package-level struct type and the names of a package-level var or
// const declaration.
func members(fset *token.FileSet, g *ast.GenDecl, pkg, file string, gen bool) []Func {
	var out []Func
	line := func(p token.Pos) int { return fset.Position(p).Line }
	for _, sp := range g.Specs {
		switch s := sp.(type) {
		case *ast.TypeSpec:
			st, ok := s.Type.(*ast.StructType)
			if !ok || st.Fields == nil {
				continue
			}
			for _, f := range st.Fields.List {
				for _, id := range f.Names {
					l := line(id.Pos())
					out = append(out, Func{Pkg: pkg, File: file, Line: l, End: line(f.End()), Recv: s.Name.Name,
						Name: id.Name, Lines: line(f.End()) - l + 1, Kind: "field", Gen: gen})
				}
			}
		case *ast.ValueSpec:
			kind := "var"
			if g.Tok == token.CONST {
				kind = "const"
			}
			start, end := line(s.Pos()), line(s.End())
			for i, id := range s.Names {
				f := Func{Pkg: pkg, File: file, Line: start, End: end, Name: id.Name, Lines: end - start + 1, Kind: kind, Gen: gen}
				if len(s.Values) == len(s.Names) {
					f.Cog, f.Cyc, f.Residue = scoreValue(fset, s.Values[i])
				}
				out = append(out, f)
			}
		}
	}
	return out
}

func scanFile(fset *token.FileSet, root, path string) ([]Func, error) {
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	head := src
	if i := strings.Index(string(src), "\npackage "); i >= 0 {
		head = src[:i]
	}
	gen := generatedRE.Match(head)
	f, err := parser.ParseFile(fset, path, src, parser.SkipObjectResolution)
	if err != nil {
		return nil, err
	}
	rel, _ := filepath.Rel(root, path)
	rel = filepath.ToSlash(rel)
	pkg, file := filepath.ToSlash(filepath.Dir(rel)), filepath.Base(rel)
	var out []Func
	for _, decl := range f.Decls {
		if g, ok := decl.(*ast.GenDecl); ok {
			out = append(out, members(fset, g, pkg, file, gen)...)
			continue
		}
		d, ok := decl.(*ast.FuncDecl)
		if !ok || d.Body == nil {
			continue
		}
		cog, cyc, residue, cl := Score(fset, d)
		start := fset.Position(d.Pos()).Line
		end := fset.Position(d.End()).Line
		out = append(out, Func{
			Pkg: pkg, File: file, Line: start, End: end, Recv: recvName(d.Recv), Name: d.Name.Name,
			Cog: cog, Cyc: cyc, Lines: end - start + 1, Residue: residue, Gen: gen, Closures: cl,
		})
	}
	return out, nil
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: gocog <root> <dir>...")
		os.Exit(2)
	}
	root := os.Args[1]
	fset := token.NewFileSet()
	var funcs []Func
	for _, dir := range os.Args[2:] {
		err := filepath.WalkDir(filepath.Join(root, dir), func(p string, e fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if e.IsDir() {
				if e.Name() == "testdata" || e.Name() == "vendor" {
					return filepath.SkipDir
				}
				return nil
			}
			if !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
				return nil
			}
			fs, err := scanFile(fset, root, p)
			funcs = append(funcs, fs...)
			return err
		})
		if err != nil {
			fmt.Fprintln(os.Stderr, "gocog:", err)
			os.Exit(1)
		}
	}
	sort.Slice(funcs, func(i, j int) bool {
		if funcs[i].Pkg != funcs[j].Pkg {
			return funcs[i].Pkg < funcs[j].Pkg
		}
		if funcs[i].File != funcs[j].File {
			return funcs[i].File < funcs[j].File
		}
		if funcs[i].Line != funcs[j].Line {
			return funcs[i].Line < funcs[j].Line
		}
		return funcs[i].Name < funcs[j].Name
	})
	enc := json.NewEncoder(os.Stdout)
	if err := enc.Encode(map[string]any{"version": version, "funcs": funcs}); err != nil {
		fmt.Fprintln(os.Stderr, "gocog:", err)
		os.Exit(1)
	}
}
