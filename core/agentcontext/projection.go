package agentcontext

import (
	"encoding/json"
	"fmt"
)

// Projection is the exact input retained in the decision trace. A stage policy
// must say both which consumer is deciding and which concrete renderer ran.
type Projection struct {
	Scope           Scope            `json:"scope"`
	Compaction      *Compaction      `json:"compaction,omitempty"`
	Artifacts       []Artifact       `json:"artifacts,omitempty"`
	SourceMetadata  *DecisionContext `json:"source_metadata,omitempty"`
	SchemaVersion   string           `json:"schema_version"`
	RendererVersion string           `json:"renderer_version"`
	Stage           string           `json:"stage,omitempty"`
	Format          string           `json:"format"`
	PolicyVersion   string           `json:"policy_version,omitempty"`
	SHA256          string           `json:"sha256"`
	Text            string           `json:"text"`
	SemanticSHA256  string           `json:"semantic_sha256,omitempty"`
	Factors         *FactorSpec      `json:"factors,omitempty"`
	Model           string           `json:"model,omitempty"`
	ModelRequest    json.RawMessage  `json:"model_request,omitempty"`
	// A string preserves exact transport bytes across map/SQLite/HTTP projections.
	// model_request remains a convenient structured view for older consumers.
	ModelRequestJSON   string `json:"model_request_json,omitempty"`
	ModelRequestSHA256 string `json:"model_request_sha256,omitempty"`
}

func (p Projection) ModelRequestBytes() ([]byte, error) {
	data := []byte(p.ModelRequest)
	if p.ModelRequestJSON != "" {
		data = []byte(p.ModelRequestJSON)
	}
	if len(data) == 0 || !json.Valid(data) || Hash(string(data)) != p.ModelRequestSHA256 {
		return nil, fmt.Errorf("exact model request missing or digest mismatch")
	}
	return append([]byte(nil), data...), nil
}

func Project(d Document, stage string) (Projection, error) {
	if stage != "" {
		d.Stage = stage
	}
	if Mode() == "factorial" {
		spec, err := ProductionFactors()
		if err != nil {
			return Projection{}, err
		}
		p := DecisionFrom(d)
		r, err := RenderFactors(p, spec)
		if err != nil {
			return Projection{}, err
		}
		return Projection{SchemaVersion: DecisionVersion, RendererVersion: r.Version, Stage: p.Stage, Format: "factorial", PolicyVersion: "factorial-fixed.v1", SHA256: r.SHA256, SemanticSHA256: r.SemanticSHA256, Factors: &spec, Text: r.Text}, nil
	}
	selection := SelectionFor(d)
	text, err := Render(d, selection.Format)
	if err != nil {
		return Projection{}, err
	}
	return Projection{SchemaVersion: Version, RendererVersion: RendererVersion, Stage: selection.Stage, Format: selection.Format, PolicyVersion: selection.PolicyVersion, SHA256: Hash(text), Text: text}, nil
}
