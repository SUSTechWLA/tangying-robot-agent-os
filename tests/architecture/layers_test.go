package architecture_test

import (
	"sort"
	"strings"
	"testing"
)

// Domain contracts must stay usable by both cloud and edge without pulling in
// either implementation. A growing blacklist of particular databases alone
// cannot enforce this: an apparently harmless fleet import also couples releases.
func TestDomainAndRuntimePortsDependOnlyOnCore(t *testing.T) {
	packages := goList(t, "./core/...", "./edge/runtime/...")
	var violations []string
	for _, pkg := range packages {
		for _, imported := range pkg.Imports {
			if strings.HasPrefix(imported, modulePath+"/") &&
				!strings.HasPrefix(imported, modulePath+"/core/") {
				violations = append(violations, pkg.ImportPath+" imports "+imported)
			}
		}
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("core and Robot Runtime ports must not depend on application or adapter implementations:\n%s", strings.Join(violations, "\n"))
	}
}
