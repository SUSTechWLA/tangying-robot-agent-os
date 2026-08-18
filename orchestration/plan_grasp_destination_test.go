package orchestration

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestSystemPromptIncludesDestinationForPlanGrasp(t *testing.T) {
	planner := &LLMPlanner{catalog: manipulation.Catalog()}
	prompt := planner.systemPrompt(nil)
	want := `"skill": "plan_grasp", "arguments": {"objectId": "@object", "destinationId": "@destination"}`
	if !strings.Contains(prompt, want) {
		t.Fatalf("system prompt plan_grasp example lacks destinationId")
	}
}
