package worldmodel

import "time"

type PredicateStatus string

const (
	PredicateTrue    PredicateStatus = "TRUE"
	PredicateFalse   PredicateStatus = "FALSE"
	PredicateUnknown PredicateStatus = "UNKNOWN"
)

const (
	ReasonSatisfied        = "PREDICATE_SATISFIED"
	ReasonEntityMissing    = "ENTITY_UNKNOWN"
	ReasonRobotMissing     = "ROBOT_UNKNOWN"
	ReasonResourceMissing  = "RESOURCE_UNKNOWN"
	ReasonSourceMissing    = "SOURCE_UNKNOWN"
	ReasonEvidenceStale    = "EVIDENCE_STALE"
	ReasonRelationMismatch = "RELATION_MISMATCH"
	ReasonHeldMismatch     = "HELD_MISMATCH"
	ReasonOwnerMismatch    = "OWNER_MISMATCH"
	ReasonNotStable        = "ENTITY_NOT_STABLE"
)

type PredicateResult struct {
	Status   PredicateStatus `json:"status"`
	Reason   string          `json:"reason"`
	Evidence []EvidenceRef   `json:"evidence,omitempty"`
}

type Predicate interface {
	Evaluate(Snapshot) PredicateResult
}

type predicateFunc func(Snapshot) PredicateResult

func (predicate predicateFunc) Evaluate(snapshot Snapshot) PredicateResult {
	return predicate(snapshot)
}

func EntityInside(entityID, zoneID string, maxAge time.Duration) Predicate {
	return predicateFunc(func(snapshot Snapshot) PredicateResult {
		entity, ok := snapshot.Entities[entityID]
		if !ok {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEntityMissing}
		}
		if evidenceStale(snapshot.ProjectedAt, entity.Evidence.ObservedAt, maxAge) {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEvidenceStale, Evidence: []EvidenceRef{entity.Evidence}}
		}
		if entity.Relations["inside"] != zoneID {
			return PredicateResult{Status: PredicateFalse, Reason: ReasonRelationMismatch, Evidence: []EvidenceRef{entity.Evidence}}
		}
		return PredicateResult{Status: PredicateTrue, Reason: ReasonSatisfied, Evidence: []EvidenceRef{entity.Evidence}}
	})
}

func EntityStable(entityID string, observations int, maxAge time.Duration) Predicate {
	return predicateFunc(func(snapshot Snapshot) PredicateResult {
		entity, ok := snapshot.Entities[entityID]
		if !ok {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEntityMissing}
		}
		if evidenceStale(snapshot.ProjectedAt, entity.Evidence.ObservedAt, maxAge) {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEvidenceStale, Evidence: []EvidenceRef{entity.Evidence}}
		}
		if entity.StableObservations < observations {
			return PredicateResult{Status: PredicateFalse, Reason: ReasonNotStable, Evidence: []EvidenceRef{entity.Evidence}}
		}
		return PredicateResult{Status: PredicateTrue, Reason: ReasonSatisfied, Evidence: []EvidenceRef{entity.Evidence}}
	})
}

func RobotHeld(robotID, entityID string, maxAge time.Duration) Predicate {
	return predicateFunc(func(snapshot Snapshot) PredicateResult {
		robot, ok := snapshot.Robots[robotID]
		if !ok {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonRobotMissing}
		}
		if evidenceStale(snapshot.ProjectedAt, robot.Evidence.ObservedAt, maxAge) {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEvidenceStale, Evidence: []EvidenceRef{robot.Evidence}}
		}
		if robot.Held != entityID {
			return PredicateResult{Status: PredicateFalse, Reason: ReasonHeldMismatch, Evidence: []EvidenceRef{robot.Evidence}}
		}
		return PredicateResult{Status: PredicateTrue, Reason: ReasonSatisfied, Evidence: []EvidenceRef{robot.Evidence}}
	})
}

func ResourceOwner(resourceID, owner string, maxAge time.Duration) Predicate {
	return predicateFunc(func(snapshot Snapshot) PredicateResult {
		resource, ok := snapshot.Resources[resourceID]
		if !ok {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonResourceMissing}
		}
		if evidenceStale(snapshot.ProjectedAt, resource.Evidence.ObservedAt, maxAge) {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEvidenceStale, Evidence: []EvidenceRef{resource.Evidence}}
		}
		if resource.Owner != owner {
			return PredicateResult{Status: PredicateFalse, Reason: ReasonOwnerMismatch, Evidence: []EvidenceRef{resource.Evidence}}
		}
		return PredicateResult{Status: PredicateTrue, Reason: ReasonSatisfied, Evidence: []EvidenceRef{resource.Evidence}}
	})
}

func SourceFresh(sourceID string, maxAge time.Duration) Predicate {
	return predicateFunc(func(snapshot Snapshot) PredicateResult {
		source, ok := snapshot.Sources[sourceID]
		if !ok {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonSourceMissing}
		}
		if evidenceStale(snapshot.ProjectedAt, source.LastObservedAt, maxAge) {
			return PredicateResult{Status: PredicateUnknown, Reason: ReasonEvidenceStale}
		}
		return PredicateResult{Status: PredicateTrue, Reason: ReasonSatisfied}
	})
}

func All(predicates ...Predicate) Predicate {
	return predicateFunc(func(snapshot Snapshot) PredicateResult {
		result := PredicateResult{Status: PredicateTrue, Reason: ReasonSatisfied}
		for _, predicate := range predicates {
			current := predicate.Evaluate(snapshot)
			result.Evidence = append(result.Evidence, current.Evidence...)
			if current.Status == PredicateFalse {
				current.Evidence = result.Evidence
				return current
			}
			if current.Status == PredicateUnknown && result.Status == PredicateTrue {
				result.Status = PredicateUnknown
				result.Reason = current.Reason
			}
		}
		return result
	})
}

func evidenceStale(now, observedAt time.Time, maxAge time.Duration) bool {
	return observedAt.IsZero() || maxAge <= 0 || now.Sub(observedAt) > maxAge
}
