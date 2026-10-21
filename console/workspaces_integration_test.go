package console

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"github.com/gorilla/websocket"
)

// Use the real console routes, task service and durable stores here. Router
// stubs cannot catch a correctly scoped route backed by the wrong database.
func TestWorkspaceRealTaskHistoryAndEventsRemainIsolatedAcrossRestart(t *testing.T) {
	type peer struct {
		id, database, tokenFile, address string
		store                            *sqlite.Store
		server                           *httptest.Server
		closedSockets                    chan struct{}
	}
	peers := []*peer{}
	profiles := []workspaceProfile{}
	start := func(p *peer, token string) {
		t.Helper()
		store, err := sqlite.Open(p.database)
		if err != nil {
			t.Fatal(err)
		}
		p.store = store
		handler := NewServer(tasks.NewService(store, intent.NewDeterministicParser()), nil,
			WithWorkspaceIdentity(p.id), WithSessionToken(token), WithEvidence(store)).Handler()
		server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if strings.HasSuffix(r.URL.Path, "/events/ws") {
				defer func() { p.closedSockets <- struct{}{} }()
			}
			handler.ServeHTTP(w, r)
		}))
		if p.address != "" {
			server.Listener.Close()
			server.Listener, err = net.Listen("tcp", p.address)
			if err != nil {
				store.Close()
				t.Fatal(err)
			}
		}
		server.Start()
		p.server, p.address = server, server.Listener.Addr().String()
		if err := os.WriteFile(p.tokenFile, []byte(token+"\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []string{"ugv", "legged"} {
		p := &peer{id: id, database: filepath.Join(t.TempDir(), "agent.db"),
			tokenFile: filepath.Join(t.TempDir(), "console-session"), closedSockets: make(chan struct{}, 8)}
		t.Cleanup(func() {
			if p.server != nil {
				p.server.Close()
			}
			if p.store != nil {
				p.store.Close()
			}
		})
		start(p, "session-"+id)
		now := time.Now().UTC()
		task := &tasks.Task{
			ID: "shared-task-id", Request: "保存的历史属于 " + id, Adapter: "ros2",
			Intent: manipulation.Intent{RobotID: id}, State: taskgraph.StateSucceeded,
			CreatedAt: now, UpdatedAt: now,
			Events: []tasks.TaskEvent{{Sequence: 1, Type: "TASK_COMPLETED",
				Message: "完成记录属于 " + id, Payload: map[string]any{"robotId": id}, OccurredAt: now}},
		}
		if err := p.store.Create(t.Context(), task); err != nil {
			t.Fatal(err)
		}
		peers = append(peers, p)
		profiles = append(profiles, workspaceProfile{ID: id, RobotID: id, Label: id,
			URL: p.server.URL, SessionFile: p.tokenFile})
	}
	root := httptest.NewServer(workspaceRoot(workspaceConfig(t, profiles...)).Handler())
	defer root.Close()
	jar, _ := cookiejar.New(nil)
	client := &http.Client{Jar: jar, Timeout: 5 * time.Second}
	response, err := client.Get(root.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()

	readHistory := func(id string) error {
		response, err := client.Get(root.URL + "/robots/" + id + "/v1/tasks/shared-task-id")
		if err != nil {
			return err
		}
		defer response.Body.Close()
		var task tasks.Task
		if err := json.NewDecoder(response.Body).Decode(&task); err != nil {
			return err
		}
		if response.StatusCode != 200 || task.Intent.RobotID != id ||
			task.Request != "保存的历史属于 "+id || len(task.Events) != 1 ||
			task.Events[0].Payload["robotId"] != id || task.State != taskgraph.StateSucceeded {
			return fmt.Errorf("wrong history for %s: status=%d task=%+v", id, response.StatusCode, task)
		}
		return nil
	}
	readEvents := func(id string) error {
		origin, _ := url.Parse(root.URL)
		headers := make(http.Header)
		headers.Set("Origin", root.URL)
		for _, cookie := range jar.Cookies(origin) {
			headers.Add("Cookie", cookie.Name+"="+cookie.Value)
		}
		dialer := websocket.Dialer{HandshakeTimeout: 3 * time.Second}
		connection, response, err := dialer.Dial(
			"ws"+strings.TrimPrefix(root.URL, "http")+"/robots/"+id+"/v1/tasks/shared-task-id/events/ws", headers)
		if err != nil {
			if response != nil {
				response.Body.Close()
			}
			return err
		}
		defer connection.Close()
		connection.SetReadDeadline(time.Now().Add(3 * time.Second))
		var event tasks.TaskEvent
		if err := connection.ReadJSON(&event); err != nil {
			return err
		}
		if event.Sequence != 1 || event.Payload["robotId"] != id || event.Message != "完成记录属于 "+id {
			return fmt.Errorf("wrong event for %s: %+v", id, event)
		}
		connection.Close()
		for _, p := range peers {
			if p.id != id {
				continue
			}
			select {
			case <-p.closedSockets:
			case <-time.After(2 * time.Second):
				return fmt.Errorf("%s task event handler kept polling after the browser closed its idle socket", id)
			}
		}
		return nil
	}
	type result struct {
		robotID, taskID string
		err             error
	}
	results := make(chan result, len(peers))
	for _, p := range peers {
		go func(id string) {
			for range 6 {
				if err := readHistory(id); err != nil {
					results <- result{err: err}
					return
				}
			}
			if err := readEvents(id); err != nil {
				results <- result{err: err}
				return
			}
			response, err := client.Post(root.URL+"/robots/"+id+"/v1/tasks", "application/json",
				strings.NewReader(`{"request":"把红色杯子放进右侧收纳盒"}`))
			if err != nil {
				results <- result{err: err}
				return
			}
			defer response.Body.Close()
			var task tasks.Task
			if err := json.NewDecoder(response.Body).Decode(&task); err != nil {
				results <- result{err: err}
				return
			}
			if response.StatusCode != 201 || task.ID == "" {
				results <- result{err: fmt.Errorf("%s task creation failed: %d", id, response.StatusCode)}
				return
			}
			results <- result{robotID: id, taskID: task.ID}
		}(p.id)
	}
	outcomes := make([]result, 0, len(peers))
	for range peers {
		outcomes = append(outcomes, <-results)
	}
	for _, outcome := range outcomes {
		if outcome.err != nil {
			t.Fatal(outcome.err)
		}
		for _, p := range peers {
			_, err := p.store.Get(t.Context(), outcome.taskID)
			if p.id == outcome.robotID && err != nil {
				t.Fatalf("created task missing from selected store: %v", err)
			}
			if p.id != outcome.robotID && !errors.Is(err, tasks.ErrTaskNotFound) {
				t.Fatalf("created task leaked to %s: %v", p.id, err)
			}
		}
	}

	// Close/reopen the actual SQLite database and HTTP listener at the original
	// endpoint, with a rotated console credential. The other robot stays online.
	restarted := peers[0]
	restarted.server.Close()
	if err := restarted.store.Close(); err != nil {
		t.Fatal(err)
	}
	start(restarted, "rotated-ugv-session")
	for _, p := range peers {
		if err := readHistory(p.id); err != nil {
			t.Fatal(err)
		}
		if err := readEvents(p.id); err != nil {
			t.Fatal(err)
		}
		response, err := client.Get(root.URL + "/robots/" + p.id + "/v1/runtime")
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if response.StatusCode != http.StatusServiceUnavailable {
			t.Fatalf("fixture has no Runtime; history must not masquerade as live state: %d", response.StatusCode)
		}
	}
}
