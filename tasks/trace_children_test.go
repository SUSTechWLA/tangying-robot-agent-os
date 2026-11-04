package tasks

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

// Only LoadState may be called by this read-only projection. Any accidental
// commit/outbox operation calls the nil embedded Store and fails the test.
type traceChildStore struct {
	*MemoryStore
	eventstore.Store
	states map[string]eventstore.AggregateState
	loads  []string
	err    error
}

func (s *traceChildStore) LoadState(_ context.Context, key string) (eventstore.AggregateState, bool, error) {
	s.loads = append(s.loads, key)
	state, found := s.states[key]
	return state, found, s.err
}

func childTraceFixture(t *testing.T) (*Service, *Task, *Task, *traceChildStore) {
	t.Helper()
	plan := &capability.Plan{RobotID: "agv-a", CatalogRevision: strings.Repeat("1", 64), Calls: []capability.Call{{Tool: "sensors.read", Arguments: map[string]any{}}}}
	child := &Task{ID: "parent-edge-r2-p1", Request: "edge generated bounded goal", CurrentRevision: 2, State: taskgraph.StateExecuting, Plan: &orchestration.Bundle{Capabilities: plan}}
	now := time.Date(2026, 11, 2, 1, 0, 0, 0, time.UTC)
	step := "rev-2-cap-01"
	for _, kind := range []string{"CAPABILITY_CALL", "CAPABILITY_RPC_STARTED", "CAPABILITY_RPC_FINISHED", "CAPABILITY_VERIFIED"} {
		payload := map[string]any{"tool": "sensors.read", "robotId": "agv-a", "taskRevision": 2, "commandId": child.ID + "/" + step, "mutatesWorld": false}
		if strings.HasPrefix(kind, "CAPABILITY_RPC_") {
			payload["rpcId"], payload["service"], payload["requestId"], payload["phase"], payload["status"] = "atomic-rpc", "sensors.read", child.ID+"/"+step, "dispatch", "STARTED"
			if kind == "CAPABILITY_RPC_FINISHED" {
				payload["status"] = "SUCCEEDED"
			}
		}
		if kind == "CAPABILITY_VERIFIED" {
			payload["evidence"] = map[string]any{"readResult": "sample"}
		}
		child.Events = append(child.Events, TaskEvent{Sequence: uint64(len(child.Events) + 1), Type: kind, StepID: step, Payload: payload, OccurredAt: now})
	}
	parent := &Task{ID: "parent", Request: "用户原始全局目标", CurrentRevision: 2, Plan: &orchestration.Bundle{Capabilities: &capability.Plan{Calls: []capability.Call{{Tool: objective.Tool}}}}, Events: []TaskEvent{
		{Sequence: 1, Type: "EDGE_PLAN_CREATED", Payload: map[string]any{"childId": child.ID, "parentRevision": 2, "planDigest": objective.PlanDigest(plan)}},
		{Sequence: 2, Type: "EDGE_CHILD_EVENT", Payload: map[string]any{"childId": child.ID, "kind": "CAPABILITY_VERIFIED", "detail": map[string]any{"fabricated": true}}},
	}}
	wire, err := json.Marshal(child)
	if err != nil {
		t.Fatal(err)
	}
	store := &traceChildStore{MemoryStore: NewMemoryStore(), states: map[string]eventstore.AggregateState{"edge-child/" + child.ID: {AggregateID: "edge-child/" + child.ID, Version: 5, Data: wire, UpdatedAt: now}}}
	return NewService(store, nil), parent, child, store
}

func childHasFinding(child TraceChild, code string) bool {
	for _, finding := range child.Findings {
		if finding.Code == code {
			return true
		}
	}
	return false
}

func TestTraceChildrenReadsDurableAtomicEvidenceWithoutInventingUserRequestOrRevision(t *testing.T) {
	service, parent, child, store := childTraceFixture(t)
	// A completed goal can re-deliver the same declaration. It must not create
	// a second child or replace its authoritative events with parent mirrors.
	parent.Events = append(parent.Events, TaskEvent{Sequence: 3, Type: "EDGE_PLAN_CREATED", Payload: parent.Events[0].Payload})
	children, err := service.traceChildren(context.Background(), parent)
	if err != nil || len(children) != 1 {
		t.Fatalf("children=%+v err=%v", children, err)
	}
	got := children[0]
	if got.Coverage != "found" || !got.Found || !got.BindingVerified || got.AggregateVersion != 5 || len(store.loads) != 1 || len(got.ParentEventSequences) != 3 || got.Task.Request != child.Request || got.Task.Request == parent.Request || len(got.Task.Events) != 4 {
		t.Fatalf("wrong child source: %+v", got)
	}
	if len(got.Steps) != 1 || got.Steps[0].Status != "CONFIRMED" || len(got.RPCCalls) != 1 || got.RPCCalls[0].Status != "SUCCEEDED" {
		t.Fatalf("atomic evidence omitted: %+v", got)
	}
	if !childHasFinding(got, "ORIGINAL_REQUEST_MISSING") || !childHasFinding(got, "CURRENT_REVISION_MISSING") || !childHasFinding(got, "CHILD_SNAPSHOT_HISTORY_SCOPE") || !strings.Contains(got.RequestSource, "not original") {
		t.Fatalf("snapshot was promoted to immutable user revision: %+v", got)
	}
	if got.SnapshotSHA256 == "" {
		t.Fatal("source has no stable digest")
	}
	if got.ParentTaskID != parent.ID {
		t.Fatal("missing explicit parent task identity")
	}
	for _, finding := range got.Findings {
		want := child.ID
		if strings.HasPrefix(finding.Code, "CHILD_") {
			want = parent.ID
		}
		if finding.TaskID != want {
			t.Fatalf("finding sequence belongs to ambiguous ledger: %+v", finding)
		}
	}
}

func TestTraceChildrenRefusesCrossTaskOrConflictingDeclarationsBeforeReading(t *testing.T) {
	for _, scenario := range []string{"foreign task", "revision mismatch", "noncanonical generation", "zero generation", "missing digest", "conflicting digest"} {
		t.Run(scenario, func(t *testing.T) {
			service, parent, _, store := childTraceFixture(t)
			parent.Events = parent.Events[:1]
			payload := parent.Events[0].Payload
			switch scenario {
			case "foreign task":
				payload["childId"] = "other-edge-r2-p1"
			case "revision mismatch":
				payload["parentRevision"] = 3
			case "noncanonical generation":
				payload["childId"] = "parent-edge-r2-p01"
			case "zero generation":
				payload["childId"] = "parent-edge-r2-p0"
			case "missing digest":
				delete(payload, "planDigest")
			case "conflicting digest":
				parent.Events = append(parent.Events, TaskEvent{Sequence: 2, Type: "EDGE_PLAN_CREATED", Payload: map[string]any{"childId": payload["childId"], "parentRevision": 2, "planDigest": strings.Repeat("f", 64)}})
			}
			children, err := service.traceChildren(context.Background(), parent)
			if err != nil || len(children) != 1 || len(store.loads) != 0 || children[0].Coverage != "invalid" || children[0].Task != nil || children[0].BindingVerified {
				t.Fatalf("untrusted declaration selected aggregate: %+v loads=%v err=%v", children, store.loads, err)
			}
		})
	}
}

func TestTraceChildrenReportsMissingUnsupportedAndAdvisoryOnlyCoverage(t *testing.T) {
	for _, scenario := range []string{"missing", "unsupported", "advisory only", "not planned"} {
		t.Run(scenario, func(t *testing.T) {
			service, parent, _, store := childTraceFixture(t)
			want := scenario
			switch scenario {
			case "missing":
				store.states = map[string]eventstore.AggregateState{}
			case "unsupported":
				service.store = NewMemoryStore()
			case "advisory only":
				parent.Events = parent.Events[1:]
				want = "undeclared"
			case "not planned":
				parent.Events = nil
				want = "undeclared"
			}
			children, err := service.traceChildren(context.Background(), parent)
			if err != nil || len(children) != 1 || children[0].Coverage != want || children[0].Task != nil || children[0].Found || children[0].BindingVerified || len(children[0].Findings) == 0 {
				t.Fatalf("coverage disappeared: %+v err=%v", children, err)
			}
		})
	}
}

func TestTraceChildrenRejectsCorruptOrMismatchedAggregateWithoutBorrowingFacts(t *testing.T) {
	for _, scenario := range []string{"aggregate identity", "zero version", "invalid JSON", "trailing JSON", "child identity", "revision", "plan digest"} {
		t.Run(scenario, func(t *testing.T) {
			service, parent, child, store := childTraceFixture(t)
			key := "edge-child/" + child.ID
			state := store.states[key]
			switch scenario {
			case "aggregate identity":
				state.AggregateID = "edge-child/other"
			case "zero version":
				state.Version = 0
			case "invalid JSON":
				state.Data = json.RawMessage("{bad")
			case "trailing JSON":
				state.Data = append(state.Data, []byte(" {}")...)
			case "child identity":
				child.ID = "other-edge-r2-p1"
				state.Data, _ = json.Marshal(child)
			case "revision":
				child.CurrentRevision = 3
				state.Data, _ = json.Marshal(child)
			case "plan digest":
				child.Plan.Capabilities.RobotID = "other-robot"
				state.Data, _ = json.Marshal(child)
			}
			store.states[key] = state
			children, err := service.traceChildren(context.Background(), parent)
			if err != nil || len(children) != 1 || !children[0].Found || children[0].BindingVerified || children[0].Coverage != "invalid" || len(children[0].Steps) != 0 || len(children[0].RPCCalls) != 0 || children[0].SnapshotSHA256 == "" {
				t.Fatalf("invalid aggregate became execution facts: %+v err=%v", children, err)
			}
			if scenario != "plan digest" && children[0].Task != nil {
				t.Fatal("foreign or unreadable task was exported")
			}
		})
	}
}

func TestTraceChildrenBoundsAggregateReadsAndDisclosesOmittedReferences(t *testing.T) {
	service, parent, child, store := childTraceFixture(t)
	parent.Events = nil
	for i := 1; i <= 70; i++ {
		parent.Events = append(parent.Events, TaskEvent{Sequence: uint64(i), Type: "EDGE_PLAN_CREATED", Payload: map[string]any{"childId": fmt.Sprintf("parent-edge-r2-p%d", i), "parentRevision": 2, "planDigest": objective.PlanDigest(child.Plan.Capabilities)}})
	}
	children, err := service.traceChildren(context.Background(), parent)
	if err != nil || len(children) != 64 || len(store.loads) != 64 || !children[63].CoverageTruncated || children[63].OmittedReferences != 6 || !childHasFinding(children[63], "CHILD_TRACE_LIMIT") {
		t.Fatalf("unbounded/hidden omission: count=%d loads=%d err=%v", len(children), len(store.loads), err)
	}
}

func TestTraceChildrenDoesNotConvertStorageFailureIntoMissingEvidence(t *testing.T) {
	service, parent, _, store := childTraceFixture(t)
	cause := errors.New("aggregate disk unavailable")
	store.err = cause
	if _, err := service.traceChildren(context.Background(), parent); !errors.Is(err, cause) {
		t.Fatalf("storage failure lost: %v", err)
	}
}
