package capabilityagent

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestEveryCapabilityLifecycleRecordRetainsApprovedExecutionBindings(t *testing.T) {
	f := &fakeProvider{}
	executor, task := setupGoal(t, f)
	if _, err := executor.Run(context.Background(), task, nil, func(context.Context, capability.Call, string) error { return nil }); err != nil {
		t.Fatal(err)
	}
	reloaded, err := executor.Tasks.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]int{}
	for _, event := range reloaded.Events {
		if !strings.HasPrefix(event.Type, "CAPABILITY_") && event.Type != "TOOL_ACTIVITY" {
			continue
		}
		seen[event.Type]++
		revision, _ := json.Marshal(event.Payload["taskRevision"])
		if event.Payload["commandId"] != task.ID+"/"+event.StepID || event.Payload["robotId"] != task.Plan.Capabilities.RobotID || string(revision) != "1" || event.Payload["tool"] == "" {
			t.Fatalf("unbound lifecycle record: %#v", event)
		}
		if _, explicit := event.Payload["mutatesWorld"].(bool); !explicit {
			t.Fatalf("lost manifest effect classification: %#v", event)
		}
	}
	if seen["CAPABILITY_CALL"] != 3 || seen["CAPABILITY_RECEIPT"] != 2 || seen["CAPABILITY_VERIFIED"] != 3 || seen["TOOL_ACTIVITY"] != 8 {
		t.Fatalf("missing lifecycle coverage: %+v", seen)
	}
	// The ordinary stored task can reconstruct each completed parent capability
	// after restart without consulting executor-local state or dispatching again.
	view := tasks.ContextFor(*reloaded, "recovery", time.Unix(100, 0))
	actions := 0
	for _, record := range view.Records {
		if !strings.HasPrefix(record.ID, "memory:action:") {
			continue
		}
		actions++
		if !strings.Contains(record.Statement, `"recorded_state":"COMPLETION_RECORDED"`) || !strings.Contains(record.Statement, `"unresolved_dispatch":false`) {
			t.Fatalf("durable bound completion became an unresolved dispatch: %s", record.Statement)
		}
	}
	if actions != 3 {
		t.Fatalf("mirrored activity duplicated execution memory: %d", actions)
	}
}

func TestFailedCapabilityRecordRetainsTheSameBindingsWithoutClaimingCompletion(t *testing.T) {
	f := &fakeProvider{}
	executor, task := setupGoal(t, f)
	executor.Provider = transportFailureProvider{fakeProvider: f, tool: "mapping.build"}
	if _, err := executor.Run(context.Background(), task, nil, nil); err == nil {
		t.Fatal("transport failure disappeared")
	}
	reloaded, err := executor.Tasks.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	for _, event := range reloaded.Events {
		if event.Type == "CAPABILITY_FAILED" {
			if event.Payload["commandId"] != task.ID+"/rev-1-cap-02" || event.Payload["robotId"] != task.Plan.Capabilities.RobotID || event.Payload["outcomeUnknown"] != true {
				t.Fatalf("failure lost its physical identity: %#v", event)
			}
		}
	}
	view := tasks.ContextFor(*reloaded, "recovery", time.Unix(100, 0))
	unknown := 0
	for _, record := range view.Records {
		if strings.HasPrefix(record.ID, "memory:action:") && strings.Contains(record.Statement, `"tool":"mapping.build"`) {
			if !strings.Contains(record.Statement, `"outcome_unknown":true`) || !strings.Contains(record.Statement, `"unresolved_dispatch":true`) {
				t.Fatalf("physical uncertainty was dropped: %s", record.Statement)
			}
			unknown++
		}
	}
	if unknown != 1 {
		t.Fatalf("expected one bound unknown physical action, got %d", unknown)
	}
}
