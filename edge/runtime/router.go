package runtime

import (
	"context"
	"fmt"
	"sync"
)

// Router routes capability commands to registered Robot Runtime clients.
// The robot runtime never knows whether a command came from a cloud brain,
// a local brain, or an automated recovery policy. Routing identity stays in
// the distributed AgentOS layer.
type Router struct {
	mu        sync.RWMutex
	clients   map[string]Client
	defaultID string
}

func NewRouter(defaultID string, defaultClient Client) *Router {
	router := &Router{clients: map[string]Client{}, defaultID: defaultID}
	if defaultClient != nil {
		router.clients[defaultID] = defaultClient
	}
	return router
}

func (r *Router) Register(robotID string, client Client) error {
	if robotID == "" || client == nil {
		return fmt.Errorf("robotID and client are required")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.clients[robotID] = client
	return nil
}

func (r *Router) client(robotID string) (Client, string, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if robotID == "" {
		robotID = r.defaultID
	}
	client, ok := r.clients[robotID]
	if !ok {
		return nil, "", fmt.Errorf("%w: %s", ErrRobotUnknown, robotID)
	}
	return client, robotID, nil
}

func (r *Router) Invoke(ctx context.Context, command Command) (Result, error) {
	client, _, err := r.client(command.RobotID)
	if err != nil {
		return Result{}, err
	}
	return client.Invoke(ctx, command)
}

func (r *Router) Info(ctx context.Context) (Snapshot, error) {
	client, _, err := r.client("")
	if err != nil {
		return Snapshot{}, err
	}
	return client.Info(ctx)
}

func (r *Router) InfoFor(ctx context.Context, robotID string) (Snapshot, error) {
	client, _, err := r.client(robotID)
	if err != nil {
		return Snapshot{}, err
	}
	return client.Info(ctx)
}

func (r *Router) Cancel(ctx context.Context, commandID, reason string) (bool, error) {
	client, _, err := r.client("")
	if err != nil {
		return false, err
	}
	return client.Cancel(ctx, commandID, reason)
}

func (r *Router) CancelFor(ctx context.Context, robotID, commandID, reason string) (bool, error) {
	client, _, err := r.client(robotID)
	if err != nil {
		return false, err
	}
	return client.Cancel(ctx, commandID, reason)
}

func (r *Router) EmergencyStop(ctx context.Context, reason string) error {
	client, _, err := r.client("")
	if err != nil {
		return err
	}
	return client.EmergencyStop(ctx, reason)
}

func (r *Router) EmergencyStopFor(ctx context.Context, robotID, reason string) error {
	client, _, err := r.client(robotID)
	if err != nil {
		return err
	}
	return client.EmergencyStop(ctx, reason)
}
