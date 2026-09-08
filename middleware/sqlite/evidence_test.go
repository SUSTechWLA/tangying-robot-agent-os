package sqlite

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func evidenceSnapshot(taskID, captureID string, sequence uint64) telemetry.Snapshot {
	stamp := time.Date(2024, 1, 2, 3, 4, 5, 0, time.UTC) // historical evidence stays readable
	profile := &robotcontract.Profile{
		SchemaVersion: "robot.profile.v1", RobotID: "robot-1", AdapterID: "rgbd", AdapterVersion: "1",
		ModelID: "rgbd-rig", Embodiment: "sensor_rig", Joints: []robotcontract.Joint{},
		EndEffectors: []robotcontract.EndEffector{}, ActionLimits: map[string]robotcontract.ActionLimit{},
		Sensors: []robotcontract.Sensor{{SourceID: "head", SourceType: "rgbd_camera", FrameID: "optical", TransformRevision: "cal-1", MaxAgeMS: 2000}},
		Tools:   []string{"observe_scene", "emergency_stop"},
	}
	var frame bytes.Buffer
	picture := image.NewNRGBA(image.Rect(0, 0, 2, 2))
	picture.Set(0, 0, color.NRGBA{R: 255, A: 255})
	_ = png.Encode(&frame, picture)
	return telemetry.Snapshot{
		SchemaVersion: "telemetry.v1", ObservedAt: stamp, TaskID: taskID, StepID: "pick", RobotID: "robot-1", Adapter: "rgbd",
		RobotProfile: profile, Reconstruction: &robotcontract.Reconstruction{
			SchemaVersion: "scene.reconstruction.v1", RobotID: "robot-1", ObservationID: captureID,
			SourceID: "head", SourceType: "rgbd_camera", SourceFrameID: "optical", FrameID: "world", TransformRevision: "cal-1",
			ObservedAtUnixMS: stamp.UnixMilli(), Sequence: sequence, Units: "m", Points: [][]float64{{1, 2, 3}},
		}, Frame: frame.Bytes(), FrameMediaType: "image/png", DepthFrame: frame.Bytes(), DepthFrameMediaType: "image/png",
	}
}

func openEvidenceStore(t *testing.T, path string) *Store {
	t.Helper()
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store
}

func createEvidenceTask(t *testing.T, store *Store, id string) {
	t.Helper()
	if err := store.Create(context.Background(), &tasks.Task{ID: id}); err != nil {
		t.Fatal(err)
	}
}

func TestEvidencePersistsExactHistoricalCaptureAndHashesAcrossRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "evidence.db")
	store := openEvidenceStore(t, path)
	createEvidenceTask(t, store, "task-1")
	snapshot := evidenceSnapshot("task-1", "robot-1/head-rgbd/1", 1)
	record, err := store.RecordEvidence(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}
	expected := sha256.Sum256(snapshot.Frame)
	if record.RGBSHA256 != hex.EncodeToString(expected[:]) || record.CaptureID != snapshot.Reconstruction.ObservationID {
		t.Fatalf("record did not retain source identity/hash: %#v", record)
	}
	snapshot.Frame[0] = 0 // storage owns its bytes
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened := openEvidenceStore(t, path)
	actual, err := reopened.Evidence(context.Background(), "task-1", record.ID)
	if err != nil {
		t.Fatal(err)
	}
	if actual.Expired || len(actual.Snapshot) == 0 || actual.RGB[0] != 137 || actual.ObservedAtUnixMS != time.Date(2024, 1, 2, 3, 4, 5, 0, time.UTC).UnixMilli() {
		t.Fatalf("historical capture changed or expired: %#v", actual)
	}
	list, err := reopened.ListEvidence(context.Background(), "task-1", 50, 0)
	if err != nil || len(list) != 1 || len(list[0].RGB) != 0 || len(list[0].Snapshot) != 0 {
		t.Fatalf("list must return only metadata: %#v, %v", list, err)
	}
}

func TestEvidenceScopeAndImmutableReplay(t *testing.T) {
	store := openEvidenceStore(t, filepath.Join(t.TempDir(), "evidence.db"))
	createEvidenceTask(t, store, "task-1")
	createEvidenceTask(t, store, "task-2")
	snapshot := evidenceSnapshot("task-1", "shared/capture", 1)
	record, err := store.RecordEvidence(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := store.RecordEvidence(context.Background(), snapshot)
	if err != nil || replay.ID != record.ID || replay.RecordIndex != record.RecordIndex {
		t.Fatalf("replay=%#v err=%v", replay, err)
	}
	if _, err := store.Evidence(context.Background(), "task-2", record.ID); !errors.Is(err, tasks.ErrEvidenceNotFound) {
		t.Fatalf("cross-task read: %v", err)
	}
	snapshot.Reconstruction.Points[0][0] = 99
	if _, err := store.RecordEvidence(context.Background(), snapshot); !errors.Is(err, tasks.ErrEvidenceConflict) {
		t.Fatalf("rewrite should conflict: %v", err)
	}
	snapshot.StepID = "verify"
	next, err := store.RecordEvidence(context.Background(), snapshot)
	if err != nil || next.ID == record.ID {
		t.Fatalf("same capture in separate step must have distinct identity: %v", err)
	}
	list, err := store.ListEvidence(context.Background(), "task-1", 1, next.RecordIndex)
	if err != nil || len(list) != 1 || list[0].ID != record.ID {
		t.Fatalf("task-scoped cursor=%#v err=%v", list, err)
	}
}

func TestEvidenceRetentionLeavesExpiredMetadataAndNeverRehydratesOldReplay(t *testing.T) {
	store := openEvidenceStore(t, filepath.Join(t.TempDir(), "evidence.db"))
	createEvidenceTask(t, store, "task-1")
	first, err := store.RecordEvidence(context.Background(), evidenceSnapshot("task-1", "first", 1))
	if err != nil {
		t.Fatal(err)
	}
	for index := 2; index <= tasks.MaxEvidenceCaptures+1; index++ {
		if _, err := store.RecordEvidence(context.Background(), evidenceSnapshot("task-1", fmt.Sprint(index), uint64(index))); err != nil {
			t.Fatal(err)
		}
	}
	old, err := store.Evidence(context.Background(), "task-1", first.ID)
	if err != nil || !old.Expired || len(old.RGB) != 0 || len(old.Depth) != 0 || len(old.Snapshot) != 0 || old.RGBSHA256 == "" {
		t.Fatalf("expired=%#v err=%v", old, err)
	}
	replay, err := store.RecordEvidence(context.Background(), evidenceSnapshot("task-1", "first", 1))
	if err != nil || !replay.Expired {
		t.Fatalf("old replay incorrectly resurrected raw bytes: %#v %v", replay, err)
	}
	var count int
	if err := store.db.QueryRow(`SELECT count(*) FROM observation_evidence WHERE expired = 0`).Scan(&count); err != nil || count != tasks.MaxEvidenceCaptures {
		t.Fatalf("retained=%d err=%v", count, err)
	}
}

func TestEvidenceRejectsUnlinkedMismatchedAndOversizedCaptures(t *testing.T) {
	store := openEvidenceStore(t, filepath.Join(t.TempDir(), "evidence.db"))
	createEvidenceTask(t, store, "task-1")
	for _, change := range []func(*telemetry.Snapshot){
		func(s *telemetry.Snapshot) { s.TaskID = "" },
		func(s *telemetry.Snapshot) { s.StepID = "" },
		func(s *telemetry.Snapshot) { s.ObservedAt = s.ObservedAt.Add(time.Second) },
		func(s *telemetry.Snapshot) { s.RobotID = "different-robot" },
		func(s *telemetry.Snapshot) { s.FrameMediaType = "text/html" },
		func(s *telemetry.Snapshot) { s.Frame = make([]byte, tasks.MaxEvidenceImageBytes+1) },
		func(s *telemetry.Snapshot) { s.Reconstruction = nil },
	} {
		snapshot := evidenceSnapshot("task-1", "bad", 1)
		change(&snapshot)
		if _, err := store.RecordEvidence(context.Background(), snapshot); !errors.Is(err, tasks.ErrEvidenceInvalid) {
			t.Fatalf("invalid snapshot was accepted: %v", err)
		}
	}
	if _, err := store.RecordEvidence(context.Background(), evidenceSnapshot("unknown-task", "capture", 1)); !errors.Is(err, tasks.ErrTaskNotFound) {
		t.Fatalf("unknown task accepted: %v", err)
	}
}

func TestEvidenceDetectsPersistedContentCorruption(t *testing.T) {
	store := openEvidenceStore(t, filepath.Join(t.TempDir(), "evidence.db"))
	createEvidenceTask(t, store, "task-1")
	record, err := store.RecordEvidence(context.Background(), evidenceSnapshot("task-1", "capture", 1))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.Exec(`UPDATE observation_evidence SET rgb = ? WHERE id = ?`, []byte("changed"), record.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Evidence(context.Background(), "task-1", record.ID); !errors.Is(err, tasks.ErrEvidenceCorrupt) {
		t.Fatalf("corrupted raw evidence returned: %v", err)
	}
}

func TestEvidenceByteBudgetExpiresOldRawBeforeCaptureCountIsReached(t *testing.T) {
	store := openEvidenceStore(t, filepath.Join(t.TempDir(), "evidence.db"))
	createEvidenceTask(t, store, "task-1")
	first, err := store.RecordEvidence(context.Background(), evidenceSnapshot("task-1", "first", 1))
	if err != nil {
		t.Fatal(err)
	}
	latest, err := store.RecordEvidence(context.Background(), evidenceSnapshot("task-1", "latest", 2))
	if err != nil {
		t.Fatal(err)
	}
	tx, err := store.db.BeginTx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	budget := latest.SnapshotBytes + latest.RGBBytes + latest.DepthBytes + 1
	if err := expireEvidence(context.Background(), tx, time.Now(), tasks.MaxEvidenceCaptures, budget); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	old, err := store.Evidence(context.Background(), "task-1", first.ID)
	if err != nil || !old.Expired {
		t.Fatalf("byte budget did not expire oldest content: %#v %v", old, err)
	}
	kept, err := store.Evidence(context.Background(), "task-1", latest.ID)
	if err != nil || kept.Expired || len(kept.RGB) == 0 {
		t.Fatalf("newest content was lost: %#v %v", kept, err)
	}
}

func TestEvidenceSurvivesBriefConcurrentTaskDatabaseWrite(t *testing.T) {
	store := openEvidenceStore(t, filepath.Join(t.TempDir(), "evidence.db"))
	createEvidenceTask(t, store, "task-1")
	tx, err := store.db.BeginTx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`UPDATE tasks SET request = 'operator update' WHERE id = 'task-1'`); err != nil {
		t.Fatal(err)
	}
	committed := make(chan error, 1)
	go func() {
		time.Sleep(30 * time.Millisecond)
		committed <- tx.Commit()
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	record, err := store.RecordEvidence(ctx, evidenceSnapshot("task-1", "during-operator-update", 1))
	if commitErr := <-committed; commitErr != nil {
		t.Fatal(commitErr)
	}
	if err != nil || record.ID == "" {
		t.Fatalf("brief task writer lost capture evidence: %v", err)
	}
}
