package agent_test

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestUnknownPhysicalStepCannotBeBypassedWithAnotherRevision(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.MarkStepStarted(context.Background(), middleware.StepRecord{TaskID: "uncertain", StepID: "pick", IdempotencyKey: "original-pick"}); err != nil {
		t.Fatal(err)
	}
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "uncertain", Intent: parsed, Approved: true, CurrentRevision: 2}
	robot := &recordingRobot{counts: map[string]int{}}
	_, err = agent.NewRunner(store, robot, robot).Run(context.Background(), task)
	if !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) || robot.count("manipulation.pick") != 0 {
		t.Fatalf("unknown prior action bypassed: err=%v picks=%d", err, robot.count("manipulation.pick"))
	}
}

func TestRecoverySafetyComesFromCanonicalCapabilityNotStoredSafetyLabel(t *testing.T) {
	for _, test := range []struct {
		name, capability, label string
		safe                    bool
	}{
		{"known-read-with-empty-old-label", "observe_scene", "", true},
		{"known-read-with-wrong-label", "verify_grasp", "physical_motion", true},
		{"physical-with-forged-read-label", "manipulation.pick", "read_only", false},
		{"unknown-with-read-label", "unregistered.tool", "read_only", false},
		{"legacy-with-no-capability", "", "", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			if err := store.MarkStepStarted(context.Background(), middleware.StepRecord{TaskID: "task", StepID: "old-step", IdempotencyKey: "old-step", Capability: test.capability, SafetyLevel: test.label}); err != nil {
				t.Fatal(err)
			}
			err = agent.NewRunner(store, nil, nil).CheckRecovery(context.Background(), "task")
			if test.safe && err != nil {
				t.Fatalf("canonical read-only retry blocked: %v", err)
			}
			if !test.safe && !errors.Is(err, agent.ErrPhysicalOutcomeUnknown) {
				t.Fatalf("uncertain physical execution accepted: %v", err)
			}
		})
	}
}

func TestNewExecutionRecordsNormalizeSafetyFromCapabilityCatalog(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	robot := &recordingRobot{counts: map[string]int{}}
	if _, err := agent.NewRunner(store, robot, robot).Run(context.Background(), &tasks.Task{ID: "canonical-record", Intent: parsed, Approved: true}); err != nil {
		t.Fatal(err)
	}
	runs, err := store.ListStepRuns(context.Background(), "canonical-record")
	if err != nil {
		t.Fatal(err)
	}
	for _, run := range runs {
		want := string(skills.SafetyReadOnly)
		if run.Capability == "manipulation.pick" || run.Capability == "manipulation.place" {
			want = string(skills.SafetyPhysical)
		}
		if run.SafetyLevel != want {
			t.Errorf("capability=%s safety=%q want=%q", run.Capability, run.SafetyLevel, want)
		}
	}
}

func TestCancelledRunnerDoesNotStartAnyToolsEvenWhenInvokerIgnoresContext(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	robot := &recordingRobot{counts: map[string]int{}}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = agent.NewRunner(store, robot, robot).Run(ctx, &tasks.Task{ID: "cancelled", Intent: parsed, Approved: true})
	if !errors.Is(err, context.Canceled) || len(robot.taskIDs) != 0 {
		t.Fatalf("err=%v calls=%v", err, robot.taskIDs)
	}
}

func TestSuccessfulToolActivityHasDurableTerminalStatus(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	robot := &recordingRobot{counts: map[string]int{}}
	runner := agent.NewRunner(store, robot, robot)
	final := map[string]string{}
	runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
		final[event.StepID], _ = event.Payload["activityStatus"].(string)
		return nil
	}
	if _, err := runner.Run(context.Background(), &tasks.Task{ID: "completed", Intent: parsed, Approved: true}); err != nil {
		t.Fatal(err)
	}
	for step, status := range final {
		if status != "CONFIRMED" {
			t.Errorf("step %s remains %s after completion", step, status)
		}
	}
}
