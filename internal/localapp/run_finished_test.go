package localapp

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type failedGroundRobot struct{ testRobot }

func (*failedGroundRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{}, errors.New("fixture localization unavailable")
}

func TestRunFinishedAuditsActualOutcomeAndDoesNotReplay(t *testing.T) {
	for _, fail := range []bool{false, true} {
		name := "success"
		if fail {
			name = "failure"
		}
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			service := tasks.NewService(store, intent.NewDeterministicParser())
			robot := &testRobot{}
			runner := agent.NewRunner(store, robot, robot)
			want := taskgraph.StateSucceeded
			if fail {
				runner = agent.NewRunner(store, &failedGroundRobot{}, robot)
				want = taskgraph.StateRecoverableFailure
			}
			app := New(service, runner, memory.NewQueue[string](1))
			task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
			if err != nil {
				t.Fatal(err)
			}
			if _, err = service.Approve(ctx, task.ID, "test-operator"); err != nil {
				t.Fatal(err)
			}
			app.run(ctx, task.ID)
			current, err := service.Get(ctx, task.ID)
			if err != nil || current.State != want || app.ExecutionActive(task.ID) {
				t.Fatalf("outcome=%+v error=%v", current, err)
			}
			last := current.Events[len(current.Events)-1]
			if last.Type != "LOCAL_RUN_FINISHED" || last.Payload["state"] != string(want) {
				t.Fatalf("missing or misleading closure: %+v", last)
			}
			if fail && robot.calls() != 0 {
				t.Fatal("failed grounding reached physical tools")
			}
			before := len(current.Events)
			calls := robot.calls()
			app.run(ctx, task.ID)
			after, _ := service.Get(ctx, task.ID)
			if len(after.Events) != before || robot.calls() != calls {
				t.Fatal("finished attempt was replayed or given a false extra closure")
			}
		})
	}
}
