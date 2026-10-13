package robotclient

import (
	"context"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

// Services are registered by the robot runtime; callers never select a driver.
func (c *Client) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	return c.robot.ListServices(ctx, &robotv1.GetRuntimeInfoRequest{})
}

func (c *Client) CallService(ctx context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if scope, ok := capability.OwnedStopFromContext(ctx); ok {
		if request == nil || !scope.Allows(ctx, request.RobotId, request.Name, request.RequestId, request.OperationId, request.OperationOwnerId, request.OperationLeaseMs, len(request.GetParameters().GetFields())) {
			return nil, fmt.Errorf("%w: request exceeds owned operation stop scope", ErrContractChanged)
		}
		c.pinMu.Lock()
		admitted := c.pinSeen && c.admittedRobotID == scope.RobotID
		c.pinMu.Unlock()
		if (c.expectedRobotID != "" && c.expectedRobotID != scope.RobotID) || !admitted {
			return nil, fmt.Errorf("%w: stop scope does not match previously admitted robot", ErrContractChanged)
		}
		// Keep the same authenticated Runtime connection. Only these exact
		// receipt-bound zero-lease status/cancel calls bypass fresh Info(), which
		// may be unavailable precisely when an in-flight operation needs stopping.
		return c.robot.CallService(ctx, request)
	}
	if c.contractPinPath != "" {
		if _, err := c.Info(ctx); err != nil {
			return nil, err
		}
	}
	return c.robot.CallService(ctx, request)
}
