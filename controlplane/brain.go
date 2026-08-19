// Package controlplane defines the distributed AgentOS brain boundary.
//
// A Brain may run in the cloud, on a laptop, or on an on-premises server. The
// Robot Runtime never receives the brain identity as part of a capability
// command; it only sees a deterministic runtime.Command with safety controls
// already materialized by the edge executor.
package controlplane

import (
	"context"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// Brain turns natural language into a validated, executable task plan.
type Brain interface {
	Parse(string) (manipulation.Intent, error)
	Plan(string, manipulation.Intent) (orchestration.Bundle, error)
}

// TaskSource is the distributed task transport boundary. The edge worker does
// not know whether tasks come from a local SQLite queue, an HTTP cloud
// control plane, a message queue, or a future gRPC fleet service.
type TaskSource interface {
	Next(context.Context) (taskID string, err error)
}

// LocalBrain is the deterministic/LLM brain implementation shipped with the
// local-first AgentOS. A cloud deployment can replace it with an HTTP/gRPC
// adapter that implements the same Brain interface.
type LocalBrain struct {
	parser  intent.Parser
	planner orchestration.Planner
}

func NewLocalBrain(parser intent.Parser, planner orchestration.Planner) *LocalBrain {
	return &LocalBrain{parser: parser, planner: planner}
}

func (b *LocalBrain) Parse(request string) (manipulation.Intent, error) {
	return b.parser.Parse(request)
}

func (b *LocalBrain) Plan(request string, intent manipulation.Intent) (orchestration.Bundle, error) {
	return b.planner.Plan(request, intent)
}
