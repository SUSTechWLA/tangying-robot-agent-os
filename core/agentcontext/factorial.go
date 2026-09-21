package agentcontext

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
)

const FactorialVersion = "factorial-renderer.v1.2"

// These are independent interventions. Production callers never mask fields.
type FactorSpec struct {
	Syntax     string `json:"syntax"`
	Order      string `json:"order"`
	Annotation bool   `json:"annotation"`
}
type Atom struct {
	Path      string `json:"path"`
	Container string `json:"container,omitempty"`
	Value     any    `json:"value"`
}
type FactRendering struct {
	Text           string     `json:"text"`
	SHA256         string     `json:"sha256"`
	SemanticSHA256 string     `json:"semantic_sha256"`
	Version        string     `json:"renderer_version"`
	Spec           FactorSpec `json:"factors"`
}

func escapePointer(v string) string {
	return strings.ReplaceAll(strings.ReplaceAll(v, "~", "~0"), "/", "~1")
}
func unescapePointer(v string) string {
	return strings.ReplaceAll(strings.ReplaceAll(v, "~1", "/"), "~0", "~")
}
func flatten(v any, path string, out *[]Atom) {
	switch x := v.(type) {
	case map[string]any:
		*out = append(*out, Atom{Path: path, Container: "object"})
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			flatten(x[k], path+"/"+escapePointer(k), out)
		}
	case []any:
		*out = append(*out, Atom{Path: path, Container: "array"})
		for i, a := range x {
			flatten(a, fmt.Sprintf("%s/%d", path, i), out)
		}
	default:
		*out = append(*out, Atom{Path: path, Value: v})
	}
}
func normalized(v any) (map[string]any, error) {
	b, e := json.Marshal(v)
	if e != nil {
		return nil, e
	}
	var out map[string]any
	d := json.NewDecoder(strings.NewReader(string(b)))
	d.UseNumber()
	e = d.Decode(&out)
	return out, e
}

// Applicable only checks metadata. The result is not a physical verdict.
func Applicable(p DecisionContext, r DecisionRecord) []string {
	reasons := []string{}
	eq := func(a, b *string) bool { return a != nil && b != nil && *a == *b }
	if !eq(p.Scope.TaskID, r.Scope.TaskID) {
		reasons = append(reasons, "task_scope_unknown_or_mismatch")
	}
	if !eq(p.Scope.RobotID, r.Scope.RobotID) {
		reasons = append(reasons, "robot_scope_unknown_or_mismatch")
	}
	if p.Scope.Revision == nil || r.Scope.Revision == nil || *p.Scope.Revision != *r.Scope.Revision {
		reasons = append(reasons, "revision_unknown_or_mismatch")
	}
	if !eq(p.Scope.EpisodeID, r.Scope.EpisodeID) {
		reasons = append(reasons, "episode_unknown_or_mismatch")
	}
	if p.Snapshot.ClockDomain != "utc" || r.ClockDomain != "utc" {
		reasons = append(reasons, "incomparable_clock_domain")
	}
	if p.Snapshot.AsOfMS == nil || r.ObservedMS == nil || r.ValidUntilMS == nil {
		reasons = append(reasons, "time_unknown")
	} else {
		if *r.ObservedMS > *p.Snapshot.AsOfMS {
			reasons = append(reasons, "future_record")
		}
		if *r.ValidUntilMS <= *p.Snapshot.AsOfMS {
			reasons = append(reasons, "expired_record")
		}
		if *r.ValidUntilMS <= *r.ObservedMS {
			reasons = append(reasons, "invalid_time_window")
		}
	}
	return reasons
}
func annotations(p DecisionContext) map[string]any {
	base := map[string][]string{}
	byID := map[string]DecisionRecord{}
	for _, r := range p.Records {
		base[r.ID] = Applicable(p, r)
		byID[r.ID] = r
	}
	out := map[string]any{}
	for _, r := range p.Records {
		why := append([]string{}, base[r.ID]...)
		for _, newer := range p.Records {
			if len(base[newer.ID]) > 0 || newer.Kind != r.Kind || (newer.Kind != "verification" && newer.Kind != "system" && newer.Kind != "guard") || newer.ID == r.ID {
				continue
			}
			if compact(newer.Scope) != compact(r.Scope) || compact(newer.StepID) != compact(r.StepID) || compact(newer.AttemptID) != compact(r.AttemptID) {
				continue
			}
			for _, id := range newer.Supersedes {
				if id == r.ID && newer.ObservedMS != nil && r.ObservedMS != nil && *newer.ObservedMS >= *r.ObservedMS {
					why = append(why, "superseded_by:"+newer.ID)
				}
			}
		}
		sort.Strings(why)
		out[r.ID] = map[string]any{"metadata_applicable": len(why) == 0, "reasons": why, "source_kind": r.Kind}
	}
	return out
}
func RenderFactors(p DecisionContext, spec FactorSpec) (FactRendering, error) {
	if strings.HasPrefix(spec.Syntax, "contract_") {
		base := spec
		base.Syntax = strings.TrimPrefix(spec.Syntax, "contract_")
		if base.Syntax != "json" && base.Syntax != "nested_json" {
			return FactRendering{}, fmt.Errorf("unsupported decision-contract syntax")
		}
		r, err := RenderFactors(WithDecisionContract(p), base)
		r.Spec, r.Version = spec, "decision-contract-renderer.v1"
		return r, err
	}
	if spec.Syntax == "nested_json" || spec.Syntax == "entity_cnl" {
		return RenderHierarchical(p, spec)
	}
	if err := p.Validate(); err != nil {
		return FactRendering{}, err
	}
	if spec.Syntax != "json" && spec.Syntax != "cnl" || spec.Order != "source" && spec.Order != "decision" {
		return FactRendering{}, fmt.Errorf("invalid factorial settings")
	}
	raw, err := normalized(p)
	if err != nil {
		return FactRendering{}, err
	}
	semantic := Hash(compact(raw))
	var atoms []Atom
	flatten(raw, "", &atoms)
	if spec.Annotation {
		flatten(annotations(p), "/derived_metadata", &atoms)
	}
	if spec.Order == "decision" {
		// Reorder whole record subtrees; JSON Pointer identities never change.
		priority := func(path string) (int, int64) {
			fields := strings.Split(path, "/")
			if len(fields) > 2 && fields[1] == "records" {
				i, e := strconv.Atoi(fields[2])
				if e == nil && i >= 0 && i < len(p.Records) {
					r := p.Records[i]
					ranks := map[string]int{"guard": 0, "verification": 1, "observation": 2, "system": 3, "user": 4, "tool_return": 5, "hypothesis": 6}
					rank, ok := ranks[r.Kind]
					if !ok {
						rank = 7
					}
					var t int64
					if r.ObservedMS != nil {
						t = *r.ObservedMS
					}
					return rank, -t
				}
			}
			return -1, 0
		}
		sort.SliceStable(atoms, func(i, j int) bool {
			a, x := priority(atoms[i].Path)
			b, y := priority(atoms[j].Path)
			if a != b {
				return a < b
			}
			return x < y
		})
	}
	var text string
	if spec.Syntax == "json" {
		text = compact(atoms)
	} else {
		var b strings.Builder
		for _, a := range atoms {
			if a.Container != "" {
				fmt.Fprintf(&b, "字段 %s 的容器类型为 %s。\n", compact(a.Path), compact(a.Container))
			} else {
				fmt.Fprintf(&b, "字段 %s 的值为 %s。\n", compact(a.Path), compact(a.Value))
			}
		}
		text = b.String()
	}
	return FactRendering{Text: text, SHA256: Hash(text), SemanticSHA256: semantic, Version: FactorialVersion, Spec: spec}, nil
}

// ParseFactorText is an executable left inverse, including null/false/zero and
// empty containers. It treats quoted content as data, never instructions.
func ParseFactorText(text, syntax string) (map[string]any, error) {
	syntax = strings.TrimPrefix(syntax, "contract_")
	if syntax == "nested_json" {
		var out map[string]any
		d := json.NewDecoder(strings.NewReader(text))
		d.UseNumber()
		if err := d.Decode(&out); err != nil {
			return nil, err
		}
		if d.Decode(new(any)) != io.EOF {
			return nil, fmt.Errorf("trailing JSON")
		}
		return out, nil
	}
	if syntax == "entity_cnl" {
		syntax = "cnl"
	}
	var atoms []Atom
	if syntax == "json" {
		d := json.NewDecoder(strings.NewReader(text))
		d.UseNumber()
		if err := d.Decode(&atoms); err != nil {
			return nil, err
		}
	} else if syntax == "cnl" {
		for _, line := range strings.Split(strings.TrimSuffix(text, "\n"), "\n") {
			if !strings.HasPrefix(line, "字段 ") || !strings.HasSuffix(line, "。") {
				return nil, fmt.Errorf("invalid controlled sentence")
			}
			part := strings.TrimSuffix(strings.TrimPrefix(line, "字段 "), "。")
			d := json.NewDecoder(strings.NewReader(part))
			var path string
			if err := d.Decode(&path); err != nil {
				return nil, err
			}
			rest := part[d.InputOffset():]
			a := Atom{Path: path}
			if strings.HasPrefix(rest, " 的容器类型为 ") {
				if err := json.Unmarshal([]byte(strings.TrimPrefix(rest, " 的容器类型为 ")), &a.Container); err != nil {
					return nil, err
				}
			} else if strings.HasPrefix(rest, " 的值为 ") {
				decoder := json.NewDecoder(strings.NewReader(strings.TrimPrefix(rest, " 的值为 ")))
				decoder.UseNumber()
				if err := decoder.Decode(&a.Value); err != nil {
					return nil, err
				}
				if decoder.Decode(new(any)) != io.EOF {
					return nil, fmt.Errorf("trailing value")
				}
			} else {
				return nil, fmt.Errorf("invalid sentence predicate")
			}
			atoms = append(atoms, a)
		}
	} else {
		return nil, fmt.Errorf("unknown syntax")
	}
	// Build by path depth; natural order and semantic ordering decode identically.
	sort.SliceStable(atoms, func(i, j int) bool { return strings.Count(atoms[i].Path, "/") < strings.Count(atoms[j].Path, "/") })
	nodes := map[string]any{}
	seen := map[string]bool{}
	for _, a := range atoms {
		if seen[a.Path] {
			return nil, fmt.Errorf("duplicate path")
		}
		seen[a.Path] = true
		var v any = a.Value
		if a.Container == "object" {
			v = map[string]any{}
		} else if a.Container == "array" {
			v = []any{}
		} else if a.Container != "" {
			return nil, fmt.Errorf("unknown container")
		}
		nodes[a.Path] = v
	}
	// Fill deepest containers first so slices are attached only after growth.
	sort.SliceStable(atoms, func(i, j int) bool { return strings.Count(atoms[i].Path, "/") > strings.Count(atoms[j].Path, "/") })
	for _, a := range atoms {
		if a.Path == "" {
			continue
		}
		split := strings.LastIndex(a.Path, "/")
		if split < 0 {
			return nil, fmt.Errorf("invalid pointer")
		}
		parent, key := a.Path[:split], unescapePointer(a.Path[split+1:])
		value := nodes[a.Path]
		switch p := nodes[parent].(type) {
		case map[string]any:
			p[key] = value
		case []any:
			i, e := strconv.Atoi(key)
			if e != nil || i < 0 || i > 100000 {
				return nil, fmt.Errorf("invalid array index")
			}
			for len(p) <= i {
				p = append(p, nil)
			}
			p[i] = value
			nodes[parent] = p
		default:
			return nil, fmt.Errorf("missing parent")
		}
	}
	out, ok := nodes[""].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("missing root object")
	}
	return out, nil
}
