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
		"./agentruntime/...",
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

// The agent runtime must not import any concrete agent.
//
// This is the mechanical form of the promise that adding an EvalAgent,
// ExperienceAgent or EscalationAgent needs only an implementation and a
// registration: a runtime that imported the agents it hosts would have to be
// edited every time one was added, and the promise would quietly stop being
// true. The check is by import path because that is the thing that would
// actually change.
func TestAgentRuntimeDoesNotImportConcreteAgents(t *testing.T) {
	packages := goList(t, "./agentruntime/...")
	concreteAgents := []string{
		modulePath + "/edge/agent",
		modulePath + "/core/harness",
		modulePath + "/agent",
	}
	var violations []string
	for _, pkg := range packages {
		for _, imported := range pkg.Imports {
			for _, concrete := range concreteAgents {
				if imported == concrete || strings.HasPrefix(imported, concrete+"/") {
					violations = append(violations, pkg.ImportPath+" imports "+imported)
				}
			}
		}
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("the agent runtime must host agents, not depend on them:\n%s", strings.Join(violations, "\n"))
	}
}

// The agent contract is what both sides depend on, so it must stay dependency
// free. If it grew an infrastructure or agent dependency, the execution path and
// the runtime could no longer share it without one of them importing the other.
func TestAgentContractHasNoInternalDependencies(t *testing.T) {
	packages := goList(t, "./core/agentcontract/...")
	if len(packages) == 0 {
		t.Fatal("the agent contract package was not found")
	}
	var violations []string
	for _, pkg := range packages {
		for _, imported := range pkg.Imports {
			if strings.HasPrefix(imported, modulePath) {
				violations = append(violations, pkg.ImportPath+" imports "+imported)
			}
		}
	}
	if len(violations) != 0 {
		t.Fatalf("the agent contract must not depend on the rest of the module:\n%s", strings.Join(violations, "\n"))
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
