package closedloop_test

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
)

func TestReadOnlyToolNeedsNoEvidence(t *testing.T) {
	decision := closedloop.Gate(closedloop.Declaration{}, time.Time{}, nil)
	if !decision.Satisfied || decision.Required {
		t.Fatalf("read-only tool was gated: %+v", decision)
	}
	if decision.Reason != closedloop.ReasonNotRequired {
		t.Fatalf("reason = %s", decision.Reason)
	}
	if err := decision.Require(); err != nil {
		t.Fatalf("read-only tool refused completion: %v", err)
	}
}

func TestWorldMutationWithoutEvidenceIsRefused(t *testing.T) {
	for name, declaration := range map[string]closedloop.Declaration{
		"declared locally only":  {Manifest: true, RuntimeCapabilityKnown: false},
		"declared remotely only": {RuntimeMutatesWorld: true, RuntimeCapabilityKnown: true},
		"declared by both":       {Manifest: true, RuntimeMutatesWorld: true, RuntimeCapabilityKnown: true},
	} {
		decision := closedloop.Gate(declaration, dispatch, nil)
		if decision.Satisfied {
			t.Fatalf("%s: a successful return code alone completed a write", name)
		}
		if !decision.Required || decision.Reason != closedloop.ReasonMissing {
			t.Fatalf("%s: decision = %+v", name, decision)
		}
		if err := decision.Require(); !errors.Is(err, closedloop.ErrNotSatisfied) {
			t.Fatalf("%s: Require() = %v", name, err)
		}
	}
}

func TestWorldMutationRequiresPostCommandObservation(t *testing.T) {
	// Evidence captured before the command was dispatched cannot prove the
	// command did anything, however fresh it still looks.
	preCommand := &closedloop.Evidence{ObservationID: "obs-before", ObservedAt: dispatch.Add(-time.Second), Freshness: "FRESH"}
	decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch, preCommand)
	if decision.Satisfied || decision.Reason != closedloop.ReasonStale {
		t.Fatalf("pre-command observation closed the loop: %+v", decision)
	}

	postCommand := &closedloop.Evidence{ObservationID: "obs-after", ObservedAt: dispatch.Add(50 * time.Millisecond), Freshness: "FRESH"}
	decision = closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch, postCommand)
	if !decision.Satisfied || !decision.Required || decision.Reason != closedloop.ReasonRequired {
		t.Fatalf("fresh post-command observation was refused: %+v", decision)
	}
	if !strings.Contains(decision.Message, "obs-after") {
		t.Fatalf("decision does not name the evidence: %s", decision.Message)
	}
}

func TestWorldMutationRejectsUnusableEvidence(t *testing.T) {
	for name, evidence := range map[string]*closedloop.Evidence{
		"no observation id":       {ObservedAt: dispatch.Add(time.Second), Freshness: "FRESH"},
		"no observation time":     {ObservationID: "obs", Freshness: "FRESH"},
		"earlier millisecond":     {ObservationID: "obs", ObservedAt: dispatch.Add(-time.Millisecond), Freshness: "FRESH"},
		"stale source verdict":    {ObservationID: "obs", ObservedAt: dispatch.Add(time.Second), Freshness: "STALE"},
		"unknown source verdict":  {ObservationID: "obs", ObservedAt: dispatch.Add(time.Second), Freshness: "UNKNOWN"},
		"lowercase stale verdict": {ObservationID: "obs", ObservedAt: dispatch.Add(time.Second), Freshness: "stale"},
	} {
		decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch, evidence)
		if decision.Satisfied {
			t.Fatalf("%s: unusable evidence closed the loop", name)
		}
		if decision.Reason != closedloop.ReasonStale && decision.Reason != closedloop.ReasonMissing {
			t.Fatalf("%s: reason = %s", name, decision.Reason)
		}
	}
}

// Runtime captures are stamped in whole milliseconds, so an observation sharing
// the dispatch millisecond cannot be ordered against it. Treating it as
// post-command is the documented rule; an observation from any earlier
// millisecond is still refused.
func TestObservationInTheDispatchMillisecondCounts(t *testing.T) {
	subMillisecondDispatch := dispatch.Add(400 * time.Microsecond)
	sameMillisecond := &closedloop.Evidence{
		ObservationID: "obs", ObservedAt: subMillisecondDispatch.Add(100 * time.Microsecond), Freshness: "FRESH",
	}
	if decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, subMillisecondDispatch, sameMillisecond); !decision.Satisfied {
		t.Fatalf("same-millisecond observation refused: %+v", decision)
	}
	previousMillisecond := &closedloop.Evidence{
		ObservationID: "obs", ObservedAt: subMillisecondDispatch.Add(-time.Millisecond), Freshness: "FRESH",
	}
	if decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, subMillisecondDispatch, previousMillisecond); decision.Satisfied {
		t.Fatal("an observation from the previous millisecond closed the loop")
	}
}

// A write without a dispatch time cannot be proven fresh. Refusing is the only
// safe answer: the alternative would let an arbitrarily old observation verify
// a command whose start time is unknown.
func TestWorldMutationWithoutDispatchTimeIsRefused(t *testing.T) {
	evidence := &closedloop.Evidence{ObservationID: "obs", ObservedAt: dispatch, Freshness: "FRESH"}
	decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, time.Time{}, evidence)
	if decision.Satisfied || decision.Reason != closedloop.ReasonNoCommandTime {
		t.Fatalf("decision = %+v", decision)
	}
}

func TestDeclarationMutatesIsTrueWhenEitherSourceDeclaresIt(t *testing.T) {
	for declaration, want := range map[closedloop.Declaration]bool{
		{}:                          false,
		{Manifest: true}:            true,
		{RuntimeMutatesWorld: true}: true,
		{Manifest: true, RuntimeMutatesWorld: true}:     true,
		{RuntimeCapabilityKnown: true}:                  false,
		{RuntimeCapabilityKnown: true, Manifest: false}: false,
	} {
		if got := declaration.Mutates(); got != want {
			t.Fatalf("%+v.Mutates() = %v, want %v", declaration, got, want)
		}
	}
}
