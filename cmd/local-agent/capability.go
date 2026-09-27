package main

import (
	"encoding/json"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"strings"
)

func (c config) goalPlanner(provider capabilityagent.Provider, parser intent.Parser) (*capabilityagent.Planner, error) {
	endpoint := c.model(modelroute.Goal)
	if err := endpoint.Validate(); err != nil {
		return nil, err
	}
	p := &capabilityagent.Planner{Provider: provider, ParseLegacy: func(request string) (json.RawMessage, error) {
		parsed, err := parser.Parse(request)
		if err != nil {
			return nil, err
		}
		return json.Marshal(parsed)
	}}
	if strings.EqualFold(endpoint.Provider, "openai") {
		client, err := c.assist.ClientFor(endpoint)
		if err != nil {
			return nil, err
		}
		if client != nil {
			endpoint.APIKey = ""
		}
		p.Decider = &actionloop.LLMDecider{BaseURL: endpoint.BaseURL, APIKey: endpoint.APIKey, Model: endpoint.Model, Client: client}
	}
	return p, nil
}
