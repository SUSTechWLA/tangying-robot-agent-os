package console_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The endpoint that turns a displayed plan into an executed step. Its job is to be
// the boundary: an action not in the catalog, or one the catalog refuses, must not
// reach the executor at all.

// fakeRecoveryExecutors is the composition-root-shaped executor: one object that
// satisfies the console's task-executor port and the recovery ports, exactly as
// `localapp.App` does in production.
type fakeRecoveryExecutors struct {
	result   recoveryexec.Result
	err      error
	requests []recoveryexec.Request
	catalog  *agentruntime.RecoveryCatalog
}

// Enqueue and Cancel are the console's task-executor port, unused here.
func (f *fakeRecoveryExecutors) Enqueue(string) error { return nil }
func (f *fakeRecoveryExecutors) Cancel(string) error  { return nil }

func (f *fakeRecoveryExecutors) Execute(_ context.Context, request recoveryexec.Request) (recoveryexec.Result, error) {
	f.requests = append(f.requests, request)
	return f.result, f.err
}

func (f *fakeRecoveryExecutors) Lookup(id string) (agentruntime.RecoveryAction, bool) {
	if f.catalog == nil {
		f.catalog = agentruntime.DefaultRecoveryCatalog()
	}
	return f.catalog.Lookup(id)
}

// recoveryServer builds a console whose executor is the given fake. The real
// composition root hands the console one object that plays both roles.
func recoveryServer(t *testing.T, executor console.Executor) *httptest.Server {
	t.Helper()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	server := httptest.NewServer(console.NewServer(service, executor, console.WithSessionToken(testSessionToken)).Handler())
	t.Cleanup(server.Close)
	return server
}

func recoveryServerWith(t *testing.T, executor *fakeRecoveryExecutors) (*httptest.Server, *fakeRecoveryExecutors) {
	t.Helper()
	return recoveryServer(t, executor), executor
}

func postExecute(t *testing.T, server *httptest.Server, body string) (int, map[string]any) {
	t.Helper()
	request, err := http.NewRequest(http.MethodPost, server.URL+"/v1/recovery/execute", strings.NewReader(body))
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set(console.SessionHeaderName, testSessionToken)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	defer response.Body.Close()
	var decoded map[string]any
	_ = json.NewDecoder(response.Body).Decode(&decoded)
	return response.StatusCode, decoded
}

// With no executor wired the endpoint says so rather than pretending.
func TestRecoveryExecutionIsUnavailableWhenUnconfigured(t *testing.T) {
	server := recoveryServer(t, nil)
	status, body := postExecute(t, server, `{"actionId":"map.activate"}`)
	if status != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", status)
	}
	if body["code"] != "RECOVERY_EXECUTION_UNAVAILABLE" {
		t.Fatalf("body = %v", body)
	}
}

// An action has to be named. Without one there is nothing to approve.
func TestAnExecutionNeedsAnAction(t *testing.T) {
	server, executor := recoveryServerWith(t, &fakeRecoveryExecutors{})
	status, body := postExecute(t, server, `{"planId":"plan-1","taskId":"task-1"}`)
	if status != http.StatusBadRequest || body["code"] != "RECOVERY_ACTION_REQUIRED" {
		t.Fatalf("status = %d body = %v", status, body)
	}
	if len(executor.requests) != 0 {
		t.Fatal("a request with no action reached the executor")
	}
}

// An action the catalog does not contain never reaches the executor: approving
// something the system has no entry for is approving nothing.
func TestAnUnknownActionIsRefusedAtTheBoundary(t *testing.T) {
	server, executor := recoveryServerWith(t, &fakeRecoveryExecutors{})
	status, body := postExecute(t, server, `{"actionId":"robot.take_over"}`)
	if status != http.StatusNotFound || body["code"] != "RECOVERY_ACTION_UNKNOWN" {
		t.Fatalf("status = %d body = %v", status, body)
	}
	if len(executor.requests) != 0 {
		t.Fatal("an unknown action reached the executor")
	}
}

// A never-automatic action is refused with the catalog's own sentence — the one the
// plan showed the operator.
func TestANeverAutomaticActionIsRefusedWithItsOwnReason(t *testing.T) {
	server, executor := recoveryServerWith(t, &fakeRecoveryExecutors{})
	for _, id := range []string{"estop.release", "hardware.replug", "calibration.change"} {
		status, body := postExecute(t, server, `{"actionId":"`+id+`"}`)
		if status != http.StatusConflict || body["code"] != "RECOVERY_ACTION_REFUSED" {
			t.Fatalf("%s: status = %d body = %v", id, status, body)
		}
		action, _ := agentruntime.DefaultRecoveryCatalog().Lookup(id)
		if body["message"] != action.Refusal {
			t.Fatalf("%s: message = %v, want the catalog's refusal %q", id, body["message"], action.Refusal)
		}
	}
	if len(executor.requests) != 0 {
		t.Fatal("a refused action reached the executor")
	}
}

// An executable action reaches the executor with the action resolved from the
// catalog — not with a string the caller supplied.
func TestAnExecutableActionReachesTheExecutor(t *testing.T) {
	server, executor := recoveryServerWith(t, &fakeRecoveryExecutors{
		result: recoveryexec.Result{
			ActionID: "map.activate", Executed: true, Verified: true,
			Reason: "已加载", Verification: "地图已启用",
		},
	})
	status, body := postExecute(t, server,
		`{"actionId":"map.activate","planId":"plan-1","taskId":"task-1"}`)
	if status != http.StatusOK {
		t.Fatalf("status = %d body = %v", status, body)
	}
	if body["verified"] != true || body["executed"] != true {
		t.Fatalf("body = %v", body)
	}
	if len(executor.requests) != 1 {
		t.Fatalf("executor requests = %d", len(executor.requests))
	}
	request := executor.requests[0]
	if request.Action.ID != "map.activate" {
		t.Fatalf("action = %+v", request.Action)
	}
	// The resolved action carries its declared tools, which is what the execution
	// is scoped to. A caller-supplied string would carry none.
	if len(request.Action.Tools) == 0 {
		t.Fatal("the action reached the executor without its declared tools")
	}
	if request.PlanID != "plan-1" || request.TaskID != "task-1" {
		t.Fatalf("request = %+v", request)
	}
}

// An execution failure is reported as a failure, not as a success with an empty
// result.
func TestAnExecutionErrorIsReportedAsOne(t *testing.T) {
	server, _ := recoveryServerWith(t, &fakeRecoveryExecutors{err: errors.New("robot unreachable")})
	status, body := postExecute(t, server, `{"actionId":"map.read-status"}`)
	if status != http.StatusBadGateway || body["code"] != "RECOVERY_EXECUTION_FAILED" {
		t.Fatalf("status = %d body = %v", status, body)
	}
	if !strings.Contains(body["message"].(string), "robot unreachable") {
		t.Fatalf("message = %v", body["message"])
	}
}

// A read-only action runs through the same endpoint, because the endpoint's job is
// to be the boundary and not to decide which risks are worth exposing.
func TestAReadOnlyActionIsExecutableThroughTheSameEndpoint(t *testing.T) {
	server, executor := recoveryServerWith(t, &fakeRecoveryExecutors{
		result: recoveryexec.Result{ActionID: "map.read-status", Executed: true},
	})
	status, _ := postExecute(t, server, `{"actionId":"map.read-status"}`)
	if status != http.StatusOK {
		t.Fatalf("status = %d", status)
	}
	if len(executor.requests) != 1 || executor.requests[0].Action.ID != "map.read-status" {
		t.Fatalf("requests = %+v", executor.requests)
	}
	if executor.requests[0].Action.RequiresApproval() {
		t.Fatal("a read-only action was treated as needing approval")
	}
}
