package tasks

import (
	"context"
	"errors"
	"fmt"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"strings"
)

// CreateObjective persists an unapproved parent goal, without invoking a planner.
// Approval of this immutable envelope authorizes derived edge plans inside it.
func (s *Service) CreateObjective(ctx context.Context, c objective.Contract, adapter string) (*Task, error) {
	return s.createObjective(ctx, c, adapter, newID("task"))
}
func (s *Service) createObjective(ctx context.Context, c objective.Contract, adapter, id string) (*Task, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	if !c.ExpiresAt.After(s.now()) {
		return nil, fmt.Errorf("objective already expired")
	}
	return s.createCapabilityTaskWithID(ctx, c.Goal, adapter, orchestration.Bundle{Source: "delegated_objective", Capabilities: &capability.Plan{RobotID: c.RobotID, CatalogRevision: c.CatalogRevision, Calls: []capability.Call{c.Call()}}}, id)
}

// CreateObjectiveOnce makes goal delivery retryable across HTTP response loss
// and server restarts. A key belongs to one authenticated actor and immutable
// envelope. Reusing it never resets approval, progress, expiry or termination.
func (s *Service) CreateObjectiveOnce(ctx context.Context, c objective.Contract, adapter, key, actor string) (*Task, error) {
	if strings.TrimSpace(key) == "" || len(key) > 128 || strings.TrimSpace(actor) == "" {
		return nil, fmt.Errorf("bounded objective idempotency key and actor required")
	}
	if err := c.Validate(); err != nil {
		return nil, err
	}
	id := "task-goal-" + capability.Fingerprint([]string{actor, key})[:32]
	s.mu.Lock()
	defer s.mu.Unlock()
	check := func(task *Task) (*Task, error) {
		if task.Plan == nil || task.Plan.Capabilities == nil || len(task.Plan.Capabilities.Calls) != 1 || task.Adapter != NormalizeAdapter(adapter) || capability.Fingerprint(task.Plan.Capabilities.Calls[0]) != capability.Fingerprint(c.Call()) {
			return nil, fmt.Errorf("%w: objective key reused with different envelope", ErrIdempotencyConflict)
		}
		return task, nil
	}
	if task, err := s.store.Get(ctx, id); err == nil {
		return check(task)
	} else if !errors.Is(err, ErrTaskNotFound) {
		return nil, err
	}
	task, err := s.createObjective(ctx, c, adapter, id)
	if err != nil {
		if existing, readErr := s.store.Get(ctx, id); readErr == nil {
			return check(existing)
		}
	}
	return task, err
}
