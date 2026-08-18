package architecture_test

import (
	"bytes"
	"encoding/json"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
)

const modulePath = "github.com/SUSTechWLA/tangying-robot-agent-os"

type listedPackage struct {
	ImportPath string
	Imports    []string
}

func TestForbiddenImportsDetectsConcreteAdapter(t *testing.T) {
	violations := forbiddenImports(listedPackage{
		ImportPath: modulePath + "/agent/example",
		Imports:    []string{modulePath + "/middleware/sqlite"},
	})
	if len(violations) != 1 {
		t.Fatalf("violations = %#v, want one", violations)
	}
}

func TestCorePackagesDoNotImportConcreteInfrastructure(t *testing.T) {
	packages := goList(t,
		"./agent/...",
		"./orchestration/...",
		"./tasks/...",
		"./core/...",
		"./edge/agent/...",
		"./edge/runtime/...",
	)
	var violations []string
	for _, pkg := range packages {
		violations = append(violations, forbiddenImports(pkg)...)
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("core package dependency violations:\n%s", strings.Join(violations, "\n"))
	}
}

func forbiddenImports(pkg listedPackage) []string {
	var result []string
	for _, imported := range pkg.Imports {
		if forbiddenImport(imported) {
			result = append(result, pkg.ImportPath+" imports "+imported)
		}
	}
	return result
}

func forbiddenImport(imported string) bool {
	for _, exact := range []string{
		"database/sql",
		modulePath + "/gen/go/robot/v1",
		modulePath + "/middleware/sqlite",
		modulePath + "/middleware/postgres",
		modulePath + "/middleware/redis",
		modulePath + "/middleware/kafka",
		"google.golang.org/grpc",
	} {
		if imported == exact || strings.HasPrefix(imported, exact+"/") {
			return true
		}
	}
	for _, vendorFragment := range []string{
		"github.com/jackc/pgx",
		"github.com/redis/",
		"github.com/segmentio/kafka-go",
		"github.com/confluentinc/confluent-kafka-go",
	} {
		if strings.HasPrefix(imported, vendorFragment) {
			return true
		}
	}
	return false
}

func goList(t *testing.T, patterns ...string) []listedPackage {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve architecture test path")
	}
	root := filepath.Clean(filepath.Join(filepath.Dir(filename), "../.."))
	arguments := append([]string{"list", "-json"}, patterns...)
	command := exec.Command("go", arguments...)
	command.Dir = root
	output, err := command.Output()
	if err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			t.Fatalf("go list failed: %v\n%s", err, exit.Stderr)
		}
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(output))
	var packages []listedPackage
	for decoder.More() {
		var pkg listedPackage
		if err := decoder.Decode(&pkg); err != nil {
			t.Fatal(err)
		}
		packages = append(packages, pkg)
	}
	return packages
}
