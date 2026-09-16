package robotclient

import (
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

// acceptFaults reads the robot's `robot.faults.v1` view out of its observation.
//
// Three cases, deliberately distinguished:
//
//   - the runtime publishes no faults at all: unknown, not "healthy". A driver
//     that predates the contract is not lying, so telemetry still flows.
//   - it publishes a report that decodes: that is the current state of the robot,
//     and it travels with the observation into the world model.
//   - it publishes something malformed: refused. A fault list decides whether a
//     capability may be used, so an unreadable one is not "probably fine" - the
//     Robot Runtime is the one place that knows which module is broken, and if
//     that answer cannot be read, the fleet must not guess it.
func acceptFaults(wire *robotv1.Observation) (*robotcontract.FaultReport, error) {
	if wire == nil || wire.RobotState == nil {
		return nil, nil
	}
	raw, published := wire.RobotState.AsMap()["faults"]
	if !published || raw == nil {
		return nil, nil
	}
	values, ok := raw.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("faults rejected: robot_state.faults is %T, not an object", raw)
	}
	report, err := robotcontract.DecodeFaults(values)
	if err != nil {
		return nil, fmt.Errorf("faults rejected: %w", err)
	}
	return report, nil
}
