package modelroute

import (
	"path/filepath"
	"testing"
)

func TestOutputBudgetIsValidatedAndStageLocal(t *testing.T) {
	values := map[string]string{"AGENT_PROVIDER": "openai", "AGENT_BASE_URL": "http://localhost/v1", "AGENT_MODEL": "local",
		"AGENT_GOAL_OUTPUT_TOKENS": "1024", "AGENT_MODEL_USAGE_FILE": filepath.Join(t.TempDir(), "usage.jsonl")}
	goal := Resolve(values, Goal)
	if err := goal.Validate(); err != nil || goal.OutputTokens != 1024 || goal.UsageFile != values["AGENT_MODEL_USAGE_FILE"] {
		t.Fatalf("goal accounting config: %+v, %v", goal, err)
	}
	if Resolve(values, Recovery).OutputTokens != 0 {
		t.Fatal("GOAL budget crossed into recovery")
	}
	for _, raw := range []string{"0", "-1", "NaN", "1024.5", "127", "16385", "999999999999999999999999999"} {
		values["AGENT_GOAL_OUTPUT_TOKENS"] = raw
		if Resolve(values, Goal).Validate() == nil {
			t.Fatalf("invalid cap accepted: %s", raw)
		}
	}
	delete(values, "AGENT_GOAL_OUTPUT_TOKENS")
	for _, stage := range []string{Intent, Planning, System} {
		values["AGENT_"+stage+"_OUTPUT_TOKENS"] = "512"
		if Resolve(values, stage).Validate() == nil {
			t.Fatalf("unwired output cap silently accepted: %s", stage)
		}
	}
	values["AGENT_MODEL_USAGE_FILE"] = "relative.jsonl"
	if Resolve(values, Goal).Validate() == nil {
		t.Fatal("relative accounting path accepted")
	}
}

func TestOutputBudgetEnvironment(t *testing.T) {
	t.Setenv("AGENT_PROVIDER", "openai")
	t.Setenv("AGENT_BASE_URL", "http://localhost/v1")
	t.Setenv("AGENT_MODEL", "local")
	t.Setenv("AGENT_RECOVERY_OUTPUT_TOKENS", "512")
	t.Setenv("AGENT_MODEL_USAGE_FILE", filepath.Join(t.TempDir(), "usage.jsonl"))
	e := Environment(Recovery)
	if e.OutputTokens != 512 || e.UsageFile == "" || e.Validate() != nil {
		t.Fatalf("environment not wired: %+v", e)
	}
}
