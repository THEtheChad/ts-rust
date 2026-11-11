package modulespecifiers

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"github.com/microsoft/TypeScript/tsc/internal/collections"
	"github.com/microsoft/TypeScript/tsc/internal/core"
	"github.com/microsoft/TypeScript/tsc/internal/symlinks"
	"github.com/microsoft/TypeScript/tsc/internal/testutil/parsetestutil"
	"github.com/microsoft/TypeScript/tsc/internal/tspath"
)

// b5Host embeds Go's mock and answers the redirect targets and the
// package.json directories.
type b5Host struct {
	mockModuleSpecifierGenerationHost
	redirects   []tspath.RootedFilePath
	packageDirs []tspath.RootedDirectoryPath
}

func (h *b5Host) GetRedirectTargets(path tspath.PathKey) []tspath.RootedFilePath {
	return h.redirects
}

func (h *b5Host) GetNearestAncestorDirectoryWithPackageJson(dirname tspath.RootedDirectoryPath) tspath.RootedDirectoryPath {
	best := tspath.RootedDirectoryPath("")
	for _, d := range h.packageDirs {
		if (dirname == d || strings.HasPrefix(dirname.AsString(), d.AsString()+"/")) && len(d) > len(best) {
			best = d
		}
	}
	return best
}

func newB5Host(root string, cs tspath.CaseSensitivity, redirects []tspath.RootedFilePath, packageDirs []tspath.RootedDirectoryPath) *b5Host {
	return &b5Host{
		mockModuleSpecifierGenerationHost: mockModuleSpecifierGenerationHost{
			currentDir:      tspath.RootedDirectoryPath(root),
			caseSensitivity: cs,
			symlinkCache:    symlinks.NewKnownSymlinks(cs),
		},
		redirects:   redirects,
		packageDirs: packageDirs,
	}
}

func TestB5ModuleSpecifierProbe(t *testing.T) {
	mp := func(f string) ModulePath { return ModulePath{FileName: tspath.RootedFilePath(f)} }
	// 1. comparePathsByRedirect direction.
	for _, pair := range [][2]string{
		{"^/~ts-uri~/mem/auth/node_modules/PKG/index.d.ts", "^/~ts-uri~/mem/auth/node_modules/pkg/index.d.ts"},
		{"^/~ts-uri~/mem/auth/node_modules/pkg/index.d.ts", "^/~ts-uri~/mem/auth/node_modules/PKG/index.d.ts"},
		{"/repo/node_modules/PKG/index.d.ts", "/repo/node_modules/pkg/index.d.ts"},
	} {
		for _, cs := range []tspath.CaseSensitivity{tspath.CaseInsensitive, tspath.CaseSensitive} {
			fmt.Printf("compare a=%q b=%q sensitive=%v -> %d\n", pair[0], pair[1], cs.IsCaseSensitive(), comparePathsByRedirect(mp(pair[0]), mp(pair[1]), cs))
		}
	}

	file := parsetestutil.ParseTypeScript("export {};\n", false)
	opts := &core.CompilerOptions{Module: core.ModuleKindCommonJS, Target: core.ScriptTargetES2020}
	// 2. Module path order and the d.ts specifier.
	for _, root := range []string{"^/~ts-uri~/mem/auth/", "/repo/"} {
		for _, cs := range []tspath.CaseSensitivity{tspath.CaseInsensitive, tspath.CaseSensitive} {
			for _, imported := range []string{"pkg", "PKG"} {
				other := map[string]string{"pkg": "PKG", "PKG": "pkg"}[imported]
				importing := tspath.RootedFilePath(root + "src/main.ts")
				importedFile := tspath.RootedFilePath(root + "node_modules/" + imported + "/index.d.ts")
				redirect := tspath.RootedFilePath(root + "node_modules/" + other + "/index.d.ts")
				orders := map[string]bool{}
				specs := map[string]bool{}
				for range 200 {
					host := newB5Host(root, cs, []tspath.RootedFilePath{redirect}, nil)
					paths := getAllModulePaths(getInfo(importing, host), importedFile, host, opts, UserPreferences{}, ModuleSpecifierOptions{})
					var names []string
					for _, p := range paths {
						names = append(names, p.FileName.AsString())
					}
					orders[fmt.Sprintf("%q", names)] = true
					specs[GetModuleSpecifier(opts, host, file, importing, "", importedFile, ModuleSpecifierOptions{}).AsString()] = true
				}
				fmt.Printf("paths root=%q sensitive=%v imported=%s redirect=%s orders=%v specifiers=%v\n", root, cs.IsCaseSensitive(), imported, other, keys(orders), keys(specs))
			}
		}
	}

	// 3. packageJsonPathsAreEqual through UpdateModuleSpecifier, project-relative.
	for _, root := range []string{"^/~ts-uri~/mem/auth/", "/repo/"} {
		for _, cs := range []tspath.CaseSensitivity{tspath.CaseInsensitive, tspath.CaseSensitive} {
			for _, dirs := range [][2]string{{"a", "A"}, {"a", "a"}, {"a", "b"}} {
				importing := tspath.RootedFilePath(root + "packages/" + dirs[0] + "/src/main.ts")
				target := tspath.RootedFilePath(root + "packages/" + dirs[1] + "/x.ts")
				paths := collections.NewOrderedMapWithSizeHint[string, []string](1)
				paths.Set("@lib/*", []string{"./packages/" + dirs[1] + "/*"})
				o := &core.CompilerOptions{Module: core.ModuleKindCommonJS, Target: core.ScriptTargetES2020, Paths: paths, PathsBasePath: tspath.RootedDirectoryPath(root)}
				host := newB5Host(root, cs, nil, []tspath.RootedDirectoryPath{
					tspath.RootedDirectoryPath(root + "packages/" + dirs[0]),
					tspath.RootedDirectoryPath(root + "packages/" + dirs[1]),
				})
				eq := packageJsonPathsAreEqual(tspath.RootedDirectoryPath(root+"packages/"+dirs[1]), tspath.RootedDirectoryPath(root+"packages/"+dirs[0]), cs)
				got := UpdateModuleSpecifier(o, host, file, importing, "", target, UserPreferences{ImportModuleSpecifierPreference: ImportModuleSpecifierPreferenceProjectRelative}, ModuleSpecifierOptions{})
				fmt.Printf("pkgjson root=%q sensitive=%v source=%s target=%s equal=%v specifier=%q\n", root, cs.IsCaseSensitive(), dirs[0], dirs[1], eq, got)
			}
		}
	}
}

func keys(m map[string]bool) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
