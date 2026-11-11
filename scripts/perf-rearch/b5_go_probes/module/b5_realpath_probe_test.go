package module

import (
	"fmt"
	"strings"
	"testing"

	"github.com/microsoft/TypeScript/tsc/internal/core"
	"github.com/microsoft/TypeScript/tsc/internal/tspath"
	"github.com/microsoft/TypeScript/tsc/internal/vfs/vfstest"
	"github.com/microsoft/TypeScript/tsc/internal/vfs/wrapvfs"
)

// Lane B5: getOriginalAndResolvedFileName when the realpath differs from the
// file name only in case (the "pkg" directory comes back as "PKG").
func TestB5RealpathProbe(t *testing.T) {
	for _, root := range []string{"^/~ts-uri~/mem/auth/", "/repo/"} {
		for _, cs := range []tspath.CaseSensitivity{tspath.CaseInsensitive, tspath.CaseSensitive} {
			files := map[string]string{
				root + "src/main.ts":                           "",
				root + "node_modules/pkg/package.json":         `{"name":"pkg","types":"index.d.ts"}`,
				root + "node_modules/pkg/index.d.ts":           "export const x: number;",
				root + "node_modules/@types/tpkg/package.json": `{"name":"@types/tpkg","types":"index.d.ts"}`,
				root + "node_modules/@types/tpkg/index.d.ts":   "declare const y: number;",
			}
			fs := wrapvfs.Wrap(vfstest.FromMap(files, cs), wrapvfs.Replacements{
				Realpath: func(p tspath.RootedPath) tspath.RootedPath {
					s := strings.Replace(p.AsString(), "/node_modules/pkg/", "/node_modules/PKG/", 1)
					s = strings.Replace(s, "/@types/tpkg/", "/@types/TPKG/", 1)
					return tspath.RootedPath(s)
				},
			})
			host := &resolutionTestInputs{fs: fs, cwd: tspath.RootedDirectoryPath(root)}
			opts := &core.CompilerOptions{
				ModuleResolution: core.ModuleResolutionKindBundler,
				Module:           core.ModuleKindESNext,
				Target:           core.ScriptTargetESNext,
			}
			resolver := NewResolver(ResolverOptions{Host: host, CompilerOptions: opts})
			m, _, _ := resolver.ResolveModuleName("pkg", tspath.RootedFilePathFromNormalized(root+"src/main.ts"), core.ModuleKindESNext, nil)
			fmt.Printf("module root=%q sensitive=%v resolvedFileName=%q originalPath=%q resolvedPath=%q\n", root, cs.IsCaseSensitive(), m.ResolvedFileName, m.OriginalPath, m.ResolvedPath)
			d, _ := resolver.ResolveTypeReferenceDirective("tpkg", tspath.RootedFilePathFromNormalized(root+"src/main.ts"), core.ModuleKindESNext, nil)
			fmt.Printf("typeref root=%q sensitive=%v resolvedFileName=%q originalPath=%q resolvedPath=%q\n", root, cs.IsCaseSensitive(), d.ResolvedFileName, d.OriginalPath, d.ResolvedPath)
		}
	}
}
