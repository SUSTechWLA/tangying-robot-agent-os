package agent_test

import (
	"context"
	"path/filepath"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type recordingRobot struct {
	mu      sync.Mutex
	counts  map[string]int
	taskIDs []string
}

func (r *recordingRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.98},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
	}, nil
}

func (r *recordingRobot) Execute(_ context.Context, taskID string, step taskgraph.SkillStep) (agent.SkillResult, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.counts[step.Skill]++
	r.taskIDs = append(r.taskIDs, taskID)
	return agent.SkillResult{Success: true, VerificationConfidence: 0.98}, nil
}

func (r *recordingRobot) count(skill string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.counts[skill]
}

func TestRunnerRestartDoesNotRepeatCompletedPick(t *testing.T) {
	store, err := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &orchestrator.Task{ID: "task-1", Intent: parsed, Approved: true}
	robot := &recordingRobot{counts: map[string]int{}}

	if _, err := agent.NewRunner(store, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if _, err := agent.NewRunner(store, robot).Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if got := robot.count("manipulation.pick"); got != 1 {
		t.Fatalf("pick count = %d, want 1", got)
	}
	for _, taskID := range robot.taskIDs {
		if taskID != task.ID {
			t.Fatalf("executed step with task ID %q, want %q", taskID, task.ID)
		}
	}
}

func TestRunnerRequiresApprovalBeforePhysicalSkill(t *testing.T) {
	store, _ := localstore.Open(filepath.Join(t.TempDir(), "agent.db"))
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &orchestrator.Task{ID: "task-2", Intent: parsed, Approved: false}
	robot := &recordingRobot{counts: map[string]int{}}
	_, err := agent.NewRunner(store, robot).Run(context.Background(), task)
	if err == nil || robot.count("manipulation.pick") != 0 {
		t.Fatalf("err = %v, pick count = %d", err, robot.count("manipulation.pick"))
	}
}
