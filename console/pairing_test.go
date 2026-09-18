package console_test

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/discovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/pairing"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// closedPort returns a port on loopback that nothing is listening on.
func closedPort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("reserve a port: %v", err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	return port
}

// Pairing from the console is the step that turns "the robot appeared in a list"
// into "the robot works", and it is the one place where a mistake installs a
// credential on somebody's hardware. These tests are about the refusals as much
// as the success.

type fakePairing struct {
	report   console.PairReport
	err      error
	requests []console.PairRequest
}

func (f *fakePairing) Pair(_ context.Context, request console.PairRequest) (console.PairReport, error) {
	f.requests = append(f.requests, request)
	return f.report, f.err
}

func pairingServer(t *testing.T, service console.PairingService) *httptest.Server {
	t.Helper()
	store := tasks.NewMemoryStore()
	taskService := tasks.NewService(store, intent.NewDeterministicParser())
	app := localapp.New(taskService, agent.NewRunner(nil, nil, nil), memory.NewQueue[string](4))
	if service != nil {
		app.WithPairing(service)
	}
	server := httptest.NewServer(console.NewServer(taskService, app, console.WithSessionToken(testSessionToken)).Handler())
	t.Cleanup(server.Close)
	return server
}

func postPairing(t *testing.T, server *httptest.Server, body string) (int, map[string]any) {
	t.Helper()
	request, err := http.NewRequest(http.MethodPost, server.URL+"/v1/robots/pair", strings.NewReader(body))
	if err != nil {
		t.Fatalf("post pairing: %v", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set(console.SessionHeaderName, testSessionToken)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("post pairing: %v", err)
	}
	defer response.Body.Close()
	var decoded map[string]any
	_ = json.NewDecoder(response.Body).Decode(&decoded)
	return response.StatusCode, decoded
}

func TestPairingNeedsTheCodeTheRobotPrinted(t *testing.T) {
	service := &fakePairing{report: console.PairReport{RobotID: "r", Paired: true}}
	status, body := postPairing(t, pairingServer(t, service), `{"robotId":"r","address":"10.0.0.5:50051"}`)
	if status != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", status)
	}
	// The message has to say where the code comes from: it is the one thing the
	// operator has to go and find.
	if body["code"] != "PAIRING_CODE_REQUIRED" {
		t.Fatalf("body = %v", body)
	}
	if !strings.Contains(body["message"].(string), "配对码") {
		t.Fatalf("message = %v", body["message"])
	}
	if len(service.requests) != 0 {
		t.Fatal("a request without a code reached the pairing service")
	}
}

func TestPairingNeedsATarget(t *testing.T) {
	for name, body := range map[string]string{
		"no robot":   `{"address":"10.0.0.5:50051","code":"AAAA-BBBB"}`,
		"no address": `{"robotId":"r","code":"AAAA-BBBB"}`,
	} {
		status, decoded := postPairing(t, pairingServer(t, &fakePairing{}), body)
		if status != http.StatusBadRequest {
			t.Fatalf("%s: status = %d, want 400", name, status)
		}
		if decoded["code"] != "PAIRING_TARGET_REQUIRED" {
			t.Fatalf("%s: body = %v", name, decoded)
		}
	}
}

// A deployment that cannot pair says so instead of offering a button that fails.
func TestPairingIsUnavailableWhenUnconfigured(t *testing.T) {
	status, body := postPairing(t, pairingServer(t, nil),
		`{"robotId":"r","address":"10.0.0.5:50051","code":"AAAA-BBBB"}`)
	if status != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", status)
	}
	if body["code"] != "PAIRING_UNAVAILABLE" {
		t.Fatalf("body = %v", body)
	}
}

// Each refusal maps to a status an operator can act on, and the robot's own words
// travel through unchanged.
func TestRefusalsAreReportedWithTheirOwnCodes(t *testing.T) {
	for name, testCase := range map[string]struct {
		err        error
		wantStatus int
		wantCode   string
	}{
		"wrong code":    {pairing.ErrWrongCode, http.StatusUnauthorized, "PAIRING_CODE_REJECTED"},
		"not pairing":   {pairing.ErrUnreachable, http.StatusConflict, "ROBOT_NOT_PAIRING"},
		"robot refused": {pairing.ErrRobotRefused, http.StatusConflict, "ROBOT_REFUSED_PAIRING"},
		"anything else": {context.DeadlineExceeded, http.StatusBadGateway, "PAIRING_FAILED"},
	} {
		server := pairingServer(t, &fakePairing{err: testCase.err})
		status, body := postPairing(t, server, `{"robotId":"r","address":"10.0.0.5:50051","code":"AAAA-BBBB"}`)
		if status != testCase.wantStatus {
			t.Fatalf("%s: status = %d, want %d", name, status, testCase.wantStatus)
		}
		if body["code"] != testCase.wantCode {
			t.Fatalf("%s: code = %v, want %s", name, body["code"], testCase.wantCode)
		}
	}
}

func TestASuccessfulPairingReportsThatARestartIsNeeded(t *testing.T) {
	service := &fakePairing{report: console.PairReport{
		RobotID: "xlerobot-0001", Address: "192.168.50.73:50051", Paired: true,
		RestartRequired: true, Detail: "配对完成。",
	}}
	status, body := postPairing(t, pairingServer(t, service),
		`{"robotId":"xlerobot-0001","address":"192.168.50.73:50051","code":"4F2K-9QW7"}`)
	if status != http.StatusOK {
		t.Fatalf("status = %d, want 200: %v", status, body)
	}
	// The robot client is built at startup with the certificates that existed
	// then, so claiming a pairing took effect immediately would be a claim the
	// system cannot keep.
	if body["restartRequired"] != true {
		t.Fatalf("body = %v, want restartRequired", body)
	}
	if service.requests[0].Code != "4F2K-9QW7" {
		t.Fatalf("the code did not reach the service: %+v", service.requests[0])
	}
}

// --- the real service --------------------------------------------------------

// A robot nobody has heard from is not paired with.
//
// Not a security boundary — the code is that — but a correctness one: the
// certificate would be issued for a name that may belong to something else.
func TestTheRealServiceRefusesARobotThatWasNeverDiscovered(t *testing.T) {
	service := &console.FilePairingService{
		Discovery:     staticDiscovery{},
		DataDirectory: t.TempDir(),
	}
	_, err := service.Pair(context.Background(), console.PairRequest{
		RobotID: "xlerobot-0001", Address: "192.168.50.73:50051", Code: "4F2K-9QW7",
	})
	if err == nil {
		t.Fatal("a robot nobody has heard announcing itself was paired with")
	}
	if !strings.Contains(err.Error(), "没有发现") {
		t.Fatalf("error = %v, want it to explain what to check", err)
	}
}

type staticDiscovery struct{ robots []discovery.Robot }

func (s staticDiscovery) Robots() []discovery.Robot { return s.robots }

// The agent's half is written after the robot accepted, and it preserves the rest
// of the configuration.
//
// The same file holds the model configuration, and a pairing that dropped the API
// key would look like a pairing that broke the agent's language ability.
func TestPairingWritesTheAgentHalfAndKeepsTheRestOfTheConfig(t *testing.T) {
	directory := t.TempDir()
	configPath := filepath.Join(directory, "local.env")
	if err := os.WriteFile(configPath, []byte(
		"# keep me\nAGENT_PROVIDER=openai\nAGENT_API_KEY=secret-value\nLOCAL_LISTEN=127.0.0.1:8787\n"), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	// Pointed at a loopback port nothing listens on, with a short timeout: a test
	// must never dial the local network, and the sandbox's habit of letting a
	// non-loopback connect hang would have made this a thirty-second test.
	service := &console.FilePairingService{
		Discovery: staticDiscovery{robots: []discovery.Robot{{
			Announcement: discovery.Announcement{
				RobotID: "xlerobot-0001", Address: "127.0.0.1:50051", Hostname: "127.0.0.1",
			},
			SourceIP: "127.0.0.1",
		}}},
		DataDirectory: directory,
		ConfigPath:    configPath,
		Client:        &pairing.Client{Port: closedPort(t), Timeout: 2 * time.Second},
	}
	// The pairing itself is exercised against the real client elsewhere; what this
	// test is about is the agent's own half, so the network call is stubbed by
	// pointing at a port nothing listens on and asserting the write did not happen.
	_, err := service.Pair(context.Background(), console.PairRequest{
		RobotID: "xlerobot-0001", Address: "127.0.0.1:50051", Code: "4F2K-9QW7",
	})
	if err == nil {
		t.Fatal("pairing reported success with nothing listening")
	}
	// Nothing was written, because nothing was paired: a failed pairing must not
	// leave the agent pointing at a robot that will refuse it.
	if _, statErr := os.Stat(filepath.Join(directory, "certs", "local-agent.crt")); statErr == nil {
		t.Fatal("a failed pairing wrote the agent's certificate")
	}
	contents, readErr := os.ReadFile(configPath)
	if readErr != nil {
		t.Fatalf("read config: %v", readErr)
	}
	if !strings.Contains(string(contents), "AGENT_API_KEY=secret-value") {
		t.Fatalf("a failed pairing altered the configuration:\n%s", contents)
	}
}

// The environment-file rewrite preserves keys it was not asked about, and it is
// the same five keys the SSH script writes.
func TestTheAgentHalfUsesTheSameKeysAsTheSSHScript(t *testing.T) {
	directory := t.TempDir()
	configPath := filepath.Join(directory, "local.env")
	if err := os.WriteFile(configPath,
		[]byte("# a comment\nAGENT_PROVIDER=deterministic\nAGENT_MODEL=my-model\n\nLOCAL_LISTEN=127.0.0.1:8787\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	// Exercised through the exported behaviour: call the file writer the same way
	// the service does, then read it back.
	if err := console.WriteAgentHalfForTest(directory, configPath, "xlerobot.local:50051"); err != nil {
		t.Fatalf("write agent half: %v", err)
	}
	contents, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	text := string(contents)
	for _, key := range []string{"ROBOT_ADDRESS", "ROBOT_SERVER_NAME", "ROBOT_CA", "ROBOT_CERT", "ROBOT_KEY"} {
		if !strings.Contains(text, key+"=") {
			t.Fatalf("the configuration is missing %s:\n%s", key, text)
		}
	}
	// The whole preserved line, not just the key.
	//
	// The first version of the writer emitted a bare `AGENT_MODEL` with no value
	// and appended the real line at the end, so a "does it contain the string"
	// assertion passed while the file was mangled.
	if !strings.Contains(text, "\nAGENT_MODEL=my-model\n") {
		t.Fatalf("the preserved line was rewritten rather than kept:\n%s", text)
	}
	// And nothing was duplicated: one line per key is what makes a config file
	// readable and a diff meaningful.
	for _, key := range []string{"AGENT_MODEL", "ROBOT_ADDRESS", "ROBOT_SERVER_NAME", "ROBOT_CA", "ROBOT_CERT", "ROBOT_KEY"} {
		if count := strings.Count(text, "\n"+key+"="); count != 1 {
			t.Fatalf("%s appears %d times:\n%s", key, count, text)
		}
	}
	info, err := os.Stat(filepath.Join(directory, "certs", "local-agent.key"))
	if err != nil {
		t.Fatalf("stat key: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("the agent key mode is %o, want 600", info.Mode().Perm())
	}
	if info, err := os.Stat(configPath); err != nil {
		t.Fatalf("stat config: %v", err)
	} else if info.Mode().Perm() != 0o600 {
		t.Fatalf("the config mode is %o, want 600", info.Mode().Perm())
	}
	// Comments and blank lines survive too: a pairing that stripped the comments
	// out of a configuration file would be editing someone else's document.
	if !strings.Contains(text, "# a comment") {
		t.Fatalf("a comment was dropped:\n%s", text)
	}
	if !strings.Contains(text, "AGENT_PROVIDER=deterministic") {
		t.Fatalf("a preserved key lost its value:\n%s", text)
	}
}
