package project

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"github.com/microsoft/TypeScript/tsc/internal/core"
	"github.com/microsoft/TypeScript/tsc/internal/lsp/lsproto"
	"github.com/microsoft/TypeScript/tsc/internal/tspath"
	"github.com/microsoft/TypeScript/tsc/internal/vfs/vfstest"
)

// Lane B5: overlayFS.GetAccessibleEntries with names that differ only in
// case, on a case-insensitive host. Prints each case's possible answers
// (Go ranges over maps, so 500 runs collect every order).
func TestB5OverlayProbe(t *testing.T) {
	type tcase struct {
		name  string
		host  map[string]string
		files []string // overlay file names or URIs (contain ":")
		dir   string
	}
	cases := []tcase{
		{"disk_overlay_dotted_I_over_host_i", map[string]string{"/dir/i.ts": "", "/dir/b.ts": ""}, []string{"/dir/İ.ts"}, "/dir"},
		{"disk_two_overlays_dotted_I_and_i", map[string]string{"/dir/b.ts": ""}, []string{"/dir/İ.ts", "/dir/i.ts"}, "/dir"},
		{"disk_overlay_dotless_i_over_host_i", map[string]string{"/dir/i.ts": ""}, []string{"/dir/ı.ts"}, "/dir"},
		{"disk_overlay_upper_I_over_host_i", map[string]string{"/dir/i.ts": ""}, []string{"/dir/I.ts"}, "/dir"},
		{"untitled_ascii_pair", map[string]string{"/x.ts": ""}, []string{"untitled:Untitled-1", "untitled:untitled-1"}, ""},
		{"untitled_dotted_I_pair", map[string]string{"/x.ts": ""}, []string{"untitled:İ.ts", "untitled:i.ts"}, ""},
		{"untitled_dotless_i_pair", map[string]string{"/x.ts": ""}, []string{"untitled:ı.ts", "untitled:i.ts"}, ""},
	}
	for _, c := range cases {
		host := vfstest.FromMap(c.host, tspath.CaseInsensitive)
		overlays := map[tspath.PathKey]*Overlay{}
		dir := c.dir
		for _, f := range c.files {
			var fileName tspath.RootedFilePath
			if strings.Contains(f, ":") {
				fileName = lsproto.DocumentUri(f).FileName()
			} else {
				fileName = tspath.RootedFilePath(f)
			}
			key := tspath.CaseInsensitive.PathKey(fileName.AsPath())
			overlays[key] = newOverlay(fileName, "x", 1, core.ScriptKindTS)
			if dir == "" {
				dir = fileName.Directory().AsString()
			}
			fmt.Printf("%s file %q key %q\n", c.name, fileName, key)
		}
		seen := map[string]bool{}
		for range 500 {
			fs := newOverlayFS(host, overlays, lsproto.PositionEncodingKindUTF16)
			e := fs.GetAccessibleEntries(tspath.RootedDirectoryPath(dir))
			files := append([]string{}, e.Files...)
			dirs := append([]string{}, e.Directories...)
			seen[fmt.Sprintf("files=%q dirs=%q", files, dirs)] = true
		}
		var outs []string
		for k := range seen {
			outs = append(outs, k)
		}
		sort.Strings(outs)
		fmt.Printf("%s dir %q answers %d\n", c.name, dir, len(outs))
		for _, o := range outs {
			fmt.Printf("  %s\n", o)
		}
	}
}
