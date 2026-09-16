package robotcontract

import (
	"errors"
	"fmt"
	"slices"
)

// Fault is one entry of `robot.faults.v1`: which module is broken, how badly,
// and who has to do something about it.
//
// The robot publishes this as part of its observation, so it is a contract and
// not a log line: the world model keeps it, the console shows it, and an Agent
// reads it before planning instead of discovering a dead chassis by trying to
// drive.
type Fault struct {
	ModuleID         string            `json:"moduleId"`
	Kind             string            `json:"kind"`
	Code             string            `json:"code"`
	Severity         string            `json:"severity"`
	Detail           string            `json:"detail"`
	Occurrences      int               `json:"occurrences"`
	Remedy           string            `json:"remedy"`
	UserInstruction  string            `json:"userInstruction"`
	DetectedAtUnixMS int64             `json:"detectedAtUnixMs"`
	LastSeenUnixMS   int64             `json:"lastSeenUnixMs"`
	AgeMS            *int64            `json:"ageMs,omitempty"`
	SinceLastSeenMS  *int64            `json:"sinceLastSeenMs,omitempty"`
	Evidence         map[string]string `json:"evidence"`
}

// FaultReport is the whole robot-side view: the headline severity, every current
// fault, and - when the robot declared which modules its capabilities need -
// which capabilities the faults have taken away.
type FaultReport struct {
	SchemaVersion           string   `json:"schemaVersion"`
	Severity                string   `json:"severity"`
	Count                   int      `json:"count"`
	Faults                  []Fault  `json:"faults"`
	OperatorActions         []string `json:"operatorActions,omitempty"`
	UnavailableCapabilities []string `json:"unavailableCapabilities,omitempty"`
	// CapabilityBlockers says, per capability, which faults took it away. The
	// sorted list above is the same fact for readers that only need to know what
	// is gone; when both are published they must agree, because a console that
	// explains "why can this robot not navigate" is reading this map.
	CapabilityBlockers map[string][]string `json:"capabilityBlockers,omitempty"`
}

// Fault and remedy vocabulary. It mirrors the robot-side module, and is repeated
// here rather than imported because this is the wire contract: a publisher that
// invents a severity must be refused, not accommodated.
var (
	faultSeverities = []string{"info", "degraded", "blocked", "safety"}
	faultKinds      = []string{"chassis", "arm", "gripper", "head", "camera", "compute", "power", "bus", "custom"}
	faultRemedies   = []string{"self_recover", "operator_assist", "service_required"}
)

// WorstSeverity is the most serious severity in the report, or "info" when the
// robot is clean.
func (r FaultReport) WorstSeverity() string {
	worst := "info"
	for _, fault := range r.Faults {
		if slices.Index(faultSeverities, fault.Severity) > slices.Index(faultSeverities, worst) {
			worst = fault.Severity
		}
	}
	return worst
}

// Blocking reports the faults that take a capability away. `info` is news, not a
// capability loss, so it is excluded.
func (r FaultReport) Blocking() []Fault {
	blocking := make([]Fault, 0, len(r.Faults))
	for _, fault := range r.Faults {
		if fault.Severity != "info" {
			blocking = append(blocking, fault)
		}
	}
	return blocking
}

// Keys returns the `module:CODE` identities, which is what a capability blocker
// names. It is the stable form to compare across snapshots.
func (r FaultReport) Keys() []string {
	keys := make([]string, 0, len(r.Faults))
	for _, fault := range r.Faults {
		keys = append(keys, fault.ModuleID+":"+fault.Code)
	}
	return keys
}

// SafetyStopped reports whether the robot declares a safety-severity fault: it
// has stopped itself and will not resume without a person.
func (r FaultReport) SafetyStopped() bool {
	for _, fault := range r.Faults {
		if fault.Severity == "safety" {
			return true
		}
	}
	return false
}

func (r FaultReport) Validate() error {
	if r.SchemaVersion != "robot.faults.v1" {
		return fmt.Errorf("unsupported fault schema %q", r.SchemaVersion)
	}
	if !oneOf(r.Severity, faultSeverities...) {
		return fmt.Errorf("unknown fault severity %q", r.Severity)
	}
	if r.Count != len(r.Faults) {
		// A report that disagrees with itself cannot be trusted to gate anything.
		return fmt.Errorf("fault count %d does not match %d entries", r.Count, len(r.Faults))
	}
	if worst := r.WorstSeverity(); worst != r.Severity {
		return fmt.Errorf("headline severity %q is not the worst of %d faults (%q)", r.Severity, len(r.Faults), worst)
	}
	seen := make(map[string]bool, len(r.Faults))
	for _, fault := range r.Faults {
		if err := fault.validate(); err != nil {
			return err
		}
		key := fault.ModuleID + ":" + fault.Code
		if seen[key] {
			return fmt.Errorf("fault %q appears twice; the robot must report one entry per module and code", key)
		}
		seen[key] = true
	}
	for capability, blockers := range r.CapabilityBlockers {
		if !validText(capability) {
			return errors.New("capabilityBlockers names an empty capability")
		}
		if len(blockers) == 0 {
			return fmt.Errorf("capability %q is listed as blocked by nothing; omit it instead", capability)
		}
		for _, key := range blockers {
			if !seen[key] {
				return fmt.Errorf("capability %q is blocked by unknown fault %q", capability, key)
			}
		}
	}
	if r.CapabilityBlockers != nil && r.UnavailableCapabilities != nil {
		blocked := make([]string, 0, len(r.CapabilityBlockers))
		for capability := range r.CapabilityBlockers {
			blocked = append(blocked, capability)
		}
		slices.Sort(blocked)
		if !slices.Equal(blocked, r.UnavailableCapabilities) {
			// Two views of one fact disagreeing means the publisher is confused
			// about what it just removed, which is exactly when not to guess.
			return fmt.Errorf("unavailableCapabilities %v does not match capabilityBlockers %v",
				r.UnavailableCapabilities, blocked)
		}
	}
	return nil
}

func (f Fault) validate() error {
	if !validText(f.ModuleID) {
		return errors.New("fault is missing a moduleId")
	}
	if !validText(f.Code) {
		return fmt.Errorf("fault on module %q is missing a code", f.ModuleID)
	}
	if !oneOf(f.Severity, faultSeverities...) {
		return fmt.Errorf("fault %s:%s has unknown severity %q", f.ModuleID, f.Code, f.Severity)
	}
	if !oneOf(f.Kind, faultKinds...) {
		return fmt.Errorf("fault %s:%s has unknown module kind %q", f.ModuleID, f.Code, f.Kind)
	}
	if !oneOf(f.Remedy, faultRemedies...) {
		return fmt.Errorf("fault %s:%s has unknown remedy %q; a fault must say who can fix it",
			f.ModuleID, f.Code, f.Remedy)
	}
	if f.Occurrences < 1 {
		return fmt.Errorf("fault %s:%s reports %d occurrences", f.ModuleID, f.Code, f.Occurrences)
	}
	if f.DetectedAtUnixMS <= 0 {
		return fmt.Errorf("fault %s:%s has no detection time", f.ModuleID, f.Code)
	}
	return nil
}

// DecodeFaults reads a published `robot.faults.v1` document. Unknown fields and
// null values are refused: a fault list is an input to capability gating, so a
// payload that is not understood is not accepted as "probably fine".
func DecodeFaults(values map[string]any) (*FaultReport, error) {
	if err := required(values, "schemaVersion", "severity", "count", "faults"); err != nil {
		return nil, err
	}
	if err := requiredItems(values, "faults", "moduleId", "kind", "code", "severity", "remedy",
		"occurrences", "detectedAtUnixMs"); err != nil {
		return nil, err
	}
	var report FaultReport
	if err := decode(values, &report); err != nil {
		return nil, err
	}
	if err := report.Validate(); err != nil {
		return nil, err
	}
	return &report, nil
}
