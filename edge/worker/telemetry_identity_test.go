package worker

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/fleetcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestFleetTelemetryRejectsConflictingOrMissingRobotIdentity(t *testing.T) {
	for name, mutate := range map[string]func(*telemetry.Snapshot){
		"foreign snapshot":       func(s *telemetry.Snapshot) { s.RobotID = "other-robot" },
		"missing snapshot":       func(s *telemetry.Snapshot) { s.RobotID = "" },
		"foreign profile":        func(s *telemetry.Snapshot) { s.RobotProfile.RobotID = "other-robot" },
		"missing profile":        func(s *telemetry.Snapshot) { s.RobotProfile.RobotID = "" },
		"foreign reconstruction": func(s *telemetry.Snapshot) { s.Reconstruction.RobotID = "other-robot" },
		"missing reconstruction": func(s *telemetry.Snapshot) { s.Reconstruction.RobotID = "" },
	} {
		t.Run(name, func(t *testing.T) {
			snapshot := contractSnapshot()
			mutate(&snapshot)
			w := New(Config{RobotID: "mobile-7", Observer: staticObserver{snapshot}})
			if sample, err := w.buildSample(context.Background()); !errors.Is(err, ErrTelemetryIdentityMismatch) || sample.RobotID != "" {
				t.Fatalf("foreign evidence became a fleet sample: sample=%#v error=%v", sample, err)
			}
			if events, err := w.BuildObservations(context.Background()); !errors.Is(err, ErrTelemetryIdentityMismatch) || len(events) != 0 {
				t.Fatalf("foreign evidence became world facts: events=%#v error=%v", events, err)
			}
			if len(w.observation.sequence) != 0 {
				t.Fatal("rejected evidence allocated authoritative observation identities")
			}
		})
	}
}

func TestFleetTelemetryRechecksIdentityOnEveryPoll(t *testing.T) {
	w := New(Config{RobotID: "mobile-7", Observer: staticObserver{contractSnapshot()}})
	first, err := w.BuildObservations(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	foreign := contractSnapshot()
	foreign.RobotID = "reconnected-robot"
	w.config.Observer = staticObserver{foreign}
	if events, err := w.BuildObservations(context.Background()); !errors.Is(err, ErrTelemetryIdentityMismatch) || len(events) != 0 {
		t.Fatalf("a changed Runtime identity was accepted: events=%#v error=%v", events, err)
	}
	w.config.Observer = staticObserver{contractSnapshot()}
	next, err := w.BuildObservations(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if next[0].SourceSequence != first[0].SourceSequence+1 {
		t.Fatal("foreign poll changed the robot's evidence sequence")
	}
}

func TestFleetLegacyTelemetryRequiresRobotIdentityWithoutRequiringProfile(t *testing.T) {
	w := New(Config{RobotID: "legacy-1", Observer: staticObserver{telemetry.Snapshot{RobotID: "legacy-1"}}})
	if sample, err := w.buildSample(context.Background()); err != nil || sample.RobotID != "legacy-1" {
		t.Fatalf("matching legacy Runtime was rejected: sample=%#v error=%v", sample, err)
	}
	w.config.RobotID = ""
	if _, err := w.buildSample(context.Background()); !errors.Is(err, ErrTelemetryIdentityMismatch) {
		t.Fatalf("unbound worker must not publish: %v", err)
	}
}

type scriptedTelemetryObserver struct{ snapshots chan telemetry.Snapshot }

func (s scriptedTelemetryObserver) Telemetry(ctx context.Context, _ string) (telemetry.Snapshot, error) {
	select {
	case snapshot := <-s.snapshots:
		return snapshot, nil
	case <-ctx.Done():
		return telemetry.Snapshot{}, ctx.Err()
	}
}

type telemetryCaptureCloud struct {
	activityCloud
	samples chan fleetcontract.Sample
}

func (c *telemetryCaptureCloud) ReportTelemetry(_ context.Context, sample fleetcontract.Sample) error {
	c.samples <- sample
	return nil
}

func TestFleetTelemetryLoopDoesNotPublishMisconnectedRobotToCloud(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	observer := scriptedTelemetryObserver{snapshots: make(chan telemetry.Snapshot, 2)}
	cloud := &telemetryCaptureCloud{samples: make(chan fleetcontract.Sample, 2)}
	w := New(Config{RobotID: "mobile-7", Observer: observer, Cloud: cloud, TelemetryInterval: time.Millisecond})
	foreign := contractSnapshot()
	foreign.RobotID = "other-robot"
	foreign.Activity = "FOREIGN"
	observer.snapshots <- foreign
	matching := contractSnapshot()
	matching.Activity = "MATCHING"
	observer.snapshots <- matching
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.telemetryLoop(ctx)
	}()
	defer func() { cancel(); <-done }()
	select {
	case sample := <-cloud.samples:
		if sample.RobotID != "mobile-7" || sample.Activity != "MATCHING" {
			t.Fatalf("wrong robot was relabeled and published to the cloud: %#v", sample)
		}
	case <-ctx.Done():
		t.Fatal("matching telemetry was not delivered after rejecting the wrong robot")
	}
}
