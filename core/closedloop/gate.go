package closedloop

import (
	"errors"
	"fmt"
	"time"
)

// Declaration is the union of the two independent places a tool's world effect
// can be declared. Both are consulted because either one may know something the
// other does not:
//
//   - Manifest is the Agent's trusted local catalog. It covers the reference
//     tools and cannot be changed by a remote adapter.
//   - RuntimeMutatesWorld is what the connected runtime advertises. It covers
//     adapter-specific tools the local catalog has never heard of.
//
// A step mutates the world if either source says so. Declaring a write only
// remotely still gets the gate; declaring it only locally still gets the gate.
type Declaration struct {
	Manifest               bool
	RuntimeMutatesWorld    bool
	RuntimeCapabilityKnown bool
}

// Mutates reports whether the completion gate applies to this step.
func (d Declaration) Mutates() bool {
	return d.Manifest || d.RuntimeMutatesWorld
}

// Reason values for a refusal. They are stable identifiers so callers can
// classify and surface them without parsing prose.
const (
	ReasonNotRequired   = "CLOSURE_NOT_REQUIRED"
	ReasonRequired      = "CLOSURE_EVIDENCE_SATISFIED"
	ReasonMissing       = "CLOSURE_EVIDENCE_REQUIRED"
	ReasonStale         = "CLOSURE_EVIDENCE_STALE"
	ReasonNoCommandTime = "CLOSURE_DISPATCH_TIME_REQUIRED"
)

// Decision is the gate verdict for one completed tool result.
type Decision struct {
	// Satisfied is true when the step may be recorded as complete.
	Satisfied bool
	// Required reports whether the gate applied at all, for diagnostics.
	Required bool
	// Reason is a stable identifier from the Reason* set.
	Reason string
	// Message is a human-readable explanation for the operator.
	Message string
}

// Gate decides whether a tool that reported success may be recorded as
// complete.
//
// It is deliberately separate from the runtime's return code: a physical write
// is complete only when a fresh observation taken after the command was
// dispatched confirms it. A read-only tool needs no such evidence, because
// there is no world change to confirm.
func Gate(declaration Declaration, dispatchedAt time.Time, evidence *Evidence) Decision {
	if !declaration.Mutates() {
		return Decision{Satisfied: true, Required: false, Reason: ReasonNotRequired,
			Message: "read-only tool needs no post-condition evidence"}
	}
	if dispatchedAt.IsZero() {
		return Decision{Satisfied: false, Required: true, Reason: ReasonNoCommandTime,
			Message: "cannot prove freshness without the dispatch time of this command"}
	}
	if evidence == nil {
		return Decision{Satisfied: false, Required: true, Reason: ReasonMissing,
			Message: "tool reported success but no post-command observation was attached; " +
				"the physical outcome is unverified"}
	}
	if err := validateFreshness(dispatchedAt, *evidence); err != nil {
		return Decision{Satisfied: false, Required: true, Reason: ReasonStale, Message: err.Error()}
	}
	return Decision{Satisfied: true, Required: true, Reason: ReasonRequired,
		Message: fmt.Sprintf("confirmed by observation %s taken after dispatch", evidence.ObservationID)}
}

// validateFreshness is shared by Gate and Track.Fresh so the two entry points
// cannot drift into different definitions of "fresh". Ordering is compared in
// whole milliseconds because that is the resolution runtimes timestamp captures
// with; see DispatchPrecision.
func validateFreshness(dispatchedAt time.Time, evidence Evidence) error {
	if evidence.ObservationID == "" {
		return fmt.Errorf("%w: no observation id", ErrEvidenceRequired)
	}
	if evidence.ObservedAt.IsZero() {
		return fmt.Errorf("%w: no observation time", ErrEvidenceRequired)
	}
	dispatchedMS := NormalizeDispatchTime(dispatchedAt)
	observedMS := evidence.ObservedAt.UTC().Truncate(DispatchPrecision)
	if observedMS.Before(dispatchedMS) {
		return fmt.Errorf("%w: observed %s, dispatched %s", ErrEvidenceStale,
			observedMS.Format(time.RFC3339Nano), dispatchedMS.Format(time.RFC3339Nano))
	}
	switch {
	case equalFold(evidence.Freshness, "STALE"):
		return fmt.Errorf("%w: source verdict STALE", ErrEvidenceStale)
	case equalFold(evidence.Freshness, "UNKNOWN"):
		return fmt.Errorf("%w: source verdict UNKNOWN", ErrEvidenceStale)
	}
	return nil
}

// equalFold avoids importing strings solely for two comparisons.
func equalFold(value, want string) bool {
	if len(value) != len(want) {
		return false
	}
	for index := 0; index < len(value); index++ {
		lower := value[index]
		if 'A' <= lower && lower <= 'Z' {
			lower += 'a' - 'A'
		}
		upper := want[index]
		if 'A' <= upper && upper <= 'Z' {
			upper += 'a' - 'A'
		}
		if lower != upper {
			return false
		}
	}
	return true
}

// ErrNotSatisfied is returned by Require when the gate refuses completion.
var ErrNotSatisfied = errors.New("closed-loop post-condition not satisfied")

// Require returns nil only when the decision permits completion.
func (d Decision) Require() error {
	if d.Satisfied {
		return nil
	}
	return fmt.Errorf("%w: %s: %s", ErrNotSatisfied, d.Reason, d.Message)
}
