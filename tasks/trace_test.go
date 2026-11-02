package tasks_test

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func traceFixture() (tasks.Task, []tasks.RevisionRecord) {
	now := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	task := tasks.Task{ID: "trace-task", Request: "检查一楼", CurrentRevision: 1, AggregateVersion: 1, State: taskgraph.StateExecuting,
		Events: []tasks.TaskEvent{{Sequence: 1, Type: "TASK_CREATED", OccurredAt: now}}}
	history := []tasks.RevisionRecord{{Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 1, Request: task.Request}}}
	return task, history
}

func traceActivity(task *tasks.Task, revision any, command, status string) {
	task.Events = append(task.Events, tasks.TaskEvent{Sequence: uint64(len(task.Events) + 1), Type: "TOOL_ACTIVITY", StepID: "scan",
		OccurredAt: task.Events[0].OccurredAt.Add(time.Duration(len(task.Events)) * time.Second),
		Payload: map[string]any{"taskRevision": revision, "robotId": "agv-a", "stepId": "scan", "commandId": command,
			"toolName": "observe_scene", "activityStatus": status, "mutatesWorld": false}})
}

func traceHas(t *testing.T, trace tasks.Trace, code string) {
	t.Helper()
	for _, f := range trace.Integrity.Findings {
		if f.Code == code {
			return
		}
	}
	t.Fatalf("missing %s: %+v", code, trace.Integrity)
}

func TestTraceOriginalRequestAndCommandsNeverBorrowNewRevisionOrRetry(t *testing.T) {
	task, history := traceFixture()
	task.Request, task.CurrentRevision = "改为检查二楼", 2
	history = append(history, tasks.RevisionRecord{Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 2, Request: task.Request}})
	traceActivity(&task, 1, "old-command", "RUNNING")
	traceActivity(&task, 2, "new-attempt-1", "RUNNING")
	traceActivity(&task, 2, "new-attempt-2", "RUNNING")
	traceActivity(&task, 2, "new-attempt-2", "CONFIRMED")
	trace := tasks.BuildTrace(task, history, nil, true, false)
	if trace.OriginalRequest.Text != "检查一楼" || trace.CurrentRequest != "改为检查二楼" || len(trace.Steps) != 3 || trace.Steps[0].Status != "RUNNING" || trace.Steps[1].Status != "RUNNING" || trace.Steps[2].Status != "CONFIRMED" {
		t.Fatalf("identity collapse: %+v", trace)
	}
	task.State = taskgraph.StateSucceeded
	traceHas(t, tasks.BuildTrace(task, history, nil, true, false), "SUCCESS_WITH_OPEN_COMMAND")
}

func TestTraceCorruptionAndLegacyGapsRemainVisible(t *testing.T) {
	for _, test := range []struct {
		name, code string
		change     func(*tasks.Task)
	}{
		{"sequence-gap", "EVENT_SEQUENCE_INVALID", func(x *tasks.Task) { x.Events[1].Sequence = 4 }},
		{"fractional-revision", "STEP_BINDING_INCOMPLETE", func(x *tasks.Task) { x.Events[1].Payload["taskRevision"] = 1.5 }},
		{"step-conflict", "STEP_BINDING_INCOMPLETE", func(x *tasks.Task) { x.Events[1].Payload["stepId"] = "other" }},
		{"old-state", "STATE_TRANSITION_INCOMPLETE", func(x *tasks.Task) { x.Events[1].Type = "STATE_CHANGED" }},
		{"clock-rollback", "EVENT_CLOCK_REGRESSION", func(x *tasks.Task) { x.Events[1].OccurredAt = x.Events[0].OccurredAt.Add(-time.Second) }},
		{"identity-conflict", "EVENT_ID_CONFLICT", func(x *tasks.Task) {
			x.Events[1].Payload["eventId"] = "shared"
			traceActivity(x, 1, "command", "CONFIRMED")
			x.Events[2].Payload["eventId"] = "shared"
		}},
		{"snapshot-state", "STATE_SNAPSHOT_MISMATCH", func(x *tasks.Task) {
			x.Events[1].Type = "STATE_CHANGED"
			x.Events[1].Payload = map[string]any{"previousState": "READY", "state": "OBSERVING"}
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			task, history := traceFixture()
			traceActivity(&task, 1, "command", "RUNNING")
			test.change(&task)
			traceHas(t, tasks.BuildTrace(task, history, nil, true, false), test.code)
		})
	}
	task, _ := traceFixture()
	trace := tasks.BuildTrace(task, nil, nil, false, false)
	traceHas(t, trace, "ORIGINAL_REQUEST_MISSING")
	if trace.OriginalRequest.Text != "" {
		t.Fatal("current request fabricated as original")
	}
}

func TestTraceEvidenceMatchesWholeBindingAndReportsExpiry(t *testing.T) {
	task, history := traceFixture()
	traceActivity(&task, 1, "command", "RUNNING")
	traceActivity(&task, 1, "command", "CONFIRMED")
	task.Events[2].Payload["evidenceIds"] = []string{"capture-a"}
	evidence := []tasks.EvidenceRecord{{ID: "record-a", TaskID: task.ID, TaskRevision: 2, StepID: "scan", RobotID: "agv-a", CaptureID: "capture-a"}}
	traceHas(t, tasks.BuildTrace(task, history, evidence, true, false), "EVIDENCE_BINDING_CONFLICT")
	evidence[0].TaskRevision = 1
	evidence[0].Expired = true
	evidence[0].Snapshot = json.RawMessage(`{"privateRawSensor":true}`)
	trace := tasks.BuildTrace(task, history, evidence, true, false)
	traceHas(t, trace, "EVIDENCE_EXPIRED")
	wire, _ := json.Marshal(trace)
	if strings.Contains(string(wire), "privateRawSensor") {
		t.Fatal("raw sensor embedded in export")
	}
}

func TestTraceKnownMirrorsDoNotCountAsCommandsOrConflicts(t *testing.T) {
	task, history := traceFixture()
	traceActivity(&task, 1, "command", "RUNNING")
	task.Events[1].Payload["eventId"] = "action-1"
	mirror := task.Events[1]
	mirror.Sequence = 3
	mirror.Type = "action.executed"
	task.Events = append(task.Events, mirror)
	traceActivity(&task, 1, "command", "CONFIRMED")
	trace := tasks.BuildTrace(task, history, nil, true, false)
	if len(trace.Steps) != 1 || trace.Integrity.Status != "consistent" {
		t.Fatalf("mirror not recognized: %+v", trace)
	}
}

func TestTraceStableAcrossSQLiteReopen(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "trace.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	if err = service.Transition(ctx, task.ID, taskgraph.StateObserving, "start"); err != nil {
		t.Fatal(err)
	}
	before, err := service.Trace(ctx, task.ID, store)
	if err != nil {
		t.Fatal(err)
	}
	if err = store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	after, err := tasks.NewService(reopened, nil).Trace(ctx, task.ID, reopened)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := json.Marshal(before)
	b, _ := json.Marshal(after)
	if string(a) != string(b) || !after.Source.Stable || len(after.Source.SnapshotSHA256) != 64 {
		t.Fatalf("reopen changed export: before=%s after=%s", a, b)
	}
}

type movingTraceStore struct {
	tasks.Repository
	reads int
}

func (s *movingTraceStore) Get(ctx context.Context, id string) (*tasks.Task, error) {
	x, err := s.Repository.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	s.reads++
	x.AggregateVersion = uint64(s.reads)
	return x, nil
}

func TestTraceRefusesMixedSnapshot(t *testing.T) {
	base := tasks.NewMemoryStore()
	service := tasks.NewService(base, intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	moving := &movingTraceStore{Repository: base}
	trace, err := tasks.NewService(moving, nil).Trace(context.Background(), task.ID, nil)
	if trace != nil || !errors.Is(err, tasks.ErrTraceBusy) || moving.reads != 6 {
		t.Fatalf("mixed snapshot exposed: %+v %v reads=%d", trace, err, moving.reads)
	}
}

func TestTraceDoesNotPromoteEmptyCapabilityProofOrOrphanRPC(t *testing.T) {
	task, history := traceFixture()
	traceActivity(&task, 1, "command", "RUNNING")
	task.Events[1].Type = "CAPABILITY_CALL"
	task.Events[1].Payload["tool"] = "observe_scene"
	task.Events = append(task.Events, tasks.TaskEvent{Sequence: 3, Type: "CAPABILITY_VERIFIED", StepID: "scan", OccurredAt: task.Events[1].OccurredAt.Add(time.Second), Payload: map[string]any{"taskRevision": 1, "robotId": "agv-a", "commandId": "command", "tool": "observe_scene", "mutatesWorld": true, "evidence": map[string]any{}}})
	traceHas(t, tasks.BuildTrace(task, history, nil, true, false), "CAPABILITY_VERIFICATION_CHAIN_INCOMPLETE")
	task, history = traceFixture()
	for i, kind := range []string{"CAPABILITY_RPC_STARTED", "CAPABILITY_RPC_FINISHED"} {
		task.Events = append(task.Events, tasks.TaskEvent{Sequence: uint64(i + 2), Type: kind, StepID: "scan", OccurredAt: task.Events[0].OccurredAt.Add(time.Duration(i+1) * time.Second), Payload: map[string]any{"rpcId": "rpc-orphan", "service": "mapping.status", "commandId": "absent", "taskRevision": 7, "robotId": "agv-a", "status": "banana"}})
	}
	trace := tasks.BuildTrace(task, history, nil, true, false)
	for _, code := range []string{"RPC_REVISION_MISSING", "RPC_PARENT_MISSING", "RPC_STATUS_INVALID"} {
		traceHas(t, trace, code)
	}
}

func TestTraceRevisionPrefixedLegacyCaptureMatchesOnlyItsOwnRevision(t *testing.T) {
	task, history := traceFixture()
	task.CurrentRevision = 2
	history = append(history, tasks.RevisionRecord{Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 2, Request: task.Request}})
	traceActivity(&task, 2, "command", "RUNNING")
	traceActivity(&task, 2, "command", "CONFIRMED")
	task.Events[2].Payload["evidenceIds"] = []string{"capture-2"}
	records := []tasks.EvidenceRecord{{ID: "record-2", TaskID: task.ID, TaskRevision: 2, StepID: "revision-2/scan", RobotID: "agv-a", CaptureID: "capture-2"}}
	trace := tasks.BuildTrace(task, history, records, true, false)
	if trace.Integrity.Status != "consistent" {
		t.Fatalf("valid producer binding rejected: %+v", trace.Integrity)
	}
	records[0].StepID = "revision-3/scan"
	traceHas(t, tasks.BuildTrace(task, history, records, true, false), "EVIDENCE_BINDING_CONFLICT")
}
