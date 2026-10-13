package worker

import (
	"context"
	"errors"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
)

// TaskSource pulls ready task ids. Two transports exist: HTTP long-poll
// through the cloud control plane, and direct Redis Stream consumption for
// robots that share the LAN with the queue broker.
type TaskSource interface {
	// Next returns a task id, or ("", nil) when no task is currently
	// available (callers must retry), or an error.
	Next(context.Context) (string, error)
}

// ErrSourceStopped is returned when the source context is cancelled.
var ErrSourceStopped = errors.New("task source stopped")

// HTTPTaskSource long-polls the cloud ready queue for one robot.
type HTTPTaskSource struct {
	cloud   *cloudclient.Client
	robotID string
}

func NewHTTPTaskSource(cloud *cloudclient.Client, robotID string) *HTTPTaskSource {
	return &HTTPTaskSource{cloud: cloud, robotID: robotID}
}

func (s *HTTPTaskSource) Next(ctx context.Context) (string, error) {
	taskID, err := s.cloud.NextTask(ctx, s.robotID)
	if errors.Is(err, cloudclient.ErrTaskUnavailable) {
		return "", nil
	}
	return taskID, err
}
