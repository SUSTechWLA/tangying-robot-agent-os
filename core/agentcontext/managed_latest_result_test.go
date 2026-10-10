package agentcontext

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestLatestSmallBusinessResultArchivesWhenRequiredStateUsesRemainingBudget(t *testing.T) {
	scope := Scope{TaskID: "task", RobotID: "robot", PlanRevision: 1}
	d := Document{SchemaVersion: Version, Role: "recovery", Goal: "读取历史", Scope: scope,
		Records:  []Record{{ID: "pending", Kind: "guard", Scope: scope, Statement: strings.Repeat("x", 6000)}},
		Attempts: []Attempt{{ID: "round:1", Tool: "execution.read-history", Arguments: map[string]any{"selector": "bound-task"}, Verdict: "SATISFIED"}},
	}
	base, err := Render(d, "json")
	if err != nil {
		t.Fatal(err)
	}
	budget := Budget{MaxContextBytes: len(base) + 1000, MaxRequestBytes: len(base) + 10000, OutputTokens: 512}
	d.Attempts[0].Detail = strings.Repeat("原始回执", 160)
	attemptBytes, _ := json.Marshal(d.Attempts[0])
	if len(attemptBytes) >= budget.MaxContextBytes/3 {
		t.Fatal("fixture must exercise the previously protected small latest result")
	}
	p, err := ProjectManaged(d, "recovery", budget)
	if err != nil || len(p.Text) > budget.MaxContextBytes || len(p.Artifacts) != 1 || p.Compaction.ArchivedAttempts != 1 {
		t.Fatalf("latest detail blocked a context with representable exact guards: %+v %v", p.Compaction, err)
	}
	var view Document
	if err := json.Unmarshal([]byte(p.Text), &view); err != nil {
		t.Fatal(err)
	}
	if view.Records[0].Statement != d.Records[0].Statement || view.Attempts[0].ID != "round:1" || view.Attempts[0].Verdict != "SATISFIED" || view.Attempts[0].Arguments["selector"] != "bound-task" {
		t.Fatal("archiving verbose detail changed required state or exact attempt arguments")
	}
	page, err := p.ReadArtifact(scope, p.Artifacts[0].SHA256, "round:1", 0, 4096)
	if err != nil || !page.Complete {
		t.Fatalf("original result cannot be retrieved: %v", err)
	}
	var original Attempt
	if err := json.Unmarshal([]byte(page.Content), &original); err != nil {
		t.Fatal(err)
	}
	if original.Detail != d.Attempts[0].Detail {
		t.Fatal("latest result was truncated instead of archived")
	}
	// A retrieved page is intentionally different: archiving it immediately
	// would force a reader to repeat the same retrieval indefinitely.
	d.Attempts[0].Tool = ArchiveReadTool
	if _, err := ProjectManaged(d, "recovery", budget); !errors.Is(err, ErrContextBudget) {
		t.Fatalf("oversized required state discarded the current retrieval page: %v", err)
	}
}
