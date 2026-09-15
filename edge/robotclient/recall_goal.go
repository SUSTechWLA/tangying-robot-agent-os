package robotclient

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

// RecallGoalMaxAgeMS bounds how old a remembered sighting may be before it is
// still worth driving to. Beyond this the position is a hint about a room, not a
// destination: the object has had hours to be moved, and a task that drives
// across the house on a stale coordinate wastes the operator's afternoon.
//
// It is a deployment parameter, not a constant of nature: a site that maps once
// a week wants a much larger window, and one that maps before every shift wants
// a smaller one. Set TANGYING_RECALL_GOAL_MAX_AGE_MS to override it, and record
// the value wherever the deployment's expectations are recorded - a task that
// used a remembered pose must be explainable later.
const RecallGoalMaxAgeMS = 15 * 60 * 1000

// RecallGoalMaxAgeEnv names the environment variable that overrides the window.
const RecallGoalMaxAgeEnv = "TANGYING_RECALL_GOAL_MAX_AGE_MS"

// RecallGoalMaxAge resolves the configured window: the documented default, or a
// positive millisecond value from the environment. A malformed value is ignored
// in favour of the default rather than disabling the freshness bound, because
// "unset" and "typo" must not both mean "trust anything".
func RecallGoalMaxAge(environ func(string) string) int64 {
	if environ == nil {
		return RecallGoalMaxAgeMS
	}
	raw := strings.TrimSpace(environ(RecallGoalMaxAgeEnv))
	if raw == "" {
		return RecallGoalMaxAgeMS
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 || value > int64(7*24*time.Hour/time.Millisecond) {
		return RecallGoalMaxAgeMS
	}
	return value
}

// maxRecallCandidates bounds how many remembered positions a category may carry.
const maxRecallCandidates = 8

// recalledGoal returns the map-frame pose where the given work area last saw
// something of the requested category, when that sighting is fresh enough to be
// a destination.
//
// The semantic layer answers three separate questions and this uses the third:
// where the named places are (semantic_navigation), which objects may be
// operated at all (semantic_objects, deliberately without measured poses), and
// where an object was last actually seen (semantic_recall, measured by a survey
// and carrying the age of that measurement).
//
// A recalled position is preferred over the commissioned work-area waypoint
// because arriving at a room is not the same as being able to see the object:
// the reference workcell's mug is outside the fixed pose's view, and a
// re-surveyed map can leave that pose unreachable entirely. The caller still
// verifies arrival against the pose actually commanded, and must re-observe the
// object before touching it.
//
// Absence is not an error: no recall contract, no entry for the category, or
// only stale sightings all mean "use the commissioned waypoint", which is the
// behaviour that existed before this preference.
func recalledGoal(info runtime.Snapshot, state map[string]any, category, workArea string,
	now time.Time, maxAgeMS int64) ([]float64, int64, error) {
	if maxAgeMS <= 0 {
		maxAgeMS = RecallGoalMaxAgeMS
	}
	if strings.TrimSpace(category) == "" || strings.TrimSpace(workArea) == "" {
		return nil, 0, nil
	}
	recall, ok := state["semantic_recall"].(map[string]any)
	if !ok || len(recall) == 0 {
		return nil, 0, nil
	}
	if recall["schemaVersion"] != "semantic.recall.v1" {
		return nil, 0, errors.New("semantic recall schema is unsupported")
	}
	if recall["frameId"] != "map" {
		return nil, 0, errors.New("semantic recall frame must be map")
	}
	active, ok := state["active_map"].(map[string]any)
	if !ok || len(active) == 0 {
		return nil, 0, errors.New("semantic recall requires an active map contract")
	}
	for _, field := range []string{"mapId", "mapRevision"} {
		value, ok := recall[field].(string)
		activeValue, activeOK := active[field].(string)
		if !ok || !activeOK || strings.TrimSpace(value) == "" || value != activeValue {
			// A remembered position from another map revision is a coordinate in
			// a frame this robot no longer navigates in.
			return nil, 0, fmt.Errorf("semantic recall %s does not match active map", field)
		}
	}
	categories, ok := recall["categories"].(map[string]any)
	if !ok {
		return nil, 0, errors.New("semantic recall categories are missing")
	}
	entries, ok := categories[strings.ToLower(strings.TrimSpace(category))]
	if !ok {
		return nil, 0, nil
	}
	list, ok := entries.([]any)
	if !ok || len(list) > maxRecallCandidates {
		return nil, 0, errors.New("semantic recall entries are invalid or exceed limits")
	}
	if now.IsZero() {
		now = time.Now()
	}
	for _, raw := range list {
		entry, ok := raw.(map[string]any)
		if !ok {
			return nil, 0, errors.New("semantic recall entry is not an object")
		}
		age, ok := entry["ageMs"].(float64)
		if !ok || math.IsNaN(age) || math.IsInf(age, 0) || age < 0 {
			return nil, 0, errors.New("semantic recall entry has an invalid age")
		}
		if int64(age) > maxAgeMS {
			// Entries arrive sorted by age, so the first stale one ends the search.
			return nil, 0, nil
		}
		// The goal is the vantage the robot occupied when it saw the object, not
		// the object's own position: a base cannot drive to a mug on a table. An
		// entry without a recorded vantage is skipped rather than approximated.
		rawPose, present := entry["vantagePose"]
		if !present || rawPose == nil {
			continue
		}
		pose, err := semanticPose(rawPose)
		if err != nil {
			return nil, 0, fmt.Errorf("semantic recall vantage pose: %w", err)
		}
		if err := withinWorkspace(info, pose); err != nil {
			return nil, 0, err
		}
		return pose, int64(age), nil
	}
	return nil, 0, nil
}

// recallRecord is the evidence a grounded goal carries about where it came from.
// It travels with the plan so no reader has to guess whether a navigation goal
// was the commissioned waypoint or a remembered sighting.
type recallRecord struct {
	GoalSource string `json:"goalSource"`
	AgeMS      int64  `json:"recallAgeMs,omitempty"`
}

func (r recallRecord) mapValue() map[string]any {
	value := map[string]any{"goalSource": r.GoalSource}
	if r.GoalSource == "recalled" {
		value["recallAgeMs"] = r.AgeMS
	}
	return value
}
