// Package queue fans distributed task ids out to per-robot ready queues and
// serves pull-based dequeues for a specific robot.
//
// A task is published to the queue of every robot that appears in its intent
// list (plus the shared "any" queue when an intent is unbound). Each edge
// worker consumes only its own queue (own queue first, "any" as fallback),
// which keeps cross-robot coordination in the coordinator and the queue
// oblivious to intent semantics.
package queue

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// AnyRobot is the queue key for unbound intents.
const AnyRobot = ""

// Router is a set of per-robot queues.
type Router struct {
	mu      sync.RWMutex
	queues  map[string]middleware.Queue[string]
	timeout time.Duration
}

// NewRouter creates an empty router. DequeueTimeout bounds each queue poll
// so an HTTP long-poll can serve both the robot queue and the any queue.
func NewRouter(dequeueTimeout time.Duration) *Router {
	return &Router{queues: map[string]middleware.Queue[string]{}, timeout: dequeueTimeout}
}

// Add registers a queue for one robot id ("" registers the any queue).
func (r *Router) Add(robotID string, queue middleware.Queue[string]) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.queues[robotID] = queue
}

// Queue returns the queue for one robot id (nil when absent).
func (r *Router) Queue(robotID string) middleware.Queue[string] {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.queues[robotID]
}

// Enqueue publishes the task id to every distinct robot queue, plus the any
// queue when an unbound intent exists or no robot appears at all.
func (r *Router) Enqueue(ctx context.Context, taskID string, robotIDs []string) error {
	seen := map[string]struct{}{}
	for _, robotID := range robotIDs {
		if robotID == "" {
			seen[AnyRobot] = struct{}{}
			continue
		}
		seen[robotID] = struct{}{}
	}
	if len(seen) == 0 {
		seen[AnyRobot] = struct{}{}
	}
	var errs []error
	for robotID := range seen {
		if err := r.enqueueOne(ctx, robotID, taskID); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

func (r *Router) enqueueOne(ctx context.Context, robotID, taskID string) error {
	r.mu.RLock()
	queue, ok := r.queues[robotID]
	r.mu.RUnlock()
	if !ok {
		return nil
	}
	return queue.Enqueue(ctx, taskID)
}

// Dequeue pulls the next task for a robot: its own queue first, then the
// any queue. Returns (taskID, nil) or ("", nil) when nothing arrives within
// the poll budget.
func (r *Router) Dequeue(ctx context.Context, robotID string) (string, error) {
	if robotID != "" {
		if taskID, err := r.dequeueOne(ctx, robotID); err != nil || taskID != "" {
			return taskID, err
		}
	}
	return r.dequeueOne(ctx, AnyRobot)
}

func (r *Router) dequeueOne(ctx context.Context, robotID string) (string, error) {
	r.mu.RLock()
	queue, ok := r.queues[robotID]
	r.mu.RUnlock()
	if !ok {
		return "", nil
	}
	pollCtx, cancel := context.WithTimeout(ctx, r.timeout)
	defer cancel()
	return queue.Dequeue(pollCtx)
}

// Close closes every registered queue.
func (r *Router) Close() error {
	r.mu.RLock()
	defer r.mu.RUnlock()
	var errs []error
	for _, queue := range r.queues {
		if err := queue.Close(); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}
