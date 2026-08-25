package tasks_test

import (
	"slices"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestBuildChangeSetRetainsSatisfiedCompatibleStepWithValidEvidence(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "same", Status: tasks.StepSatisfied,
		HarnessEvidenceIDs: []string{"evidence-1"},
	}}}}
	proposed := []tasks.RevisionStep{
		{StepID: "handoff/sender", SemanticFingerprint: "same"},
		{StepID: "handoff/receiver", SemanticFingerprint: "new"},
	}

	change := tasks.BuildChangeSet(previous, proposed, tasks.RevisionBasis{
		EvidenceValidity: map[string]bool{"handoff/sender": true},
	})

	if !slices.Equal(change.Retained, []string{"handoff/sender"}) {
		t.Fatalf("retained = %v", change.Retained)
	}
	if !slices.Equal(change.Added, []string{"handoff/receiver"}) {
		t.Fatalf("added = %v", change.Added)
	}
	if len(change.Changed) != 0 || len(change.Paused) != 0 {
		t.Fatalf("unexpected change set = %#v", change)
	}
}

func TestBuildChangeSetDoesNotRetainSatisfiedStepWithInvalidEvidence(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "same", Status: tasks.StepSatisfied,
		HarnessEvidenceIDs: []string{"evidence-old-world"},
	}}}}
	proposed := []tasks.RevisionStep{{StepID: "handoff/sender", SemanticFingerprint: "same"}}

	change := tasks.BuildChangeSet(previous, proposed, tasks.RevisionBasis{
		EvidenceValidity: map[string]bool{"handoff/sender": false},
	})

	if !slices.Equal(change.Changed, []string{"handoff/sender"}) || len(change.Retained) != 0 {
		t.Fatalf("invalid evidence must force rework: %#v", change)
	}
}

func TestBuildChangeSetNeverReplaysSatisfiedSimulationEpisodePreparation(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "prepare", Action: "prepare_simulation", SemanticFingerprint: "same", Status: tasks.StepSatisfied,
	}}}}
	proposed := []tasks.RevisionStep{{
		StepID: "prepare", Action: "prepare_simulation", SemanticFingerprint: "same", Status: tasks.StepSatisfied,
	}}

	change := tasks.BuildChangeSet(previous, proposed, tasks.RevisionBasis{
		EvidenceValidity: map[string]bool{"prepare": false},
	})

	if !slices.Equal(change.Retained, []string{"prepare"}) || len(change.Changed) != 0 {
		t.Fatalf("one-shot preparation must remain retained: %#v", change)
	}
}

func TestBuildChangeSetPausesChangedRunningPhysicalStep(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "old", Status: tasks.StepRunning,
	}}}}
	change := tasks.BuildChangeSet(previous, []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "changed",
	}}, tasks.RevisionBasis{RunningStepIDs: []string{"handoff/sender"}})

	if !slices.Equal(change.Changed, []string{"handoff/sender"}) {
		t.Fatalf("changed = %v", change.Changed)
	}
	if !slices.Equal(change.Paused, []string{"handoff/sender"}) {
		t.Fatalf("paused = %v", change.Paused)
	}
}

func TestBuildChangeSetCancelsRemovedPendingStep(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "old/pending", SemanticFingerprint: "old", Status: tasks.StepPending,
	}}}}

	change := tasks.BuildChangeSet(previous, nil, tasks.RevisionBasis{})

	if !slices.Equal(change.Changed, []string{"old/pending"}) {
		t.Fatalf("removed pending step must be changed/cancelled: %#v", change)
	}
}
