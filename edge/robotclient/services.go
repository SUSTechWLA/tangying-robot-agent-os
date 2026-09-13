package robotclient

import (
	"context"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

// Services are registered by the robot runtime; callers never select a driver.
func (c *Client) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	return c.robot.ListServices(ctx, &robotv1.GetRuntimeInfoRequest{})
}

func (c *Client) CallService(ctx context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	return c.robot.CallService(ctx, request)
}
