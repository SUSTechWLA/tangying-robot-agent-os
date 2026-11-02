package capabilityagent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type failingRPCEvidence struct {
	TaskEvents
	kind, phase string
	cause       error
}

type appendOnlyEvidence struct {
	TaskEvents
	recorder interface {
		RecordEvent(context.Context, string, tasks.TaskEvent) (tasks.TaskEvent, error)
	}
	appends, records int
}

func (r *appendOnlyEvidence) AppendEvent(context.Context, string, tasks.TaskEvent) (*tasks.Task, error) {
	r.appends++
	return nil, errors.New("snapshot rewriting path must not be used")
}

func (r *appendOnlyEvidence) RecordEvent(ctx context.Context, id string, event tasks.TaskEvent) (tasks.TaskEvent, error) {
	r.records++
	return r.recorder.RecordEvent(ctx, id, event)
}

func TestCapabilityRPCUsesAppendOnlyEvidenceWhenSupported(t *testing.T) {
	e, task := setupGoal(t, &fakeProvider{})
	optimized := &appendOnlyEvidence{TaskEvents: e.Tasks, recorder: e.Tasks.(interface {
		RecordEvent(context.Context, string, tasks.TaskEvent) (tasks.TaskEvent, error)
	})}
	e.Tasks = optimized
	if _, err := e.Run(context.Background(), task, nil, func(context.Context, capability.Call, string) error { return nil }); err != nil {
		t.Fatal(err)
	}
	if optimized.appends != 0 || optimized.records != 24 {
		t.Fatalf("snapshot fallback=%d recorded=%d", optimized.appends, optimized.records)
	}
	current, err := e.Tasks.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	previous := uint64(0)
	for _, event := range current.Events {
		if event.Sequence <= previous {
			t.Fatalf("append-only sequence regressed: %+v", event)
		}
		previous = event.Sequence
	}
}

func (f failingRPCEvidence) AppendEvent(ctx context.Context, id string, event tasks.TaskEvent) (*tasks.Task, error) {
	if (f.kind == "" || f.kind == event.Type) && strings.HasPrefix(event.Type, "CAPABILITY_RPC_") &&
		event.StepID == "rev-1-cap-02" && (f.phase == "" || strings.HasPrefix(event.Payload["phase"].(string), f.phase)) {
		return nil, f.cause
	}
	return f.TaskEvents.AppendEvent(ctx, id, event)
}

func TestRPCTraceSeparatesDispatchStatusAndVerificationWithBoundEvidence(t *testing.T) {
	e, task := setupGoal(t, &fakeProvider{})
	if _, err := e.Run(context.Background(), task, nil, func(context.Context, capability.Call, string) error { return nil }); err != nil {
		t.Fatal(err)
	}
	current, _ := e.Tasks.Get(context.Background(), task.ID)
	starts := map[string]tasks.TaskEvent{}
	phases := map[string]int{}
	finished := 0
	for _, event := range current.Events {
		if !strings.HasPrefix(event.Type, "CAPABILITY_RPC_") {
			continue
		}
		rpc, _ := event.Payload["rpcId"].(string)
		if rpc == "" || event.Payload["robotId"] != task.Plan.Capabilities.RobotID || event.Payload["commandId"] != task.ID+"/"+event.StepID || event.Payload["taskRevision"] == nil {
			t.Fatalf("unbound RPC: %+v", event)
		}
		if event.Type == "CAPABILITY_RPC_STARTED" {
			if _, duplicate := starts[rpc]; duplicate {
				t.Fatalf("reused RPC identity: %s", rpc)
			}
			starts[rpc] = event
			continue
		}
		start, exists := starts[rpc]
		if !exists || start.Payload["service"] != event.Payload["service"] || start.Payload["requestId"] != event.Payload["requestId"] || event.Payload["status"] != "SUCCEEDED" || event.Payload["resultSHA256"] == nil || event.Payload["result"] == nil {
			t.Fatalf("unpaired or incomplete result: %+v", event)
		}
		finished++
		phases[event.Payload["phase"].(string)]++
	}
	if len(starts) != 4 || finished != 4 || phases["dispatch"] != 2 || phases["status"] != 1 || phases["verification"] != 1 {
		t.Fatalf("missing atomic calls: starts=%d finished=%d phases=%v", len(starts), finished, phases)
	}
}

func TestRPCTracePersistenceFailureNeverSilentlyDispatchesOrSucceeds(t *testing.T) {
	for _, kind := range []string{"CAPABILITY_RPC_STARTED", "CAPABILITY_RPC_FINISHED"} {
		t.Run(kind, func(t *testing.T) {
			f := &fakeProvider{}
			e, task := setupGoal(t, f)
			cause := errors.New("injected trace disk failure")
			original := e.Tasks
			e.Tasks = failingRPCEvidence{TaskEvents: original, kind: kind, phase: "dispatch", cause: cause}
			_, err := e.Run(context.Background(), task, nil, nil)
			if !errors.Is(err, cause) {
				t.Fatalf("lost persistence cause: %v", err)
			}
			state, _ := e.Store.StepStatus(context.Background(), task.ID, "rev-1-cap-02")
			if kind == "CAPABILITY_RPC_STARTED" {
				if f.starts != 0 || !errors.Is(err, errRPCNotDispatched) || errors.Is(err, ErrOutcomeUnknown) || state != middleware.StepFailed {
					t.Fatalf("failed start dispatched or invented uncertainty: calls=%d state=%s err=%v", f.starts, state, err)
				}
			} else {
				if f.starts != 1 || !errors.Is(err, ErrOutcomeUnknown) || state != middleware.StepStarted {
					t.Fatalf("lost result became success: calls=%d state=%s err=%v", f.starts, state, err)
				}
				e.Tasks = original
				if _, err := e.Run(context.Background(), task, nil, nil); !errors.Is(err, ErrOutcomeUnknown) || f.starts != 1 {
					t.Fatalf("unrecorded write replayed: calls=%d err=%v", f.starts, err)
				}
			}
		})
	}
}

func TestOwnedStopKeepsBindingsAndRunsEvenWhenRPCEvidenceDiskFails(t *testing.T) {
	for _, failEvidence := range []bool{false, true} {
		t.Run(map[bool]string{false: "recorded", true: "disk failure"}[failEvidence], func(t *testing.T) {
			f := &fakeProvider{keepRunning: true}
			e, task := setupGoal(t, f)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			cause := errors.New("stop audit disk unavailable")
			if failEvidence {
				e.Tasks = failingRPCEvidence{TaskEvents: e.Tasks, phase: "stop_", cause: cause}
			}
			e.Tasks = cancelAfterProgressCommit{TaskEvents: e.Tasks, cancel: cancel}
			_, err := e.Run(ctx, task, nil, nil)
			if !errors.Is(err, context.Canceled) || f.cancels != 1 || f.starts != 1 {
				t.Fatalf("owned stop blocked: err=%v starts=%d cancels=%d", err, f.starts, f.cancels)
			}
			if failEvidence && (!errors.Is(err, cause) || !errors.Is(err, ErrOutcomeUnknown)) {
				t.Fatalf("stop evidence outage hidden: %v", err)
			}
			current, _ := e.Tasks.Get(context.Background(), task.ID)
			stop := false
			for _, event := range current.Events {
				if event.Type != "CAPABILITY_STOP_CONFIRMED" {
					continue
				}
				stop = true
				if event.Payload["commandId"] != task.ID+"/rev-1-cap-02" || event.Payload["robotId"] != task.Plan.Capabilities.RobotID || event.Payload["tool"] != "mapping.build" || event.Payload["taskRevision"] == nil {
					t.Fatalf("stop lost approved binding: %+v", event)
				}
			}
			if !stop {
				t.Fatal("missing terminal stop observation")
			}
		})
	}
}

func TestRPCTraceRetainsFailedVerificationObservation(t *testing.T) {
	e, task := setupGoal(t, &fakeProvider{badEvidence: true})
	if _, err := e.Run(context.Background(), task, nil, nil); err == nil {
		t.Fatal("bad evidence was accepted")
	}
	current, _ := e.Tasks.Get(context.Background(), task.ID)
	for _, event := range current.Events {
		if event.Type == "CAPABILITY_RPC_FINISHED" && event.Payload["phase"] == "verification" {
			result := event.Payload["result"].(map[string]any)
			if result["state"] != "completed" || result["activeMap"] != nil || event.Payload["status"] != "SUCCEEDED" {
				t.Fatalf("actual successful transport result was rewritten by verification failure: %+v", event)
			}
			return
		}
	}
	t.Fatal("failed verification lost the actual provider observation")
}

func TestRPCTraceGivesReadRetriesDistinctAttemptIdentities(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	e.EndIndex = 1
	p := &interruptedReadProvider{fakeProvider: f, failure: status.Error(codes.Unavailable, "transient")}
	e.Provider = p
	e.RecoverRead = func(context.Context, string, string, string, error) error { return nil }
	if _, err := e.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatal(err)
	}
	current, _ := e.Tasks.Get(context.Background(), task.ID)
	ids := map[string]bool{}
	statuses := map[string]int{}
	for _, event := range current.Events {
		if event.Type != "CAPABILITY_RPC_FINISHED" {
			continue
		}
		id := event.Payload["rpcId"].(string)
		if ids[id] || event.Payload["requestId"] != task.ID+"/rev-1-cap-01" {
			t.Fatalf("retry identity lost: %+v", event)
		}
		ids[id] = true
		statuses[event.Payload["status"].(string)]++
	}
	if len(ids) != 2 || statuses["FAILED"] != 1 || statuses["SUCCEEDED"] != 1 {
		t.Fatalf("retry history merged: %v %v", ids, statuses)
	}
}

func TestRPCAuditProjectionExplicitlyMarksOmissions(t *testing.T) {
	payload := map[string]any{}
	auditValue(payload, "result", map[string]any{"points": []any{1, 2, 3}, "state": "ready"})
	if payload["resultProjected"] != true || payload["resultOmitted"] != false || payload["resultSHA256"] == nil || payload["result"].(map[string]any)["points"] != nil {
		t.Fatalf("bulk omission hidden: %v", payload)
	}
	auditValue(payload, "arguments", map[string]any{"message": strings.Repeat("x", 20*1024)})
	if payload["argumentsOmitted"] != true || payload["arguments"] != nil || payload["argumentsSHA256"] == nil {
		t.Fatal("oversized payload silently truncated")
	}
}

func TestReadRecoveryDoesNotHideRPCTracePersistenceFailure(t *testing.T) {
	for _, kind := range []string{"CAPABILITY_RPC_STARTED", "CAPABILITY_RPC_FINISHED"} {
		t.Run(kind, func(t *testing.T) {
			base := &fakeProvider{}
			e, task := setupGoal(t, base)
			provider := &interruptedReadProvider{fakeProvider: base, failure: status.Error(codes.Unavailable, "provider interrupted")}
			e.Provider = provider
			cause := status.Error(codes.Unavailable, "audit store unavailable")
			e.Tasks = failingRPCEvidence{TaskEvents: e.Tasks, kind: kind, cause: cause}
			recoveries := 0
			e.RecoverRead = func(context.Context, string, string, string, error) error { recoveries++; return nil }
			read := false
			ctx := context.WithValue(context.Background(), executionEventBindingKey{}, executionEventBinding{taskID: task.ID, stepID: "rev-1-cap-02", robotID: task.Plan.Capabilities.RobotID, tool: "calibration.get", revision: 1, mutatesWorld: &read})
			_, err := e.callRecoverableRead(ctx, task.ID, "rev-1-cap-02", task.Plan.Capabilities.RobotID, "calibration.get", task.ID+"/rev-1-cap-02", map[string]any{}, false)
			if !errors.Is(err, cause) || recoveries != 0 {
				t.Fatalf("audit outage converted into a recoverable robot error: recoveries=%d err=%v", recoveries, err)
			}
			want := map[string]int{"CAPABILITY_RPC_STARTED": 0, "CAPABILITY_RPC_FINISHED": 1}[kind]
			if provider.calls != want {
				t.Fatalf("provider calls=%d want=%d", provider.calls, want)
			}
		})
	}
}

func TestUndispatchedStatusReadDoesNotClaimParentWriteWasNotDispatched(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	e.Tasks = failingRPCEvidence{TaskEvents: e.Tasks, kind: "CAPABILITY_RPC_STARTED", phase: "status", cause: errors.New("poll audit unavailable")}
	if _, err := e.Run(context.Background(), task, nil, nil); err == nil || f.starts != 1 {
		t.Fatalf("write/failed status not exercised: starts=%d err=%v", f.starts, err)
	}
	current, _ := e.Tasks.Get(context.Background(), task.ID)
	for _, event := range current.Events {
		if event.Type != "CAPABILITY_FAILED" {
			continue
		}
		if event.Payload["phase"] != "status" || event.Payload["outcomeUnknown"] != true || event.Payload["requestNotDispatched"] != false {
			t.Fatalf("missing status dispatch erased the original write: %+v", event)
		}
		return
	}
	t.Fatal("failed status has no parent failure evidence")
}

type failStepPersistence struct {
	middleware.ExecutionStore
	cause error
}

func (s failStepPersistence) MarkStepFailed(context.Context, middleware.StepRecord) error {
	return s.cause
}

func TestStepFailurePersistenceDoesNotDiscardProviderFailure(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	e.Provider = transportFailureProvider{fakeProvider: f, tool: "calibration.get"}
	cause := errors.New("step store write failed")
	e.Store = failStepPersistence{ExecutionStore: e.Store, cause: cause}
	_, err := e.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, cause) || status.Code(err) != codes.Unavailable {
		t.Fatalf("one of the two failures was discarded: %v", err)
	}
}

type failReceiptPersistence struct {
	TaskEvents
	cause error
}

func (s failReceiptPersistence) AppendEvent(ctx context.Context, id string, event tasks.TaskEvent) (*tasks.Task, error) {
	if event.Type == "CAPABILITY_RECEIPT" && event.StepID == "rev-1-cap-02" {
		return nil, s.cause
	}
	return s.TaskEvents.AppendEvent(ctx, id, event)
}

func TestReceiptFailureRetainsStorageCauseAndDoesNotReplayWrite(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	cause := errors.New("receipt store write failed")
	original := e.Tasks
	e.Tasks = failReceiptPersistence{TaskEvents: e.Tasks, cause: cause}
	if _, err := e.Run(context.Background(), task, nil, nil); !errors.Is(err, cause) || !errors.Is(err, ErrOutcomeUnknown) || f.starts != 1 {
		t.Fatalf("receipt failure disappeared: starts=%d err=%v", f.starts, err)
	}
	e.Tasks = original
	if _, err := e.Run(context.Background(), task, nil, nil); !errors.Is(err, ErrOutcomeUnknown) || f.starts != 1 {
		t.Fatalf("receipt loss replayed motion: starts=%d err=%v", f.starts, err)
	}
}

func TestRPCProviderTextIsBoundedUTF8WithOriginalDigest(t *testing.T) {
	for _, field := range []string{"providerMessage", "error", "providerCode"} {
		payload := map[string]any{}
		original := strings.Repeat("异常证据", 1024)
		limit := 2048
		if field == "providerCode" {
			limit = 256
		}
		auditText(payload, field, original, limit)
		digest := sha256.Sum256([]byte(original))
		preview := payload[field].(string)
		if !utf8.ValidString(preview) || len(preview) > limit || !strings.HasPrefix(original, preview) || payload[field+"Bytes"] != len(original) || payload[field+"SHA256"] != hex.EncodeToString(digest[:]) || payload[field+"Truncated"] != true || payload[field+"Projected"] != true {
			t.Fatalf("invalid bounded %s evidence: %+v", field, payload)
		}
	}
	payload := map[string]any{}
	auditText(payload, "error", "invalid\xffUTF8", 2048)
	if !utf8.ValidString(payload["error"].(string)) || payload["errorProjected"] != true || payload["errorTruncated"] != false {
		t.Fatalf("invalid UTF-8 was not disclosed: %+v", payload)
	}
}
