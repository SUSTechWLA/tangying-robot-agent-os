package console

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/gorilla/websocket"
	"golang.org/x/sys/unix"
)

const rootWorkspaceToken = "root-console-token"

func workspaceConfig(t *testing.T, profiles ...workspaceProfile) *WorkspaceRegistry {
	t.Helper()
	data, err := json.Marshal(map[string]any{"schemaVersion": "edge.workspaces.v1", "workspaces": profiles})
	if err != nil {
		t.Fatal(err)
	}
	filename := filepath.Join(t.TempDir(), "workspaces.json")
	if err := os.WriteFile(filename, data, 0600); err != nil {
		t.Fatal(err)
	}
	registry, err := LoadWorkspacesFile(filename)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(registry.transport.CloseIdleConnections)
	return registry
}

func workspaceTokenFile(t *testing.T, token string) string {
	t.Helper()
	filename := filepath.Join(t.TempDir(), "console-session")
	if err := os.WriteFile(filename, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	return filename
}

func workspaceRoot(registry *WorkspaceRegistry) *Server {
	return NewServer(nil, nil, WithWorkspaceIdentity("robot-root"), WithWorkspaces(registry), WithSessionToken(rootWorkspaceToken))
}

func workspaceRequest(t *testing.T, handler http.Handler, method, route, token string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(method, "http://127.0.0.1:8787"+route, nil)
	request.Header.Set(SessionHeaderName, token)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func TestWorkspaceRegistryValidation(t *testing.T) {
	valid := workspaceProfile{ID: "ugv", Label: "无人车", RobotID: "robot-ugv", URL: "http://127.0.0.1:8788", SessionFile: "/private/console-session"}
	for _, test := range []struct {
		name   string
		change func(*workspaceProfile)
	}{
		{"reserved local", func(p *workspaceProfile) { p.ID = "local" }},
		{"path ID", func(p *workspaceProfile) { p.ID = "../ugv" }},
		{"empty label", func(p *workspaceProfile) { p.Label = " " }},
		{"empty robot", func(p *workspaceProfile) { p.RobotID = "" }},
		{"newline robot", func(p *workspaceProfile) { p.RobotID = "robot\nugv" }},
		{"relative session", func(p *workspaceProfile) { p.SessionFile = "console-session" }},
		{"unclean session", func(p *workspaceProfile) { p.SessionFile = "/tmp/../console-session" }},
		{"non-loopback HTTP", func(p *workspaceProfile) { p.URL = "http://192.168.1.2:8787" }},
		{"hostname HTTP", func(p *workspaceProfile) { p.URL = "http://localhost:8787" }},
		{"userinfo", func(p *workspaceProfile) { p.URL = "https://user:password@example.com" }},
		{"path", func(p *workspaceProfile) { p.URL = "https://example.com/api" }},
		{"query", func(p *workspaceProfile) { p.URL = "https://example.com?token=secret" }},
		{"empty query", func(p *workspaceProfile) { p.URL = "https://example.com?" }},
		{"fragment", func(p *workspaceProfile) { p.URL = "https://example.com#secret" }},
		{"empty fragment", func(p *workspaceProfile) { p.URL = "https://example.com#" }},
		{"protocol", func(p *workspaceProfile) { p.URL = "file:///tmp/robot" }},
		{"empty hostname", func(p *workspaceProfile) { p.URL = "https://:8787" }},
		{"port", func(p *workspaceProfile) { p.URL = "https://example.com:99999" }},
	} {
		t.Run(test.name, func(t *testing.T) {
			profile := valid
			test.change(&profile)
			data, _ := json.Marshal(map[string]any{"schemaVersion": "edge.workspaces.v1", "workspaces": []workspaceProfile{profile}})
			filename := filepath.Join(t.TempDir(), "workspaces.json")
			os.WriteFile(filename, data, 0600)
			if _, err := LoadWorkspacesFile(filename); err == nil {
				t.Fatal("invalid registry accepted")
			}
		})
	}
	for _, endpoint := range []string{"http://127.0.0.1:8787/", "http://[::1]:8787", "https://robot.example.com"} {
		t.Run(endpoint, func(t *testing.T) {
			profile := valid
			profile.URL = endpoint
			registry := workspaceConfig(t, profile)
			if registry.transport.Proxy != nil {
				t.Fatal("proxy environment must be disabled")
			}
			if err := registry.ValidateIdentity(profile.RobotID); err == nil {
				t.Fatal("self alias accepted")
			}
			if err := registry.ValidateIdentity("root"); err != nil {
				t.Fatal(err)
			}
		})
	}
	for _, document := range []string{
		`{"schemaVersion":"wrong","workspaces":[]}`,
		`{"schemaVersion":"edge.workspaces.v1","workspaces":[],"extra":true}`,
		`{"schemaVersion":"wrong","schemaVersion":"edge.workspaces.v1","workspaces":[]}`,
		`{"schemaVersion":"edge.workspaces.v1","workspaces":[]} {}`,
	} {
		filename := filepath.Join(t.TempDir(), "workspaces.json")
		os.WriteFile(filename, []byte(document), 0600)
		if _, err := LoadWorkspacesFile(filename); err == nil {
			t.Errorf("invalid JSON accepted: %s", document)
		}
	}
	for _, collision := range []string{"id", "robot", "endpoint", "limit"} {
		t.Run("duplicate "+collision, func(t *testing.T) {
			second := valid
			second.ID, second.RobotID, second.URL = "uav", "robot-uav", "http://127.0.0.1:8789"
			switch collision {
			case "id":
				second.ID = valid.ID
			case "robot":
				second.RobotID = valid.RobotID
			case "endpoint":
				second.URL = valid.URL
			}
			profiles := []workspaceProfile{valid, second}
			if collision == "limit" {
				profiles = make([]workspaceProfile, 33)
			}
			data, _ := json.Marshal(map[string]any{"schemaVersion": "edge.workspaces.v1", "workspaces": profiles})
			filename := filepath.Join(t.TempDir(), "workspaces.json")
			os.WriteFile(filename, data, 0600)
			if _, err := LoadWorkspacesFile(filename); err == nil {
				t.Fatal("collision/overflow accepted")
			}
		})
	}
}

func TestWorkspaceIdentitySurvivesRuntimeOfflineAndPinsRequests(t *testing.T) {
	handler := workspaceRoot(nil).Handler()
	response := workspaceRequest(t, handler, "GET", "/v1/workspace", "")
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"robotId":"robot-root"`) {
		t.Fatalf("immutable identity: %d %s", response.Code, response.Body.String())
	}
	if got := workspaceRequest(t, handler, "GET", "/v1/runtime", "").Code; got != 503 {
		t.Fatalf("runtime status=%d", got)
	}
	request := httptest.NewRequest("POST", "/v1/tasks", nil)
	request.Header.Set(WorkspaceIdentityHeader, "different-robot")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 409 {
		t.Fatalf("identity mismatch not stopped before mutation: %d", response.Code)
	}
}

func TestWorkspaceScopesIsolateIdenticalTaskIDsAndNeverSelectGlobally(t *testing.T) {
	var mu sync.Mutex
	counts := map[string]int{}
	profiles := []workspaceProfile{}
	for _, id := range []string{"ugv", "uav"} {
		token := "session-for-" + id
		agent := NewServer(nil, nil, WithWorkspaceIdentity(id), WithSessionToken(token))
		agent.mux.HandleFunc("GET /v1/tasks/shared", func(w http.ResponseWriter, r *http.Request) {
			writeJSON(w, 200, map[string]string{"id": "shared", "robotId": id})
		})
		agent.mux.HandleFunc("POST /v1/tasks/shared/cancel", func(w http.ResponseWriter, r *http.Request) {
			if !agent.allowOperatorWrite(w, r) {
				return
			}
			mu.Lock()
			counts[id]++
			mu.Unlock()
			writeJSON(w, 200, map[string]string{"robotId": id})
		})
		upstream := httptest.NewServer(agent.Handler())
		t.Cleanup(upstream.Close)
		profiles = append(profiles, workspaceProfile{ID: id, RobotID: id, Label: id, URL: upstream.URL, SessionFile: workspaceTokenFile(t, token)})
	}
	root := workspaceRoot(workspaceConfig(t, profiles...))
	handler := root.Handler()
	for _, id := range []string{"ugv", "uav", "ugv"} {
		response := workspaceRequest(t, handler, "GET", "/robots/"+id+"/v1/tasks/shared", rootWorkspaceToken)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"robotId":"`+id+`"`) {
			t.Fatalf("scope=%s response=%d %s", id, response.Code, response.Body.String())
		}
		response = workspaceRequest(t, handler, "POST", "/robots/"+id+"/v1/tasks/shared/cancel", rootWorkspaceToken)
		if response.Code != 200 {
			t.Fatalf("write scope=%s response=%d %s", id, response.Code, response.Body.String())
		}
	}
	if counts["ugv"] != 2 || counts["uav"] != 1 {
		t.Fatalf("cross-routed writes: %#v", counts)
	}
	if response := workspaceRequest(t, handler, "GET", "/v1/workspace", ""); !strings.Contains(response.Body.String(), `"robotId":"robot-root"`) {
		t.Fatalf("root globally selected a different robot: %s", response.Body.String())
	}
	for _, route := range []string{"/robots/unknown/v1/tasks/shared", "/robots/unknown/", "/robots/ugv/../uav/v1/tasks/shared", "/robots/ugv/v1/%2e%2e/tasks"} {
		response := workspaceRequest(t, handler, "GET", route, rootWorkspaceToken)
		if response.Code != 404 && response.Code != 400 {
			t.Fatalf("malformed/unknown scope=%s status=%d", route, response.Code)
		}
	}
	response := workspaceRequest(t, handler, "GET", "/robots/ugv/v1/workspaces", "")
	body := response.Body.String()
	if response.Code != 200 || !strings.Contains(body, `"currentId":"ugv"`) || !strings.Contains(body, `"href":"/robots/uav/"`) ||
		strings.Contains(body, profiles[0].URL) || strings.Contains(body, profiles[0].SessionFile) || strings.Contains(body, "session-for-") {
		t.Fatalf("unsafe or nested registry: %d %s", response.Code, body)
	}
}

func TestWorkspaceProxyRefusesUnauthenticatedCrossSiteAndPairing(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer upstream.Close()
	root := workspaceRoot(workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: upstream.URL, SessionFile: workspaceTokenFile(t, "token")}))
	for _, test := range []struct {
		name, method, route, token, origin string
		status                             int
	}{
		{"anonymous read", "GET", "/v1/tasks", "", "", 401},
		{"anonymous write", "POST", "/v1/tasks", "", "", 401},
		{"cross-site read", "GET", "/v1/tasks", rootWorkspaceToken, "https://evil.example", 403},
		{"cross-site write", "POST", "/v1/tasks", rootWorkspaceToken, "https://evil.example", 403},
		{"pairing", "POST", "/v1/robots/pair", rootWorkspaceToken, "", 403},
		{"registry mutation", "POST", "/v1/workspaces", rootWorkspaceToken, "", 405},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(test.method, "http://127.0.0.1:8787/robots/ugv"+test.route, nil)
			request.Header.Set(SessionHeaderName, test.token)
			request.Header.Set("Origin", test.origin)
			response := httptest.NewRecorder()
			root.Handler().ServeHTTP(response, request)
			if response.Code != test.status {
				t.Fatalf("status=%d, want %d: %s", response.Code, test.status, response.Body.String())
			}
		})
	}
	if calls.Load() != 0 {
		t.Fatal("rejected requests reached upstream")
	}
	response := workspaceRequest(t, root.Handler(), "GET", "/robots/ugv/", "")
	if response.Code != 200 || !strings.Contains(response.Body.String(), "<html") || len(response.Result().Cookies()) == 0 {
		t.Fatal("offline workspace page must bootstrap a root session from embedded UI")
	}
}

func TestWorkspaceIdentityFailuresNeverSendSessionOrAction(t *testing.T) {
	for _, mode := range []string{"mismatch", "redirect", "offline", "malformed", "untrusted-tls"} {
		t.Run(mode, func(t *testing.T) {
			var leaked atomic.Bool
			upstreamHandler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get(SessionHeaderName) != "" || r.Header.Get("Cookie") != "" || r.URL.Path != "/v1/workspace" {
					leaked.Store(true)
				}
				switch mode {
				case "redirect":
					http.Redirect(w, r, "/v1/other", 302)
				case "malformed":
					w.Write([]byte(`{"schemaVersion":"edge.workspace.v1","robotId":"ugv"} {}`))
				default:
					writeJSON(w, 200, map[string]string{"schemaVersion": "edge.workspace.v1", "robotId": "other"})
				}
			})
			upstream := httptest.NewServer(upstreamHandler)
			if mode == "untrusted-tls" {
				upstream.Close()
				upstream = httptest.NewTLSServer(upstreamHandler)
			}
			defer upstream.Close()
			if mode == "offline" {
				upstream.Close()
			}
			root := workspaceRoot(workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: upstream.URL, SessionFile: workspaceTokenFile(t, "private-token")}))
			response := workspaceRequest(t, root.Handler(), "POST", "/robots/ugv/v1/tasks", rootWorkspaceToken)
			if response.Code != 502 || leaked.Load() {
				t.Fatalf("failed identity leaked credential/action: status=%d leaked=%t", response.Code, leaked.Load())
			}
		})
	}
}

func TestWorkspaceProxyCredentialsAreReplacedAndRedirectCookiesAreBlocked(t *testing.T) {
	tokenPath := workspaceTokenFile(t, "upstream-private-session")
	var received http.Header
	var redirects atomic.Int32
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { redirects.Add(1) }))
	defer destination.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/workspace" {
			writeJSON(w, 200, map[string]string{"schemaVersion": "edge.workspace.v1", "robotId": "ugv"})
			return
		}
		received = r.Header.Clone()
		http.SetCookie(w, &http.Cookie{Name: "upstream-secret", Value: "secret", Path: "/"})
		if r.URL.Path == "/v1/redirect" {
			http.Redirect(w, r, destination.URL, 307)
			return
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	}))
	defer upstream.Close()
	root := workspaceRoot(workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: upstream.URL, SessionFile: tokenPath}))
	request := httptest.NewRequest("POST", "http://127.0.0.1:8787/robots/ugv/v1/test", strings.NewReader(`{}`))
	request.Header.Set(SessionHeaderName, rootWorkspaceToken)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer browser-secret")
	request.Header.Set("Cookie", "browser-secret=cookie")
	request.Header.Set("Origin", "http://127.0.0.1:8787")
	request.Header.Set("Forwarded", "host=evil.example")
	request.Header.Set("X-Forwarded-Host", "evil.example")
	response := httptest.NewRecorder()
	root.Handler().ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("request failed: %d %s", response.Code, response.Body.String())
	}
	if received.Get(SessionHeaderName) != "upstream-private-session" || received.Get(WorkspaceIdentityHeader) != "ugv" || received.Get("Origin") != upstream.URL {
		t.Fatalf("missing credential/identity/origin binding: %v", received)
	}
	for _, key := range []string{"Authorization", "Cookie", "Forwarded", "X-Forwarded-Host", "X-Forwarded-For"} {
		if received.Get(key) != "" {
			t.Errorf("forwarded credential/address header %s", key)
		}
	}
	if strings.Contains(strings.Join(response.Header().Values("Set-Cookie"), "\n"), "upstream-secret") {
		t.Fatal("upstream cookie reached root browser origin")
	}
	// Operator-controlled session rotation is observed without a root restart.
	os.WriteFile(tokenPath, []byte("rotated-session"), 0600)
	response = workspaceRequest(t, root.Handler(), "GET", "/robots/ugv/v1/test", rootWorkspaceToken)
	if response.Code != 200 || received.Get(SessionHeaderName) != "rotated-session" {
		t.Fatal("session file rotation was not observed")
	}
	response = workspaceRequest(t, root.Handler(), "GET", "/robots/ugv/v1/redirect", rootWorkspaceToken)
	if response.Code != 502 || redirects.Load() != 0 || response.Header().Get("Location") != "" {
		t.Fatalf("redirect escaped fixed destination: %d %v", response.Code, response.Header())
	}
}

func TestWorkspacePinnedHeaderStopsEndpointChangeBetweenProbeAndWrite(t *testing.T) {
	original := NewServer(nil, nil, WithWorkspaceIdentity("ugv"), WithSessionToken("same-token"))
	replacement := NewServer(nil, nil, WithWorkspaceIdentity("other-robot"), WithSessionToken("same-token"))
	var writes atomic.Int32
	replacement.mux.HandleFunc("POST /v1/test", func(w http.ResponseWriter, r *http.Request) { writes.Add(1) })
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/workspace" {
			original.Handler().ServeHTTP(w, r)
		} else {
			replacement.Handler().ServeHTTP(w, r)
		}
	}))
	defer upstream.Close()
	root := workspaceRoot(workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: upstream.URL, SessionFile: workspaceTokenFile(t, "same-token")}))
	response := workspaceRequest(t, root.Handler(), "POST", "/robots/ugv/v1/test", rootWorkspaceToken)
	if response.Code != 409 || writes.Load() != 0 {
		t.Fatalf("identity changed under request: code=%d writes=%d", response.Code, writes.Load())
	}
}

func TestWorkspaceProxyWebSocketPreservesScopeAndChecksOrigin(t *testing.T) {
	agent := NewServer(nil, nil, WithWorkspaceIdentity("ugv"), WithSessionToken("remote-token"))
	agent.mux.HandleFunc("GET /v1/test/ws", func(w http.ResponseWriter, r *http.Request) {
		if !agent.session.matches(r) {
			w.WriteHeader(401)
			return
		}
		connection, err := (&websocket.Upgrader{CheckOrigin: sameOriginRequest}).Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer connection.Close()
		connection.WriteJSON(map[string]string{"robotId": r.Header.Get(WorkspaceIdentityHeader), "taskId": "same"})
	})
	upstream := httptest.NewServer(agent.Handler())
	defer upstream.Close()
	root := httptest.NewServer(workspaceRoot(workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: upstream.URL, SessionFile: workspaceTokenFile(t, "remote-token")})).Handler())
	defer root.Close()
	dialer := websocket.Dialer{HandshakeTimeout: 2 * time.Second}
	endpoint := "ws" + strings.TrimPrefix(root.URL, "http") + "/robots/ugv/v1/test/ws"
	headers := http.Header{"Cookie": {SessionCookieName + "=" + rootWorkspaceToken}, "Origin": {root.URL}}
	connection, response, err := dialer.Dial(endpoint, headers)
	if err != nil {
		if response != nil {
			data, _ := io.ReadAll(response.Body)
			t.Fatalf("WS upgrade failed: %v %s", err, data)
		}
		t.Fatal(err)
	}
	defer connection.Close()
	var event map[string]string
	if err := connection.ReadJSON(&event); err != nil || event["robotId"] != "ugv" || event["taskId"] != "same" {
		t.Fatalf("wrong scoped WS event: %#v %v", event, err)
	}
	headers.Set("Origin", "https://evil.example")
	if socket, response, err := dialer.Dial(endpoint, headers); err == nil {
		socket.Close()
		t.Fatal("cross-origin WS accepted")
	} else if response == nil || response.StatusCode != 403 {
		t.Fatalf("cross-origin status: %#v %v", response, err)
	}
}

func TestWorkspaceSessionFileMustRemainPrivate(t *testing.T) {
	for _, mode := range []string{"missing", "public", "empty", "newline", "oversize", "directory", "symlink", "fifo"} {
		t.Run(mode, func(t *testing.T) {
			filename := workspaceTokenFile(t, "valid-token")
			switch mode {
			case "missing":
				os.Remove(filename)
			case "public":
				os.Chmod(filename, 0644)
			case "empty":
				os.WriteFile(filename, nil, 0600)
			case "newline":
				os.WriteFile(filename, []byte("one\ntwo"), 0600)
			case "oversize":
				os.WriteFile(filename, bytes.Repeat([]byte("x"), 4097), 0600)
			case "directory":
				filename = t.TempDir()
			case "symlink":
				link := filepath.Join(t.TempDir(), "session-link")
				if err := os.Symlink(filename, link); err != nil {
					t.Fatal(err)
				}
				filename = link
			case "fifo":
				filename = filepath.Join(t.TempDir(), "fifo")
				if err := unix.Mkfifo(filename, 0600); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := workspaceSession(filename); err == nil {
				t.Fatal("invalid credential accepted")
			}
		})
	}
}

func TestWorkspaceIdentityProbeDoesNotUseEnvironmentProxy(t *testing.T) {
	// The transport setting is checked directly, because Go caches proxy-env
	// resolution process-wide and other tests may already have initialized it.
	registry := workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: "https://robot.example.com", SessionFile: "/private/session"})
	if registry.transport.Proxy != nil || registry.transport.TLSClientConfig != nil && registry.transport.TLSClientConfig.InsecureSkipVerify {
		t.Fatal("registry must use direct connections and verified TLS")
	}
	if registry.transport.ResponseHeaderTimeout < 10*time.Minute {
		t.Fatal("proxy deadline must accommodate the existing synchronous planning budget")
	}
	parsed, _ := url.Parse("https://robot.example.com")
	if parsed.String() != registry.profiles[0].endpoint.String() {
		t.Fatal(fmt.Sprintf("endpoint changed: %s", registry.profiles[0].endpoint))
	}
}

func TestWorkspaceProxyRefusesExecutableAPIResponses(t *testing.T) {
	for _, media := range []string{"text/html; charset=utf-8", "application/javascript", "image/svg+xml"} {
		t.Run(media, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/v1/workspace" {
					writeJSON(w, 200, map[string]string{"schemaVersion": "edge.workspace.v1", "robotId": "ugv"})
					return
				}
				w.Header().Set("Content-Type", media)
				w.Header().Set("Content-Security-Policy", "script-src 'unsafe-inline'")
				w.Write([]byte(`<script>window.execute()</script>`))
			}))
			defer upstream.Close()
			root := workspaceRoot(workspaceConfig(t, workspaceProfile{ID: "ugv", RobotID: "ugv", Label: "UGV", URL: upstream.URL, SessionFile: workspaceTokenFile(t, "session")}))
			response := workspaceRequest(t, root.Handler(), "GET", "/robots/ugv/v1/test", rootWorkspaceToken)
			if response.Code != 502 || strings.Contains(response.Body.String(), "<script>") ||
				strings.Contains(response.Header().Get("Content-Security-Policy"), "script-src 'unsafe-inline'") {
				t.Fatalf("executable upstream body escaped: %d %s", response.Code, response.Body.String())
			}
		})
	}
}

type workspaceIdleWorld struct {
	subscribed chan context.Context
}

func (*workspaceIdleWorld) Snapshot(context.Context) (worldmodel.Snapshot, error) {
	return worldmodel.Snapshot{}, nil
}

func (world *workspaceIdleWorld) Subscribe(ctx context.Context, _ uint64) (<-chan worldmodel.Delta, error) {
	world.subscribed <- ctx
	// Deliberately never close the producer channel. The consumer must stop
	// on its own canceled context even if a provider does not close promptly.
	return make(chan worldmodel.Delta), nil
}

func TestWorkspaceWorldSocketCloseCancelsIdleSubscription(t *testing.T) {
	world := &workspaceIdleWorld{subscribed: make(chan context.Context, 1)}
	server := httptest.NewServer(NewServer(nil, nil, WithWorld(world)).Handler())
	defer server.Close()
	connection, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http")+"/v1/world/events/ws", nil)
	if err != nil {
		t.Fatal(err)
	}
	var ctx context.Context
	select {
	case ctx = <-world.subscribed:
	case <-time.After(time.Second):
		connection.Close()
		t.Fatal("world subscription did not start")
	}
	connection.Close()
	select {
	case <-ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("leaving workspace did not release idle world subscription")
	}
}

type workspaceRuntimeProvider struct{ robotID string }

func (provider workspaceRuntimeProvider) Info(context.Context) (runtime.Snapshot, error) {
	return runtime.Snapshot{RobotID: provider.robotID}, nil
}

func TestWorkspaceRuntimeCannotReportDifferentBoundRobot(t *testing.T) {
	for _, identity := range []string{"robot-root", "other-robot", ""} {
		handler := NewServer(nil, nil, WithWorkspaceIdentity("robot-root"), WithRuntime(workspaceRuntimeProvider{identity})).Handler()
		response := workspaceRequest(t, handler, "GET", "/v1/runtime", "")
		want := http.StatusConflict
		if identity == "robot-root" {
			want = http.StatusOK
		}
		if response.Code != want {
			t.Fatalf("runtime identity=%q status=%d want=%d", identity, response.Code, want)
		}
	}
}
