package agentcontext

import (
	"fmt"
	"os"
)

// ProductionFactors exposes only lossless interventions. Critical-field masking
// exists exclusively in the eval generator and is never a production option.
func ProductionFactors() (FactorSpec, error) {
	s := FactorSpec{Syntax: "json", Order: "source"}
	if v := os.Getenv("TANGYING_CONTEXT_SYNTAX"); v != "" {
		s.Syntax = v
	}
	if v := os.Getenv("TANGYING_CONTEXT_ORDER"); v != "" {
		s.Order = v
	}
	switch os.Getenv("TANGYING_CONTEXT_ANNOTATION") {
	case "", "0":
	case "1":
		s.Annotation = true
	default:
		return s, fmt.Errorf("context annotation must be 0 or 1")
	}
	if s.Syntax != "json" && s.Syntax != "cnl" && s.Syntax != "nested_json" && s.Syntax != "entity_cnl" && s.Syntax != "contract_json" && s.Syntax != "contract_nested_json" || s.Order != "source" && s.Order != "decision" {
		return s, fmt.Errorf("invalid lossless context format")
	}
	if (s.Syntax == "nested_json" || s.Syntax == "entity_cnl" || s.Syntax == "contract_nested_json") && (s.Order != "source" || s.Annotation) {
		return s, fmt.Errorf("hierarchical expression requires source/raw")
	}
	return s, nil
}
