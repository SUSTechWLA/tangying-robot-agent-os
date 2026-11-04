package main

import "testing"

func TestWorkerRejectsInvalidRecipeModeBeforeOpeningRuntime(t *testing.T) {
	t.Setenv("AGENT_GOAL_RECIPE_MODE", "unvalidated")
	if _, err := edgeGoalPlanner(); err == nil {
		t.Fatal("unsafe recipe mode accepted")
	}
}
