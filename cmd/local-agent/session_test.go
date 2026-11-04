package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	session "github.com/SUSTechWLA/tangying-robot-agent-os/core/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// This uses actual HTTP model serialization, console admission and SQLite reopen.
// The model server is a fixture; no live provider or robot effects are claimed.
func TestAutomaticSessionHTTPModelAndJournalRestart(t *testing.T) {
	var mu sync.Mutex
	var bodies []string
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		bodies = append(bodies, string(body))
		mu.Unlock()
		_, _ = w.Write([]byte(`{"choices":[{"message":{"tool_calls":[{"function":{"name":"propose_capability_plan","arguments":"{\"calls\":[{\"tool\":\"calibration.get\",\"arguments\":{}}]}"}}]}}]}`))
	}))
	defer model.Close()
	path := filepath.Join(t.TempDir(), "agent.db")
	provider := &planningOnlyProvider{}
	open := func() (*sqlite.Store, http.Handler) {
		store, err := sqlite.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		manager, err := agentsession.New(store, store, "audit-robot")
		if err != nil {
			t.Fatal(err)
		}
		service := tasks.NewService(store, intent.NewDeterministicParser())
		service.SetCreationSessions(manager)
		service.SetGoalPlanner(withFailedPlanningRecorder(&capabilityagent.Planner{Provider: provider,
			Decider: &actionloop.LLMDecider{BaseURL: model.URL, Model: "session-fixture"}}, &planningAttemptStore{store: store}))
		return store, console.NewServer(service, nil, console.WithSessionToken("fixture-session")).Handler()
	}
	binding := func(task tasks.Task) session.Binding {
		var b session.Binding
		for _, e := range task.Events {
			if e.Type == "TASK_CREATED" {
				raw, _ := json.Marshal(e.Payload["session"])
				if err := json.Unmarshal(raw, &b); err != nil {
					t.Fatal(err)
				}
			}
		}
		if err := b.Validate(); err != nil {
			t.Fatal(err)
		}
		return b
	}
	create := func(h http.Handler, request string) tasks.Task {
		raw, _ := json.Marshal(map[string]string{"request": request, "adapter": "gazebo"})
		res := planningAuditRequest(t, h, "POST", "/v1/tasks", string(raw), "fixture-session")
		if res.Code != 201 {
			t.Fatalf("create %d: %s", res.Code, res.Body.String())
		}
		var task tasks.Task
		if err := json.Unmarshal(res.Body.Bytes(), &task); err != nil {
			t.Fatal(err)
		}
		if task.Request != request || task.Approved {
			t.Fatal("history changed request or approved execution")
		}
		return task
	}
	store, handler := open()
	first := create(handler, "只读取整机标定状态，第一轮标记alpha")
	b1 := binding(first)
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, handler = open()
	defer store.Close()
	second := create(handler, "重新读取当前整机标定状态，第二轮标记beta")
	b2 := binding(second)
	if b1.SessionID != b2.SessionID || b2.Sequence != 2 || b1.TurnID == b2.TurnID || b2.TaskID != second.ID {
		t.Fatal("restart lost automatic session association")
	}
	mu.Lock()
	sent := append([]string(nil), bodies...)
	mu.Unlock()
	if len(sent) != 2 || !strings.Contains(sent[1], "第一轮标记alpha") || !strings.Contains(sent[1], first.ID) {
		t.Fatal("bounded history did not reach actual model request")
	}
	if provider.invocations.Load() != 0 {
		t.Fatal("intake dispatched provider effects")
	}
	loaded, err := store.Get(context.Background(), second.ID)
	if err != nil {
		t.Fatal(err)
	}
	if binding(*loaded) != b2 {
		t.Fatal("session binding was not durable")
	}
	res := planningAuditRequest(t, handler, "POST", "/v1/tasks", `{"request":"继续","adapter":"gazebo"}`, "fixture-session")
	if res.Code != 422 {
		t.Fatalf("ambiguous physical continuation accepted: %d", res.Code)
	}
	all, err := store.List(context.Background())
	if err != nil || len(all) != 2 {
		t.Fatal("continuation invented a task")
	}
}
