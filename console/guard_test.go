package console_test

import (
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
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
		if cookie.Name == console.SessionCookieNameForHost("127.0.0.1:8897") {
			found = cookie
		}
	}
	if found == nil {
		t.Fatalf("no host-scoped cookie on a read; cookies = %v", response.Result().Cookies())
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

func TestTwoLocalConsolesRetainIndependentBrowserSessions(t *testing.T) {
	newConsole := func() *httptest.Server {
		service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
		server := httptest.NewServer(console.NewServer(service, nil).Handler())
		t.Cleanup(server.Close)
		return server
	}
	first, second := newConsole(), newConsole()
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	client := &http.Client{Jar: jar}
	for _, server := range []*httptest.Server{first, second} {
		response, err := client.Get(server.URL + "/healthz")
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
	}
	firstURL, _ := url.Parse(first.URL)
	secondURL, _ := url.Parse(second.URL)
	if firstURL.Hostname() != secondURL.Hostname() {
		t.Fatal("the test must exercise cookies shared by one host")
	}
	if console.SessionCookieNameForHost(firstURL.Host) == console.SessionCookieNameForHost(secondURL.Host) {
		t.Fatal("distinct ports must have distinct cookie names")
	}
	// A browser sends both cookies to both ports. Loading the second page must
	// neither replace the first credential nor authorise it at the second.
	if cookies := jar.Cookies(firstURL); len(cookies) != 2 {
		t.Fatalf("want two independent browser credentials, got %d", len(cookies))
	}
	for _, server := range []*httptest.Server{first, second, first} {
		response, err := client.Post(server.URL+"/v1/tasks", "application/json",
			strings.NewReader(`{"request":"把红色杯子放进右侧收纳盒"}`))
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusCreated {
			t.Fatalf("console write after visiting another port: status %d", response.StatusCode)
		}
	}
}

func TestConsoleCookieNamespaceAndLegacyCompatibility(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	handler := console.NewServer(service, nil, console.WithSessionToken(testSessionToken)).Handler()
	for _, test := range []struct {
		name, cookieName, token string
		want                    int
	}{
		{"current host", console.SessionCookieNameForHost("127.0.0.1:8897"), testSessionToken, http.StatusCreated},
		{"other port namespace", console.SessionCookieNameForHost("127.0.0.1:8898"), testSessionToken, http.StatusUnauthorized},
		{"another console token", console.SessionCookieNameForHost("127.0.0.1:8897"), "another-console", http.StatusUnauthorized},
		{"legacy exact token", console.SessionCookieName, testSessionToken, http.StatusCreated},
		{"legacy wrong token", console.SessionCookieName, "another-console", http.StatusUnauthorized},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8897/v1/tasks",
				strings.NewReader(`{"request":"把红色杯子放进右侧收纳盒"}`))
			request.Header.Set("Content-Type", "application/json")
			request.AddCookie(&http.Cookie{Name: test.cookieName, Value: test.token})
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != test.want {
				t.Fatalf("status = %d, want %d: %s", response.Code, test.want, response.Body.String())
			}
			if len(response.Result().Cookies()) != 0 {
				t.Fatal("a write must not mint a session cookie")
			}
		})
	}
	if console.SessionCookieNameForHost("LOCALHOST:8897") != console.SessionCookieNameForHost("localhost:8897") {
		t.Fatal("hostnames are case insensitive")
	}
	// Shared wire vectors with tests/install/test_console_session.py: the CLI
	// must recognise the exact cookie the Go server publishes.
	for host, want := range map[string]string{
		"127.0.0.1:8897": "tangying_session_9413f133eae9cf7bfd27546d1a0c1174",
		"LOCALHOST:8897": "tangying_session_8f572b71aa46f9313027157ad2fd1b8a",
		"[::1]:8897":     "tangying_session_50e7cd31d98c48e27aa517b5cbff6734",
	} {
		if got := console.SessionCookieNameForHost(host); got != want {
			t.Fatalf("cookie namespace for %q = %q, want %q", host, got, want)
		}
	}
}

func TestConsoleReadMigratesLegacyCookieAndRefreshesRestartedSession(t *testing.T) {
	for _, scheme := range []string{"http", "https"} {
		t.Run(scheme, func(t *testing.T) {
			handler := console.NewServer(nil, nil, console.WithSessionToken(testSessionToken)).Handler()
			request := httptest.NewRequest(http.MethodGet, scheme+"://127.0.0.1:8897/healthz", nil)
			request.AddCookie(&http.Cookie{Name: console.SessionCookieName, Value: testSessionToken})
			request.AddCookie(&http.Cookie{Name: console.SessionCookieNameForHost(request.Host), Value: "previous-process"})
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			cookies := response.Result().Cookies()
			if len(cookies) != 1 || cookies[0].Name != console.SessionCookieNameForHost(request.Host) ||
				cookies[0].Value != testSessionToken || cookies[0].Path != "/" ||
				cookies[0].Secure != (scheme == "https") {
				t.Fatalf("read did not replace only the current host credential: %#v", cookies)
			}
			request = httptest.NewRequest(http.MethodGet, scheme+"://127.0.0.1:8897/healthz", nil)
			request.AddCookie(cookies[0])
			response = httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if len(response.Result().Cookies()) != 0 {
				t.Fatal("a current credential must not be reissued on every poll")
			}
		})
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
