// Package capability defines provider-owned contracts shared by goal planning,
// execution and presentation. No simulator or robot morphology is encoded here.
package capability

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

type Verification struct {
	Service  string            `json:"service"`
	Required []string          `json:"required,omitempty"`
	Match    map[string]string `json:"match,omitempty"`
}
type Operation struct {
	LeaseSupported     bool     `json:"leaseSupported,omitempty"`
	StatusService      string   `json:"statusService"`
	CancelService      string   `json:"cancelService"`
	IdentityPath       string   `json:"identityPath"`
	StatusIdentityPath string   `json:"statusIdentityPath"`
	StatePath          string   `json:"statePath"`
	Success            []string `json:"success"`
	Failure            []string `json:"failure"`
	Running            []string `json:"running"`
}
type Contract struct {
	Version   string   `json:"version"`
	Effects   []string `json:"effects"`
	Resources []string `json:"resources,omitempty"`
	// PlanningFields are top-level structured result fields that the provider
	// explicitly permits a GOAL model to inspect before approval. An empty list
	// keeps the read service outside the model's planning context.
	PlanningFields []string       `json:"planningFields,omitempty"`
	Verification   *Verification  `json:"verification,omitempty"`
	Operation      *Operation     `json:"operation,omitempty"`
	OutputSchema   map[string]any `json:"outputSchema,omitempty"`
}
type Manifest struct {
	Name         string         `json:"name"`
	Description  string         `json:"description"`
	InputSchema  map[string]any `json:"inputSchema"`
	MutatesWorld bool           `json:"mutatesWorld"`
	Contract     Contract       `json:"contract"`
}
type Call struct {
	LegacyIntent json.RawMessage `json:"legacyIntent,omitempty"`
	Tool         string          `json:"tool"`
	Arguments    map[string]any  `json:"arguments"`
}
type Plan struct {
	RobotID         string         `json:"robotId"`
	CatalogRevision string         `json:"catalogRevision"`
	Calls           []Call         `json:"calls"`
	PlanningTrace   []PlanningStep `json:"planningTrace,omitempty"`
}

// PlanningStep records a model decision made before the approved plan was
// frozen. Only read-only provider calls may execute during this phase.
type PlanningStep struct {
	Context   *agentcontext.Projection `json:"context,omitempty"`
	Round     int                      `json:"round"`
	Tool      string                   `json:"tool"`
	Arguments map[string]any           `json:"arguments,omitempty"`
	Result    map[string]any           `json:"result,omitempty"`
	Verdict   string                   `json:"verdict"`
	Detail    string                   `json:"detail,omitempty"`
}

func Fingerprint(value any) string {
	wire, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return fmt.Sprintf("%x", sha256.Sum256(wire))
}
func (m Manifest) Validate() error {
	if m.Name == "" {
		return fmt.Errorf("capability name is required")
	}
	if m.InputSchema["type"] != "object" {
		return fmt.Errorf("%s requires an object input schema", m.Name)
	}
	if err := ValidateSchema(m.InputSchema); err != nil {
		return fmt.Errorf("%s: %w", m.Name, err)
	}
	if !m.MutatesWorld && len(m.Contract.Effects) == 0 {
		return nil
	} // conservative legacy reads
	if m.Contract.Version != "1" || len(m.Contract.Effects) == 0 {
		return fmt.Errorf("%s has no supported effect contract", m.Name)
	}
	readOnly := true
	for _, effect := range m.Contract.Effects {
		switch effect {
		case "READ":
		case "ARTIFACT_WRITE", "CONFIG_CHANGE", "PHYSICAL_MOTION", "SAFETY_STOP":
			readOnly = false
		default:
			return fmt.Errorf("unknown effect %s", effect)
		}
	}
	if m.MutatesWorld == readOnly {
		return fmt.Errorf("%s has contradictory mutation and effect declarations", m.Name)
	}
	if !readOnly && (len(m.Contract.Resources) == 0 || m.Contract.Verification == nil) {
		return fmt.Errorf("%s requires resources and completion contract", m.Name)
	}
	if m.Contract.OutputSchema != nil {
		if err := ValidateSchema(m.Contract.OutputSchema); err != nil {
			return err
		}
	}
	if v := m.Contract.Verification; v != nil && (v.Service == "" || (len(v.Required) == 0 && len(v.Match) == 0)) {
		return fmt.Errorf("%s has empty verification contract", m.Name)
	}
	if op := m.Contract.Operation; op != nil {
		if op.StatusService == "" || op.CancelService == "" || op.IdentityPath == "" || op.StatusIdentityPath == "" || op.StatePath == "" || len(op.Success) == 0 || len(op.Running) == 0 {
			return fmt.Errorf("%s has incomplete operation contract", m.Name)
		}
		states := map[string]bool{}
		for _, group := range [][]string{op.Success, op.Failure, op.Running} {
			if len(group) == 0 {
				return fmt.Errorf("%s has empty operation states", m.Name)
			}
			for _, state := range group {
				if state == "" || states[state] {
					return fmt.Errorf("%s has overlapping or empty operation states", m.Name)
				}
				states[state] = true
			}
		}
	}
	return nil
}
