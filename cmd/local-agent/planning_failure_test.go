package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/types/known/structpb"
)

type rejectedGoalFunc func(context.Context, string) (orchestration.Bundle, bool, error)

func (f rejectedGoalFunc) PlanGoal(ctx context.Context, request string) (orchestration.Bundle, bool, error) {
	return f(ctx, request)
}

type planningOnlyProvider struct{ invocations atomic.Int32 }

func (*planningOnlyProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	schema, _ := structpb.NewStruct(map[string]any{"type": "object", "additionalProperties": false})
	contract, _ := structpb.NewStruct(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": []any{"state"}})
	return &robotv1.ServiceCatalog{RobotId: "audit-robot", Services: []*robotv1.ServiceDefinition{
		{Name: "calibration.get", Available: true, InputSchema: schema, Contract: contract},
	}}, nil
}
func (p *planningOnlyProvider) CallService(context.Context, *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.invocations.Add(1)
	return nil, errors.New("no provider invocation expected")
}

func planningAuditRequest(t *testing.T, handler http.Handler, method, path, body, token string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if body != "" {
		r.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		r.Header.Set(console.SessionHeaderName, token)
	}
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	return w
}

func TestFailedGoalRealModelSQLiteRestartHTTPPreservesExactInput(t *testing.T) {
	var sent string
	var sentMu sync.Mutex
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		sentMu.Lock()
		sent = string(body)
		sentMu.Unlock()
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"cannot_proceed","arguments":"{\"reason\":\"缺少可验证条件\"}"}}]}}]}`))
	}))
	defer model.Close()
	path := filepath.Join(t.TempDir(), "failed-goal.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	provider := &planningOnlyProvider{}
	service := tasks.NewService(store, intent.NewDeterministicParser())
	archive := &planningAttemptStore{store: store}
	service.SetGoalPlanner(withFailedPlanningRecorder(&capabilityagent.Planner{Provider: provider,
		Decider: &actionloop.LLMDecider{BaseURL: model.URL, Model: "audit-fixture", APIKey: "fixture-header-only-secret"}}, archive))
	server := console.NewServer(service, nil, console.WithPlanningAttempts(archive), console.WithSessionToken("audit-session"))
	w := planningAuditRequest(t, server.Handler(), "POST", "/v1/tasks", `{"request":"规划后仍需核验的长程任务"}`, "audit-session")
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("creation status %d: %s", w.Code, w.Body.String())
	}
	var failure map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &failure); err != nil {
		t.Fatal(err)
	}
	id, _ := failure["planningAttemptId"].(string)
	if !planningAttemptIDPattern.MatchString(id) || failure["auditUnavailable"] != nil || provider.invocations.Load() != 0 {
		t.Fatalf("invalid rejected creation evidence: %v", failure)
	}
	all, err := store.ListSummaries(context.Background())
	if err != nil || len(all) != 0 {
		t.Fatalf("rejection created tasks: %+v %v", all, err)
	}
	events, err := store.ListEvents(context.Background(), "planning_attempt", id, 0, 10)
	if err != nil || len(events) != 1 || events[0].EventType != "PLANNING_ATTEMPT_REJECTED" || events[0].Payload["executable"] != false {
		t.Fatalf("atomic audit event missing: %+v %v", events, err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	server = console.NewServer(tasks.NewService(store, nil), nil,
		console.WithPlanningAttempts(&planningAttemptStore{store: store}), console.WithSessionToken("new-session"))
	if got := planningAuditRequest(t, server.Handler(), "GET", "/v1/planning-attempts/"+id, "", ""); got.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated read: %d", got.Code)
	}
	w = planningAuditRequest(t, server.Handler(), "GET", "/v1/planning-attempts/"+id, "", "new-session")
	var attempt capability.PlanningAttempt
	if err := json.Unmarshal(w.Body.Bytes(), &attempt); err != nil || w.Code != http.StatusOK {
		t.Fatalf("read attempt: %d %s %v", w.Code, w.Body.String(), err)
	}
	if attempt.Scope != (agentcontext.Scope{RobotID: "audit-robot"}) || attempt.ID != id || !attempt.TraceAvailable ||
		attempt.CatalogRevision == "" || agentcontext.Hash(attempt.OriginalSourceTrace) != attempt.SourceTraceSHA256 {
		t.Fatalf("attempt identity/hash lost: %+v", attempt)
	}
	var rounds []capability.PlanningStep
	if err := json.Unmarshal([]byte(attempt.OriginalSourceTrace), &rounds); err != nil || len(rounds) != 1 || rounds[0].Context == nil {
		t.Fatalf("trace not retained: %v", err)
	}
	input, err := rounds[0].Context.ModelRequestBytes()
	sentMu.Lock()
	defer sentMu.Unlock()
	if err != nil || string(input) != sent || agentcontext.Hash(sent) != rounds[0].Context.ModelRequestSHA256 ||
		strings.Contains(w.Body.String(), "fixture-header-only-secret") {
		t.Fatalf("exact HTTP input lost or credentials retained: %v", err)
	}
	var wire map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &wire)
	for _, forbidden := range []string{"taskId", "plan", "calls", "approved"} {
		if _, exists := wire[forbidden]; exists {
			t.Fatalf("audit carries task authority: %s", forbidden)
		}
	}
}

type recordingPlanningStore struct {
	eventlog.Store
	err            error
	errAfterCommit error
	ctxErr         error
	remaining      time.Duration
	commits        []eventlog.CommitRequest
}

func (s *recordingPlanningStore) Commit(ctx context.Context, request eventlog.CommitRequest) error {
	s.commits = append(s.commits, request)
	s.ctxErr = ctx.Err()
	deadline, ok := ctx.Deadline()
	if ok {
		s.remaining = time.Until(deadline)
	}
	if s.err != nil {
		return s.err
	}
	if err := s.Store.Commit(ctx, request); err != nil {
		return err
	}
	return s.errAfterCommit
}

func TestFailedGoalUnknownCommitResultDoesNotAdvertiseAttempt(t *testing.T) {
	store := &recordingPlanningStore{Store: eventlog.NewMemoryStore(), errAfterCommit: errors.New("commit response interrupted")}
	archive := &planningAttemptStore{store: store}
	planner := rejectedGoalFunc(func(context.Context, string) (orchestration.Bundle, bool, error) {
		return orchestration.Bundle{}, true, intent.ErrClarificationRequired
	})
	service := tasks.NewService(tasks.NewMemoryStore(), nil)
	service.SetGoalPlanner(withFailedPlanningRecorder(planner, archive))
	handler := console.NewServer(service, nil, console.WithPlanningAttempts(archive), console.WithSessionToken("s")).Handler()
	response := planningAuditRequest(t, handler, "POST", "/v1/tasks", `{"request":"ambiguous journal result"}`, "s")
	var failure map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &failure); err != nil || response.Code != 422 ||
		failure["auditUnavailable"] != true || failure["planningAttemptId"] != nil {
		t.Fatalf("unknown commit falsely claimed success: %d %s %v", response.Code, response.Body.String(), err)
	}
	if len(store.commits) != 1 || len(store.commits[0].Outbox) != 0 || store.commits[0].Checkpoint != nil {
		t.Fatal("rejection retried or produced dispatch/checkpoint")
	}
	// A journal row can exist despite an uncertain response, but no usable ID is
	// minted from that uncertainty and no execution authority follows from it.
	attempt, err := archive.PlanningAttempt(context.Background(), store.commits[0].State.AggregateID)
	if err != nil || attempt.Request != "ambiguous journal result" {
		t.Fatalf("fixture did not commit before returning failure: %+v %v", attempt, err)
	}
	all, err := service.List(context.Background())
	if err != nil || len(all) != 0 {
		t.Fatalf("uncertain audit produced tasks: %v %v", all, err)
	}
}

func TestFailedGoalCancellationAndUnavailableArchivePreserveClassification(t *testing.T) {
	cause := errors.Join(intent.ErrClarificationRequired, context.Canceled)
	planner := rejectedGoalFunc(func(context.Context, string) (orchestration.Bundle, bool, error) {
		return orchestration.Bundle{}, true, cause
	})
	if withFailedPlanningRecorder(nil, nil) != nil {
		t.Fatal("nil planner development fallback changed")
	}
	for _, fail := range []bool{false, true} {
		store := &recordingPlanningStore{Store: eventlog.NewMemoryStore()}
		if fail {
			store.err = errors.New("disk unavailable")
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, _, err := withFailedPlanningRecorder(planner, &planningAttemptStore{store: store}).PlanGoal(ctx, "cancelled request")
		var failure *capability.FailedPlanningError
		if !errors.Is(err, context.Canceled) || !errors.Is(err, intent.ErrClarificationRequired) || !errors.As(err, &failure) {
			t.Fatalf("classification was replaced: %v", err)
		}
		if store.ctxErr != nil || store.remaining <= 0 || store.remaining > failedPlanningRecordTimeout ||
			failure.AuditUnavailable != fail || (failure.PlanningAttemptID == "") != fail {
			t.Fatalf("detached archival contract: failure=%+v timeout=%v cancelled=%v", failure, store.remaining, store.ctxErr)
		}
		if len(store.commits) != 1 || len(store.commits[0].Outbox) != 0 || store.commits[0].Checkpoint != nil {
			t.Fatal("failed planning produced dispatch or execution checkpoint")
		}
		if !fail {
			attempt, err := (&planningAttemptStore{store: store}).PlanningAttempt(context.Background(), failure.PlanningAttemptID)
			if err != nil || attempt.TraceAvailable || attempt.OriginalSourceTrace != "[]" || attempt.RobotID != "" {
				t.Fatalf("missing source fabricated: %+v %v", attempt, err)
			}
		}
	}
	_, _, err := withFailedPlanningRecorder(planner, nil).PlanGoal(context.Background(), "no archive")
	var failure *capability.FailedPlanningError
	if !errors.As(err, &failure) || !failure.AuditUnavailable || failure.PlanningAttemptID != "" {
		t.Fatalf("nil archive fabricated persistence: %v", err)
	}
	service := tasks.NewService(tasks.NewMemoryStore(), nil)
	service.SetGoalPlanner(withFailedPlanningRecorder(planner, nil))
	w := planningAuditRequest(t, console.NewServer(service, nil, console.WithSessionToken("s")).Handler(), "POST", "/v1/tasks", `{"request":"cannot archive"}`, "s")
	var body map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != 422 || body["auditUnavailable"] != true || body["planningAttemptId"] != nil {
		t.Fatalf("failed commit HTTP was dishonest: %d %v", w.Code, body)
	}
}

func TestFailedGoalExactArchiveSurvivesGenericProjectionSQLiteRestart(t *testing.T) {
	scope := agentcontext.Scope{RobotID: "large-json-robot"}
	document := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "planning", Scope: scope, Goal: "保留完整规划原文",
		Attempts: []agentcontext.Attempt{{ID: "round:1", Tool: "catalog.read", Verdict: "SATISFIED",
			Arguments: map[string]any{"sequence": uint64(9007199254740993)}, Detail: strings.Repeat("不可舍入的历史回执；", 2000)}}}
	projection, err := agentcontext.ProjectManaged(document, "planning", agentcontext.Budget{MaxContextBytes: 8192, MaxRequestBytes: 16384, OutputTokens: 256})
	if err != nil || len(projection.Artifacts) != 1 {
		t.Fatalf("build archived context: %v", err)
	}
	exactArchive := projection.Artifacts[0].DataJSON
	exactModel := `{"model":"exact","messages":[{"role":"user","content":"审计输入"}],"max_tokens":256}`
	projection.ModelRequest, projection.ModelRequestJSON = json.RawMessage(exactModel), exactModel
	projection.ModelRequestSHA256 = agentcontext.Hash(exactModel)
	// A generic transport is deliberately lossy for numeric display objects;
	// authoritative JSON strings must remain exact through it and the journal.
	raw, _ := json.Marshal(projection)
	var generic map[string]any
	_ = json.Unmarshal(raw, &generic)
	raw, _ = json.Marshal(generic)
	var lossy agentcontext.Projection
	if err := json.Unmarshal(raw, &lossy); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(lossy.Artifacts[0].Data, []byte("9007199254740993")) {
		t.Fatal("fixture did not exercise rounded display data")
	}
	path := filepath.Join(t.TempDir(), "exact.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	bundle := orchestration.Bundle{Source: orchestration.SourceLLM, Capabilities: &capability.Plan{
		RobotID: scope.RobotID, CatalogRevision: "catalog-at-rejection", PlanningTrace: []capability.PlanningStep{{Round: 1, Context: &lossy}}}}
	id, err := (&planningAttemptStore{store: store}).record(context.Background(), document.Goal, bundle, intent.ErrClarificationRequired)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	handler := console.NewServer(tasks.NewService(store, nil), nil, console.WithSessionToken("s"),
		console.WithPlanningAttempts(&planningAttemptStore{store: store})).Handler()
	response := planningAuditRequest(t, handler, "GET", "/v1/planning-attempts/"+id, "", "s")
	var attempt capability.PlanningAttempt
	if err := json.Unmarshal(response.Body.Bytes(), &attempt); err != nil || response.Code != 200 {
		t.Fatalf("read exact archive: %d %v", response.Code, err)
	}
	var trace []capability.PlanningStep
	if err := json.Unmarshal([]byte(attempt.OriginalSourceTrace), &trace); err != nil || len(trace) != 1 {
		t.Fatalf("read exact trace: %v", err)
	}
	restored := trace[0].Context
	archive, err := restored.Artifacts[0].SourceBytes()
	if err != nil || string(archive) != exactArchive || !bytes.Contains(archive, []byte("9007199254740993")) {
		t.Fatalf("original archive rounded or reordered: %v", err)
	}
	model, err := restored.ModelRequestBytes()
	if err != nil || string(model) != exactModel || agentcontext.Hash(attempt.OriginalSourceTrace) != attempt.SourceTraceSHA256 {
		t.Fatalf("original model/trace hash lost: %v", err)
	}
}

func TestFailedGoalPassThroughAndScopeValidation(t *testing.T) {
	store := &recordingPlanningStore{Store: eventlog.NewMemoryStore()}
	archive := &planningAttemptStore{store: store}
	planner := rejectedGoalFunc(func(context.Context, string) (orchestration.Bundle, bool, error) {
		return orchestration.Bundle{Source: orchestration.SourceLLM}, true, nil
	})
	bundle, handled, err := withFailedPlanningRecorder(planner, archive).PlanGoal(context.Background(), "accepted")
	if err != nil || !handled || bundle.Source != orchestration.SourceLLM || len(store.commits) != 0 {
		t.Fatal("successful planner changed or unnecessarily archived")
	}
	badScope := agentcontext.Scope{RobotID: "robot", TaskID: "invented-task", PlanRevision: 1}
	planner = rejectedGoalFunc(func(context.Context, string) (orchestration.Bundle, bool, error) {
		return orchestration.Bundle{Capabilities: &capability.Plan{RobotID: "robot", PlanningTrace: []capability.PlanningStep{{
			Context: &agentcontext.Projection{Scope: badScope}}}}}, true, intent.ErrClarificationRequired
	})
	_, _, err = withFailedPlanningRecorder(planner, archive).PlanGoal(context.Background(), "invalid scope")
	var failure *capability.FailedPlanningError
	if !errors.As(err, &failure) || !failure.AuditUnavailable || failure.PlanningAttemptID != "" || len(store.commits) != 0 {
		t.Fatalf("invented pre-task scope accepted: %v", err)
	}
	archive.commitOnce.Do(func() { archive.commitSlot = make(chan struct{}, 1) })
	archive.commitSlot <- struct{}{}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	id, err := archive.record(ctx, "bounded wait", orchestration.Bundle{}, intent.ErrClarificationRequired)
	if id != "" || !errors.Is(err, context.DeadlineExceeded) || len(store.commits) != 0 {
		t.Fatalf("commit queue exceeded deadline or fabricated ID: %q %v", id, err)
	}
}

func TestFailedGoalReadEndpointRejectsCrossSiteUnknownAndCorrupt(t *testing.T) {
	store := eventlog.NewMemoryStore()
	archive := &planningAttemptStore{store: store}
	id, err := archive.record(context.Background(), "audited request", orchestration.Bundle{}, intent.ErrClarificationRequired)
	if err != nil {
		t.Fatal(err)
	}
	handler := console.NewServer(tasks.NewService(tasks.NewMemoryStore(), nil), nil,
		console.WithPlanningAttempts(archive), console.WithSessionToken("s")).Handler()
	request := httptest.NewRequest("GET", "/v1/planning-attempts/"+id, nil)
	request.Header.Set(console.SessionHeaderName, "s")
	request.Header.Set("Origin", "https://unrelated.invalid")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 403 {
		t.Fatalf("cross-site read accepted: %d", response.Code)
	}
	for _, path := range []string{"/v1/planning-attempts", "/v1/planning-attempts/not-an-id", "/v1/planning-attempts/planning-attempt-00000000000000000000000000000000"} {
		if got := planningAuditRequest(t, handler, "GET", path, "", "s"); got.Code != 404 {
			t.Fatalf("unknown/list endpoint %q: %d", path, got.Code)
		}
	}
	state, _, err := store.LoadState(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	var attempt capability.PlanningAttempt
	_ = json.Unmarshal(state.Data, &attempt)
	attempt.OriginalSourceTrace = "[{}]" // Deliberately leave its original hash.
	state.Data, _ = json.Marshal(attempt)
	state.Version++
	if err := store.Commit(context.Background(), eventlog.CommitRequest{ExpectedVersion: 1, State: state}); err != nil {
		t.Fatal(err)
	}
	if got := planningAuditRequest(t, handler, "GET", "/v1/planning-attempts/"+id, "", "s"); got.Code != 500 {
		t.Fatalf("corrupt evidence returned as verified: %d", got.Code)
	}
}

func TestFailedGoalConcurrentAttemptsHaveIndependentIDsAndNoTasks(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "concurrent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	planner := rejectedGoalFunc(func(context.Context, string) (orchestration.Bundle, bool, error) {
		return orchestration.Bundle{}, true, intent.ErrClarificationRequired
	})
	service := tasks.NewService(store, nil)
	service.SetGoalPlanner(withFailedPlanningRecorder(planner, &planningAttemptStore{store: store}))
	ids := make(chan string, 8)
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := service.Create(context.Background(), "same rejected goal", "auto")
			var failure *capability.FailedPlanningError
			if !errors.As(err, &failure) || failure.AuditUnavailable {
				t.Errorf("attempt unavailable: %v", err)
				return
			}
			ids <- failure.PlanningAttemptID
		}()
	}
	wg.Wait()
	close(ids)
	seen := map[string]bool{}
	for id := range ids {
		if seen[id] || !planningAttemptIDPattern.MatchString(id) {
			t.Fatalf("duplicate/missing attempt id: %s", id)
		}
		seen[id] = true
	}
	all, err := store.ListSummaries(context.Background())
	if err != nil || len(all) != 0 || len(seen) != 8 {
		t.Fatalf("attempts=%d tasks=%d err=%v", len(seen), len(all), err)
	}
}
