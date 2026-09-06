package worldmodel

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
)

func TestCheckpointRestoreMarksRobotsResourcesAndSourcesStale(t *testing.T) {
	now := time.Now().UTC()
	p := NewProjector("world-test", time.Minute)
	robot := entityObservation(1, "robot", 0, "", now)
	robot.Kind = observation.RobotStateUpsert
	robot.Payload = observation.RobotPayload{RobotID: "robot-1", State: map[string]float64{"joint": 0.25}}
	resource := entityObservation(2, "resource", 0, "", now)
	resource.SourceType = observation.SourceToolResultEvidence
	resource.Kind = observation.ResourceUpsert
	resource.Payload = observation.ResourcePayload{ResourceID: "object", Owner: "robot-1", FencingToken: 3}
	for _, event := range []observation.Envelope{robot, resource} {
		if _, _, err := p.Apply(event); err != nil {
			t.Fatal(err)
		}
	}
	restored, err := RestoreProjector("world-test", time.Minute, p.Checkpoint())
	if err != nil {
		t.Fatal(err)
	}
	snapshot := restored.Snapshot()
	if snapshot.Robots["robot-1"].Freshness != Stale || snapshot.Resources["object"].Freshness != Stale || snapshot.Sources[robot.SourceID].Freshness != Stale {
		t.Fatalf("restored facts are fresh: %#v", snapshot)
	}
	clone := restored.Clone()
	robot.SourceSequence, robot.ObservationID = 3, "new-robot"
	robot.ObservedAt, robot.ReceivedAt = time.Now().UTC(), time.Now().UTC()
	if _, _, err := clone.Apply(robot); err != nil {
		t.Fatal(err)
	}
	if got := clone.Snapshot(); got.Robots["robot-1"].Freshness != Fresh || got.Resources["object"].Freshness != Stale {
		t.Fatalf("fresh observation must not refresh unrelated facts: %#v", got)
	}
	if got := restored.Snapshot(); got.Revision != 2 || got.Robots["robot-1"].Freshness != Stale {
		t.Fatalf("clone mutated original: %#v", got)
	}
}

func TestCheckpointRejectsBrokenInternalInvariants(t *testing.T) {
	cases := map[string]func(*Checkpoint){
		"version":           func(c *Checkpoint) { c.SchemaVersion = "future" },
		"cursor":            func(c *Checkpoint) { c.Snapshot.EventCursor = "missing" },
		"source high water": func(c *Checkpoint) { c.SourceSequences["robot-1/sim"] = 0 },
		"transform":         func(c *Checkpoint) { c.SourceTransforms["robot-1/sim"] = "other" },
		"entity identity": func(c *Checkpoint) {
			e := c.Snapshot.Entities["red-block"]
			e.EntityID = "other"
			c.Snapshot.Entities["red-block"] = e
		},
		"entity evidence": func(c *Checkpoint) {
			e := c.Snapshot.Entities["red-block"]
			e.Evidence.ObservationID = "missing"
			c.Snapshot.Entities["red-block"] = e
		},
		"revision": func(c *Checkpoint) { c.Snapshot.Revision = 8 },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			p := NewProjector("world-test", time.Minute)
			if _, _, err := p.Apply(entityObservation(1, "one", 1, "table", time.Now().UTC())); err != nil {
				t.Fatal(err)
			}
			checkpoint := p.Checkpoint()
			mutate(&checkpoint)
			if _, err := RestoreProjector("world-test", time.Minute, checkpoint); err == nil {
				t.Fatal("invalid checkpoint accepted")
			}
		})
	}
}

func TestRecoveryRequiresPostRestartEvidenceAndRestartsStabilityCount(t *testing.T) {
	p := NewProjector("world-test", time.Minute)
	beforeRestart := time.Now().UTC().Add(-time.Second)
	for i := uint64(1); i <= 2; i++ {
		if _, _, err := p.Apply(entityObservation(i, string(rune('a'+i)), 1, "table", beforeRestart)); err != nil {
			t.Fatal(err)
		}
	}
	restored, err := RestoreProjector("world-test", time.Minute, p.Checkpoint())
	if err != nil {
		t.Fatal(err)
	}
	cached := entityObservation(3, "cached-with-new-sequence", 1, "table", beforeRestart)
	if snapshot, _, err := restored.Apply(cached); err != nil || snapshot.Entities["red-block"].Freshness != Stale || snapshot.Sources[cached.SourceID].Freshness != Stale {
		t.Fatalf("cached pre-restart evidence became fresh: %#v %v", snapshot, err)
	}
	fresh := entityObservation(4, "fresh", 1, "table", time.Now().UTC())
	if snapshot, _, err := restored.Apply(fresh); err != nil || snapshot.Entities["red-block"].Freshness != Fresh || snapshot.Entities["red-block"].StableObservations != 1 {
		t.Fatalf("fresh evidence retained pre-restart stability: %#v %v", snapshot, err)
	}
}

func TestRecoveredStaticSourcePublishesStalenessWhenOldCacheArrivesAfterFreshData(t *testing.T) {
	beforeRestart := time.Now().UTC().Add(-time.Second)
	p := NewProjector("world-test", time.Minute)
	if _, _, err := p.Apply(staticEntityObservation(1, "old", "box", beforeRestart)); err != nil {
		t.Fatal(err)
	}
	restored, err := RestoreProjector("world-test", time.Minute, p.Checkpoint())
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := restored.Apply(staticEntityObservation(2, "fresh", "box", time.Now().UTC())); err != nil {
		t.Fatal(err)
	}
	snapshot, accepted, err := restored.Apply(staticEntityObservation(3, "old-cache", "box", beforeRestart))
	if err != nil || !accepted || snapshot.Revision != 3 || snapshot.Entities["fixture"].Freshness != Stale || snapshot.Sources["robot-1/sim"].Freshness != Stale {
		t.Fatalf("staleness changed without revision: %#v accepted=%v err=%v", snapshot, accepted, err)
	}
}
