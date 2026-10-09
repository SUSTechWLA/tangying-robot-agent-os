package tasks

import (
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

func TestContextKeepsEventScopeUnknownAndCompleteHistory(t *testing.T) {
	task := Task{ID: "t1", Request: "拿杯子", CurrentRevision: 4, Plan: &orchestration.Bundle{Plans: []taskgraph.TaskPlan{{ID: "p1", Revision: 4, Steps: []taskgraph.SkillStep{{ID: "s1", Skill: "pick", Arguments: map[string]any{"target": "cup-4"}}}}}}}
	for i := 0; i < 70; i++ {
		task.Events = append(task.Events, TaskEvent{Sequence: uint64(i), Type: "tool.result", StepID: "s1", Payload: map[string]any{"success": true, "agent_context": "recursive-secret-marker", "decision_rounds": "recursive-secret-marker"}, OccurredAt: time.Unix(10, 0)})
	}
	d := ContextFor(task, "reflection", time.Unix(100, 0))
	if d.Role != "reflection" || d.Scope.PlanRevision != 4 || d.Steps[0].State != "UNKNOWN" {
		t.Fatalf("bad task projection %+v", d)
	}
	events := 0
	for _, r := range d.Records {
		if r.ID == "history:omitted" {
			t.Fatal("source projection must not truncate the durable ledger")
		}
		if strings.HasPrefix(r.ID, "event:") {
			events++
			if r.Scope.PlanRevision != 0 || r.ObservedMS != 0 {
				t.Fatal("invented event provenance")
			}
		}
		if strings.Contains(r.Statement, "recursive-secret-marker") {
			t.Fatal("recursively included model context")
		}
	}
	if events != len(task.Events) {
		t.Fatalf("projected %d/%d events", events, len(task.Events))
	}
}
