package fleet

import (
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

func TestModelAssistIsDeviceOnlyAndForcesCloudModel(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer cloud-secret" {
			t.Error("upstream model credential missing")
		}
		var request map[string]json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
		}
		if string(request["model"]) != `"cloud-large"` {
			t.Errorf("device controlled cloud model: %s", request["model"])
		}
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	assist, err := NewModelAssist(ModelAssistConfig{BaseURL: upstream.URL, APIKey: "cloud-secret", Model: "cloud-large"})
	if err != nil {
		t.Fatal(err)
	}
	authenticator, err := auth.New(auth.Options{OperatorUser: "op", OperatorPass: "password", DeviceCredentials: map[string]string{"robot-7": "device-secret"}})
	if err != nil {
		t.Fatal(err)
	}
	server := NewServer(nil, nil, WithAuthenticator(authenticator), WithModelAssist(assist))
	call := func(robot, token string) *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodPost, "/v1/assist/chat/completions", strings.NewReader(`{"model":"cloud-assist","messages":[{"role":"user","content":"help"}]}`))
		request.Header.Set("X-Robot-ID", robot)
		request.Header.Set("X-Device-Token", token)
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		return response
	}
	if response := call("robot-7", "device-secret"); response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"choices"`) {
		t.Fatalf("valid device response: %d %s", response.Code, response.Body.String())
	}
	if response := call("robot-8", "device-secret"); response.Code != http.StatusUnauthorized {
		t.Fatalf("different robot got %d", response.Code)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/assist/chat/completions", strings.NewReader(`{"messages":[]}`))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated request got %d", response.Code)
	}
}

func TestModelAssistStageAliasAndTokenLimitOverTLS(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Device-Token") != "" || r.Header.Get("X-Robot-ID") != "" {
			t.Error("device identity was forwarded to model server")
		}
		var request map[string]json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
		}
		if string(request["model"]) != `"planning-large"` || string(request["max_tokens"]) != "128" {
			t.Errorf("wrong cloud routing or cap: %v", request)
		}
		_, _ = io.WriteString(w, `{"choices":[{"message":{"content":"ok"}}]}`)
	}))
	defer upstream.Close()
	assist, err := NewModelAssist(ModelAssistConfig{BaseURL: upstream.URL, Model: "default-large",
		StageModels: map[string]string{"cloud-planning": "planning-large"}, MaxOutputTokens: 128})
	if err != nil {
		t.Fatal(err)
	}
	authenticator, err := auth.New(auth.Options{OperatorUser: "op", OperatorPass: "password",
		DeviceCredentials: map[string]string{"robot-7": "device-secret"},
		AssistCredentials: map[string]string{"robot-7": "assist-secret"}})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewTLSServer(NewServer(nil, nil, WithAuthenticator(authenticator), WithModelAssist(assist)).Handler())
	defer server.Close()
	caPath := filepath.Join(t.TempDir(), "fleet-ca.pem")
	if err := os.WriteFile(caPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	baseURL := server.URL + "/v1/assist"
	client, err := (modelroute.Assist{URL: baseURL, RobotID: "robot-7", DeviceToken: "assist-secret", CAFile: caPath}).ClientFor(modelroute.Endpoint{BaseURL: baseURL})
	if err != nil {
		t.Fatal(err)
	}
	response, err := client.Post(baseURL+"/chat/completions", "application/json", strings.NewReader(`{"model":"cloud-planning","max_tokens":999999,"messages":[{"role":"user","content":"plan"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(response.Body)
		t.Fatalf("status %d: %s", response.StatusCode, body)
	}
}

func TestModelAssistRejectsUnconfiguredAliasAndBadPayload(t *testing.T) {
	assist, err := NewModelAssist(ModelAssistConfig{BaseURL: "https://models.example/v1", Model: "large"})
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{
		`{"model":"unapproved","messages":[{"role":"user","content":"help"}]}`,
		`{"model":"cloud-assist","messages":[]}`,
		`{"model":"cloud-assist","messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"http://internal"}}]}]}`,
		`{"model":"cloud-assist","messages":[{"role":"user","content":"help"}],"stream":true}`,
		`{"model":"cloud-assist","messages":[{"role":"user","content":"help"}],"extra_body":{"dangerous":true}}`,
	} {
		response := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/v1/assist/chat/completions", strings.NewReader(body))
		assist.serveHTTP(response, request, "robot-7")
		if response.Code != http.StatusBadRequest {
			t.Errorf("body %s got status %d", body, response.Code)
		}
	}
}

func TestModelAssistPreservesCapacityForOtherRobots(t *testing.T) {
	assist, err := NewModelAssist(ModelAssistConfig{BaseURL: "https://models.example/v1", Model: "large", MaxConcurrent: 3})
	if err != nil {
		t.Fatal(err)
	}
	if !assist.acquire("robot-1") || !assist.acquire("robot-1") || assist.acquire("robot-1") {
		t.Fatal("one robot exceeded its two-slot limit")
	}
	if !assist.acquire("robot-2") {
		t.Fatal("other robot could not use remaining capacity")
	}
	assist.release("robot-1")
	assist.release("robot-1")
	assist.release("robot-2")
}
