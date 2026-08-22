// Package toolcatalog defines immutable, content-addressed robot tool catalogs.
package toolcatalog

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
)

type SideEffectClass string

const (
	ReadOnly       SideEffectClass = "read_only"
	Idempotent     SideEffectClass = "idempotent"
	PhysicalAtomic SideEffectClass = "physical_atomic"
	Emergency      SideEffectClass = "emergency"
)

type Tool struct {
	Name              string          `json:"name"`
	Description       string          `json:"description,omitempty"`
	DisplayName       string          `json:"displayName,omitempty"`
	Purpose           string          `json:"purpose,omitempty"`
	InputParameters   []string        `json:"inputParameters,omitempty"`
	OutputParameters  []string        `json:"outputParameters,omitempty"`
	SafeArgumentNames []string        `json:"safeArgumentNames,omitempty"`
	SideEffectClass   SideEffectClass `json:"sideEffectClass"`
	SafetyLevel       string          `json:"safetyLevel,omitempty"`
	Available         bool            `json:"available"`
}

type Snapshot struct {
	RobotID        string `json:"robotId"`
	AdapterID      string `json:"adapterId"`
	AdapterVersion string `json:"adapterVersion,omitempty"`
	Revision       string `json:"revision"`
	Tools          []Tool `json:"tools"`
}

func NewSnapshot(robotID, adapterID, adapterVersion string, tools []Tool) (Snapshot, error) {
	if strings.TrimSpace(robotID) == "" {
		return Snapshot{}, errors.New("tool catalog robot id is required")
	}
	if strings.TrimSpace(adapterID) == "" {
		return Snapshot{}, errors.New("tool catalog adapter id is required")
	}
	canonical, err := canonicalTools(tools)
	if err != nil {
		return Snapshot{}, err
	}
	revision, err := revisionFromCanonical(canonical)
	if err != nil {
		return Snapshot{}, err
	}
	return Snapshot{
		RobotID:        robotID,
		AdapterID:      adapterID,
		AdapterVersion: adapterVersion,
		Revision:       revision,
		Tools:          canonical,
	}, nil
}

func Revision(tools []Tool) (string, error) {
	canonical, err := canonicalTools(tools)
	if err != nil {
		return "", err
	}
	return revisionFromCanonical(canonical)
}

func canonicalTools(tools []Tool) ([]Tool, error) {
	canonical := make([]Tool, len(tools))
	seen := make(map[string]struct{}, len(tools))
	for index, tool := range tools {
		tool.Name = strings.TrimSpace(tool.Name)
		if tool.Name == "" {
			return nil, errors.New("tool name is required")
		}
		if _, exists := seen[tool.Name]; exists {
			return nil, fmt.Errorf("duplicate tool name %q", tool.Name)
		}
		seen[tool.Name] = struct{}{}
		switch tool.SideEffectClass {
		case ReadOnly, Idempotent, PhysicalAtomic, Emergency:
		default:
			return nil, fmt.Errorf("tool %q has invalid side effect class %q", tool.Name, tool.SideEffectClass)
		}
		canonical[index] = tool
		canonical[index].InputParameters = append([]string(nil), tool.InputParameters...)
		canonical[index].OutputParameters = append([]string(nil), tool.OutputParameters...)
		canonical[index].SafeArgumentNames = append([]string(nil), tool.SafeArgumentNames...)
		sort.Strings(canonical[index].InputParameters)
		sort.Strings(canonical[index].OutputParameters)
		canonical[index].SafeArgumentNames = sortedUnique(canonical[index].SafeArgumentNames)
	}
	sort.Slice(canonical, func(i, j int) bool { return canonical[i].Name < canonical[j].Name })
	return canonical, nil
}

func sortedUnique(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	cleaned := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			cleaned = append(cleaned, value)
		}
	}
	sort.Strings(cleaned)
	unique := cleaned[:0]
	for _, value := range cleaned {
		if len(unique) == 0 || unique[len(unique)-1] != value {
			unique = append(unique, value)
		}
	}
	return unique
}

func revisionFromCanonical(tools []Tool) (string, error) {
	wire, err := json.Marshal(tools)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:]), nil
}
