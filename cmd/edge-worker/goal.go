package main

import (
	"encoding/json"
	"os"
	"strings"

	llmagent "github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

// Host supplies the authorized local provider for each delegation.
func edgeGoalPlanner() (*capabilityagent.Planner, error) {
	if err := capabilityagent.ValidateRecipeMode(os.Getenv("AGENT_GOAL_RECIPE_MODE")); err != nil {
		return nil, err
	}
	goal := modelroute.Environment(modelroute.Goal)
	understanding := modelroute.Environment(modelroute.Intent)
	if err := goal.Validate(); err != nil {
		return nil, err
	}
	if err := understanding.Validate(); err != nil {
		return nil, err
	}
	parser := llmagent.NewParser(llmagent.Config{Provider: understanding.Provider, BaseURL: understanding.BaseURL, APIKey: understanding.APIKey, Model: understanding.Model})
	p := &capabilityagent.Planner{ParseLegacy: func(request string) (json.RawMessage, error) {
		parsed, err := parser.Parse(request)
		if err != nil {
			return nil, err
		}
		return json.Marshal(parsed)
	}}
	if strings.EqualFold(goal.Provider, "openai") {
		p.Decider = &actionloop.LLMDecider{BaseURL: goal.BaseURL, APIKey: goal.APIKey, Model: goal.Model, Cache: goal.Cache,
			Stage: modelroute.Goal, OutputTokens: goal.OutputTokens, UsageFile: goal.UsageFile}
	}
	return p, nil
}
