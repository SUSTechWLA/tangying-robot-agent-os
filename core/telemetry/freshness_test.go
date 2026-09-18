package telemetry_test

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

// A freshness verdict has to be computed, not assumed.
//
// Both evidence constructors wrote the literal "FRESH" and downgraded it only for
// an emergency stop, so the completion gate's staleness rule could not fire for
// staleness at all: a snapshot from a frozen stream closed a physical write
// exactly like a fresh one. The rule itself already existed — the profile
// contract rejects a reconstruction older than its sensor's budget — and nothing
// applied it here.

func profileWith(maxAgeMS int64) *robotcontract.Profile {
	return &robotcontract.Profile{
		Sensors: []robotcontract.Sensor{{
			SourceID: "robot-1/head-rgbd", SourceType: "rgbd_camera", FrameID: "world",
			TransformRevision: "v1", MaxAgeMS: maxAgeMS,
		}},
	}
}

func snapshotAt(observedAt time.Time, profile *robotcontract.Profile) telemetry.Snapshot {
	return telemetry.Snapshot{
		SchemaVersion: "robot.v1", RobotID: "robot-1", Adapter: "mujoco",
		ObservedAt: observedAt, RobotProfile: profile,
	}
}

func TestAnObservationInsideItsDeclaredBudgetIsFresh(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	got := snapshotAt(now.Add(-2*time.Second), profileWith(5000)).EvidenceFreshness(now)
	if got != worldmodel.Fresh {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Fresh)
	}
}

func TestAnObservationPastItsDeclaredBudgetIsStale(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	// Well past the five-second budget this profile declares. Under the old
	// literal this was FRESH, and the step closed on a frozen stream.
	got := snapshotAt(now.Add(-30*time.Second), profileWith(5000)).EvidenceFreshness(now)
	if got != worldmodel.Stale {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Stale)
	}
}

// A robot that sends no profile still gets a verdict. Using the profile
// contract's own ceiling is the conservative reading: never more permissive than
// any valid declaration, and not so tight that a deployment without a profile has
// every physical step refused.
func TestWithoutAProfileTheContractCeilingBoundsStaleness(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	if got := snapshotAt(now.Add(-time.Second), nil).EvidenceFreshness(now); got != worldmodel.Fresh {
		t.Fatalf("a one-second-old observation was %q, want %q", got, worldmodel.Fresh)
	}
	if got := snapshotAt(now.Add(-2*telemetry.DefaultMaxAge), nil).EvidenceFreshness(now); got != worldmodel.Stale {
		t.Fatalf("a two-minute-old observation was %q, want %q", got, worldmodel.Stale)
	}
}

// A source the profile does not know gets the ceiling too, rather than being
// treated as fresh by omission.
// A snapshot with no reconstruction names no sensor, so it must not be allowed to
// claim more freshness than the strictest sensor the robot declares.
func TestAnUnidentifiedSourceUsesTheStrictestDeclaredBudget(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	profile := profileWith(5000)
	profile.Sensors = append(profile.Sensors, robotcontract.Sensor{
		SourceID: "robot-1/base-rgbd", SourceType: "rgbd_camera", FrameID: "world",
		TransformRevision: "v1", MaxAgeMS: 1000,
	})
	// Three seconds: inside the camera's five-second budget, outside the base's
	// one-second one. Unidentified means the strictest applies.
	if got := snapshotAt(now.Add(-3*time.Second), profile).EvidenceFreshness(now); got != worldmodel.Stale {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Stale)
	}
	// An unknown source name gets the same treatment rather than the ceiling.
	snapshot := snapshotAt(now.Add(-3*time.Second), profile)
	snapshot.Reconstruction = &robotcontract.Reconstruction{
		SourceID: "robot-1/camera-nobody-declared", ObservedAtUnixMS: now.Add(-3 * time.Second).UnixMilli(),
	}
	if got := snapshot.EvidenceFreshness(now); got != worldmodel.Stale {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Stale)
	}
}

// A reconstruction carries the observation's own time, and it is what the
// evidence is dated by. Judging the verdict from the snapshot's time instead
// would let a stale reconstruction ride on a fresh envelope.
func TestAStaleReconstructionIsJudgedByItsOwnTime(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	snapshot := snapshotAt(now, profileWith(5000))
	snapshot.Reconstruction = &robotcontract.Reconstruction{
		SourceID: "robot-1/head-rgbd", ObservedAtUnixMS: now.Add(-time.Minute).UnixMilli(),
	}
	if got := snapshot.EvidenceFreshness(now); got != worldmodel.Stale {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Stale)
	}
}

// An observation with no time is not fresh. The gate refuses both, so between two
// verdicts with the same consequence the honest one admits it does not know.
func TestAnUndatedObservationIsUnknownRatherThanFresh(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	if got := snapshotAt(time.Time{}, profileWith(5000)).EvidenceFreshness(now); got != worldmodel.Unknown {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Unknown)
	}
}

// Two clocks that disagree slightly must not become a verdict.
func TestASlightlyFutureDatedObservationIsNotStale(t *testing.T) {
	now := time.Unix(1700000000, 0).UTC()
	if got := snapshotAt(now.Add(100*time.Millisecond), profileWith(5000)).EvidenceFreshness(now); got != worldmodel.Fresh {
		t.Fatalf("freshness = %q, want %q", got, worldmodel.Fresh)
	}
	// Beyond the skew allowance the time cannot be trusted to date anything. The
	// allowance is seconds, not the few hundred milliseconds two clocks on one
	// machine would need: an embedded board without a synced time source drifts by
	// seconds, and turning that into "the outcome is unknown" on every physical
	// step is a worse failure than the one it guards against.
	if got := snapshotAt(now.Add(telemetry.ClockSkewAllowance-time.Second), profileWith(5000)).EvidenceFreshness(now); got != worldmodel.Fresh {
		t.Fatalf("within the skew allowance: freshness = %q, want %q", got, worldmodel.Fresh)
	}
	if got := snapshotAt(now.Add(telemetry.ClockSkewAllowance+time.Second), profileWith(5000)).EvidenceFreshness(now); got != worldmodel.Unknown {
		t.Fatalf("beyond the skew allowance: freshness = %q, want %q", got, worldmodel.Unknown)
	}
}
