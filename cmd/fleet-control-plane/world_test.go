package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

func TestBuildWorldUsesConfiguredDurableSnapshot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	t.Setenv("FLEET_WORLD_SNAPSHOT_PATH", path)
	t.Setenv("FLEET_WORLD_ID", "test-world")
	ctx := context.Background()
	hub, closeHub, err := buildWorld(ctx)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	_, err = hub.Ingest(ctx, observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "robot-1-7", WorldID: "test-world",
		SourceID: "robot-1", SourceType: observation.SourceProprioception, SourceSequence: 7,
		ObservedAt: now, ReceivedAt: now, FrameID: "world", TransformRevision: "map-v1",
		Kind: observation.RobotStateUpsert, Payload: observation.RobotPayload{RobotID: "robot-1"},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "runtime", Version: "v1"},
	})
	if err != nil {
		closeHub()
		t.Fatal(err)
	}
	if err := closeHub(); err != nil {
		t.Fatal(err)
	}
	recovered, closeRecovered, err := buildWorld(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer closeRecovered()
	snapshot, err := recovered.Snapshot(ctx)
	if err != nil || snapshot.Revision != 1 || snapshot.Sources["robot-1"].SourceSequence != 7 || snapshot.Robots["robot-1"].Freshness != worldmodel.Stale {
		t.Fatalf("configured checkpoint not recovered: %#v %v", snapshot, err)
	}
}

func TestBuildWorldRejectsConfiguredCorruptSnapshot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	if err := os.WriteFile(path, []byte("interrupted write"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FLEET_WORLD_SNAPSHOT_PATH", path)
	if _, closeHub, err := buildWorld(context.Background()); err == nil {
		closeHub()
		t.Fatal("corrupt configured checkpoint silently reset")
	}
}
