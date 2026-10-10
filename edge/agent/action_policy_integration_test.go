package agent_test

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type policyRecordingRobot struct {
	recordingRobot
	commands []runtime.Command
}

func (r *policyRecordingRobot) Invoke(ctx context.Context, c runtime.Command) (runtime.Result, error) {
	r.commands = append(r.commands, c)
	return r.recordingRobot.Invoke(ctx, c)
}
func policyParameters(until time.Time) map[string]any {
	return map[string]any{"action_chunk": []any{map[string]any{"jaw.pos": 0.2}}, "policy_execution": map[string]any{"inferenceId": "candidate-1", "validUntil": until.Format(time.RFC3339Nano)}}
}
func policyRunnerFixture(t *testing.T) (*agent.Runner, *sqlite.Store, *tasks.Task, *policyRecordingRobot) {
	t.Helper()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: "local-policy-task", Intent: parsed, Approved: true}
	robot := &policyRecordingRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
	runner := agent.NewRunner(store, robot, robot)
	runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
		task.Events = append(task.Events, event)
		return nil
	}
	return runner, store, task, robot
}
func TestActionPolicyDispatchAndRestartKeepSemanticBindings(t *testing.T) {
	runner, _, task, robot := policyRunnerFixture(t)
	inferences := 0
	runner.ActionPolicy = func(_ context.Context, c runtime.Command, _ runtime.Snapshot) (map[string]any, error) {
		if c.Capability != runtime.CapabilityPick && c.Capability != runtime.CapabilityPlace {
			return nil, nil
		}
		inferences++
		// The hook gets a deep copy, including nested values, and cannot rewrite
		// the operator-approved target through shared Go map storage.
		c.Parameters["objectId"] = "unapproved-target"
		return policyParameters(time.Now().Add(2 * time.Second)), nil
	}
	if _, err := runner.Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if _, err := runner.Run(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	if inferences != 2 || robot.count("manipulation.pick") != 1 || robot.count("manipulation.place") != 1 {
		t.Fatalf("inferences=%d commands=%+v", inferences, robot.counts)
	}
	prepared := 0
	for _, c := range robot.commands {
		if c.Capability != runtime.CapabilityPick && c.Capability != runtime.CapabilityPlace {
			continue
		}
		if c.Parameters["objectId"] == "unapproved-target" || c.Parameters["action_chunk"] == nil {
			t.Fatalf("invalid command %+v", c)
		}
		meta := c.Parameters["policy_execution"].(map[string]any)
		expires, _ := time.Parse(time.RFC3339Nano, meta["validUntil"].(string))
		if c.Deadline.After(expires) {
			t.Fatal("receiver deadline outlives the observation")
		}
	}
	for _, e := range task.Events {
		raw, _ := json.Marshal(e.Payload)
		if strings.Contains(string(raw), "action_chunk") || strings.Contains(string(raw), "unapproved-target") {
			t.Fatalf("policy polluted durable semantic binding: %s", raw)
		}
		if e.Type == "POLICY_PREPARED" {
			prepared++
		}
	}
	if prepared != 2 {
		t.Fatalf("provenance events=%d", prepared)
	}
}
func TestActionPolicyFailureNeverStartsPhysicalStep(t *testing.T) {
	cases := []struct {
		name      string
		result    func(context.Context) (map[string]any, error)
		noEvents  bool
		failAudit bool
		cancel    bool
	}{
		{name: "provider timeout", result: func(context.Context) (map[string]any, error) { return nil, policy.ErrProviderTimeout }},
		{name: "changed target", result: func(context.Context) (map[string]any, error) {
			p := policyParameters(time.Now().Add(time.Second))
			p["objectId"] = "other"
			return p, nil
		}},
		{name: "missing provenance", result: func(context.Context) (map[string]any, error) { return map[string]any{"action_chunk": []any{1}}, nil }},
		{name: "expired before dispatch", result: func(context.Context) (map[string]any, error) {
			return policyParameters(time.Now().Add(-time.Second)), nil
		}},
		{name: "absent audit", result: func(context.Context) (map[string]any, error) {
			return policyParameters(time.Now().Add(time.Second)), nil
		}, noEvents: true},
		{name: "audit failure", result: func(context.Context) (map[string]any, error) {
			return policyParameters(time.Now().Add(time.Second)), nil
		}, failAudit: true},
		{name: "cancel during inference", result: func(context.Context) (map[string]any, error) {
			return policyParameters(time.Now().Add(time.Second)), nil
		}, cancel: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			runner, store, task, robot := policyRunnerFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if tc.noEvents {
				runner.TaskEvents = nil
			}
			if tc.failAudit {
				runner.TaskEvents = func(_ context.Context, _ string, e tasks.TaskEvent) error {
					if e.Type == "POLICY_PREPARED" {
						return errors.New("audit storage unavailable")
					}
					return nil
				}
			}
			runner.ActionPolicy = func(prep context.Context, c runtime.Command, _ runtime.Snapshot) (map[string]any, error) {
				if c.Capability != runtime.CapabilityPick {
					return nil, nil
				}
				if tc.cancel {
					cancel()
				}
				return tc.result(prep)
			}
			if _, err := runner.Run(ctx, task); err == nil {
				t.Fatal("unsafe preparation accepted")
			}
			if robot.count("manipulation.pick") != 0 || robot.count("manipulation.place") != 0 {
				t.Fatal("physical command dispatched")
			}
			runs, err := store.ListStepRuns(context.Background(), task.ID)
			if err != nil {
				t.Fatal(err)
			}
			for _, run := range runs {
				if run.SafetyLevel == "PHYSICAL" && run.Status == middleware.StepStarted {
					t.Fatalf("preparation became unknown physical outcome: %+v", run)
				}
			}
			if err := runner.CheckRecovery(context.Background(), task.ID); err != nil {
				t.Fatalf("pre-dispatch failure blocked recovery: %v", err)
			}
		})
	}
}
func TestActionPolicyAuditTimeCannotExtendFreshness(t *testing.T) {
	runner, _, task, robot := policyRunnerFixture(t)
	now := time.Now()
	runner.Now = func() time.Time { return now }
	robot.Now = runner.Now
	runner.ActionPolicy = func(_ context.Context, c runtime.Command, _ runtime.Snapshot) (map[string]any, error) {
		if c.Capability != runtime.CapabilityPick {
			return nil, nil
		}
		return policyParameters(now.Add(time.Second)), nil
	}
	runner.TaskEvents = func(_ context.Context, _ string, e tasks.TaskEvent) error {
		if e.Type == "POLICY_PREPARED" {
			now = now.Add(2 * time.Second)
		}
		return nil
	}
	if _, err := runner.Run(context.Background(), task); err == nil {
		t.Fatal("expired trajectory accepted after audit")
	}
	if robot.count("manipulation.pick") != 0 {
		t.Fatal("expired trajectory reached hardware")
	}
	if err := runner.CheckRecovery(context.Background(), task.ID); err != nil {
		t.Fatal(err)
	}
}
