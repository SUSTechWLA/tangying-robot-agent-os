package console_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
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

func TestCalibrationDocumentIsServedForInspectionAndEditing(t *testing.T) {
	// The calibration page is where somebody sees and edits what the robot was
	// measured to be. Serving only a progress summary left them reading "16 servos,
	// 2 cameras" without a single number behind it.
	path := filepath.Join(t.TempDir(), "calibration.json")
	document := map[string]any{
		"schemaVersion": "robot.calibration.v1", "robotId": "xlerobot-mujoco-tabletop",
		"adapterId": "mujoco", "source": "simulation", "updatedAtUnixMs": 1,
		"motors": map[string]any{
			"left_arm_gripper": map[string]any{"id": 6, "drive_mode": 0,
				"homing_offset": -12, "range_min": 940, "range_max": 3120},
		},
		"cameras": map[string]any{
			"head-rgbd": map[string]any{"sourceId": "r/head-rgbd", "width": 320, "height": 240,
				"intrinsics": map[string]any{"fx": 171.4, "fy": 171.4, "cx": 159.5, "cy": 119.5},
				"distortion": map[string]any{"model": "none", "coefficients": []any{}},
				"extrinsics": map[string]any{"parentLink": "head_tilt_link", "xyz": []float64{0.05, 0, 0.06}, "rpy": []float64{1.5708, 0, 0}},
			},
		},
		"geometry": map[string]any{"gripper": map[string]any{"openM": 0.081, "closedM": 0.0}},
		"safety":   map[string]any{"maxRelativeTargetDeg": 8.0, "maxActionChunkLength": 64, "maxLinearSpeedMPerS": 0.05, "maxAngularSpeedRadPerS": 0.2},
		"hash":     strings.Repeat("a", 64),
	}
	raw, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("TANGYING_CALIBRATION_DOCUMENT", path)
	response := httptest.NewRecorder()
	console.NewServer(nil, nil).Handler().ServeHTTP(response, httptest.NewRequest("GET", "/v1/calibration", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("%d %s", response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["available"] != true {
		t.Fatalf("expected the document, got %#v", payload)
	}
	documentOut, _ := payload["document"].(map[string]any)
	if documentOut["source"] != "simulation" {
		t.Fatalf("the source must survive so a simulation calibration is not read as measured: %#v", documentOut)
	}
	cameras, _ := documentOut["cameras"].(map[string]any)
	head, _ := cameras["head-rgbd"].(map[string]any)
	intrinsics, _ := head["intrinsics"].(map[string]any)
	if intrinsics["fx"] != 171.4 {
		t.Fatalf("the intrinsics must reach the page: %#v", intrinsics)
	}
}

func TestAMissingCalibrationDocumentIsReportedNotInvented(t *testing.T) {
	t.Setenv("TANGYING_CALIBRATION_DOCUMENT", filepath.Join(t.TempDir(), "absent.json"))
	response := httptest.NewRecorder()
	console.NewServer(nil, nil).Handler().ServeHTTP(response, httptest.NewRequest("GET", "/v1/calibration", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("%d", response.Code)
	}
	if !strings.Contains(response.Body.String(), `"available":false`) {
		t.Fatalf("a missing calibration must say so: %s", response.Body.String())
	}
}

// The calibration responses used to echo the absolute path of the file they read.
// Nothing consumes it — not the page, not a test — and it hands the operator's
// directory layout to any reader, so it is replaced by the two facts an operator
// actually needs: which configuration chose the file, and its name.
func TestCalibrationResponsesDoNotEchoTheOperatorFilesystem(t *testing.T) {
	directory := t.TempDir()
	statusPath := filepath.Join(directory, "session.status.json")
	if err := os.WriteFile(statusPath, []byte(`{"stage":"capture"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TANGYING_CALIBRATION_STATUS", statusPath)

	response := httptest.NewRecorder()
	console.NewServer(nil, nil).Handler().ServeHTTP(response,
		httptest.NewRequest("GET", "/v1/calibration/session", nil))
	if response.Code != 200 {
		t.Fatalf("status = %d", response.Code)
	}
	var payload map[string]any
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if _, leaked := payload["path"]; leaked {
		t.Fatalf("absolute path is still echoed: %v", payload["path"])
	}
	if payload["source"] != "env" {
		t.Fatalf("source = %v, want env", payload["source"])
	}
	if payload["file"] != "session.status.json" {
		t.Fatalf("file = %v", payload["file"])
	}
	body, _ := json.Marshal(payload)
	if strings.Contains(string(body), directory) {
		t.Fatalf("response still contains the directory: %s", body)
	}
}
