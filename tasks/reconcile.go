package tasks

func BuildChangeSet(previous RevisionRecord, proposed []RevisionStep, basis RevisionBasis) ChangeSet {
	previousByID := make(map[string]RevisionStep, len(previous.Revision.Steps))
	for _, step := range previous.Revision.Steps {
		previousByID[step.StepID] = step
	}
	running := make(map[string]struct{}, len(basis.RunningStepIDs))
	for _, stepID := range basis.RunningStepIDs {
		running[stepID] = struct{}{}
	}
	seen := make(map[string]struct{}, len(proposed))
	change := ChangeSet{}
	for _, step := range proposed {
		seen[step.StepID] = struct{}{}
		prior, exists := previousByID[step.StepID]
		if !exists {
			change.Added = append(change.Added, step.StepID)
			continue
		}
		compatible := prior.SemanticFingerprint == step.SemanticFingerprint
		if compatible && prior.Status == StepSatisfied {
			// A fresh simulation episode is a one-shot lifecycle transition. Once
			// it succeeded, a content-only task revision must never reset the
			// physical world again. Manipulation steps still require live Harness
			// evidence before they can be retained.
			compatible = prior.Action == "prepare_simulation" || basis.EvidenceValidity[prior.StepID]
		}
		if compatible {
			change.Retained = append(change.Retained, step.StepID)
			continue
		}
		change.Changed = append(change.Changed, step.StepID)
		if _, isRunning := running[step.StepID]; isRunning {
			change.Paused = append(change.Paused, step.StepID)
		}
	}
	for _, prior := range previous.Revision.Steps {
		if _, exists := seen[prior.StepID]; exists {
			continue
		}
		change.Changed = append(change.Changed, prior.StepID)
		if _, isRunning := running[prior.StepID]; isRunning {
			change.Paused = append(change.Paused, prior.StepID)
		}
	}
	return change
}
