package observation

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

type SourceDescriptor struct {
	SourceID          string        `json:"sourceId"`
	SourceType        SourceType    `json:"sourceType"`
	SchemaRevision    string        `json:"schemaRevision,omitempty"`
	Kind              Kind          `json:"kind"`
	FrameID           string        `json:"frameId"`
	FrameIDs          []string      `json:"frameIds,omitempty"`
	TransformRevision string        `json:"transformRevision"`
	MaxRateHz         float64       `json:"maxRateHz,omitempty"`
	MaxAge            time.Duration `json:"maxAge"`
	AdapterVersion    string        `json:"adapterVersion,omitempty"`
	Required          bool          `json:"required"`
}

type Catalog struct {
	RobotID  string             `json:"robotId"`
	Revision string             `json:"revision"`
	Sources  []SourceDescriptor `json:"sources"`
}

func NewCatalog(robotID string, sources []SourceDescriptor) (Catalog, error) {
	if strings.TrimSpace(robotID) == "" {
		return Catalog{}, errors.New("observation catalog robot id is required")
	}
	canonical, err := canonicalSources(sources)
	if err != nil {
		return Catalog{}, err
	}
	revision, err := revisionFromSources(canonical)
	if err != nil {
		return Catalog{}, err
	}
	return Catalog{RobotID: robotID, Revision: revision, Sources: canonical}, nil
}

func CatalogRevision(sources []SourceDescriptor) (string, error) {
	canonical, err := canonicalSources(sources)
	if err != nil {
		return "", err
	}
	return revisionFromSources(canonical)
}

func canonicalSources(sources []SourceDescriptor) ([]SourceDescriptor, error) {
	canonical := make([]SourceDescriptor, len(sources))
	seen := make(map[string]struct{}, len(sources))
	for index, source := range sources {
		source.SourceID = strings.TrimSpace(source.SourceID)
		if source.SourceID == "" || source.SourceType == "" || source.Kind == "" ||
			strings.TrimSpace(source.FrameID) == "" || strings.TrimSpace(source.TransformRevision) == "" {
			return nil, fmt.Errorf("observation source %d has incomplete identity", index)
		}
		if source.MaxAge <= 0 {
			return nil, fmt.Errorf("observation source %q has invalid freshness budget", source.SourceID)
		}
		if _, exists := seen[source.SourceID]; exists {
			return nil, fmt.Errorf("duplicate observation source %q", source.SourceID)
		}
		seen[source.SourceID] = struct{}{}
		canonical[index] = source
		canonical[index].FrameIDs = append([]string(nil), source.FrameIDs...)
		sort.Strings(canonical[index].FrameIDs)
	}
	sort.Slice(canonical, func(i, j int) bool { return canonical[i].SourceID < canonical[j].SourceID })
	return canonical, nil
}

func revisionFromSources(sources []SourceDescriptor) (string, error) {
	wire, err := json.Marshal(sources)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:]), nil
}
