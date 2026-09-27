package capabilityagent

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/types/known/structpb"
	"path/filepath"
	"testing"
	"time"
)

type fakeProvider struct {
	starts, polls, cancels                  int
	keepRunning, wrongIdentity, badEvidence bool
}

func fields(value map[string]any) *structpb.Struct {
	s, err := structpb.NewStruct(value)
	if err != nil {
		panic(err)
	}
	return s
}
func (f *fakeProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	schema := fields(map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false})
	read := fields(map[string]any{"version": "1", "effects": []any{"READ"}})
	contract := fields(map[string]any{"version": "1", "effects": []any{"PHYSICAL_MOTION", "ARTIFACT_WRITE"}, "resources": []any{"robot"},
		"operation":    map[string]any{"statusService": "mapping.status", "cancelService": "mapping.cancel", "identityPath": "sessionId", "statusIdentityPath": "sessionId", "statePath": "state", "running": []any{"exploring"}, "success": []any{"completed"}, "failure": []any{"cancelled", "failed"}},
		"verification": map[string]any{"service": "mapping.status", "required": []any{"activeMap.mapId", "activeMap.mapRevision", "activeMap.calibrationRevision"}}})
	return &robotv1.ServiceCatalog{RobotId: "heterogeneous-unit", Services: []*robotv1.ServiceDefinition{
		{Name: "calibration.get", Available: true, InputSchema: schema, Contract: read},
		{Name: "mapping.build", Available: true, InputSchema: fields(map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"explore"}}}, "additionalProperties": false}), Contract: contract, MutatesWorld: true},
		{Name: "mapping.status", Available: true, InputSchema: schema, Contract: read},
		{Name: "mapping.cancel", Available: true, InputSchema: schema, MutatesWorld: true},
	}}, nil
}
func (f *fakeProvider) CallService(_ context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	result := map[string]any{}
	switch r.Name {
	case "calibration.get":
		result["revision"] = "cal-1"
	case "mapping.build":
		f.starts++
		result = map[string]any{"sessionId": "owned-session", "state": "exploring"}
	case "mapping.cancel":
		f.cancels++
		result["state"] = "cancelled"
	case "mapping.status":
		f.polls++
		state := "completed"
		if f.keepRunning {
			state = "exploring"
		}
		if f.cancels > 0 {
			state = "cancelled"
		}
		result = map[string]any{"sessionId": "owned-session", "state": state, "activeMap": map[string]any{"mapId": "measured-map", "mapRevision": "hash-1", "calibrationRevision": "cal-1"}}
		if f.wrongIdentity {
			result["sessionId"] = "foreign-session"
		}
		if f.badEvidence {
			delete(result, "activeMap")
		}
	default:
		return nil, errors.New("unregistered test service")
	}
	return &robotv1.ServiceResponse{Ok: true, Result: fields(result)}, nil
}
func setupGoal(t *testing.T, f *fakeProvider) (*Executor, *tasks.Task) {
	t.Helper()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "goals.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	service := tasks.NewService(store, intent.NewDeterministicParser())
	service.SetGoalPlanner(&Planner{Provider: f, ParseLegacy: func(request string) (json.RawMessage, error) {
		parsed, err := intent.NewDeterministicParser().Parse(request)
		if err != nil {
			return nil, err
		}
		return json.Marshal(parsed)
	}})
	task, err := service.Create(context.Background(), "检查标定，建图，然后去厨房", "auto")
	if err != nil {
		t.Fatal(err)
	}
	task, err = service.Approve(context.Background(), task.ID, "test-operator")
	if err != nil {
		t.Fatal(err)
	}
	return &Executor{Provider: f, Store: store, Tasks: service, PollInterval: time.Millisecond}, task
}
func TestGoalRunsCalibrationMappingAndLegacyNavigationWithVerifiedEvidence(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	navigations := 0
	steps, err := e.Run(context.Background(), task, nil, func(_ context.Context, call capability.Call, prefix string) error {
		request, _ := call.Arguments["request"].(string)
		if request != "去厨房" || prefix == "" {
			t.Fatalf("bad legacy binding: %s %s", request, prefix)
		}
		navigations++
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(steps) != 3 || f.starts != 1 || navigations != 1 {
		t.Fatalf("steps %v starts %d nav %d", steps, f.starts, navigations)
	}
	if _, err := e.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatal(err)
	}
	if f.starts != 1 {
		t.Fatal("completed goal replayed motion")
	}
}
func TestAcknowledgedOperationResumesAfterExecutorRestartWithoutStartingAgain(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	task.Plan.Capabilities.Calls = task.Plan.Capabilities.Calls[1:2]
	call := task.Plan.Capabilities.Calls[0]
	id := stepID(task, 0)
	record := middleware.StepRecord{TaskID: task.ID, StepID: id, IdempotencyKey: task.ID + "/" + id, Capability: call.Tool, SafetyLevel: "physical_motion"}
	if err := e.Store.MarkStepStarted(context.Background(), record); err != nil {
		t.Fatal(err)
	}
	if err := e.record(context.Background(), task.ID, id, "CAPABILITY_RECEIPT", map[string]any{"binding": capability.Fingerprint(call), "result": map[string]any{"sessionId": "owned-session"}}); err != nil {
		t.Fatal(err)
	}
	restarted := *e
	if _, err := restarted.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatal(err)
	}
	if f.starts != 0 {
		t.Fatal("resume restarted an acknowledged mapping operation")
	}
}
func TestUnknownWriteWithoutReceiptNeverRetries(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	task.Plan.Capabilities.Calls = task.Plan.Capabilities.Calls[1:2]
	if err := e.Store.MarkStepStarted(context.Background(), middleware.StepRecord{TaskID: task.ID, StepID: stepID(task, 0), IdempotencyKey: "lost-write", Capability: "mapping.build", SafetyLevel: "physical_motion"}); err != nil {
		t.Fatal(err)
	}
	_, err := e.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, ErrOutcomeUnknown) || f.starts != 0 {
		t.Fatalf("unknown write retried: %v starts=%d", err, f.starts)
	}
}
func TestOperationMustMatchIdentityAndProduceMapEvidence(t *testing.T) {
	for _, bad := range []string{"identity", "evidence"} {
		t.Run(bad, func(t *testing.T) {
			f := &fakeProvider{wrongIdentity: bad == "identity", badEvidence: bad == "evidence"}
			e, task := setupGoal(t, f)
			_, err := e.Run(context.Background(), task, nil, nil)
			if err == nil {
				t.Fatal("unverified operation completed")
			}
		})
	}
}
func TestCancelWaitsForOwnedOperationStop(t *testing.T) {
	f := &fakeProvider{keepRunning: true}
	e, task := setupGoal(t, f)
	ctx, cancel := context.WithCancel(context.Background())
	before := func(context.Context) error {
		if f.starts > 0 {
			cancel()
		}
		return nil
	}
	// Cancel at the first status poll without relying on wall-clock sleeps.
	e.OperationTimeout = 3 * time.Millisecond
	_, err := e.Run(ctx, task, before, nil)
	if err == nil || f.cancels != 1 {
		t.Fatalf("cancel not verified: %v count=%d", err, f.cancels)
	}
}
func TestOfflineGrammarDoesNotTurnNegationOrUnknownClausesIntoMotion(t *testing.T) {
	p := &Planner{Provider: &fakeProvider{}}
	for _, request := range []string{"检查地图但不要移动", "检查标定，擦桌子", "建图前先问我"} {
		_, _, err := p.PlanGoal(context.Background(), request)
		if err == nil {
			t.Fatalf("unsafe partial understanding: %s", request)
		}
	}
}

type rejectedProvider struct {
	*fakeProvider
	known bool
}

func (p rejectedProvider) CallService(ctx context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if r.Name != "mapping.build" {
		return p.fakeProvider.CallService(ctx, r)
	}
	result := map[string]any{}
	if p.known {
		result["outcome"] = "REJECTED"
	}
	return &robotv1.ServiceResponse{Ok: false, Code: "PRECONDITION", Message: "fault", Result: fields(result)}, nil
}
func TestProviderPreAdmissionRejectionDoesNotInventUnknownOutcome(t *testing.T) {
	for _, known := range []bool{true, false} {
		f := &fakeProvider{}
		e, task := setupGoal(t, f)
		e.Provider = rejectedProvider{fakeProvider: f, known: known}
		_, err := e.Run(context.Background(), task, nil, nil)
		if known && (!errors.Is(err, ErrRejected) || errors.Is(err, ErrOutcomeUnknown)) {
			t.Fatalf("known rejection: %v", err)
		}
		if !known && !errors.Is(err, ErrOutcomeUnknown) {
			t.Fatalf("handler failure lost uncertainty: %v", err)
		}
	}
}
