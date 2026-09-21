package agentcontext

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// RenderHierarchical preserves entity relationships without deriving answers.
// This is a separate, post-factorial candidate family, not an unrecorded change
// to the frozen JSON Pointer treatments.
func RenderHierarchical(p DecisionContext, spec FactorSpec) (FactRendering, error) {
	if err := p.Validate(); err != nil {
		return FactRendering{}, err
	}
	if spec.Order != "source" || spec.Annotation {
		return FactRendering{}, fmt.Errorf("hierarchical candidates require source/raw")
	}
	raw, err := normalized(p)
	if err != nil {
		return FactRendering{}, err
	}
	semantic := Hash(compact(raw))
	var text string
	if spec.Syntax == "nested_json" {
		b, err := json.MarshalIndent(raw, "", "  ")
		if err != nil {
			return FactRendering{}, err
		}
		text = string(b)
	} else if spec.Syntax == "entity_cnl" {
		var b strings.Builder
		fmt.Fprintln(&b, "字段 \"\" 的容器类型为 \"object\"。")
		keys := make([]string, 0, len(raw))
		for k := range raw {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			path := "/" + escapePointer(k)
			if items, ok := raw[k].([]any); ok {
				fmt.Fprintf(&b, "字段 %s 的容器类型为 \"array\"。\n", compact(path))
				for i, item := range items {
					fmt.Fprintf(&b, "字段 %s 的值为 %s。\n", compact(fmt.Sprintf("%s/%d", path, i)), compact(item))
				}
			} else {
				fmt.Fprintf(&b, "字段 %s 的值为 %s。\n", compact(path), compact(raw[k]))
			}
		}
		text = b.String()
	} else {
		return FactRendering{}, fmt.Errorf("unknown hierarchical syntax")
	}
	return FactRendering{Text: text, SHA256: Hash(text), SemanticSHA256: semantic, Version: "hierarchical-renderer.v1", Spec: spec}, nil
}
