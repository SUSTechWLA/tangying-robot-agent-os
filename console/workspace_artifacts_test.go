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

func TestWorkspaceMapsExcludeForeignAndUnboundArtifacts(t *testing.T) {
	root := t.TempDir()
	t.Setenv(console.MapRootEnv, root)
	for name, id := range map[string]string{"own": "agv-1", "foreign": "uav-1", "legacy": ""} {
		dir := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Join(dir, "cloud"), 0700); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(map[string]any{"mapId": name, "robotId": id, "lodLevels": 1})
		if err := os.WriteFile(filepath.Join(dir, "manifest.json"), raw, 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "cloud", "lod0.bin"), []byte(id), 0600); err != nil {
			t.Fatal(err)
		}
	}
	handler := console.NewServer(nil, nil, console.WithWorkspaceIdentity("agv-1")).Handler()
	read := func(path string) *httptest.ResponseRecorder {
		t.Helper()
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		return response
	}
	list := read("/v1/maps")
	var body struct {
		Maps []struct {
			MapID string `json:"mapId"`
		} `json:"maps"`
	}
	if err := json.Unmarshal(list.Body.Bytes(), &body); err != nil || len(body.Maps) != 1 || body.Maps[0].MapID != "own" {
		t.Fatalf("list status=%d body=%s err=%v", list.Code, list.Body.String(), err)
	}
	for _, name := range []string{"foreign", "legacy"} {
		for _, suffix := range []string{"", "/cloud", "/cloud?lod=0", "/artifact/slam_session"} {
			if response := read("/v1/maps/" + name + suffix); response.Code != http.StatusNotFound {
				t.Fatalf("foreign artifact %s%s: %d %s", name, suffix, response.Code, response.Body.String())
			}
		}
	}
	if response := read("/v1/maps/own/cloud?lod=0"); response.Code != 200 || response.Body.String() != "agv-1" {
		t.Fatalf("own LOD: %d %s", response.Code, response.Body.String())
	}
}

func TestWorkspaceCalibrationFilesRequireMatchingRobotProvenance(t *testing.T) {
	for _, endpoint := range []string{"/v1/calibration/session", "/v1/calibration"} {
		for _, robotID := range []string{"agv-1", "uav-1", ""} {
			t.Run(endpoint+"/"+robotID, func(t *testing.T) {
				path := filepath.Join(t.TempDir(), "calibration.json")
				raw, _ := json.Marshal(map[string]any{"robotId": robotID, "privateParameter": "foreign-detail"})
				if err := os.WriteFile(path, raw, 0600); err != nil {
					t.Fatal(err)
				}
				t.Setenv("TANGYING_CALIBRATION_STATUS", path)
				t.Setenv("TANGYING_CALIBRATION_DOCUMENT", path)
				handler := console.NewServer(nil, nil, console.WithWorkspaceIdentity("agv-1")).Handler()
				response := httptest.NewRecorder()
				handler.ServeHTTP(response, httptest.NewRequest("GET", endpoint, nil))
				var body map[string]any
				if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				if response.Code != 200 || body["available"] != (robotID == "agv-1") {
					t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
				}
				if robotID != "agv-1" && strings.Contains(response.Body.String(), "foreign-detail") {
					t.Fatal("foreign calibration detail escaped the workspace")
				}
			})
		}
	}
}
