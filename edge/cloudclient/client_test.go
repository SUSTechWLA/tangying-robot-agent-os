package cloudclient

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
)

func TestCompleteIntentClassifiesWorldNotReady(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"code":"WORLD_NOT_READY","message":"waiting for stable evidence"}`))
	}))
	defer server.Close()
	client, err := New(Config{BaseURL: server.URL, RobotID: "robot-1", DeviceToken: "device"})
	if err != nil {
		t.Fatal(err)
	}

	err = client.CompleteIntent(context.Background(), "task-1", 0, "robot-1")

	if !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("err=%v", err)
	}
}

func TestRevisionCompletionSendsExactClaimIdentity(t *testing.T) {
	var body map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer server.Close()
	client, err := New(Config{BaseURL: server.URL, RobotID: "robot-2", DeviceToken: "device"})
	if err != nil {
		t.Fatal(err)
	}
	node := &coordinator.IntentNode{Index: 1, TaskRevision: 2, AggregateVersion: 7,
		StepID: "handoff/receiver", CommandID: "intent-command-2", FencingToken: 9}

	if err := client.CompleteIntentRevision(context.Background(), "task-1", node, "robot-2"); err != nil {
		t.Fatal(err)
	}
	if body["robotId"] != "robot-2" || body["taskRevision"] != float64(2) ||
		body["aggregateVersion"] != float64(7) || body["stepId"] != "handoff/receiver" ||
		body["commandId"] != "intent-command-2" || body["fencingToken"] != float64(9) {
		t.Fatalf("completion body=%#v", body)
	}
}

func TestCloudClientRejectsInvalidCAAndRemotePlaintext(t *testing.T) {
	if _, err := New(Config{BaseURL: "http://fleet.example/v1", RobotID: "robot-1", DeviceToken: "secret"}); err == nil {
		t.Fatal("remote plaintext fleet was accepted")
	}
	if _, err := New(Config{BaseURL: "https://fleet.example", RobotID: "robot-1", DeviceToken: "secret", CAFile: "/missing/ca.pem"}); err == nil {
		t.Fatal("missing fleet CA was silently ignored")
	}
	if _, err := New(Config{BaseURL: "https://fleet.example", RobotID: "robot-1", DeviceToken: "secret", CAFile: "client_test.go"}); err == nil {
		t.Fatal("invalid fleet CA was silently ignored")
	}
}

func TestCloudClientDoesNotForwardDeviceTokenAcrossRedirect(t *testing.T) {
	redirected := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		redirected = true
		if r.Header.Get("X-Device-Token") != "" {
			t.Error("device token escaped on redirect")
		}
	}))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	client, err := New(Config{BaseURL: server.URL, RobotID: "robot-1", DeviceToken: "secret"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.NextTask(context.Background(), "robot-1"); err == nil {
		t.Fatal("redirect was treated as a task")
	}
	if redirected {
		t.Fatal("cloud client followed a redirect")
	}
}
