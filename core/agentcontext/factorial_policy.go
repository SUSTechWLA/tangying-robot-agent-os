package agentcontext

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

type FactorPolicy struct {
	Version            string                           `json:"version"`
	Status             string                           `json:"status"`
	ConfirmationSHA256 string                           `json:"confirmation_sha256"`
	Models             map[string]map[string]FactorSpec `json:"models"`
	BlockedStages      map[string]map[string]string     `json:"blocked_stages,omitempty"`
}

// ProjectWithFactorPolicy is an explicit opt-in seam. The caller supplies the
// actual configured model, not a guessed environment alias. No global policy
// or hardware permission changes are made by loading a research profile.
func ProjectWithFactorPolicy(d Document, stage, model string, raw []byte) (Projection, error) {
	var policy FactorPolicy
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&policy); err != nil {
		return Projection{}, err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return Projection{}, fmt.Errorf("trailing policy data")
	}
	if policy.Version != "factorial-policy.v1" || policy.Status != "experimental_opt_in" || len(policy.ConfirmationSHA256) != 64 {
		return Projection{}, fmt.Errorf("unsupported or unbound research policy")
	}
	if stage == "" {
		stage = d.Stage
	}
	if stage == "" {
		stage = StageForRole(d.Role)
	}
	if reason := policy.BlockedStages[model][stage]; reason != "" {
		return Projection{}, fmt.Errorf("research expression is not eligible for model %q stage %q: %s", model, stage, reason)
	}
	spec, ok := policy.Models[model][stage]
	if !ok {
		return Projection{}, fmt.Errorf("no validated expression for model %q stage %q", model, stage)
	}
	d.Stage = stage
	p := DecisionFrom(d)
	r, err := RenderFactors(p, spec)
	if err != nil {
		return Projection{}, err
	}
	return Projection{SchemaVersion: DecisionVersion, RendererVersion: r.Version, Stage: stage,
		Format: "factorial", PolicyVersion: policy.Version + ":" + Hash(string(raw)), Model: model,
		SHA256: r.SHA256, SemanticSHA256: r.SemanticSHA256, Factors: &spec, Text: r.Text}, nil
}
