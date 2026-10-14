// Package objective defines a cloud-neutral, bounded delegation contract.
// A goal is authority to plan inside an envelope, never authority to invent it.
package objective

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
)

const Version = "robot.objective.v1"
const Tool = "edge.goal"

type Grant struct {
	Tool          string         `json:"tool"`
	Arguments     map[string]any `json:"argumentsSchema"`
	MaxExecutions int            `json:"maxExecutions,omitempty"`
}
type Check struct {
	Tool      string         `json:"tool"`
	Arguments map[string]any `json:"arguments"`
	Path      []string       `json:"path"`
	Equals    any            `json:"equals"`
}
type Budget struct {
	MaxPlans       int `json:"maxPlans"`
	MaxSteps       int `json:"maxSteps"`
	MaxReads       int `json:"maxReads"`
	TimeoutSeconds int `json:"timeoutSeconds"`
}
type Contract struct {
	SchemaVersion   string    `json:"schemaVersion"`
	RobotID         string    `json:"robotId"`
	Goal            string    `json:"goal"`
	CatalogRevision string    `json:"catalogRevision"`
	Grants          []Grant   `json:"grants"`
	Effects         []string  `json:"effects"`
	Resources       []string  `json:"resources"`
	Completion      []Check   `json:"completion"`
	Budget          Budget    `json:"budget"`
	ExpiresAt       time.Time `json:"expiresAt"`
}

func (c Contract) Validate() error {
	if c.SchemaVersion != Version || strings.TrimSpace(c.RobotID) == "" || strings.TrimSpace(c.Goal) == "" || len(c.Goal) > 4000 || len(c.CatalogRevision) != 64 || c.ExpiresAt.IsZero() {
		return fmt.Errorf("invalid objective identity, goal, catalog or expiry")
	}
	if c.Budget.MaxPlans < 1 || c.Budget.MaxPlans > 8 || c.Budget.MaxSteps < 1 || c.Budget.MaxSteps > 128 || c.Budget.MaxReads < 1 || c.Budget.MaxReads > 4096 || c.Budget.TimeoutSeconds < 1 || c.Budget.TimeoutSeconds > 86400 {
		return fmt.Errorf("invalid objective budget")
	}
	if len(c.Resources) != 1 || c.Resources[0] != "robot" {
		return fmt.Errorf("v1 delegates only the bound robot resource")
	}
	if len(c.Grants) < 1 || len(c.Grants) > 64 || len(c.Completion) < 1 || len(c.Completion) > 16 {
		return fmt.Errorf("bounded grants and completion checks required")
	}
	allowed := map[string]bool{}
	for _, e := range c.Effects {
		if allowed[e] {
			return fmt.Errorf("duplicate effect")
		}
		allowed[e] = true
		switch e {
		case "READ", "PHYSICAL_MOTION", "ARTIFACT_WRITE", "CONFIG_CHANGE":
		default:
			return fmt.Errorf("unsupported delegated effect %s", e)
		}
	}
	if !allowed["READ"] {
		return fmt.Errorf("READ authority is required for verification")
	}
	names := map[string]bool{}
	for _, g := range c.Grants {
		if g.MaxExecutions < 0 || g.MaxExecutions > c.Budget.MaxSteps {
			return fmt.Errorf("invalid grant execution quota")
		}
		if g.Tool == "" || len(g.Tool) > 128 || names[g.Tool] || g.Tool == Tool || g.Arguments["type"] != "object" {
			return fmt.Errorf("invalid or duplicate objective grant")
		}
		names[g.Tool] = true
		if err := capability.ValidateSchema(g.Arguments); err != nil {
			return err
		}
	}
	for _, check := range c.Completion {
		if !names[check.Tool] || len(check.Path) < 1 || len(check.Path) > 16 || check.Equals == nil {
			return fmt.Errorf("invalid completion check")
		}
		for _, p := range check.Path {
			if p == "" || len(p) > 128 {
				return fmt.Errorf("invalid completion path")
			}
		}
		if err := c.Arguments(check.Tool, check.Arguments); err != nil {
			return err
		}
	}
	return nil
}

func (c Contract) Arguments(tool string, arguments map[string]any) error {
	for _, g := range c.Grants {
		if g.Tool == tool {
			return capability.Validate(arguments, g.Arguments)
		}
	}
	return fmt.Errorf("tool %s is outside objective grants", tool)
}
func (c Contract) ExecutionLimit(tool string) int {
	for _, g := range c.Grants {
		if g.Tool == tool && g.MaxExecutions > 0 {
			return g.MaxExecutions
		}
	}
	return 1
}
func EffectKey(call capability.Call) string {
	return capability.Fingerprint(struct {
		Tool      string
		Arguments map[string]any
	}{call.Tool, call.Arguments})
}

func (c Contract) Authorize(call capability.Call, m capability.Manifest) error {
	if err := c.Arguments(call.Tool, call.Arguments); err != nil {
		return err
	}
	if err := capability.Validate(call.Arguments, m.InputSchema); err != nil {
		return err
	}
	for _, e := range m.Contract.Effects {
		if !contains(c.Effects, e) {
			return fmt.Errorf("effect %s is outside objective", e)
		}
	}
	for _, r := range m.Contract.Resources {
		if !contains(c.Resources, r) {
			return fmt.Errorf("resource %s is outside objective", r)
		}
	}
	return nil
}

func (c Contract) ValidateCatalog(robot string, catalog map[string]capability.Manifest) error {
	if err := c.Validate(); err != nil {
		return err
	}
	if robot != c.RobotID || capability.Fingerprint(catalog) != c.CatalogRevision {
		return fmt.Errorf("objective robot/catalog binding changed")
	}
	for _, g := range c.Grants {
		m, ok := catalog[g.Tool]
		if !ok {
			return fmt.Errorf("objective tool unavailable: %s", g.Tool)
		}
		for _, e := range m.Contract.Effects {
			if !contains(c.Effects, e) {
				return fmt.Errorf("objective lacks effect %s", e)
			}
		}
		for _, r := range m.Contract.Resources {
			if !contains(c.Resources, r) {
				return fmt.Errorf("objective lacks resource %s", r)
			}
		}
	}
	for _, check := range c.Completion {
		m := catalog[check.Tool]
		if m.MutatesWorld {
			return fmt.Errorf("completion checks must be read-only")
		}
		if err := c.Authorize(capability.Call{Tool: check.Tool, Arguments: check.Arguments}, m); err != nil {
			return err
		}
	}
	return nil
}

func (c Contract) Call() capability.Call {
	wire, _ := json.Marshal(c)
	var value map[string]any
	_ = json.Unmarshal(wire, &value)
	return capability.Call{Tool: Tool, Arguments: map[string]any{"objective": value}}
}
func Decode(call capability.Call) (Contract, error) {
	var c Contract
	if call.Tool != Tool || len(call.Arguments) != 1 || len(call.LegacyIntent) != 0 {
		return c, fmt.Errorf("invalid delegated goal call")
	}
	wire, err := json.Marshal(call.Arguments["objective"])
	if err != nil {
		return c, err
	}
	if len(wire) > 256<<10 {
		return c, fmt.Errorf("objective too large")
	}
	d := json.NewDecoder(bytes.NewReader(wire))
	d.DisallowUnknownFields()
	if err := d.Decode(&c); err != nil {
		return c, err
	}
	return c, c.Validate()
}

func Value(result map[string]any, path []string) any {
	var v any = result
	for _, k := range path {
		m, ok := v.(map[string]any)
		if !ok {
			return nil
		}
		v = m[k]
	}
	return v
}
func Equal(a, b any) bool { return capability.Fingerprint(a) == capability.Fingerprint(b) }
func PlanDigest(p *capability.Plan) string {
	return capability.Fingerprint(struct {
		Robot   string
		Catalog string
		Calls   []capability.Call
	}{p.RobotID, p.CatalogRevision, p.Calls})
}
func contains(values []string, v string) bool {
	for _, x := range values {
		if x == v {
			return true
		}
	}
	return false
}
