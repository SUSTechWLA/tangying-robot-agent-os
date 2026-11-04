package orchestration

import (
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
)

// TaskRecord is the minimal task information needed for orchestration metrics.
type TaskRecord struct {
	Source             string
	Attempts           int
	AcceptedCandidates int
	Rejections         []string
	State              string
	Sequence           bool
	PlanningTrace      []capability.PlanningStep
}

// Metrics measures LLM orchestration quality and end-to-end reliability.
type Metrics struct {
	Available                  bool           `json:"available"`
	Population                 string         `json:"population"`
	TotalTasks                 int            `json:"totalTasks"`
	SequenceTasks              int            `json:"sequenceTasks"`
	DeterministicTasks         int            `json:"deterministicTasks"`
	LLMGeneratedTasks          int            `json:"llmGeneratedTasks"`
	ConsensusTasks             int            `json:"consensusTasks"`
	LLMFallbackTasks           int            `json:"llmFallbackTasks"`
	LLMPlanRate                float64        `json:"llmPlanRate"`
	LLMCandidateRate           float64        `json:"llmCandidateRate"`
	LLMRejectionCount          int            `json:"llmRejectionCount"`
	SucceededTasks             int            `json:"succeededTasks"`
	FailedTasks                int            `json:"failedTasks"`
	CancelledTasks             int            `json:"cancelledTasks"`
	SafetyStoppedTasks         int            `json:"safetyStoppedTasks"`
	EndToEndSuccessRate        float64        `json:"endToEndSuccessRate"`
	SuccessByPlanSource        map[string]int `json:"successByPlanSource"`
	OrchestrationScore         float64        `json:"orchestrationScore"`
	LegacyCandidateAttempts    int            `json:"legacyCandidateAttempts"`
	LegacyAcceptedCandidates   int            `json:"legacyAcceptedCandidates"`
	GoalModelCallAttempts      int            `json:"goalModelCallAttempts"`
	GoalProposalAttempts       int            `json:"goalProposalAttempts"`
	GoalAcceptedProposals      int            `json:"goalAcceptedProposals"`
	GoalRecipeHits             int            `json:"goalRecipeHits"`
	GoalDecisionRejected       int            `json:"goalDecisionRejected"`
	GoalProposalAcceptanceRate *float64       `json:"goalProposalAcceptanceRate"`
}

func CalculateMetrics(records []TaskRecord) Metrics {
	metrics := Metrics{Available: true, Population: "persisted_tasks_only", SuccessByPlanSource: map[string]int{}}
	for _, record := range records {
		metrics.TotalTasks++
		if record.Sequence {
			metrics.SequenceTasks++
		}
		source := record.Source
		if source == "" {
			source = SourceDeterministic
		}
		switch source {
		case SourceDeterministic:
			metrics.DeterministicTasks++
			if record.Attempts > 0 {
				metrics.LLMFallbackTasks++
			}
		case SourceLLM:
			metrics.LLMGeneratedTasks++
		case SourceConsensus:
			metrics.LLMGeneratedTasks++
			metrics.ConsensusTasks++
		}
		if record.Attempts > 0 {
			metrics.LLMRejectionCount += len(record.Rejections)
		}
		// These are recorded client attempts, not provider HTTP requests or
		// billed tokens. Recipe revalidation has trace rows but no ModelCall.
		seenCalls := map[string]bool{}
		for _, step := range record.PlanningTrace {
			if step.Verdict == "MODEL_DECISION_REJECTED" {
				metrics.GoalDecisionRejected++
			}
			if step.Context != nil && step.Context.ModelCall != nil {
				id := step.Context.ModelCall.CallID
				if id == "" || !seenCalls[id] {
					metrics.GoalModelCallAttempts++
					seenCalls[id] = true
				}
			}
			if step.Tool == "propose_capability_plan" {
				metrics.GoalProposalAttempts++
				if step.Verdict == "FROZEN_FOR_APPROVAL" {
					metrics.GoalAcceptedProposals++
				}
			}
			if step.Tool == "planning.recipe" && step.Verdict == "RECIPE_HIT" {
				metrics.GoalRecipeHits++
			}
		}
		switch strings.ToUpper(record.State) {
		case "SUCCEEDED":
			metrics.SucceededTasks++
			metrics.SuccessByPlanSource[source]++
		case "SAFETY_STOPPED":
			metrics.SafetyStoppedTasks++
		case "FAILED", "RECOVERABLE_FAILURE":
			metrics.FailedTasks++
		case "CANCELLED":
			metrics.CancelledTasks++
		}
	}
	if metrics.TotalTasks > 0 {
		metrics.LLMPlanRate = float64(metrics.LLMGeneratedTasks) / float64(metrics.TotalTasks)
		metrics.EndToEndSuccessRate = float64(metrics.SucceededTasks) / float64(metrics.TotalTasks)
	}
	candidates := 0
	attempts := 0
	for _, record := range records {
		if record.Attempts > 0 {
			attempts += record.Attempts
			candidates += record.AcceptedCandidates
		}
	}
	if attempts > 0 {
		metrics.LLMCandidateRate = float64(candidates) / float64(attempts)
	}
	metrics.LegacyCandidateAttempts, metrics.LegacyAcceptedCandidates = attempts, candidates
	if metrics.GoalProposalAttempts > 0 {
		rate := float64(metrics.GoalAcceptedProposals) / float64(metrics.GoalProposalAttempts)
		metrics.GoalProposalAcceptanceRate = &rate
	}
	metrics.OrchestrationScore = metrics.EndToEndSuccessRate * 60
	metrics.OrchestrationScore += metrics.LLMCandidateRate * 25
	metrics.OrchestrationScore += metrics.LLMPlanRate * 15
	return metrics
}
