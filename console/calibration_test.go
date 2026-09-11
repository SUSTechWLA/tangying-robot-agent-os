package console_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
)

func calibrationServer(t *testing.T, path string) http.Handler {
	t.Helper()
	t.Setenv("TANGYING_CALIBRATION_STATUS", path)
	return console.NewServer(nil, nil).Handler()
}

func TestCalibrationSessionIsServedFromTheWizardSnapshot(t *testing.T) {
	// The wizard owns the plan and the wording; the console only renders it, so
	// the payload it writes must reach the browser unchanged.
	path := filepath.Join(t.TempDir(), "session.status.json")
	snapshot := map[string]any{
		"robotId": "xlerobot-01", "completed": 2, "total": 20,
		"next": map[string]any{"id": "zero:left_arm_elbow_flex", "title": "左臂 · 肘部",
			"instruction": "用手把小臂伸直，与地面平行，保持不动，然后确认。"},
		"summary": "已完成 2/20 步；下一步：左臂 · 肘部。",
	}
	raw, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}

	response := httptest.NewRecorder()
	calibrationServer(t, path).ServeHTTP(response, httptest.NewRequest("GET", "/v1/calibration/session", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("%d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["available"] != true {
		t.Fatalf("expected an available session, got %#v", payload)
	}
	if payload["summary"] != snapshot["summary"] {
		t.Fatalf("summary was altered: %#v", payload["summary"])
	}
	next, _ := payload["next"].(map[string]any)
	if next["instruction"] != "用手把小臂伸直，与地面平行，保持不动，然后确认。" {
		t.Fatalf("instruction was altered: %#v", next)
	}
}

func TestCalibrationSessionWithoutASnapshotIsNotAnError(t *testing.T) {
	// Most deployments never open the wizard; the console has to say so rather
	// than offering an empty card flow or a failure.
	missing := filepath.Join(t.TempDir(), "absent.status.json")
	response := httptest.NewRecorder()
	calibrationServer(t, missing).ServeHTTP(response, httptest.NewRequest("GET", "/v1/calibration/session", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("%d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["available"] != false {
		t.Fatalf("expected an unavailable session, got %#v", payload)
	}
	if payload["reason"] == nil || payload["reason"] == "" {
		t.Fatal("an unavailable session must explain itself")
	}
}

func TestCalibrationSessionReportsAMalformedSnapshotInsteadOfGuessing(t *testing.T) {
	path := filepath.Join(t.TempDir(), "broken.status.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	calibrationServer(t, path).ServeHTTP(response, httptest.NewRequest("GET", "/v1/calibration/session", nil))
	if response.Code == http.StatusOK {
		t.Fatalf("a corrupt snapshot must not be served as progress: %s", response.Body.String())
	}
}
