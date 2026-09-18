package console_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The console's write guard.
//
// Two failures are covered here, and they are the same failure seen from two
// sides. Every mutating route accepted a cross-site POST carrying a
// CORS-safelisted content type, and none of them could tell a person from any
// other process that could reach the port — so `OperatorApproved` recorded an
// approval that nothing could falsify.

// The attack this closes: a page the operator happens to have open posts to the
// local console. Browsers deliver a cross-origin POST with a simple content type
// without any preflight, so the request arrives looking ordinary.
func TestACrossSitePostCannotReachAMutatingRoute(t *testing.T) {
	for name, path := range map[string]string{
		"recovery execute": "/v1/recovery/execute",
		"create task":      "/v1/tasks",
		"approve task":     "/v1/tasks/task-1/approve",
		"config llm":       "/v1/config/llm",
		"pair robot":       "/v1/robots/pair",
		"robot service":    "/v1/robot/services",
	} {
		for _, site := range []struct{ origin, fetchSite string }{
			{"https://evil.example", "cross-site"},
			{"null", "cross-site"},
		} {
			handler := console.NewServer(nil, nil,
				console.WithSessionToken(testSessionToken), console.WithRobotServices(nil)).Handler()
			method := http.MethodPost
			if path == "/v1/config/llm" {
				method = http.MethodPut
			}
			request := httptest.NewRequest(method, "http://127.0.0.1:8897"+path, strings.NewReader(`{"actionId":"execution.read-history"}`))
			request.Header.Set("Content-Type", "text/plain")
			request.Header.Set("Origin", site.origin)
			request.Header.Set("Sec-Fetch-Site", site.fetchSite)
			// Even holding a valid token, a cross-site request is refused: the
			// origin check does not depend on the attacker failing to get one.
			request.Header.Set(console.SessionHeaderName, testSessionToken)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusForbidden {
				t.Errorf("%s from %s: status = %d, want 403 (%s)", name, site.origin, response.Code, response.Body.String())
			}
		}
	}
}

// A request that never loaded the console has no session, and a route that can
// move a robot must not run without one.
func TestWithoutASessionAMutatingRouteIsRefused(t *testing.T) {
	for name, path := range map[string]string{
		"recovery execute": "/v1/recovery/execute",
		"create task":      "/v1/tasks",
		"approve task":     "/v1/tasks/task-1/approve",
		"pause task":       "/v1/tasks/task-1/pause",
	} {
		handler := console.NewServer(nil, nil, console.WithSessionToken(testSessionToken)).Handler()
		request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8897"+path, strings.NewReader(`{}`))
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Errorf("%s: status = %d, want 401 (%s)", name, response.Code, response.Body.String())
		}
	}
}

// Read routes stay open. Requiring a session to read the console's own state
// would break the first page load — the one that hands out the cookie.
func TestReadRoutesDoNotRequireASession(t *testing.T) {
	handler := console.NewServer(nil, nil, console.WithSessionToken(testSessionToken)).Handler()
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8897/healthz", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", response.Code)
	}
}

// The browser half of the session: the page is handed a cookie on a read, so the
// front end needs no knowledge that a token exists.
func TestAReadHandsThePageItsSessionCookie(t *testing.T) {
	handler := console.NewServer(nil, nil).Handler()
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8897/", nil))

	var found *http.Cookie
	for _, cookie := range response.Result().Cookies() {
		if cookie.Name == console.SessionCookieName {
			found = cookie
		}
	}
	if found == nil {
		t.Fatalf("no %s cookie on a read; cookies = %v", console.SessionCookieName, response.Result().Cookies())
	}
	if found.Value == "" {
		t.Fatal("session cookie is empty")
	}
	// SameSite=Strict is the whole mechanism for the browser case: it is what
	// keeps the cookie off a cross-site request. A cookie without it would leave
	// the guard relying on the origin header alone.
	if found.SameSite != http.SameSiteStrictMode {
		t.Fatalf("SameSite = %v, want Strict", found.SameSite)
	}
	if !found.HttpOnly {
		t.Fatal("session cookie must not be readable from page script")
	}
}

// The cookie is a real credential, not decoration: the browser path has to work
// end to end, or the only way in would be the test header.
func TestTheCookieAloneAuthorisesAWrite(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	handler := console.NewServer(service, nil).Handler()

	// Load the page the way a browser would.
	page := httptest.NewRecorder()
	handler.ServeHTTP(page, httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8897/", nil))
	cookies := page.Result().Cookies()
	if len(cookies) == 0 {
		t.Fatal("no cookie issued on page load")
	}

	request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8897/v1/tasks", strings.NewReader(`{"request":"把红色杯子放进右侧收纳盒"}`))
	request.Header.Set("Content-Type", "application/json")
	for _, cookie := range cookies {
		request.AddCookie(cookie)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if response.Code != http.StatusCreated {
		t.Fatalf("cookie did not authorise the write: %d %s", response.Code, response.Body.String())
	}
}

// OperatorApproved is only ever recorded behind the guard, and the record now
// names the session rather than asserting a person.
func TestAnApprovedExecutionRecordsWhichSessionApprovedIt(t *testing.T) {
	server, executor := recoveryServerWith(t, &fakeRecoveryExecutors{})
	status, body := postExecute(t, server, `{"actionId":"execution.read-history","taskId":"task-1"}`)
	if status != http.StatusOK {
		t.Fatalf("status = %d body = %v", status, body)
	}
	if len(executor.requests) != 1 {
		t.Fatalf("requests = %d, want 1", len(executor.requests))
	}
	request := executor.requests[0]
	if !request.OperatorApproved {
		t.Fatal("a session-bearing call must be recorded as operator-approved")
	}
	if strings.TrimSpace(request.ApprovalEvidence) == "" {
		t.Fatal("approval evidence is empty; the boolean is unauditable on its own")
	}
	if !strings.Contains(request.ApprovalEvidence, "console-session:") {
		t.Fatalf("approval evidence does not name the session: %q", request.ApprovalEvidence)
	}
	// The bearer token itself must never reach the ledger: this evidence is
	// exported into incident bundles and training data.
	if strings.Contains(request.ApprovalEvidence, testSessionToken) {
		t.Fatalf("approval evidence leaked the token: %q", request.ApprovalEvidence)
	}
}
