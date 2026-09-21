package docs_test

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// The README's claims about this repository, checked against this repository.
//
// The README is the front page, and three of its numbers had drifted: it claimed
// 305 Go tests and 165 Python tests against 1031 and 1362, "seven failure classes"
// against the eight in core/closedloop, and a twelve-step household task as
// verified at a time when every task failed on its first step. Each was true when
// written and none was true when read, and nothing in the build could tell.
//
// A number in a document is a claim like any other, so it gets a check like any
// other. These read the same files the reader does.

func repoRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func readme(t *testing.T) string {
	t.Helper()
	content, err := os.ReadFile(filepath.Join(repoRoot(t), "README.md"))
	if err != nil {
		t.Fatal(err)
	}
	return string(content)
}

// The class count is checkable exactly, by reading the code the README names.
//
// It scrapes the source rather than calling a package function, because a
// function listing the classes would be a second place to remember to update —
// which is the drift this guards against. core/closedloop's own coverage test
// already reads the runtime's failure codes the same way.
func TestTheReadmeAgreesWithTheNumberOfFailureClasses(t *testing.T) {
	source, err := os.ReadFile(filepath.Join(repoRoot(t), "core", "closedloop", "closedloop.go"))
	if err != nil {
		t.Fatal(err)
	}
	classes := regexp.MustCompile(`(?m)^	([A-Z][A-Za-z]*) Class = "`).FindAllSubmatch(source, -1)
	if len(classes) == 0 {
		t.Fatal("no failure classes were found in core/closedloop; has the const block moved?")
	}
	numbers := map[int]string{7: "七类", 8: "八类", 9: "九类", 10: "十类"}
	want, ok := numbers[len(classes)]
	if !ok {
		t.Fatalf("core/closedloop now has %d classes; add the word for it to this test and to the README", len(classes))
	}
	body := readme(t)
	// Every place the README names the count must use the right word.
	for _, wrong := range []string{"七类失败分类", "六类失败分类", "九类失败分类"} {
		if wrong != want+"失败分类" && strings.Contains(body, wrong) {
			t.Errorf("README says %q, but core/closedloop has %d classes (%s)", wrong, len(classes), want)
		}
	}
	if !strings.Contains(body, want+"失败分类") {
		t.Errorf("README does not state the failure-class count as %q", want)
	}
}

// The test counts are checkable from the tree.
func TestTheReadmeTestCountsAreNotStale(t *testing.T) {
	root := repoRoot(t)
	goTests, pythonTests := 0, 0
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			switch entry.Name() {
			case "node_modules", ".venv", "vendor", ".git", "__pycache__", "artifacts", ".worktrees", "datasets", "XLeRobot", "tangying-ai-operation-system":
				return filepath.SkipDir
			}
			return nil
		}
		switch {
		case strings.HasSuffix(path, "_test.go"):
			goTests += countMatches(path, regexp.MustCompile(`(?m)^func Test`))
		case strings.HasPrefix(entry.Name(), "test_") && strings.HasSuffix(path, ".py"),
			strings.HasSuffix(path, "_test.py"):
			pythonTests += countMatches(path, regexp.MustCompile(`(?m)^\s*def test_`))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	body := readme(t)
	for _, claim := range []struct {
		name  string
		value int
	}{
		{"Go", goTests},
		{"Python", pythonTests},
	} {
		pattern := regexp.MustCompile(`(\d+) 个 ` + claim.name + ` 测试函数`)
		match := pattern.FindStringSubmatch(body)
		if match == nil {
			t.Fatalf("README no longer states a %s test count in the checked form; if the wording changed, change this test with it", claim.name)
		}
		claimed, err := strconv.Atoi(match[1])
		if err != nil {
			t.Fatal(err)
		}
		// A tolerance of a few percent: the exact count moves with every commit,
		// and a claim that is roughly right is not the failure this guards
		// against. An order-of-magnitude drift is.
		if claimed < claim.value*9/10 || claimed > claim.value*11/10 {
			t.Errorf("README claims %d %s test functions, the tree has %d", claimed, claim.name, claim.value)
		}
	}
}

func countMatches(path string, pattern *regexp.Regexp) int {
	content, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	return len(pattern.FindAll(content, -1))
}
