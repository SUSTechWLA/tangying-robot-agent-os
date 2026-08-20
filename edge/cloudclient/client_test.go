package cloudclient

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCompleteIntentClassifiesWorldNotReady(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"code":"WORLD_NOT_READY","message":"waiting for stable evidence"}`))
	}))
	defer server.Close()
	client := New(Config{BaseURL: server.URL, RobotID: "robot-1", DeviceToken: "device"})

	err := client.CompleteIntent(context.Background(), "task-1", 0, "robot-1")

	if !errors.Is(err, ErrWorldNotReady) {
		t.Fatalf("err=%v", err)
	}
}
