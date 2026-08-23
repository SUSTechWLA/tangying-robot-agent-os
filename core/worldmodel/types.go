package worldmodel

import "time"

const SchemaVersion = "world.snapshot.v1"

type Freshness string

const (
	Fresh    Freshness = "FRESH"
	Stale    Freshness = "STALE"
	Unknown  Freshness = "UNKNOWN"
	Degraded Freshness = "DEGRADED"
)

type EvidenceRef struct {
	ObservationID     string    `json:"observationId"`
	SourceID          string    `json:"sourceId"`
	SourceSequence    uint64    `json:"sourceSequence"`
	ObservedAt        time.Time `json:"observedAt"`
	FrameID           string    `json:"frameId"`
	TransformRevision string    `json:"transformRevision"`
}

type RobotState struct {
	RobotID          string             `json:"robotId"`
	Pose             []float64          `json:"pose,omitempty"`
	Activity         string             `json:"activity,omitempty"`
	Held             string             `json:"held,omitempty"`
	EmergencyStopped bool               `json:"emergencyStopped"`
	State            map[string]float64 `json:"state,omitempty"`
	Confidence       float64            `json:"confidence"`
	Freshness        Freshness          `json:"freshness"`
	Evidence         EvidenceRef        `json:"evidence"`
}

type EntityState struct {
	EntityID           string            `json:"entityId"`
	Category           string            `json:"category"`
	Attributes         map[string]string `json:"attributes,omitempty"`
	Pose               []float64         `json:"pose,omitempty"`
	Relations          map[string]string `json:"relations,omitempty"`
	Confidence         float64           `json:"confidence"`
	Freshness          Freshness         `json:"freshness"`
	ObservationCount   uint64            `json:"observationCount"`
	StableObservations int               `json:"stableObservations"`
	Evidence           EvidenceRef       `json:"evidence"`
}

type ResourceState struct {
	ResourceID   string      `json:"resourceId"`
	Owner        string      `json:"owner"`
	FencingToken uint64      `json:"fencingToken"`
	ExpiresAt    time.Time   `json:"expiresAt,omitempty"`
	Freshness    Freshness   `json:"freshness"`
	Evidence     EvidenceRef `json:"evidence"`
}

type SourceState struct {
	SourceID          string    `json:"sourceId"`
	SourceSequence    uint64    `json:"sourceSequence"`
	SourceType        string    `json:"sourceType"`
	LastObservedAt    time.Time `json:"lastObservedAt"`
	LastReceivedAt    time.Time `json:"lastReceivedAt"`
	TransformRevision string    `json:"transformRevision"`
	Freshness         Freshness `json:"freshness"`
	Anomalies         []string  `json:"anomalies,omitempty"`
}

type Health struct {
	DegradedSources []string `json:"degradedSources"`
	Conflicts       []string `json:"conflicts"`
}

type Snapshot struct {
	SchemaVersion string                   `json:"schemaVersion"`
	WorldID       string                   `json:"worldId"`
	Revision      uint64                   `json:"revision"`
	EventCursor   string                   `json:"eventCursor"`
	ProjectedAt   time.Time                `json:"projectedAt"`
	Robots        map[string]RobotState    `json:"robots"`
	Entities      map[string]EntityState   `json:"entities"`
	Resources     map[string]ResourceState `json:"resources"`
	Sources       map[string]SourceState   `json:"sources"`
	ActiveTasks   []string                 `json:"activeTasks"`
	Health        Health                   `json:"health"`
}

type Delta struct {
	SchemaVersion string   `json:"schemaVersion"`
	WorldID       string   `json:"worldId"`
	Revision      uint64   `json:"revision"`
	Previous      uint64   `json:"previousRevision"`
	EventCursor   string   `json:"eventCursor"`
	ObservationID string   `json:"observationId"`
	Snapshot      Snapshot `json:"snapshot"`
}
