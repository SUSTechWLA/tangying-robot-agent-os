package modelroute

import (
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStageOverridesAndExplicitDeterministic(t *testing.T) {
	values := map[string]string{
		"AGENT_PROVIDER": "openai", "AGENT_BASE_URL": "http://127.0.0.1:8000/v1", "AGENT_MODEL": "quantized-small",
		"AGENT_PLANNING_BASE_URL": "https://fleet.example/v1/assist", "AGENT_PLANNING_MODEL": "cloud-large",
		"AGENT_RECOVERY_PROVIDER": "deterministic",
	}
	intent := Resolve(values, Intent)
	planning := Resolve(values, Planning)
	recovery := Resolve(values, Recovery)
	if intent.Model != "quantized-small" || planning.Model != "cloud-large" || planning.BaseURL != "https://fleet.example/v1/assist" || recovery.Provider != "deterministic" {
		t.Fatalf("unexpected stage resolution: intent=%+v planning=%+v recovery=%+v", intent, planning, recovery)
	}
	for _, endpoint := range []Endpoint{intent, planning, recovery} {
		if err := endpoint.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	if err := (Endpoint{Provider: "openai", Model: "missing-url"}).Validate(); err == nil {
		t.Fatal("incomplete model configuration was accepted")
	}
}

func TestEmptyComposeOverrideInheritsLegacyModel(t *testing.T) {
	t.Setenv("AGENT_PROVIDER", "openai")
	t.Setenv("AGENT_BASE_URL", "http://127.0.0.1:8000/v1")
	t.Setenv("AGENT_MODEL", "legacy")
	t.Setenv("AGENT_INTENT_PROVIDER", "")
	t.Setenv("AGENT_INTENT_BASE_URL", "")
	t.Setenv("AGENT_INTENT_MODEL", "")
	endpoint := Environment(Intent)
	if endpoint.Provider != "openai" || endpoint.BaseURL != "http://127.0.0.1:8000/v1" || endpoint.Model != "legacy" {
		t.Fatalf("empty compose values hid legacy model: %+v", endpoint)
	}
}

func TestChangingStageURLCannotInheritDefaultKeyOrModel(t *testing.T) {
	values := map[string]string{"AGENT_PROVIDER": "openai", "AGENT_BASE_URL": "https://old.example/v1",
		"AGENT_MODEL": "old-large", "AGENT_API_KEY": "old-secret",
		"AGENT_PLANNING_BASE_URL": "http://127.0.0.1:8000/v1"}
	endpoint := Resolve(values, Planning)
	if endpoint.APIKey != "" || endpoint.Model != "" || endpoint.Validate() == nil {
		t.Fatalf("new endpoint inherited old credentials or model: %+v", endpoint)
	}
	values["AGENT_PLANNING_MODEL"] = "local-quantized"
	endpoint = Resolve(values, Planning)
	if endpoint.APIKey != "" || endpoint.Model != "local-quantized" || endpoint.Validate() != nil {
		t.Fatalf("keyless local endpoint was not accepted: %+v", endpoint)
	}
	t.Setenv("AGENT_PROVIDER", "openai")
	t.Setenv("AGENT_BASE_URL", "https://old.example/v1")
	t.Setenv("AGENT_MODEL", "old-large")
	t.Setenv("AGENT_API_KEY", "old-secret")
	t.Setenv("AGENT_PLANNING_BASE_URL", "http://127.0.0.1:8000/v1")
	t.Setenv("AGENT_PLANNING_MODEL", "local-quantized")
	t.Setenv("AGENT_PLANNING_API_KEY", "") // Compose injects empty values.
	endpoint = Environment(Planning)
	if endpoint.APIKey != "" || endpoint.Model != "local-quantized" {
		t.Fatalf("Compose route inherited old secret: %+v", endpoint)
	}
}

func TestCloudAssistClientUsesDeviceIdentity(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Robot-ID") != "robot-7" || r.Header.Get("X-Device-Token") != "device-secret" {
			t.Errorf("missing device identity: %v", r.Header)
		}
		_, _ = io.WriteString(w, `{"ok":true}`)
	}))
	defer server.Close()
	caPath := filepath.Join(t.TempDir(), "fleet-ca.pem")
	if err := os.WriteFile(caPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	client, err := (Assist{URL: server.URL + "/v1/assist", RobotID: "robot-7", DeviceToken: "device-secret", CAFile: caPath}).ClientFor(Endpoint{BaseURL: server.URL + "/v1/assist"})
	if err != nil {
		t.Fatal(err)
	}
	response, err := client.Post(server.URL+"/v1/assist/chat/completions", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status %d", response.StatusCode)
	}
	if _, err := (Assist{URL: "http://fleet.example/v1/assist", RobotID: "robot-7", DeviceToken: "secret"}).ClientFor(Endpoint{BaseURL: "http://fleet.example/v1/assist"}); err == nil {
		t.Fatal("plaintext cloud assist was accepted")
	}
}
