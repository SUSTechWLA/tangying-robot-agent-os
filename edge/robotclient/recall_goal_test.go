package robotclient

import (
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

// These cases hold the rule that makes a remembered position usable as a
// destination: it must belong to the active map, it must be fresh, and it must
// still be somewhere the robot is allowed to drive. Anything else falls back to
// the commissioned waypoint - remembering nothing is not an error, but driving
// to a stale or foreign coordinate would be.

func recallSnapshot() runtime.Snapshot {
	return runtime.Snapshot{
		RobotID: "test-robot",
		RobotProfile: &robotcontract.Profile{
			ActionLimits: map[string]robotcontract.ActionLimit{
				"navigation.x": {Min: -5, Max: 5},
				"navigation.y": {Min: -5, Max: 5},
				"navigation.z": {Min: 0, Max: 2},
			},
		},
	}
}

func recallState(entries []any, overrides map[string]any) map[string]any {
	recall := map[string]any{
		"schemaVersion": "semantic.recall.v1", "frameId": "map",
		"mapId": "scan-1", "mapRevision": "rev-1",
		"categories": map[string]any{"cup": entries},
	}
	for key, value := range overrides {
		recall[key] = value
	}
	return map[string]any{
		"active_map":      map[string]any{"mapId": "scan-1", "mapRevision": "rev-1", "calibrationRevision": "cal-1"},
		"semantic_recall": recall,
	}
}

// recallEntry models one entry the runtime publishes: where the object is, and
// where the robot stood when it saw it. Only the vantage is drivable.
func recallEntry(ageMS int64, x, y float64) map[string]any {
	vantage := []any{x + .4, y - .4, 0.0, 1.0, 0.0, 0.0, 0.0}
	return map[string]any{"id": "cup-000", "pose": []any{x, y, .85},
		"vantagePose": vantage, "ageMs": float64(ageMS),
		"frameId": "map", "sightings": float64(3), "confidence": .9,
		"attributes": map[string]any{"color": "white"}}
}

func TestAFreshSightingBecomesTheDestination(t *testing.T) {
	now := time.Now()
	state := recallState([]any{recallEntry(30_000, 2.25, 3.4)}, nil)
	pose, ageMS, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", now)
	if err != nil {
		t.Fatalf("recall: %v", err)
	}
	if len(pose) != 7 || pose[0] != 2.65 || pose[1] != 3.0 {
		t.Fatalf("the goal must be the vantage, not the object: %#v", pose)
	}
	if ageMS != 30_000 {
		t.Fatalf("age = %d, want the sighting's own age", ageMS)
	}
}

func TestOnlyTheNewestUsableSightingIsChosen(t *testing.T) {
	// Entries arrive sorted by age; the first acceptable one wins, and a stale
	// head must not make the caller fall through to an even staler tail.
	state := recallState([]any{recallEntry(1_000, 1.0, 1.0), recallEntry(2_000, 2.0, 2.0)}, nil)
	pose, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now())
	if err != nil || pose[1] != 0.6 {
		t.Fatalf("pose = %#v err = %v", pose, err)
	}
}

func TestAStaleSightingFallsBackToTheCommissionedWaypoint(t *testing.T) {
	state := recallState([]any{recallEntry(RecallGoalMaxAgeMS+1, 2.0, 2.0)}, nil)
	pose, ageMS, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now())
	if err != nil {
		t.Fatalf("a stale sighting is not an error: %v", err)
	}
	if pose != nil || ageMS != 0 {
		t.Fatalf("stale sighting must not be driven to: %#v age=%d", pose, ageMS)
	}
}

func TestNoRecallAtAllIsNotAnError(t *testing.T) {
	snapshot := recallSnapshot()
	for name, state := range map[string]map[string]any{
		"no contract":      {},
		"empty contract":   {"semantic_recall": map[string]any{}},
		"unknown category": recallState(nil, nil),
		"other category":   recallState([]any{recallEntry(1_000, 1, 1)}, map[string]any{"categories": map[string]any{"bottle": []any{recallEntry(1_000, 1, 1)}}}),
	} {
		pose, _, err := recalledGoal(snapshot, state, "cup", "kitchen", time.Now())
		if err != nil || pose != nil {
			t.Fatalf("%s: pose=%#v err=%v", name, pose, err)
		}
	}
}

func TestAForeignMapRevisionIsRefusedRatherThanDriven(t *testing.T) {
	state := recallState([]any{recallEntry(1_000, 1, 1)}, map[string]any{"mapRevision": "rev-2"})
	_, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now())
	if err == nil || !strings.Contains(err.Error(), "active map") {
		t.Fatalf("err = %v, want a refusal naming the active map", err)
	}
	state = recallState([]any{recallEntry(1_000, 1, 1)}, map[string]any{"mapId": "scan-2"})
	if _, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now()); err == nil {
		t.Fatal("a position from another map must be refused")
	}
}

func TestAMalformedRecallContractFailsClosed(t *testing.T) {
	for name, overrides := range map[string]map[string]any{
		"unknown schema":  {"schemaVersion": "semantic.recall.v2"},
		"world frame":     {"frameId": "world"},
		"missing entries": {"categories": "cup"},
		"too many":        {"categories": map[string]any{"cup": make([]any, maxRecallCandidates+1)}},
	} {
		state := recallState([]any{recallEntry(1_000, 1, 1)}, nil)
		recall := state["semantic_recall"].(map[string]any)
		for key, value := range overrides {
			recall[key] = value
		}
		if _, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now()); err == nil {
			t.Fatalf("%s: a malformed contract must fail closed", name)
		}
	}
}

func TestAnInvalidAgeOrPoseIsRefused(t *testing.T) {
	for name, entry := range map[string]map[string]any{
		"negative age":  {"id": "x", "pose": []any{1.0, 1.0, .8}, "ageMs": float64(-1)},
		"missing age":   {"id": "x", "pose": []any{1.0, 1.0, .8}},
		"short vantage": {"id": "x", "vantagePose": []any{1.0, 1.0}, "ageMs": float64(10)},
	} {
		state := recallState([]any{entry}, nil)
		if _, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now()); err == nil {
			t.Fatalf("%s: must be refused", name)
		}
	}
}

func TestAnEntryWithoutAVantageIsSkippedRatherThanApproximated(t *testing.T) {
	state := recallState([]any{map[string]any{"id": "x", "pose": []any{1.0, 1.0, .8},
		"ageMs": float64(1_000)}}, nil)
	pose, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now())
	if err != nil || pose != nil {
		t.Fatalf("an object position is not a destination: pose=%#v err=%v", pose, err)
	}
}

func TestASightingOutsideTheWorkspaceIsRefused(t *testing.T) {
	state := recallState([]any{recallEntry(1_000, 42, 3)}, nil)
	_, _, err := recalledGoal(recallSnapshot(), state, "cup", "kitchen", time.Now())
	if err == nil || !strings.Contains(err.Error(), "workspace") {
		t.Fatalf("err = %v, want a workspace refusal", err)
	}
}

func TestTheGoalSourceTravelsWithTheGroundedGoal(t *testing.T) {
	commissioned := recallRecord{GoalSource: "commissioned"}.mapValue()
	if commissioned["goalSource"] != "commissioned" {
		t.Fatalf("commissioned evidence = %#v", commissioned)
	}
	if _, present := commissioned["recallAgeMs"]; present {
		t.Fatal("a commissioned goal has no recall age to report")
	}
	recalled := recallRecord{GoalSource: "recalled", AgeMS: 12_345}.mapValue()
	if recalled["goalSource"] != "recalled" || recalled["recallAgeMs"] != int64(12_345) {
		t.Fatalf("recalled evidence = %#v", recalled)
	}
}
