// Package agentharness binds the same bounded decision loop to distinct edge
// and server capabilities. A model route changes reasoning, never authority.
package agentharness

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

type Role string

const (
	Edge   Role = "edge"
	Server Role = "server"
)

type ToolClass string

const (
	RobotRead  ToolClass = "robot.read"
	RobotLocal ToolClass = "robot.local"
	RobotWrite ToolClass = "robot.write"
	FleetRead  ToolClass = "fleet.read"
	FleetDraft ToolClass = "fleet.draft"
)

type Capability struct {
	Class ToolClass
	Tool  actionloop.Tool
}

type Profile struct {
	role   Role
	models map[string]modelroute.Endpoint
}

// New validates every model route needed by this deployment. A server has a
// separate SYSTEM decision model; an edge agent has a RECOVERY decision model.
func New(role Role, models map[string]modelroute.Endpoint) (Profile, error) {
	stages := []string{modelroute.Intent, modelroute.Planning}
	switch role {
	case Edge:
		stages = append(stages, modelroute.Recovery)
	case Server:
		stages = append(stages, modelroute.System)
	default:
		return Profile{}, fmt.Errorf("unsupported agent harness role %q", role)
	}
	copy := make(map[string]modelroute.Endpoint, len(stages))
	for _, stage := range stages {
		endpoint, ok := models[stage]
		if !ok {
			return Profile{}, fmt.Errorf("%s harness requires %s model route", role, stage)
		}
		if err := endpoint.Validate(); err != nil {
			return Profile{}, fmt.Errorf("%s %s model route: %w", role, stage, err)
		}
		copy[stage] = endpoint
	}
	return Profile{role: role, models: copy}, nil
}

func (p Profile) Role() Role { return p.role }

func (p Profile) Model(stage string) (modelroute.Endpoint, bool) {
	endpoint, ok := p.models[stage]
	return endpoint, ok
}

func (p Profile) Allows(class ToolClass) bool {
	switch p.role {
	case Edge:
		return class == RobotRead || class == RobotLocal || class == RobotWrite
	case Server:
		return class == FleetRead || class == FleetDraft
	default:
		return false
	}
}

func (p Profile) validateTools(capabilities []Capability) ([]actionloop.Tool, error) {
	if p.role != Edge && p.role != Server {
		return nil, errors.New("agent harness role is not configured")
	}
	tools := make([]actionloop.Tool, 0, len(capabilities))
	seen := map[string]bool{}
	for _, capability := range capabilities {
		tool := capability.Tool
		if !p.Allows(capability.Class) {
			return nil, fmt.Errorf("%s harness cannot call %s tool %q", p.role, capability.Class, tool.Name)
		}
		if tool.Name == "" || tool.Call == nil || seen[tool.Name] {
			return nil, fmt.Errorf("invalid or duplicate harness tool %q", tool.Name)
		}
		seen[tool.Name] = true
		switch capability.Class {
		case RobotRead, FleetRead:
			if tool.SafetyLevel != skills.SafetyReadOnly || tool.MutatesWorld {
				return nil, fmt.Errorf("read tool %q must be read-only", tool.Name)
			}
		case RobotWrite:
			if tool.SafetyLevel != skills.SafetyPhysical || !tool.MutatesWorld {
				return nil, fmt.Errorf("robot write tool %q needs physical approval and evidence", tool.Name)
			}
		case RobotLocal:
			if tool.SafetyLevel != skills.SafetyLocal || tool.MutatesWorld {
				return nil, fmt.Errorf("robot local tool %q must be a bounded local side effect", tool.Name)
			}
		case FleetDraft:
			if tool.SafetyLevel != skills.SafetyLocal || tool.MutatesWorld {
				return nil, fmt.Errorf("fleet draft tool %q may only change local task state", tool.Name)
			}
		}
		tools = append(tools, tool)
	}
	return tools, nil
}

type RunConfig struct {
	Decider         actionloop.Decider
	Observe         func(context.Context) (actionloop.Observation, error)
	Capabilities    []Capability
	Scope           actionloop.Scope
	Approve         actionloop.Approver
	Record          func(actionloop.Round)
	MaxRounds       int
	MaxUnproductive int
	Now             func() time.Time
}

// Run uses the same decision, approval, evidence and replay loop for both
// roles. The profile rejects forbidden or misclassified tools before the model
// is shown a candidate set or any callback is called.
func (p Profile) Run(ctx context.Context, goal string, config RunConfig) (actionloop.Outcome, error) {
	tools, err := p.validateTools(config.Capabilities)
	if err != nil {
		return actionloop.Outcome{}, err
	}
	maxRounds := 12
	contextRole := "recovery"
	if p.role == Server {
		maxRounds = 8
		contextRole = "system"
	}
	if config.MaxRounds > 0 && config.MaxRounds < maxRounds {
		maxRounds = config.MaxRounds
	}
	return (actionloop.Loop{ContextRole: contextRole, Tools: tools, Decider: config.Decider, Observe: config.Observe,
		Scope: config.Scope, Approve: config.Approve, Record: config.Record,
		MaxRounds: maxRounds, MaxUnproductive: config.MaxUnproductive, Now: config.Now}).Run(ctx, goal)
}
