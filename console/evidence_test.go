package console_test

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func historicalSnapshot(taskID string) telemetry.Snapshot {
	stamp := time.Date(2023, 1, 1, 0, 0, 0, 0, time.UTC)
	var picture bytes.Buffer
	_ = png.Encode(&picture, image.NewNRGBA(image.Rect(0, 0, 2, 2)))
	return telemetry.Snapshot{
		SchemaVersion: "telemetry.v1", TaskID: taskID, StepID: "verify", ObservedAt: stamp, RobotID: "robot-1", Adapter: "rgbd",
		RobotProfile: &robotcontract.Profile{SchemaVersion: "robot.profile.v1", RobotID: "robot-1", AdapterID: "rgbd", AdapterVersion: "1", ModelID: "rgbd", Embodiment: "sensor_rig",
			Sensors: []robotcontract.Sensor{{SourceID: "head", SourceType: "rgbd_camera", FrameID: "optical", TransformRevision: "cal-1", MaxAgeMS: 2000}}, Tools: []string{"observe_scene", "emergency_stop"}},
		Reconstruction: &robotcontract.Reconstruction{SchemaVersion: "scene.reconstruction.v1", RobotID: "robot-1", ObservationID: "robot-1/head/1", SourceID: "head", SourceType: "rgbd_camera", SourceFrameID: "optical", FrameID: "world", TransformRevision: "cal-1", ObservedAtUnixMS: stamp.UnixMilli(), Sequence: 1, Units: "m", Points: [][]float64{{1, 2, 3}}},
		Frame:          picture.Bytes(), FrameMediaType: "image/png", DepthFrame: picture.Bytes(), DepthFrameMediaType: "image/png",
	}
}

func TestHistoricalEvidenceAPIIsTaskScopedAndDoesNotApplyLiveFreshness(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	first, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "rgbd")
	if err != nil {
		t.Fatal(err)
	}
	second, err := service.Create(context.Background(), "把蓝色瓶子放进左侧收纳盒", "rgbd")
	if err != nil {
		t.Fatal(err)
	}
	snapshot := historicalSnapshot(first.ID)
	record, err := store.RecordEvidence(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}, console.WithEvidence(store)).Handler())
	defer server.Close()
	base := server.URL + "/v1/tasks/" + first.ID + "/observations"
	response, err := http.Get(base)
	if err != nil {
		t.Fatal(err)
	}
	var listing struct {
		TaskID     string                 `json:"taskId"`
		Historical bool                   `json:"historical"`
		Records    []tasks.EvidenceRecord `json:"records"`
	}
	if err := json.NewDecoder(response.Body).Decode(&listing); err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK || !listing.Historical || len(listing.Records) != 1 || listing.Records[0].ID != record.ID || len(listing.Records[0].Snapshot) != 0 {
		t.Fatalf("list=%#v status=%d", listing, response.StatusCode)
	}
	response, err = http.Get(base + "/" + record.ID)
	if err != nil {
		t.Fatal(err)
	}
	var actual tasks.EvidenceRecord
	if err := json.NewDecoder(response.Body).Decode(&actual); err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK || !actual.Historical || actual.ObservedAtUnixMS != snapshot.ObservedAt.UnixMilli() || len(actual.Snapshot) == 0 {
		t.Fatalf("old capture should remain readable: %#v", actual)
	}
	for _, kind := range []string{"rgb", "depth"} {
		response, err = http.Get(base + "/" + record.ID + "/" + kind)
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(response.Body)
		response.Body.Close()
		if err != nil || response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "image/png" || !bytes.Equal(data, snapshot.Frame) {
			t.Fatalf("historical %s response=%d %v", kind, response.StatusCode, err)
		}
		assertStatus(t, server, http.MethodGet, "/v1/tasks/"+second.ID+"/observations/"+record.ID+"/"+kind, "", http.StatusNotFound)
	}
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+second.ID+"/observations/"+record.ID, "", http.StatusNotFound)
	assertStatus(t, server, http.MethodGet, "/v1/tasks/unknown/observations", "", http.StatusNotFound)
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+first.ID+"/observations?limit=0", "", http.StatusBadRequest)
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+first.ID+"/observations?before=-1", "", http.StatusBadRequest)
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+first.ID+"/observations/%2e%2e%2fsecret/rgb", "", http.StatusNotFound)
}

type expiredEvidenceStore struct{ record tasks.EvidenceRecord }

func (s expiredEvidenceStore) RecordEvidence(context.Context, telemetry.Snapshot) (tasks.EvidenceRecord, error) {
	panic("read-only API")
}
func (s expiredEvidenceStore) ListEvidence(context.Context, string, int, int64) ([]tasks.EvidenceRecord, error) {
	return []tasks.EvidenceRecord{s.record}, nil
}
func (s expiredEvidenceStore) Evidence(context.Context, string, string) (tasks.EvidenceRecord, error) {
	return s.record, nil
}

func TestExpiredHistoricalImageReturnsGoneWithoutSubstitutingLiveFrame(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "rgbd")
	if err != nil {
		t.Fatal(err)
	}
	service.PublishTelemetry(context.Background(), historicalSnapshot(task.ID))
	record := tasks.EvidenceRecord{ID: "expired", TaskID: task.ID, Expired: true, Historical: true, RGBBytes: 100, DepthBytes: 100}
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}, console.WithEvidence(expiredEvidenceStore{record})).Handler())
	defer server.Close()
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+task.ID+"/observations/expired", "", http.StatusOK)
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+task.ID+"/observations/expired/rgb", "", http.StatusGone)
	assertStatus(t, server, http.MethodGet, "/v1/tasks/"+task.ID+"/observations/expired/depth", "", http.StatusGone)
}
