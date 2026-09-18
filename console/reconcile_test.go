package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The clearing path, end to end through the console.
//
// An unconfirmed physical step blocks readiness and forbids retrying the step.
// Both are right, and both used to be permanent: the operator who went and looked
// had nowhere to record what they saw. A block that cannot be cleared is a block
// people learn to ignore — the failure the readiness report exists to prevent.

type reconcileFixture struct {
	server *httptest.Server
	store  *sqlite.Store
	taskID string
}

func newReconcileFixture(t *testing.T) reconcileFixture {
	t.Helper()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	app := localapp.New(service, agent.NewRunner(store, nil, nil), memory.NewQueue[string](4))
	server := httptest.NewServer(console.NewServer(service, app,
		console.WithSessionToken(testSessionToken), console.WithRuntime(nil)).Handler())
	t.Cleanup(server.Close)
	// One physical step that started and never recorded an outcome: the state the
	// whole closed-loop contract is built around.
	if err := store.MarkStepStarted(context.Background(), middleware.StepRecord{
		TaskID: task.ID, StepID: "pick", Capability: "manipulation.pick", SafetyLevel: "physical",
	}); err != nil {
		t.Fatal(err)
	}
	return reconcileFixture{server: server, store: store, taskID: task.ID}
}

func (f reconcileFixture) post(t *testing.T, path, body string, withSession bool) (int, map[string]any) {
	t.Helper()
	request, err := http.NewRequest(http.MethodPost, f.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	if withSession {
		request.Header.Set(console.SessionHeaderName, testSessionToken)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var decoded map[string]any
	_ = json.NewDecoder(response.Body).Decode(&decoded)
	return response.StatusCode, decoded
}

func TestAReconciledStepStopsBlockingTheTask(t *testing.T) {
	fixture := newReconcileFixture(t)
	recovery, err := http.Get(fixture.server.URL + "/v1/tasks/" + fixture.taskID + "/recovery")
	if err != nil {
		t.Fatal(err)
	}
	var before map[string]any
	_ = json.NewDecoder(recovery.Body).Decode(&before)
	recovery.Body.Close()
	if before["requiresReconciliation"] != true {
		t.Fatalf("precondition: %v", before)
	}

	status, body := fixture.post(t, "/v1/tasks/"+fixture.taskID+"/reconcile",
		`{"stepId":"pick","outcome":"NEVER_ACTED","note":"夹爪是空的，杯子仍在桌上"}`, true)
	if status != http.StatusOK {
		t.Fatalf("status = %d body = %v", status, body)
	}

	after, err := http.Get(fixture.server.URL + "/v1/tasks/" + fixture.taskID + "/recovery")
	if err != nil {
		t.Fatal(err)
	}
	defer after.Body.Close()
	var view map[string]any
	if err := json.NewDecoder(after.Body).Decode(&view); err != nil {
		t.Fatal(err)
	}
	if view["requiresReconciliation"] != false {
		t.Fatalf("the block survived a person's conclusion: %v", view)
	}
	if len(view["uncertainStepIds"].([]any)) != 0 {
		t.Fatalf("uncertainStepIds = %v", view["uncertainStepIds"])
	}
}

// The endpoint is behind the same write guard as every other mutating route: a
// conclusion is a decision about a physical action, and it must come from the
// console rather than from any page the operator happens to have open.
func TestAConclusionCannotBeRecordedWithoutASession(t *testing.T) {
	fixture := newReconcileFixture(t)
	status, _ := fixture.post(t, "/v1/tasks/"+fixture.taskID+"/reconcile",
		`{"stepId":"pick","outcome":"NEVER_ACTED","note":"看过了"}`, false)
	if status != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", status)
	}
	runs, err := fixture.store.ListStepRuns(context.Background(), fixture.taskID)
	if err != nil {
		t.Fatal(err)
	}
	if runs[0].Reconciled != nil {
		t.Fatal("an unauthenticated conclusion was recorded")
	}
}

func TestAConclusionWithoutAReasonIsRefused(t *testing.T) {
	fixture := newReconcileFixture(t)
	for name, body := range map[string]string{
		"no step":     `{"outcome":"HAPPENED","note":"看过了"}`,
		"no outcome":  `{"stepId":"pick","note":"看过了"}`,
		"no note":     `{"stepId":"pick","outcome":"HAPPENED"}`,
		"bad outcome": `{"stepId":"pick","outcome":"PROBABLY","note":"大概"}`,
	} {
		if status, _ := fixture.post(t, "/v1/tasks/"+fixture.taskID+"/reconcile", body, true); status == http.StatusOK {
			t.Errorf("%s: accepted", name)
		}
	}
	runs, err := fixture.store.ListStepRuns(context.Background(), fixture.taskID)
	if err != nil {
		t.Fatal(err)
	}
	if runs[0].Reconciled != nil {
		t.Fatal("a refused conclusion was recorded")
	}
}
