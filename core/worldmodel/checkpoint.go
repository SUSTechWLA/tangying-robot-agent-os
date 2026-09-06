package worldmodel

import (
	"errors"
	"fmt"
	"maps"
	"math"
	"slices"
	"strings"
	"time"
)

const CheckpointSchemaVersion = "world.projector.checkpoint.v1"

// Checkpoint includes private deduplication state. Snapshot.Sources alone is not
// sufficient: suppressed static observations advance the private high-water.
type Checkpoint struct {
	SchemaVersion    string            `json:"schemaVersion"`
	Snapshot         Snapshot          `json:"snapshot"`
	ObservationIDs   []string          `json:"observationIds"`
	SourceSequences  map[string]uint64 `json:"sourceSequences"`
	SourceTransforms map[string]string `json:"sourceTransforms"`
}

var ErrInvalidCheckpoint = errors.New("invalid world projector checkpoint")

func (p *Projector) Checkpoint() Checkpoint {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.checkpointLocked()
}

func (p *Projector) checkpointLocked() Checkpoint {
	ids := make([]string, 0, len(p.observationIDs))
	for id := range p.observationIDs {
		ids = append(ids, id)
	}
	slices.Sort(ids)
	return Checkpoint{SchemaVersion: CheckpointSchemaVersion, Snapshot: p.snapshotLocked(), ObservationIDs: ids, SourceSequences: maps.Clone(p.sourceSequence), SourceTransforms: maps.Clone(p.sourceTransforms)}
}

// Clone stages an independent update without changing the authoritative state.
// Unlike recovery, cloning preserves the live projector's freshness boundary.
func (p *Projector) Clone() *Projector {
	p.mu.RLock()
	defer p.mu.RUnlock()
	clone := projectorFromCheckpoint(p.worldID, p.freshnessBudget, p.checkpointLocked())
	clone.now = p.now
	clone.recoveredAt = p.recoveredAt
	clone.recoveredObservationIDs = maps.Clone(p.recoveredObservationIDs)
	clone.recoveredSourceSequences = maps.Clone(p.recoveredSourceSequences)
	return clone
}

// RestoreProjector validates a durable checkpoint and distrusts all recovered
// evidence until a subsequent observation refreshes that individual record.
func RestoreProjector(worldID string, freshnessBudget time.Duration, checkpoint Checkpoint) (*Projector, error) {
	if err := validateCheckpoint(worldID, checkpoint); err != nil {
		return nil, err
	}
	// Construct from a deep copy, so mutations to a caller's checkpoint cannot
	// change authoritative state after validation.
	copied := projectorFromCheckpoint(worldID, freshnessBudget, checkpoint)
	p := projectorFromCheckpoint(worldID, freshnessBudget, copied.Checkpoint())
	p.recoveredAt = p.now().UTC()
	p.recoveredObservationIDs = maps.Clone(p.observationIDs)
	p.recoveredSourceSequences = maps.Clone(p.sourceSequence)
	return p, nil
}

func projectorFromCheckpoint(worldID string, freshnessBudget time.Duration, checkpoint Checkpoint) *Projector {
	p := NewProjector(worldID, freshnessBudget)
	p.revision, p.cursor = checkpoint.Snapshot.Revision, checkpoint.Snapshot.EventCursor
	for _, id := range checkpoint.ObservationIDs {
		p.observationIDs[id] = struct{}{}
	}
	p.sourceSequence, p.sourceTransforms = maps.Clone(checkpoint.SourceSequences), maps.Clone(checkpoint.SourceTransforms)
	p.robots, p.entities = checkpoint.Snapshot.Robots, checkpoint.Snapshot.Entities
	p.resources, p.sources = checkpoint.Snapshot.Resources, checkpoint.Snapshot.Sources
	return p
}

func validateCheckpoint(worldID string, c Checkpoint) error {
	invalid := func(reason string) error { return fmt.Errorf("%w: %s", ErrInvalidCheckpoint, reason) }
	s := c.Snapshot
	if c.SchemaVersion != CheckpointSchemaVersion || s.SchemaVersion != SchemaVersion {
		return invalid("unsupported schema")
	}
	if strings.TrimSpace(worldID) == "" || s.WorldID != worldID {
		return invalid("world mismatch")
	}
	if s.ProjectedAt.IsZero() || s.Robots == nil || s.Entities == nil || s.Resources == nil || s.Sources == nil || c.SourceSequences == nil || c.SourceTransforms == nil {
		return invalid("incomplete snapshot")
	}
	ids := map[string]struct{}{}
	for _, id := range c.ObservationIDs {
		if strings.TrimSpace(id) == "" {
			return invalid("empty observation ID")
		}
		if _, exists := ids[id]; exists {
			return invalid("duplicate observation ID")
		}
		ids[id] = struct{}{}
	}
	if s.Revision == 0 {
		if s.EventCursor != "" || len(ids)+len(s.Robots)+len(s.Entities)+len(s.Resources)+len(s.Sources) != 0 {
			return invalid("nonempty initial state")
		}
	} else if _, exists := ids[s.EventCursor]; !exists || uint64(len(ids)) < s.Revision {
		return invalid("revision or event cursor")
	}
	if len(c.SourceSequences) != len(s.Sources) || len(c.SourceTransforms) != len(s.Sources) {
		return invalid("source index size")
	}
	for id, source := range s.Sources {
		if strings.TrimSpace(id) == "" || id != source.SourceID || source.SourceSequence == 0 || c.SourceSequences[id] < source.SourceSequence || source.TransformRevision == "" || c.SourceTransforms[id] != source.TransformRevision || source.SourceType == "" || source.LastObservedAt.IsZero() || source.LastReceivedAt.Before(source.LastObservedAt) {
			return invalid("source state")
		}
	}
	validEvidence := func(e EvidenceRef) bool {
		source, found := s.Sources[e.SourceID]
		_, seen := ids[e.ObservationID]
		return found && seen && e.SourceSequence > 0 && e.SourceSequence <= source.SourceSequence && !e.ObservedAt.IsZero() && strings.TrimSpace(e.FrameID) != "" && e.TransformRevision == source.TransformRevision
	}
	validConfidence := func(value float64) bool {
		return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 && value <= 1
	}
	finitePose := func(values []float64) bool {
		for _, value := range values {
			if math.IsNaN(value) || math.IsInf(value, 0) {
				return false
			}
		}
		return true
	}
	for id, entity := range s.Entities {
		if id == "" || entity.EntityID != id || entity.Category == "" || entity.ObservationCount == 0 || entity.StableObservations < 1 || uint64(entity.StableObservations) > entity.ObservationCount || !validEvidence(entity.Evidence) || !validConfidence(entity.Confidence) || !finitePose(entity.Pose) {
			return invalid("entity state")
		}
	}
	for id, robot := range s.Robots {
		if id == "" || robot.RobotID != id || !validEvidence(robot.Evidence) || !validConfidence(robot.Confidence) || !finitePose(robot.Pose) {
			return invalid("robot state")
		}
	}
	for id, resource := range s.Resources {
		if id == "" || resource.ResourceID != id || resource.FencingToken == 0 || !validEvidence(resource.Evidence) {
			return invalid("resource state")
		}
	}
	return nil
}
