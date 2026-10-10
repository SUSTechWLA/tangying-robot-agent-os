package agentcontext_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestExactContextSourceSurvivesTaskSQLiteRestartAndHTTPProjection(t *testing.T) {
	ctx := context.Background()
	scope := agentcontext.Scope{TaskID: "exact-context", RobotID: "gazebo-owned", PlanRevision: 4}
	d := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "recovery", Scope: scope, Goal: "审计中文来源",
		Attempts: []agentcontext.Attempt{{ID: "round:1", Tool: "read", Verdict: "SATISFIED",
			Arguments: map[string]any{"sequence": uint64(9007199254740993)}, Detail: strings.Repeat("原始回执不授予权限；", 2000)}}}
	p, err := agentcontext.ProjectManaged(d, "recovery", agentcontext.Budget{MaxContextBytes: 8192, MaxRequestBytes: 16384, OutputTokens: 256})
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Artifacts) != 1 || p.Artifacts[0].DataJSON == "" {
		t.Fatal("exact source was not retained")
	}
	body := []byte(`{"model":"fixture","messages":[{"role":"user","content":"原始输入"}],"max_tokens":256}`)
	p.ModelRequest = append(json.RawMessage(nil), body...)
	p.ModelRequestJSON = string(body)
	p.ModelRequestSHA256 = agentcontext.Hash(string(body))
	wire, err := json.Marshal(map[string]any{"agent_context": p})
	if err != nil {
		t.Fatal(err)
	}
	// This is the generic map projection used by events. It deliberately tests
	// both field reordering and the IEEE-754 conversion of the structured view.
	var payload map[string]any
	if err := json.Unmarshal(wire, &payload); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "tasks.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{ID: scope.TaskID, Request: d.Goal, State: taskgraph.StateReady, CurrentRevision: 4, AggregateVersion: 1,
		CreatedAt: time.Unix(1700000000, 0), UpdatedAt: time.Unix(1700000001, 0),
		Events: []tasks.TaskEvent{{Type: "ops.recovery_executed", Payload: payload, OccurredAt: time.Unix(1700000001, 0)}}}
	if err := store.Create(ctx, task); err != nil {
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
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		loaded, err := store.Get(r.Context(), scope.TaskID)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(loaded); err != nil {
			t.Error(err)
		}
	}))
	defer server.Close()
	response, err := server.Client().Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	var returned tasks.Task
	if err := json.Unmarshal(raw, &returned); err != nil {
		t.Fatal(err)
	}
	projected, err := json.Marshal(returned.Events[0].Payload["agent_context"])
	if err != nil {
		t.Fatal(err)
	}
	var restored agentcontext.Projection
	if err := json.Unmarshal(projected, &restored); err != nil {
		t.Fatal(err)
	}
	if agentcontext.Hash(restored.Text) != p.SHA256 {
		t.Fatal("model-facing source changed")
	}
	if bytes.Equal(restored.Artifacts[0].Data, p.Artifacts[0].Data) || bytes.Equal(restored.ModelRequest, body) {
		t.Fatal("fixture did not exercise generic JSON byte mutation")
	}
	got, err := restored.Artifacts[0].SourceBytes()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, p.Artifacts[0].Data) || !bytes.Contains(got, []byte("9007199254740993")) {
		t.Fatal("source bytes or exact integer changed")
	}
	model, err := restored.ModelRequestBytes()
	if err != nil || !bytes.Equal(model, body) {
		t.Fatalf("exact model input lost: %v", err)
	}
	var combined strings.Builder
	for offset := 0; ; {
		page, err := restored.ReadArtifact(scope, p.Artifacts[0].SHA256, "round:1", offset, 1024)
		if err != nil {
			t.Fatal(err)
		}
		combined.WriteString(page.Content)
		if page.Complete {
			break
		}
		offset = page.NextOffset
	}
	if !strings.Contains(combined.String(), "9007199254740993") {
		t.Fatal("retrieval used a rounded display projection")
	}
	restored.Artifacts[0].DataJSON = "[]"
	if _, err := restored.Artifacts[0].SourceBytes(); err == nil {
		t.Fatal("tampered exact archive accepted")
	}
	restored.ModelRequestJSON = `{"model":"altered"}`
	if _, err := restored.ModelRequestBytes(); err == nil {
		t.Fatal("tampered exact model input accepted")
	}
}
