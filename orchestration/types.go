// Package orchestration turns a validated intent sequence into task plans.
//
// The deterministic planner is the fail-safe baseline. The LLM planner is
// schema-generated from the registered skill catalog; it may choose and order
// skills itself, but every physical control (approval, deadline, lease,
// idempotency and safety level) is re-created by the Local Agent before
// execution and never trusted from the model.
package orchestration

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

const (
	SourceDeterministic = "deterministic"
	SourceLLM           = "llm"
	SourceConsensus     = "llm_consensus"
)

// Bundle is the persisted orchestration output attached to a local task.
type Bundle struct {
	Source             string               `json:"source"`
	Plans              []taskgraph.TaskPlan `json:"plans,omitempty"`
	Attempts           int                  `json:"attempts,omitempty"`
	AcceptedCandidates int                  `json:"acceptedCandidates,omitempty"`
	Rejections         []string             `json:"rejections,omitempty"`
}

func (b Bundle) LLMGenerated() bool {
	return b.Source == SourceLLM || b.Source == SourceConsensus
}

// Planner generates one plan template per intent in execution order.
//
// The world is passed per call rather than held on the planner because it changes
// between tasks: a plan is only meaningful against the state it was formed from,
// and a planner that cached the robot's location would keep planning for wherever
// it used to be.
type Planner interface {
	Plan(request string, intent manipulation.Intent, world World) (Bundle, error)
}

// DeterministicPlanner leaves planning to the Local Agent's validated
// domain plan builder. It is intentionally dependency-free, and it ignores the
// world because it produces no steps of its own to make consistent with it.
type DeterministicPlanner struct{}

func (DeterministicPlanner) Plan(string, manipulation.Intent, World) (Bundle, error) {
	return Bundle{Source: SourceDeterministic}, nil
}
