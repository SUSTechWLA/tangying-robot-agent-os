package main

import (
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

func TestWorkerGoalOutputBudgetAndUsageAreActuallyWired(t *testing.T) {
	t.Setenv("AGENT_PROVIDER", "deterministic")
	t.Setenv("AGENT_GOAL_PROVIDER", "openai")
	t.Setenv("AGENT_GOAL_BASE_URL", "http://localhost/v1")
	t.Setenv("AGENT_GOAL_MODEL", "local")
	t.Setenv("AGENT_GOAL_OUTPUT_TOKENS", "1024")
	path := filepath.Join(t.TempDir(), "usage.jsonl")
	t.Setenv("AGENT_MODEL_USAGE_FILE", path)
	p, err := edgeGoalPlanner()
	if err != nil {
		t.Fatal(err)
	}
	d, ok := p.Decider.(*actionloop.LLMDecider)
	if !ok || d.OutputTokens != 1024 || d.Stage != modelroute.Goal || d.UsageFile != path {
		t.Fatalf("worker dropped budget/accounting config: %+v", d)
	}
	t.Setenv("AGENT_GOAL_OUTPUT_TOKENS", "127")
	if _, err := edgeGoalPlanner(); err == nil {
		t.Fatal("invalid cap not rejected at startup")
	}
}
