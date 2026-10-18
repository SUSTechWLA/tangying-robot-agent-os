package exchangecontract

import "errors"

const CoverageSchema = "agent.coverage.advisory.v1"
const CoverageQuerySchema = "agent.coverage.query.v1"

// CoverageReport is a peer's scoped sensor claim, not an execution authority or
// proof of physical coverage. Every consumer must independently verify evidence.
type CoverageReport struct {
	SchemaVersion     string   `json:"schemaVersion"`
	MissionID         string   `json:"missionId"`
	MissionRevision   string   `json:"missionRevision"`
	ObservedAtMS      int64    `json:"observedAtMs"`
	GeometryID        string   `json:"geometryId"`
	ObservedTargetIDs []string `json:"observedTargetIds"`
}
type CoverageQuery struct {
	SchemaVersion   string   `json:"schemaVersion"`
	MissionID       string   `json:"missionId"`
	MissionRevision string   `json:"missionRevision"`
	Basis           Basis    `json:"basis"`
	GeometryID      string   `json:"geometryId"`
	TargetIDs       []string `json:"targetIds"`
	MaxAgeMS        int64    `json:"maxAgeMs"`
	Limit           int      `json:"limit"`
}

func ValidateTargetIDs(ids []string) error {
	if len(ids) == 0 || len(ids) > 4096 {
		return errors.New("coverage requires 1..4096 target identifiers")
	}
	seen := map[string]bool{}
	for _, id := range ids {
		if !ValidIdentifier(id) || seen[id] {
			return errors.New("invalid or duplicate coverage target")
		}
		seen[id] = true
	}
	return nil
}
func (q CoverageQuery) Validate() error {
	if q.SchemaVersion != CoverageQuerySchema || !ValidIdentifier(q.MissionID) || !ValidIdentifier(q.MissionRevision) || !ValidIdentifier(q.GeometryID) {
		return errors.New("explicit coverage mission, revision and geometry required")
	}
	b := q.Basis
	if !ValidIdentifier(b.WorldID) || !ValidIdentifier(b.FrameID) || !ValidIdentifier(b.TransformRevision) || !ValidIdentifier(b.MapID) || !ValidIdentifier(b.MapRevision) {
		return errors.New("complete coverage spatial basis required")
	}
	if q.MaxAgeMS < 1 || q.MaxAgeMS > MaxTTLMS || q.Limit < 1 || q.Limit > 1000 {
		return errors.New("invalid coverage age or limit")
	}
	return ValidateTargetIDs(q.TargetIDs)
}
