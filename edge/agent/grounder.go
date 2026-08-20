package agent

import (
	"context"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// GrounderRouter selects the robot whose scene should be used for grounding.
// It mirrors runtime.Router: robot identity is an Agent-layer routing concern
// and never leaks into the Robot Runtime command contract.
type GrounderRouter struct {
	defaultID string
	grounders map[string]Grounder
}

func NewGrounderRouter(defaultID string, defaultGrounder Grounder) *GrounderRouter {
	router := &GrounderRouter{defaultID: defaultID, grounders: map[string]Grounder{}}
	if defaultGrounder != nil {
		router.grounders[defaultID] = defaultGrounder
	}
	return router
}

func (r *GrounderRouter) Register(robotID string, grounder Grounder) error {
	if robotID == "" || grounder == nil {
		return fmt.Errorf("robotID and grounder are required")
	}
	r.grounders[robotID] = grounder
	return nil
}

func (r *GrounderRouter) Ground(ctx context.Context, intent manipulation.Intent) (manipulation.GroundedTask, error) {
	robotID := intent.RobotID
	if robotID == "" {
		robotID = r.defaultID
	}
	grounder, ok := r.grounders[robotID]
	if !ok {
		return manipulation.GroundedTask{}, fmt.Errorf("grounder is not registered for %s", robotID)
	}
	return grounder.Ground(ctx, intent)
}

// Telemetry forwards the runtime telemetry provider of the default robot, so
// the Agent runner's execution-time activity telemetry (OBSERVING/EXECUTING)
// keeps flowing when grounding is routed through a GrounderRouter.
func (r *GrounderRouter) Telemetry(ctx context.Context, taskID string) (telemetry.Snapshot, error) {
	grounder, ok := r.grounders[r.defaultID]
	if !ok {
		return telemetry.Snapshot{}, fmt.Errorf("grounder is not registered for %s", r.defaultID)
	}
	provider, ok := grounder.(telemetryProvider)
	if !ok {
		return telemetry.Snapshot{}, fmt.Errorf("grounder %s does not provide telemetry", r.defaultID)
	}
	return provider.Telemetry(ctx, taskID)
}
