package fleet

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestTaskRecoveryGuidanceExplainsDistributedFailuresWithoutRawErrors(t *testing.T) {
	tests := []struct {
		name     string
		task     *tasks.Task
		graph    *coordinator.Snapshot
		world    *worldmodel.Snapshot
		contains string
	}{
		{
			name: "failed tool",
			task: &tasks.Task{RevisionState: tasks.RevisionActive},
			graph: &coordinator.Snapshot{Intents: []coordinator.IntentNode{{
				Status: coordinator.StatusFailed, Error: "grpc bearerToken=secret-value",
			}}},
			contains: "停止推进",
		},
		{
			name: "stale world",
			task: &tasks.Task{RevisionState: tasks.RevisionActive},
			world: &worldmodel.Snapshot{Health: worldmodel.Health{
				DegradedSources: []string{"robot-2/scene"},
			}},
			contains: "环境状态暂时不够新",
		},
		{
			name:     "waiting safe point",
			task:     &tasks.Task{RevisionState: tasks.RevisionWaitingSafePoint},
			contains: "安全动作",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			guidance := taskRecoveryGuidance(test.task, test.graph, test.world, nil)
			if guidance == nil {
				t.Fatal("missing recovery guidance")
			}
			wire := guidance.KnownState + guidance.RobotSafetyState + guidance.AutomaticAction + strings.Join(guidance.UserActions, " ")
			if !strings.Contains(wire, test.contains) {
				t.Fatalf("guidance=%#v, want %q", guidance, test.contains)
			}
			if strings.Contains(wire, "secret-value") || strings.Contains(wire, "bearerToken") || strings.Contains(wire, "grpc") {
				t.Fatalf("raw technical failure leaked: %q", wire)
			}
		})
	}
}

func TestHealthyActiveTaskDoesNotShowRecoveryPanel(t *testing.T) {
	if guidance := taskRecoveryGuidance(
		&tasks.Task{RevisionState: tasks.RevisionActive},
		&coordinator.Snapshot{},
		&worldmodel.Snapshot{},
		nil,
	); guidance != nil {
		t.Fatalf("unexpected guidance=%#v", guidance)
	}
}
