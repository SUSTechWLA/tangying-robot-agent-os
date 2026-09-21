package agentcontext

// Projection is the exact input retained in the decision trace. A stage policy
// must say both which consumer is deciding and which concrete renderer ran.
type Projection struct {
	SchemaVersion   string      `json:"schema_version"`
	RendererVersion string      `json:"renderer_version"`
	Stage           string      `json:"stage,omitempty"`
	Format          string      `json:"format"`
	PolicyVersion   string      `json:"policy_version,omitempty"`
	SHA256          string      `json:"sha256"`
	Text            string      `json:"text"`
	SemanticSHA256  string      `json:"semantic_sha256,omitempty"`
	Factors         *FactorSpec `json:"factors,omitempty"`
	Model           string      `json:"model,omitempty"`
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
