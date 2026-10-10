package agentcontext_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func managedAcceptanceDocument() agentcontext.Document {
	scope := agentcontext.Scope{TaskID: "long-task", RobotID: "robot-7", PlanRevision: 4}
	return agentcontext.Document{
		SchemaVersion: agentcontext.Version, Role: "recovery", Scope: scope, AsOfMS: 1700000000000,
		Goal: "巡检五处，再将同一个杯子放到厨房托盘", CurrentStep: "pick",
		Constraints: []string{"禁止进入卧室", "未知抓取结果必须先对账，不能重做"},
		Questions:   []string{"命令 command-pick 的实际结果是否已确认？"},
		Steps:       []agentcontext.Step{{ID: "pick", Action: "manipulation.pick", State: "OUTCOME_UNKNOWN", Expected: "同一个杯子"}},
		Records: []agentcontext.Record{
			{ID: "early-guard", Kind: "guard", Scope: scope, Statement: "最初约束：不要触碰红色杯子"},
			{ID: "pending", Kind: "constraint", Scope: scope, Statement: "command-pick 仍未决，不得用另一尝试覆盖"},
			{ID: "live-observation", Kind: "observation", Scope: scope, Statement: "夹爪观测未知", EvidenceIDs: []string{"capture-9"}},
		},
	}
}

func managedAcceptanceJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func managedAcceptanceProject(t *testing.T, d agentcontext.Document) agentcontext.Projection {
	t.Helper()
	p, err := agentcontext.ProjectManaged(d, "recovery", agentcontext.Budget{MaxContextBytes: 8192, MaxRequestBytes: 16384, OutputTokens: 512})
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func managedAcceptanceRead(t *testing.T, p agentcontext.Projection, scope agentcontext.Scope, sha, item string, limit int) []byte {
	t.Helper()
	var output bytes.Buffer
	for offset := 0; ; {
		page, err := p.ReadArtifact(scope, sha, item, offset, limit)
		if err != nil {
			t.Fatal(err)
		}
		if !utf8.ValidString(page.Content) || page.Offset != offset || page.NextOffset != offset+len(page.Content) || len(page.Content) > limit {
			t.Fatalf("invalid UTF-8 page: %+v", page)
		}
		output.WriteString(page.Content)
		if page.Complete {
			if page.NextOffset != page.TotalBytes || output.Len() != page.TotalBytes {
				t.Fatal("complete page does not account for all bytes")
			}
			return output.Bytes()
		}
		if page.NextOffset <= offset {
			t.Fatal("pagination made no progress")
		}
		offset = page.NextOffset
	}
}

func TestManagedAcceptanceThousandRecordsPreserveRequiredStateAndExactArchive(t *testing.T) {
	d := managedAcceptanceDocument()
	for i := range 1000 {
		d.Records = append(d.Records, agentcontext.Record{
			ID: fmt.Sprintf("event-%04d", i), Kind: "tool_return", Scope: d.Scope,
			Statement: strings.Repeat("历史载荷不是授权。", 32), ObservedMS: int64(i + 1), EvidenceIDs: []string{fmt.Sprintf("evidence-%d", i)},
		})
	}
	before := managedAcceptanceJSON(t, d)
	p := managedAcceptanceProject(t, d)
	if p.Compaction == nil || p.Compaction.SourceRecords != 1003 || p.Compaction.ArchivedRecords < 900 || len(p.Text) > 8192 || p.Compaction.InputBytes != len(p.Text) {
		t.Fatalf("unexpected compaction: %+v", p.Compaction)
	}
	if p.SHA256 != agentcontext.Hash(p.Text) || p.Compaction.SourceSHA256 == "" {
		t.Fatal("projection lacks exact input/source hashes")
	}
	var view agentcontext.Document
	if err := json.Unmarshal([]byte(p.Text), &view); err != nil {
		t.Fatal(err)
	}
	if view.Goal != d.Goal || view.Scope != d.Scope || view.CurrentStep != d.CurrentStep || !reflect.DeepEqual(view.Steps, d.Steps) || !reflect.DeepEqual(view.Constraints, d.Constraints) || !reflect.DeepEqual(view.Questions, d.Questions) {
		t.Fatal("compaction changed required state")
	}
	all := make(map[string]agentcontext.Record, len(d.Records))
	for _, r := range view.Records {
		all[r.ID] = r
	}
	for _, r := range d.Records[:3] {
		if !reflect.DeepEqual(all[r.ID], r) {
			t.Fatalf("protected record %s was removed or changed", r.ID)
		}
	}
	for _, a := range p.Artifacts {
		if a.Kind != "records" || !json.Valid(a.Data) || agentcontext.Hash(string(a.Data)) != a.SHA256 {
			t.Fatalf("invalid exact archive: kind=%s", a.Kind)
		}
		var archived []agentcontext.Record
		if err := json.Unmarshal(a.Data, &archived); err != nil {
			t.Fatal(err)
		}
		for _, r := range archived {
			if _, exists := all[r.ID]; exists {
				t.Fatalf("record duplicated between view and archive: %s", r.ID)
			}
			all[r.ID] = r
		}
	}
	for _, r := range d.Records {
		if !reflect.DeepEqual(all[r.ID], r) {
			t.Fatalf("record %s is not recoverable exactly", r.ID)
		}
	}
	if !bytes.Equal(before, managedAcceptanceJSON(t, d)) {
		t.Fatal("projection mutated source")
	}
	if !bytes.Equal(managedAcceptanceJSON(t, p), managedAcceptanceJSON(t, managedAcceptanceProject(t, d))) {
		t.Fatal("recompression of original source drifted")
	}
}

func TestManagedAcceptanceCJKPagesRestoreArchiveAndRejectForgedReads(t *testing.T) {
	d := managedAcceptanceDocument()
	r := agentcontext.Record{ID: "large-cjk", Kind: "tool_return", Scope: d.Scope, Statement: strings.Repeat("杯子🍵放到厨房；", 3000)}
	d.Records = append(d.Records, r)
	p := managedAcceptanceProject(t, d)
	if len(p.Artifacts) != 1 {
		t.Fatalf("want one archive, got %d", len(p.Artifacts))
	}
	a := p.Artifacts[0]
	// The persisted projection remains self-contained after a process restart.
	var restored agentcontext.Projection
	if err := json.Unmarshal(managedAcceptanceJSON(t, p), &restored); err != nil {
		t.Fatal(err)
	}
	if got := managedAcceptanceRead(t, restored, d.Scope, a.SHA256, "", 127); !bytes.Equal(got, a.Data) {
		t.Fatal("paged archive differs from original bytes")
	}
	if got := managedAcceptanceRead(t, restored, d.Scope, a.SHA256, r.ID, 131); !bytes.Equal(got, managedAcceptanceJSON(t, r)) {
		t.Fatal("paged item differs from original JSON")
	}
	for _, scope := range []agentcontext.Scope{
		{TaskID: "other-task", RobotID: d.Scope.RobotID, PlanRevision: d.Scope.PlanRevision},
		{TaskID: d.Scope.TaskID, RobotID: "other-robot", PlanRevision: d.Scope.PlanRevision},
		{TaskID: d.Scope.TaskID, RobotID: d.Scope.RobotID, PlanRevision: d.Scope.PlanRevision + 1},
	} {
		if _, err := restored.ReadArtifact(scope, a.SHA256, r.ID, 0, 128); err == nil {
			t.Fatalf("wrong scope accepted: %+v", scope)
		}
	}
	for _, query := range []struct {
		sha, item     string
		offset, limit int
	}{
		{"forged", r.ID, 0, 128}, {a.SHA256, "missing", 0, 128},
		{a.SHA256, "", -1, 128}, {a.SHA256, "", len(a.Data) + 1, 128},
		{a.SHA256, "", 0, 0}, {a.SHA256, "", 0, 4097},
		{a.SHA256, "", bytes.Index(a.Data, []byte("杯")) + 1, 128},
	} {
		if _, err := restored.ReadArtifact(d.Scope, query.sha, query.item, query.offset, query.limit); err == nil {
			t.Fatalf("invalid query accepted: %+v", query)
		}
	}
	restored.Artifacts[0].Data = append(json.RawMessage(nil), a.Data...)
	restored.Artifacts[0].Data[0] = '{'
	if _, err := restored.ReadArtifact(d.Scope, a.SHA256, "", 0, 128); err == nil {
		t.Fatal("corrupted persisted bytes accepted")
	}
}

func TestManagedAcceptanceAttemptArgumentsRetainExactLargeIntegers(t *testing.T) {
	d := managedAcceptanceDocument()
	d.Attempts = []agentcontext.Attempt{{ID: "attempt-1", Tool: "pick", Verdict: "OUTCOME_UNKNOWN",
		Arguments: map[string]any{"sequence": uint64(9007199254740993), "nested": map[string]any{"offset": 0.017}},
		Detail:    strings.Repeat("完整原始回执；", 5000)}}
	p := managedAcceptanceProject(t, d)
	var view struct {
		Attempts []struct{ Arguments json.RawMessage }
	}
	if err := json.Unmarshal([]byte(p.Text), &view); err != nil {
		t.Fatal(err)
	}
	if len(view.Attempts) != 1 || !bytes.Equal(view.Attempts[0].Arguments, managedAcceptanceJSON(t, d.Attempts[0].Arguments)) {
		t.Fatalf("attempt arguments lost numeric precision: %+v", view.Attempts)
	}
	for _, a := range p.Artifacts {
		if a.Kind == "attempts" {
			got := managedAcceptanceRead(t, p, d.Scope, a.SHA256, "attempt-1", 4096)
			if !bytes.Equal(got, managedAcceptanceJSON(t, d.Attempts[0])) {
				t.Fatal("original attempt JSON changed during archiving")
			}
			return
		}
	}
	t.Fatal("large attempt was not archived")
}

func TestManagedAcceptanceProtectedStateCannotBeSilentlyDroppedToFit(t *testing.T) {
	d := managedAcceptanceDocument()
	d.Constraints = append(d.Constraints, strings.Repeat("必须保留的禁止事项", 3000))
	_, err := agentcontext.ProjectManaged(d, "recovery", agentcontext.Budget{MaxContextBytes: 4096, MaxRequestBytes: 8192, OutputTokens: 128})
	if !errors.Is(err, agentcontext.ErrContextBudget) {
		t.Fatalf("oversized protected state should fail closed: %v", err)
	}
}

func TestManagedAcceptanceConcurrentSnapshotsAreDeterministicAndIsolated(t *testing.T) {
	d := managedAcceptanceDocument()
	d.Records = append(d.Records, agentcontext.Record{ID: "archive", Kind: "tool_return", Scope: d.Scope, Statement: strings.Repeat("原始数据", 4000)})
	want := managedAcceptanceJSON(t, managedAcceptanceProject(t, d))
	var wg sync.WaitGroup
	for range 12 {
		wg.Go(func() {
			p, err := agentcontext.ProjectManaged(d, "recovery", agentcontext.Budget{MaxContextBytes: 8192, MaxRequestBytes: 16384, OutputTokens: 512})
			if err != nil {
				t.Error(err)
				return
			}
			raw, err := json.Marshal(p)
			if err != nil || !bytes.Equal(raw, want) {
				t.Errorf("parallel projection changed: %v", err)
			}
		})
	}
	wg.Wait()
	next := d
	next.Records = append(append([]agentcontext.Record(nil), d.Records...), agentcontext.Record{ID: "new-pending", Kind: "guard", Scope: d.Scope, Statement: "新追加命令未决"})
	nextProjection := managedAcceptanceProject(t, next)
	if !strings.Contains(nextProjection.Text, "new-pending") || nextProjection.Compaction.SourceSHA256 == managedAcceptanceProject(t, d).Compaction.SourceSHA256 {
		t.Fatal("new source event was lost or shares the old source identity")
	}
	if !bytes.Equal(want, managedAcceptanceJSON(t, managedAcceptanceProject(t, d))) {
		t.Fatal("later independent snapshot mutated earlier source")
	}
}

func TestManagedAcceptanceSQLiteRestartPreservesRevisionGuardsAndUnresolvedCommands(t *testing.T) {
	ctx := context.Background()
	now := time.Unix(1700000000, 0).UTC()
	path := filepath.Join(t.TempDir(), "long-ledger.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	task := &tasks.Task{ID: "durable-long", Request: "保持杯子直立，不进入卧室", State: taskgraph.StateReady,
		CurrentRevision: 1, AggregateVersion: 1, RevisionState: tasks.RevisionActive, CreatedAt: now, UpdatedAt: now}
	event := func(revision int, command, robot, status string) tasks.TaskEvent {
		return tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: "pick", Payload: map[string]any{
			"taskRevision": revision, "commandId": command, "robotId": robot, "stepId": "pick",
			"toolName": "manipulation.pick", "mutatesWorld": true, "activityStatus": status,
		}}
	}
	task.Events = []tasks.TaskEvent{
		event(1, "old-command", "robot-1", "RUNNING"),
		event(1, "old-command", "robot-1", "CONFIRMED"),
		event(1, "old-command", "robot-1", "FAILED"),
		event(2, "current-command", "robot-1", "RUNNING"),
		event(2, "current-command", "other-robot", "CONFIRMED"),
		event(2, "", "robot-1", "CONFIRMED"),
	}
	for len(task.Events) < 1000 {
		task.Events = append(task.Events, tasks.TaskEvent{Type: "ops.root_cause_hypothesis", StepID: "background-analysis",
			Payload: map[string]any{"detail": strings.Repeat("诊断引用不改变权限。", 32)}})
	}
	for i := range task.Events {
		task.Events[i].Sequence = uint64(i + 1)
		task.Events[i].OccurredAt = now.Add(time.Duration(i) * time.Millisecond)
	}
	oldRevision := &tasks.TaskRevision{TaskID: task.ID, Revision: 1, Request: task.Request, IdempotencyKey: "initial-revision", CreatedAt: now,
		Intent: manipulation.Intent{Constraints: manipulation.Constraints{KeepUpright: true, AvoidHumans: true}}}
	if err := store.CreateWithRevision(ctx, task, oldRevision); err != nil {
		t.Fatal(err)
	}
	next := *task
	next.CurrentRevision, next.AggregateVersion, next.Request = 2, 2, "改放到厨房托盘"
	currentRevision := &tasks.TaskRevision{TaskID: task.ID, Revision: 2, BaseRevision: 1, Request: next.Request,
		IdempotencyKey: "next-revision", CreatedAt: now.Add(time.Second)}
	if err := store.CommitRevision(ctx, tasks.RevisionCommit{TaskID: task.ID, ExpectedAggregateVersion: 1, Task: &next, NewRevision: currentRevision,
		LifecycleEvents: []tasks.RevisionLifecycleEvent{{Revision: 1, Status: tasks.RevisionSuperseded, OccurredAt: now}, {Revision: 2, Status: tasks.RevisionActive, OccurredAt: now}}}); err != nil {
		t.Fatal(err)
	}
	project := func(db *sqlite.Store) agentcontext.Projection {
		t.Helper()
		loaded, err := db.Get(ctx, task.ID)
		if err != nil {
			t.Fatal(err)
		}
		revisions, err := db.ListRevisions(ctx, task.ID)
		if err != nil {
			t.Fatal(err)
		}
		d := tasks.ContextForRevisions(*loaded, revisions, "recovery", now.Add(time.Hour))
		metadata := tasks.DecisionContextForRevisions(*loaded, revisions, "recovery", now.Add(time.Hour))
		if !metadata.Snapshot.Complete || metadata.Snapshot.LedgerSequence == nil || *metadata.Snapshot.LedgerSequence != int64(len(loaded.Events)) {
			t.Fatal("persistent full-ledger coverage was lost")
		}
		p, err := agentcontext.ProjectManaged(d, "recovery", agentcontext.DefaultBudget())
		if err != nil {
			t.Fatal(err)
		}
		return p
	}
	before := project(store)
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	after := project(reopened)
	if !bytes.Equal(managedAcceptanceJSON(t, before), managedAcceptanceJSON(t, after)) || after.Compaction.ArchivedRecords < 900 {
		t.Fatal("managed source/archive changed across SQLite restart")
	}
	assertMemory := func(p agentcontext.Projection, currentPending bool) {
		t.Helper()
		var d agentcontext.Document
		if err := json.Unmarshal([]byte(p.Text), &d); err != nil {
			t.Fatal(err)
		}
		all := make(map[string]agentcontext.Record, len(d.Records))
		archiveItems := map[string]struct {
			archive agentcontext.Artifact
			raw     json.RawMessage
		}{}
		for _, r := range d.Records {
			all[r.ID] = r
		}
		for _, archive := range p.Artifacts {
			if archive.Kind != "records" {
				continue
			}
			raw, err := archive.SourceBytes()
			if err != nil {
				t.Fatal(err)
			}
			var records []json.RawMessage
			if err := json.Unmarshal(raw, &records); err != nil {
				t.Fatal(err)
			}
			for _, item := range records {
				var r agentcontext.Record
				if err := json.Unmarshal(item, &r); err != nil {
					t.Fatal(err)
				}
				if _, duplicate := all[r.ID]; duplicate {
					t.Fatalf("source record appears more than once: %s", r.ID)
				}
				all[r.ID] = r
				archiveItems[r.ID] = struct {
					archive agentcontext.Artifact
					raw     json.RawMessage
				}{archive, item}
			}
		}
		readSource := func(id string) agentcontext.Record {
			t.Helper()
			r, found := all[id]
			if !found {
				t.Fatalf("referenced source record missing: %s", id)
			}
			if item, archived := archiveItems[id]; archived {
				got := managedAcceptanceRead(t, p, item.archive.Scope, item.archive.SHA256, id, 127)
				if !bytes.Equal(got, item.raw) {
					t.Fatalf("source item changed during exact paginated retrieval: %s", id)
				}
			}
			return r
		}
		type memoryFields struct {
			Command        string   `json:"command_id"`
			Robot          string   `json:"robot_id"`
			Revision       int      `json:"source_revision"`
			State          string   `json:"recorded_state"`
			Unresolved     bool     `json:"unresolved_dispatch"`
			OutcomeUnknown bool     `json:"outcome_unknown"`
			RetryForbidden bool     `json:"automatic_retry_forbidden"`
			SourceRecords  []string `json:"source_record_ids"`
			SourceEvents   []string `json:"source_event_ids"`
		}
		parseMemory := func(r agentcontext.Record) memoryFields {
			t.Helper()
			var memory memoryFields
			start := strings.Index(r.Statement, "{")
			if start < 0 {
				t.Fatalf("memory source is not structured: %s", r.ID)
			}
			if err := json.Unmarshal([]byte(r.Statement[start:]), &memory); err != nil {
				t.Fatal(err)
			}
			return memory
		}
		oldPending, currentGuard, currentSource, oldConstraint := false, false, false, false
		for _, r := range d.Records {
			if r.ID == "revision-source:1" {
				oldConstraint = r.Kind == "guard" && r.Scope.PlanRevision == 1 && strings.Contains(r.Statement, "不进入卧室") && strings.Contains(r.Statement, `"keepUpright":true`)
			}
			if !strings.HasPrefix(r.ID, "memory:command:") {
				continue
			}
			memory := parseMemory(r)
			if r.Kind != "guard" || r.Scope.TaskID != task.ID || !memory.Unresolved || !memory.RetryForbidden || len(memory.SourceRecords) == 0 {
				t.Fatalf("unresolved command boundary lost its guard or replay prohibition: %+v", r)
			}
			for _, id := range memory.SourceRecords {
				source := readSource(id)
				detail := parseMemory(source)
				if !strings.HasPrefix(id, "memory:action:") || source.Kind != "verification" || source.Scope != r.Scope || detail.Command != memory.Command {
					t.Fatalf("command guard refers to a different source identity: guard=%+v source=%+v", r, source)
				}
				if len(detail.SourceEvents) == 0 {
					t.Fatalf("action source lost all event provenance: %s", id)
				}
				for _, eventID := range detail.SourceEvents {
					readSource(eventID)
				}
			}
			if memory.Command == "old-command" && r.Scope.RobotID == "robot-1" && r.Scope.PlanRevision == 1 {
				oldPending = memory.State == "OUTCOME_UNKNOWN" && memory.OutcomeUnknown
			}
			if memory.Command == "current-command" && r.Scope.RobotID == "robot-1" && r.Scope.PlanRevision == 2 {
				currentGuard = true
				if !currentPending || memory.State != "DISPATCH_RECORDED" {
					t.Fatalf("foreign/unbound receipt changed current dispatch: %+v", memory)
				}
			}
		}
		for id, r := range all {
			if !strings.HasPrefix(id, "memory:action:") {
				continue
			}
			memory := parseMemory(r)
			if memory.Command != "current-command" || memory.Robot != "robot-1" || memory.Revision != 2 {
				continue
			}
			currentSource = true
			readSource(id)
			wantState := "COMPLETION_RECORDED"
			if currentPending {
				wantState = "DISPATCH_RECORDED"
			}
			if r.Kind != "verification" || r.Scope.TaskID != task.ID || r.Scope.RobotID != memory.Robot || r.Scope.PlanRevision != memory.Revision || memory.Unresolved != currentPending || memory.State != wantState {
				t.Fatalf("exact current lifecycle source lost or elevated to physical verification: %+v", r)
			}
			for _, eventID := range memory.SourceEvents {
				readSource(eventID)
			}
		}
		if !oldPending || currentGuard != currentPending || !currentSource || !oldConstraint || d.Scope.PlanRevision != 2 || d.Goal != next.Request {
			t.Fatalf("required historical state missing: oldPending=%v currentGuard=%v currentSource=%v constraint=%v", oldPending, currentGuard, currentSource, oldConstraint)
		}
	}
	assertMemory(after, true)
	loaded, err := reopened.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	matched := event(2, "current-command", "robot-1", "CONFIRMED")
	matched.OccurredAt = now.Add(2 * time.Second)
	if err := reopened.UpdateWithEvent(ctx, loaded, matched); err != nil {
		t.Fatal(err)
	}
	latest := project(reopened)
	assertMemory(latest, false)
	if latest.Compaction.SourceSHA256 == after.Compaction.SourceSHA256 {
		t.Fatal("new durable terminal event reused stale source identity")
	}
	assertMemory(after, true)
}
