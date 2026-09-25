package main

import (
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

func TestParseConfigCarriesRobotMTLSFiles(t *testing.T) {
	config, err := parseConfig([]string{
		"--robot", "xlerobot.local:50051",
		"--robot-ca", "/etc/tangying/certs/ca.pem",
		"--robot-cert", "/etc/tangying/certs/mac.pem",
		"--robot-key", "/etc/tangying/certs/mac-key.pem",
		"--robot-server-name", "xlerobot.local",
	})
	if err != nil {
		t.Fatal(err)
	}
	if config.robotCA != "/etc/tangying/certs/ca.pem" {
		t.Fatalf("robotCA = %q", config.robotCA)
	}
	if config.robotCert != "/etc/tangying/certs/mac.pem" || config.robotKey != "/etc/tangying/certs/mac-key.pem" {
		t.Fatalf("client certificate files were not preserved: %#v", config)
	}
	if config.robotServerName != "xlerobot.local" {
		t.Fatalf("robotServerName = %q", config.robotServerName)
	}
}

func TestParseConfigLoadsKnownValuesFromFile(t *testing.T) {
	path := t.TempDir() + "/local.env"
	content := []byte("LOCAL_LISTEN=127.0.0.1:8787\nROBOT_ADDRESS=xlerobot.local:50051\nROBOT_SERVER_NAME=xlerobot.local\nROBOT_CA=/certs/ca.crt\nROBOT_CERT=/certs/client.crt\nROBOT_KEY=/certs/client.key\nAGENT_PROVIDER=openai\nAGENT_BASE_URL=https://llm.example/v1\nAGENT_API_KEY=secret\nAGENT_MODEL=robot-model\nAGENT_ORCHESTRATION_SAMPLES=3\n")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	config, err := parseConfig([]string{"--config", path})
	if err != nil {
		t.Fatal(err)
	}
	if config.listen != "127.0.0.1:8787" || config.robotAddress != "xlerobot.local:50051" {
		t.Fatalf("network config = %#v", config)
	}
	if config.robotCA != "/certs/ca.crt" || config.robotCert != "/certs/client.crt" || config.robotKey != "/certs/client.key" {
		t.Fatalf("TLS config = %#v", config)
	}
	if config.robotServerName != "xlerobot.local" || config.llmProvider != "openai" {
		t.Fatalf("runtime config = %#v", config)
	}
	if config.llmBaseURL != "https://llm.example/v1" || config.llmAPIKey != "secret" || config.llmModel != "robot-model" || config.llmSamples != 3 {
		t.Fatalf("LLM config = %#v", config)
	}
}

func TestOrinConfigUsesIndependentModelStages(t *testing.T) {
	path := t.TempDir() + "/edge.env"
	content := "LOCAL_ROBOT_ID=robot-7\nAGENT_PROVIDER=deterministic\n" +
		"AGENT_INTENT_PROVIDER=openai\nAGENT_INTENT_BASE_URL=http://127.0.0.1:8000/v1\nAGENT_INTENT_MODEL=quantized\n" +
		"AGENT_PLANNING_PROVIDER=openai\nAGENT_PLANNING_BASE_URL=https://fleet.example/v1/assist\nAGENT_PLANNING_MODEL=cloud-assist\n" +
		"AGENT_CLOUD_ASSIST_URL=https://fleet.example/v1/assist\nAGENT_CLOUD_ASSIST_DEVICE_TOKEN=robot-7-secret\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	configuration, err := parseConfig([]string{"--config", path})
	if err != nil {
		t.Fatal(err)
	}
	if configuration.robotID != "robot-7" || configuration.model("INTENT").Model != "quantized" || configuration.model("PLANNING").Model != "cloud-assist" || configuration.model("RECOVERY").Provider != "deterministic" {
		t.Fatalf("wrong edge model routing: %+v", configuration)
	}
	if _, _, err := configuration.taskModels(); err != nil {
		t.Fatal(err)
	}
	if _, err := configuration.recoveryDecider(); err != nil {
		t.Fatal(err)
	}
}

func TestDeploymentPreflightRejectsMissingIdentityAndMTLS(t *testing.T) {
	configuration := config{robotID: "robot-local", robotAddress: "127.0.0.1:50051"}
	if err := checkDeploymentConfig(configuration); err == nil {
		t.Fatal("default robot identity passed deployment preflight")
	}
	configuration.robotID = "robot-7"
	configuration.robotServerName = "robot-7"
	if err := checkDeploymentConfig(configuration); err == nil {
		t.Fatal("missing Runtime mTLS passed deployment preflight")
	}
}

func TestCloudAssistDoesNotReceiveInheritedModelAPIKey(t *testing.T) {
	var called atomic.Bool
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called.Store(true)
		if r.Header.Get("Authorization") != "" || r.Header.Get("X-Robot-ID") != "robot-7" || r.Header.Get("X-Device-Token") != "device-secret" {
			t.Errorf("wrong cloud assist credentials: %v", r.Header)
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{}"}}]}`))
	}))
	defer server.Close()
	caPath := filepath.Join(t.TempDir(), "fleet-ca.pem")
	if err := os.WriteFile(caPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	endpoint := server.URL + "/v1/assist"
	configuration := config{robotID: "robot-7", models: map[string]modelroute.Endpoint{
		modelroute.Intent:   {Provider: "deterministic"},
		modelroute.Planning: {Provider: "openai", BaseURL: endpoint, APIKey: "inherited-local-model-key", Model: "cloud-assist"},
	}, assist: modelroute.Assist{URL: endpoint, RobotID: "robot-7", DeviceToken: "device-secret", CAFile: caPath}}
	_, planner, err := configuration.taskModels()
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := planner.Plan("把红色杯子放进右侧收纳盒", parsed, orchestration.World{}); err != nil {
		t.Fatal(err)
	}
	if !called.Load() {
		t.Fatal("cloud assist was not called")
	}
}

func TestParseConfigRejectsRemovedCloudURL(t *testing.T) {
	path := t.TempDir() + "/local.env"
	if err := os.WriteFile(path, []byte("CLOUD_URL=https://obsolete.example\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := parseConfig([]string{"--config", path}); err == nil {
		t.Fatal("obsolete CLOUD_URL was accepted")
	}
}

func TestDefaultDataDirUsesNativeLaptopConventions(t *testing.T) {
	home := filepath.Join("home", "operator")
	if got := defaultDataDirFor("darwin", home); got != filepath.Join(home, "Library", "Application Support", "TangyingRobotAgent") {
		t.Fatalf("darwin data directory = %q", got)
	}
	if got := defaultDataDirFor("linux", home); got != filepath.Join(home, ".local", "share", "tangying-robot-agent-os") {
		t.Fatalf("linux data directory = %q", got)
	}
}

// The default configuration file has to be the one the installer and the pairing
// script write.
//
// It used to be "" — no file at all — so `make build && ./bin/local-agent`, the
// command the cold-start guide gives, silently targeted 127.0.0.1:50051 in
// plaintext and ignored the address and certificates pairing had just deployed.
// The agent could not see the robot it had been paired with, and nothing said so.
func TestTheDefaultConfigurationPathIsTheOnePairingWrites(t *testing.T) {
	t.Setenv("ROBOT_AGENT_CONFIG_DIR", "")
	t.Setenv("XDG_CONFIG_HOME", "")

	// Asserted through findConfigPath, not defaultConfigPath: a helper that
	// returns the right path is worth nothing if nothing calls it. An earlier
	// version of this test did exactly that and passed while the wiring was
	// reverted.
	path, err := findConfigPath(nil)
	if err != nil {
		t.Fatalf("find config: %v", err)
	}
	if path == "" {
		t.Fatal("no default configuration path, so a paired agent reads nothing")
	}
	if filepath.Base(path) != "local.env" {
		t.Fatalf("default config path = %q, want the local.env the installer writes", path)
	}
	if runtime.GOOS != "darwin" {
		if !strings.Contains(path, "tangying-robot-agent-os") {
			t.Fatalf("default config path = %q, want it under the product's config directory", path)
		}
	}
}

// An explicit override is honoured, because the operators of both scripts can set
// it and the agent has to look in the same place they wrote to.
func TestTheConfiguredDirectoryWins(t *testing.T) {
	t.Setenv("ROBOT_AGENT_CONFIG_DIR", "/tmp/example-config")
	got, err := findConfigPath(nil)
	if err != nil {
		t.Fatalf("find config: %v", err)
	}
	if got != "/tmp/example-config/local.env" {
		t.Fatalf("default config path = %q, want the configured directory", got)
	}
}

// An explicit --config still wins over everything.
func TestAnExplicitConfigStillWins(t *testing.T) {
	t.Setenv("ROBOT_AGENT_CONFIG_DIR", "/tmp/example-config")
	path, err := findConfigPath([]string{"--config", "/tmp/other.env"})
	if err != nil {
		t.Fatalf("find config: %v", err)
	}
	if path != "/tmp/other.env" {
		t.Fatalf("config path = %q, want the explicit one", path)
	}
}

// A missing file is not an error: defaults still apply, and an agent that refused
// to start because it had not been paired yet would be unusable out of the box.
func TestAMissingDefaultConfigIsNotAnError(t *testing.T) {
	t.Setenv("ROBOT_AGENT_CONFIG_DIR", t.TempDir())
	path, err := findConfigPath(nil)
	if err != nil {
		t.Fatalf("find config: %v", err)
	}
	values, err := readConfigFile(path)
	if err != nil {
		t.Fatalf("a missing default config was an error: %v", err)
	}
	if len(values) != 0 {
		t.Fatalf("values = %#v, want none", values)
	}
}
