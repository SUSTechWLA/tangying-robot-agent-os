package worldmodel

import (
	"errors"
	"fmt"
	"math"
	"slices"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
)

var (
	ErrWorldMismatch             = errors.New("observation world does not match projector")
	ErrTransformRevisionConflict = errors.New("observation transform revision conflicts with accepted source")
)

type Projector struct {
	mu               sync.RWMutex
	worldID          string
	freshnessBudget  time.Duration
	now              func() time.Time
	revision         uint64
	cursor           string
	observationIDs   map[string]struct{}
	sourceSequence   map[string]uint64
	sourceTransforms map[string]string
	robots           map[string]RobotState
	entities         map[string]EntityState
	resources        map[string]ResourceState
	sources          map[string]SourceState
}

func NewProjector(worldID string, freshnessBudget time.Duration) *Projector {
	if freshnessBudget <= 0 {
		freshnessBudget = time.Second
	}
	return &Projector{
		worldID:          worldID,
		freshnessBudget:  freshnessBudget,
		now:              time.Now,
		observationIDs:   map[string]struct{}{},
		sourceSequence:   map[string]uint64{},
		sourceTransforms: map[string]string{},
		robots:           map[string]RobotState{},
		entities:         map[string]EntityState{},
		resources:        map[string]ResourceState{},
		sources:          map[string]SourceState{},
	}
}

func (p *Projector) SetNow(now func() time.Time) {
	if now == nil {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.now = now
}

func (p *Projector) Apply(event observation.Envelope) (Snapshot, bool, error) {
	if err := event.Validate(); err != nil {
		return Snapshot{}, false, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if event.WorldID != p.worldID {
		return p.snapshotLocked(), false, ErrWorldMismatch
	}
	if _, seen := p.observationIDs[event.ObservationID]; seen {
		return p.snapshotLocked(), false, nil
	}
	if event.SourceSequence <= p.sourceSequence[event.SourceID] {
		return p.snapshotLocked(), false, nil
	}
	if accepted := p.sourceTransforms[event.SourceID]; accepted != "" && accepted != event.TransformRevision {
		return p.snapshotLocked(), false, fmt.Errorf("%w: source=%s accepted=%s received=%s", ErrTransformRevisionConflict, event.SourceID, accepted, event.TransformRevision)
	}
	if p.unchangedStaticEntity(event) {
		p.recordHighWater(event)
		return p.snapshotLocked(), false, nil
	}
	if err := p.reduce(event); err != nil {
		return p.snapshotLocked(), false, err
	}
	p.recordSource(event)
	p.revision++
	p.cursor = event.ObservationID
	return p.snapshotLocked(), true, nil
}

func (p *Projector) recordSource(event observation.Envelope) {
	p.recordHighWater(event)
	p.sources[event.SourceID] = SourceState{
		SourceID:          event.SourceID,
		SourceSequence:    event.SourceSequence,
		SourceType:        string(event.SourceType),
		LastObservedAt:    event.ObservedAt,
		LastReceivedAt:    event.ReceivedAt,
		TransformRevision: event.TransformRevision,
		Anomalies:         append([]string(nil), event.Quality.Anomalies...),
	}
}

func (p *Projector) recordHighWater(event observation.Envelope) {
	p.observationIDs[event.ObservationID] = struct{}{}
	p.sourceSequence[event.SourceID] = event.SourceSequence
	p.sourceTransforms[event.SourceID] = event.TransformRevision
}

func (p *Projector) unchangedStaticEntity(event observation.Envelope) bool {
	if event.Kind != observation.EntityUpsert {
		return false
	}
	payload := event.Payload.(observation.EntityPayload)
	previous, exists := p.entities[payload.EntityID]
	source, sourceExists := p.sources[event.SourceID]
	return exists && payload.Attributes["static"] == "true" && previous.Attributes["static"] == "true" &&
		sourceExists && source.SourceType == string(event.SourceType) &&
		source.TransformRevision == event.TransformRevision && slices.Equal(source.Anomalies, event.Quality.Anomalies) &&
		math.Abs(previous.Confidence-event.Confidence) <= 1e-6 && sameCompleteEntityFact(previous, payload)
}

func (p *Projector) Snapshot() Snapshot {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.snapshotLocked()
}

func (p *Projector) reduce(event observation.Envelope) error {
	evidence := evidenceFrom(event)
	switch event.Kind {
	case observation.EntityUpsert:
		payload := event.Payload.(observation.EntityPayload)
		stable := 1
		observationCount := uint64(1)
		if previous, ok := p.entities[payload.EntityID]; ok {
			observationCount = previous.ObservationCount + 1
			if sameEntityFact(previous, payload) {
				stable = previous.StableObservations + 1
			}
		}
		p.entities[payload.EntityID] = EntityState{
			EntityID: payload.EntityID, Category: payload.Category,
			Attributes: cloneStringMap(payload.Attributes), Pose: slices.Clone(payload.Pose),
			Relations: cloneStringMap(payload.Relations), Confidence: event.Confidence,
			ObservationCount: observationCount, StableObservations: stable, Evidence: evidence,
		}
	case observation.EntityDelete:
		payload := event.Payload.(observation.EntityPayload)
		delete(p.entities, payload.EntityID)
	case observation.RobotStateUpsert:
		payload := event.Payload.(observation.RobotPayload)
		p.robots[payload.RobotID] = RobotState{
			RobotID: payload.RobotID, Pose: slices.Clone(payload.Pose), Activity: payload.Activity,
			Held: payload.Held, EmergencyStopped: payload.EmergencyStopped,
			State: cloneFloatMap(payload.State), Confidence: event.Confidence, Evidence: evidence,
		}
	case observation.ResourceUpsert:
		payload := event.Payload.(observation.ResourcePayload)
		p.resources[payload.ResourceID] = ResourceState{
			ResourceID: payload.ResourceID, Owner: payload.Owner, FencingToken: payload.FencingToken,
			ExpiresAt: payload.ExpiresAt, Evidence: evidence,
		}
	case observation.SourceHealth, observation.FrameReference:
		// The accepted source fact is recorded below. Frame bytes are deliberately
		// outside the world event; frame metadata remains in the envelope/cursor.
	default:
		return observation.ErrPayloadType
	}
	return nil
}

func (p *Projector) snapshotLocked() Snapshot {
	now := p.now().UTC()
	result := Snapshot{
		SchemaVersion: SchemaVersion, WorldID: p.worldID, Revision: p.revision,
		EventCursor: p.cursor, ProjectedAt: now,
		Robots: map[string]RobotState{}, Entities: map[string]EntityState{},
		Resources: map[string]ResourceState{}, Sources: map[string]SourceState{},
		ActiveTasks: []string{}, Health: Health{DegradedSources: []string{}, Conflicts: []string{}},
	}
	for id, source := range p.sources {
		source.Anomalies = append([]string(nil), source.Anomalies...)
		if source.SourceType == string(observation.SourceToolResultEvidence) {
			source.Freshness = Fresh
		} else {
			source.Freshness = freshnessAt(now, source.LastObservedAt, p.freshnessBudget)
		}
		if source.Freshness != Fresh || len(source.Anomalies) > 0 {
			result.Health.DegradedSources = append(result.Health.DegradedSources, id)
			if len(source.Anomalies) > 0 {
				source.Freshness = Degraded
			}
		}
		result.Sources[id] = source
	}
	for id, robot := range p.robots {
		robot.Pose = slices.Clone(robot.Pose)
		robot.State = cloneFloatMap(robot.State)
		robot.Freshness = freshnessAt(now, robot.Evidence.ObservedAt, p.freshnessBudget)
		result.Robots[id] = robot
	}
	for id, entity := range p.entities {
		entity.Pose = slices.Clone(entity.Pose)
		entity.Attributes = cloneStringMap(entity.Attributes)
		entity.Relations = cloneStringMap(entity.Relations)
		if entity.Attributes["static"] == "true" {
			// A static entity is an immutable, versioned model/map fact. Its
			// publishing sensor may become stale, but the accepted geometry stays
			// current until an explicit update/delete or transform revision change.
			entity.Freshness = Fresh
		} else {
			entity.Freshness = freshnessAt(now, entity.Evidence.ObservedAt, p.freshnessBudget)
		}
		result.Entities[id] = entity
	}
	for id, resource := range p.resources {
		if !resource.ExpiresAt.IsZero() && !now.Before(resource.ExpiresAt) {
			resource.Freshness = Stale
		} else {
			resource.Freshness = Fresh
		}
		result.Resources[id] = resource
	}
	slices.Sort(result.Health.DegradedSources)
	return result
}

func evidenceFrom(event observation.Envelope) EvidenceRef {
	return EvidenceRef{
		ObservationID: event.ObservationID, SourceID: event.SourceID,
		SourceSequence: event.SourceSequence, ObservedAt: event.ObservedAt,
		FrameID: event.FrameID, TransformRevision: event.TransformRevision,
	}
}

func freshnessAt(now, observedAt time.Time, maxAge time.Duration) Freshness {
	if observedAt.IsZero() {
		return Unknown
	}
	if now.Sub(observedAt) > maxAge {
		return Stale
	}
	return Fresh
}

func sameEntityFact(previous EntityState, next observation.EntityPayload) bool {
	if !equalFloatSlices(previous.Pose, next.Pose) || len(previous.Relations) != len(next.Relations) {
		return false
	}
	for key, value := range previous.Relations {
		if next.Relations[key] != value {
			return false
		}
	}
	return true
}

func sameCompleteEntityFact(previous EntityState, next observation.EntityPayload) bool {
	if previous.Category != next.Category || len(previous.Attributes) != len(next.Attributes) || !sameEntityFact(previous, next) {
		return false
	}
	for key, value := range previous.Attributes {
		if next.Attributes[key] != value {
			return false
		}
	}
	return true
}

func equalFloatSlices(left, right []float64) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if math.Abs(left[index]-right[index]) > 1e-6 {
			return false
		}
	}
	return true
}

func cloneStringMap(source map[string]string) map[string]string {
	if source == nil {
		return nil
	}
	result := make(map[string]string, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func cloneFloatMap(source map[string]float64) map[string]float64 {
	if source == nil {
		return nil
	}
	result := make(map[string]float64, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}
