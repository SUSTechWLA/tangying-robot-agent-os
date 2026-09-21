package agentcontext

import (
	_ "embed"
	"encoding/json"
)

// StageSelection records the renderer actually used, not just the global flag.
type StageSelection struct {
	Stage         string
	Format        string
	PolicyVersion string
}

// StageForRole gives existing callers a stable migration path. New callers should
// declare their decision stage explicitly rather than infer it from raw content.
func StageForRole(role string) string {
	switch role {
	case "ops", "reflection", "recovery", "planning", "goal", "tool_result", "verification", "handoff":
		return role
	default:
		return "unknown"
	}
}

//go:embed stage-policy.json
var embeddedStagePolicy []byte

var stagePolicy = loadStagePolicy()

type routingPolicy struct {
	Version string            `json:"version"`
	Stages  map[string]string `json:"stages"`
}

func loadStagePolicy() routingPolicy {
	var policy routingPolicy
	if err := json.Unmarshal(embeddedStagePolicy, &policy); err != nil {
		panic("invalid built-in context stage policy: " + err.Error())
	}
	if policy.Version == "" || len(policy.Stages) != 8 {
		panic("incomplete built-in context stage policy")
	}
	for stage, format := range policy.Stages {
		if StageForRole(stage) != stage {
			panic("unknown stage in built-in policy")
		}
		switch format {
		case "json", "nl_sections", "nl_decision", "hybrid", "annotated":
		default:
			panic("invalid renderer in built-in policy")
		}
	}
	return policy
}

// Unknown stages retain complete JSON. Explicit format flags remain available.
func SelectionFor(d Document) StageSelection { return Resolve(d, Mode()) }

func Resolve(d Document, mode string) StageSelection {
	stage := d.Stage
	if stage == "" {
		stage = StageForRole(d.Role)
	}
	result := StageSelection{Stage: stage, Format: mode}
	if mode == "stage" {
		result.Format = "json"
		result.PolicyVersion = stagePolicy.Version
		if chosen, ok := stagePolicy.Stages[stage]; ok {
			result.Format = chosen
		}
	}
	return result
}
