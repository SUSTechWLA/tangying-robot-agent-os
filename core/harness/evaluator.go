// Package harness evaluates physical task completion from world evidence.
//
// A successful tool RPC is only a trigger to inspect the world. It is never
// itself proof that a robot changed the environment as intended.
package harness

import (
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

type Status string

const (
	Waiting          Status = "WAITING"
	Satisfied        Status = "SATISFIED"
	RetryableFailure Status = "RETRYABLE_FAILURE"
	FailedSafe       Status = "FAILED_SAFE"
)

type Intent struct {
	Action  string `json:"action"`
	RobotID string `json:"robotId"`
}

type EvidenceBasis struct {
	WorldRevision          uint64    `json:"worldRevision"`
	EntityObservationCount uint64    `json:"entityObservationCount"`
	EntitySourceID         string    `json:"entitySourceId"`
	EntitySourceSequence   uint64    `json:"entitySourceSequence"`
	RobotSourceID          string    `json:"robotSourceId"`
	RobotSourceSequence    uint64    `json:"robotSourceSequence"`
	CommandStartedAt       time.Time `json:"commandStartedAt"`
}

type ExpectedResource struct {
	ResourceID   string `json:"resourceId"`
	Owner        string `json:"owner"`
	FencingToken uint64 `json:"fencingToken"`
}

type Input struct {
	Intent                     Intent              `json:"intent"`
	Basis                      EvidenceBasis       `json:"basis"`
	Snapshot                   worldmodel.Snapshot `json:"snapshot"`
	EntityID                   string              `json:"entityId"`
	DestinationID              string              `json:"destinationId"`
	RobotID                    string              `json:"robotId"`
	ExpectedResource           ExpectedResource    `json:"expectedResource"`
	RequiredStableObservations int                 `json:"requiredStableObservations"`
}

type Verdict struct {
	Status      Status   `json:"status"`
	Reason      string   `json:"reason"`
	EvidenceIDs []string `json:"evidenceIds,omitempty"`
}

type Agent struct {
	maxAge time.Duration
}

func New(maxAge time.Duration) *Agent {
	if maxAge <= 0 {
		maxAge = 5 * time.Second
	}
	return &Agent{maxAge: maxAge}
}

func (a *Agent) Evaluate(input Input) Verdict {
	if input.EntityID == "" || input.DestinationID == "" || input.RobotID == "" {
		return Verdict{Status: FailedSafe, Reason: "HARNESS_INPUT_INVALID"}
	}
	if input.Snapshot.Revision <= input.Basis.WorldRevision {
		return Verdict{Status: Waiting, Reason: "WORLD_REVISION_NOT_ADVANCED"}
	}

	entity, ok := input.Snapshot.Entities[input.EntityID]
	if !ok {
		return Verdict{Status: Waiting, Reason: "ENTITY_UNKNOWN"}
	}
	if entity.ObservationCount < input.Basis.EntityObservationCount+2 {
		return Verdict{Status: Waiting, Reason: "ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE"}
	}
	if !entity.Evidence.ObservedAt.After(input.Basis.CommandStartedAt) {
		return Verdict{Status: Waiting, Reason: "ENTITY_EVIDENCE_PREDATES_CLAIM"}
	}
	if entity.Evidence.SourceID == input.Basis.EntitySourceID &&
		entity.Evidence.SourceSequence <= input.Basis.EntitySourceSequence {
		return Verdict{Status: Waiting, Reason: "ENTITY_EVIDENCE_PREDATES_COMMAND"}
	}

	robot, ok := input.Snapshot.Robots[input.RobotID]
	if !ok {
		return Verdict{Status: Waiting, Reason: "ROBOT_UNKNOWN"}
	}
	if input.Basis.RobotSourceID != "" && robot.Evidence.SourceID != input.Basis.RobotSourceID {
		return Verdict{Status: FailedSafe, Reason: "ROBOT_SOURCE_CHANGED"}
	}
	if robot.Evidence.SourceSequence <= input.Basis.RobotSourceSequence {
		return Verdict{Status: Waiting, Reason: "ROBOT_EVIDENCE_PREDATES_COMMAND"}
	}
	if !robot.Evidence.ObservedAt.After(input.Basis.CommandStartedAt) {
		return Verdict{Status: Waiting, Reason: "ROBOT_EVIDENCE_PREDATES_CLAIM"}
	}
	if robot.EmergencyStopped {
		return Verdict{Status: FailedSafe, Reason: "ROBOT_EMERGENCY_STOPPED"}
	}

	evidenceIDs := compactEvidenceIDs(
		entity.Evidence.ObservationID,
		robot.Evidence.ObservationID,
	)
	if stale(input.Snapshot.ProjectedAt, entity.Evidence.ObservedAt, a.maxAge) {
		return Verdict{Status: Waiting, Reason: "ENTITY_EVIDENCE_STALE", EvidenceIDs: evidenceIDs}
	}
	if stale(input.Snapshot.ProjectedAt, robot.Evidence.ObservedAt, a.maxAge) {
		return Verdict{Status: Waiting, Reason: "ROBOT_EVIDENCE_STALE", EvidenceIDs: evidenceIDs}
	}
	if sourceStale(input.Snapshot, entity.Evidence.SourceID, a.maxAge) {
		return Verdict{Status: Waiting, Reason: "ENTITY_SOURCE_STALE", EvidenceIDs: evidenceIDs}
	}
	if sourceStale(input.Snapshot, robot.Evidence.SourceID, a.maxAge) {
		return Verdict{Status: Waiting, Reason: "ROBOT_SOURCE_STALE", EvidenceIDs: evidenceIDs}
	}
	if entity.Relations["inside"] != input.DestinationID {
		return Verdict{Status: RetryableFailure, Reason: "RELATION_MISMATCH", EvidenceIDs: evidenceIDs}
	}
	requiredStable := input.RequiredStableObservations
	if requiredStable <= 0 {
		requiredStable = 2
	}
	if entity.StableObservations < requiredStable {
		return Verdict{Status: Waiting, Reason: "ENTITY_NOT_STABLE", EvidenceIDs: evidenceIDs}
	}
	if robot.Held != "" {
		return Verdict{Status: RetryableFailure, Reason: "ROBOT_STILL_HOLDING_ENTITY", EvidenceIDs: evidenceIDs}
	}

	if expected := input.ExpectedResource; expected.ResourceID != "" {
		resource, exists := input.Snapshot.Resources[expected.ResourceID]
		if !exists {
			return Verdict{Status: Waiting, Reason: "RESOURCE_UNKNOWN", EvidenceIDs: evidenceIDs}
		}
		if resource.Owner != expected.Owner {
			return Verdict{Status: FailedSafe, Reason: "RESOURCE_OWNER_MISMATCH", EvidenceIDs: evidenceIDs}
		}
		if resource.FencingToken != expected.FencingToken {
			return Verdict{Status: FailedSafe, Reason: "FENCING_TOKEN_MISMATCH", EvidenceIDs: evidenceIDs}
		}
		// Resource ownership is coordinator-issued lease state, not a sensor
		// sample. Its owner and fencing token remain authoritative until the
		// lease expires, even when physical execution takes longer than the
		// scene-observation freshness budget.
		if !resource.ExpiresAt.IsZero() && !input.Snapshot.ProjectedAt.Before(resource.ExpiresAt) {
			return Verdict{Status: Waiting, Reason: "RESOURCE_LEASE_EXPIRED", EvidenceIDs: evidenceIDs}
		}
	}

	return Verdict{
		Status: Satisfied, Reason: "PHYSICAL_POSTCONDITIONS_SATISFIED", EvidenceIDs: evidenceIDs,
	}
}

func stale(now, observedAt time.Time, maxAge time.Duration) bool {
	return now.IsZero() || observedAt.IsZero() || now.Sub(observedAt) > maxAge
}

func sourceStale(snapshot worldmodel.Snapshot, sourceID string, maxAge time.Duration) bool {
	if sourceID == "" {
		return true
	}
	source, ok := snapshot.Sources[sourceID]
	return !ok || stale(snapshot.ProjectedAt, source.LastObservedAt, maxAge)
}

func compactEvidenceIDs(ids ...string) []string {
	seen := map[string]struct{}{}
	result := make([]string, 0, len(ids))
	for _, id := range ids {
		if id == "" {
			continue
		}
		if _, ok := seen[id]; ok {
			continue
		}
		seen[id] = struct{}{}
		result = append(result, id)
	}
	return result
}
