package orchestration

import "strings"

// TaskRecord is the minimal task information needed for orchestration metrics.
type TaskRecord struct {
	Source             string
	Attempts           int
	AcceptedCandidates int
	Rejections         []string
	State              string
	Sequence           bool
}

// Metrics measures LLM orchestration quality and end-to-end reliability.
type Metrics struct {
	TotalTasks          int            `json:"totalTasks"`
	SequenceTasks       int            `json:"sequenceTasks"`
	DeterministicTasks  int            `json:"deterministicTasks"`
	LLMGeneratedTasks   int            `json:"llmGeneratedTasks"`
	ConsensusTasks      int            `json:"consensusTasks"`
	LLMFallbackTasks    int            `json:"llmFallbackTasks"`
	LLMPlanRate         float64        `json:"llmPlanRate"`
	LLMCandidateRate    float64        `json:"llmCandidateRate"`
	LLMRejectionCount   int            `json:"llmRejectionCount"`
	SucceededTasks      int            `json:"succeededTasks"`
	FailedTasks         int            `json:"failedTasks"`
	SafetyStoppedTasks  int            `json:"safetyStoppedTasks"`
	EndToEndSuccessRate float64        `json:"endToEndSuccessRate"`
	SuccessByPlanSource map[string]int `json:"successByPlanSource"`
	OrchestrationScore  float64        `json:"orchestrationScore"`
}

func CalculateMetrics(records []TaskRecord) Metrics {
	metrics := Metrics{SuccessByPlanSource: map[string]int{}}
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
		switch strings.ToUpper(record.State) {
		case "SUCCEEDED":
			metrics.SucceededTasks++
			metrics.SuccessByPlanSource[source]++
		case "SAFETY_STOPPED":
			metrics.SafetyStoppedTasks++
		case "FAILED", "RECOVERABLE_FAILURE", "CANCELLED":
			metrics.FailedTasks++
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
	metrics.OrchestrationScore = metrics.EndToEndSuccessRate * 60
	metrics.OrchestrationScore += metrics.LLMCandidateRate * 25
	metrics.OrchestrationScore += metrics.LLMPlanRate * 15
	return metrics
}
