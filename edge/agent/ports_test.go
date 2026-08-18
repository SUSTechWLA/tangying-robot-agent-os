package agent_test

import (
	"context"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type executionStoreSpy struct {
	status    map[string]middleware.StepStatus
	completed map[string]bool
}

func newExecutionStoreSpy() *executionStoreSpy {
	return &executionStoreSpy{status: map[string]middleware.StepStatus{}, completed: map[string]bool{}}
}

func (s *executionStoreSpy) StepStatus(_ context.Context, _, stepID string) (middleware.StepStatus, error) {
	if status := s.status[stepID]; status != "" {
		return status, nil
	}
	return middleware.StepPending, nil
}

func (s *executionStoreSpy) MarkStepStarted(_ context.Context, record middleware.StepRecord) error {
	s.status[record.StepID] = middleware.StepStarted
	return nil
}

func (s *executionStoreSpy) MarkStepCompleted(_ context.Context, record middleware.StepRecord) error {
	s.status[record.StepID] = middleware.StepCompleted
	s.completed[record.StepID] = true
	return nil
}

type grounderStub struct{}

func (grounderStub) Ground(_ context.Context, parsed manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{
		Action:      parsed.Action,
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: 0.99},
		Destination: manipulation.SceneRef{ID: "right-bin", Confidence: 0.99},
	}, nil
}

type invokerSpy struct {
	commands []runtime.Command
}

func (i *invokerSpy) Invoke(_ context.Context, command runtime.Command) (runtime.Result, error) {
	i.commands = append(i.commands, command)
	return runtime.Result{Success: true, VerificationConfidence: 1}, nil
}

func TestRunnerUsesExecutionStoreAndSemanticRuntimePorts(t *testing.T) {
	store := newExecutionStoreSpy()
	invoker := &invokerSpy{}
	runner := agent.NewRunner(store, grounderStub{}, invoker)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "task-1", Intent: parsed, State: taskgraph.StateReady, Approved: true}
	if _, err := runner.Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if !store.completed["pick"] || !store.completed["place"] {
		t.Fatalf("completed steps = %#v", store.completed)
	}
	if len(invoker.commands) == 0 || invoker.commands[0].TaskID != task.ID {
		t.Fatalf("semantic commands = %#v", invoker.commands)
	}
}
