package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

const failedPlanningRecordTimeout = 5 * time.Second

var planningAttemptIDPattern = regexp.MustCompile(`^planning-attempt-[0-9a-f]{32}$`)

type planningAttemptStore struct {
	store      eventlog.Store
	commitOnce sync.Once
	commitSlot chan struct{}
}

type failedPlanningRecorder struct {
	planner tasks.GoalPlanner
	archive *planningAttemptStore
}

func withFailedPlanningRecorder(planner tasks.GoalPlanner, archive *planningAttemptStore) tasks.GoalPlanner {
	if planner == nil {
		return nil // Preserve the existing development/parser fallback.
	}
	return &failedPlanningRecorder{planner: planner, archive: archive}
}

func (p *failedPlanningRecorder) PlanGoal(ctx context.Context, request string) (orchestration.Bundle, bool, error) {
	bundle, handled, err := p.planner.PlanGoal(ctx, request)
	if err == nil {
		return bundle, handled, nil
	}
	// Cancellation may be the very failure whose input must remain inspectable.
	// Only the bounded journal commit is detached, never planning or execution.
	recordCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), failedPlanningRecordTimeout)
	defer cancel()
	id, recordErr := p.archive.record(recordCtx, request, bundle, err)
	return bundle, handled, &capability.FailedPlanningError{Cause: err,
		PlanningAttemptID: id, AuditUnavailable: recordErr != nil}
}

func (s *planningAttemptStore) record(ctx context.Context, request string, bundle orchestration.Bundle, cause error) (string, error) {
	if s == nil || s.store == nil {
		return "", errors.New("planning attempt archive unavailable")
	}
	trace := []capability.PlanningStep{}
	robot, catalog := "", ""
	if bundle.Capabilities != nil {
		robot, catalog = bundle.Capabilities.RobotID, bundle.Capabilities.CatalogRevision
		trace = append(trace, bundle.Capabilities.PlanningTrace...)
	}
	scope := agentcontext.Scope{RobotID: robot}
	for _, round := range trace {
		if round.Context != nil && round.Context.Scope != scope {
			return "", errors.New("failed planning trace has conflicting pre-task scope")
		}
	}
	source, err := json.Marshal(trace)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return "", err
	}
	id := fmt.Sprintf("planning-attempt-%x", nonce)
	now := time.Now().UTC()
	attempt := capability.PlanningAttempt{SchemaVersion: capability.PlanningAttemptVersion,
		ID: id, CreatedAt: now, Request: request, Error: cause.Error(), Source: string(bundle.Source),
		Scope: scope, RobotID: robot, CatalogRevision: catalog, OriginalSourceTrace: string(source),
		SourceTraceSHA256: agentcontext.Hash(string(source)), TraceAvailable: len(trace) > 0}
	wire, err := json.Marshal(attempt)
	if err != nil {
		return "", err
	}
	// SQLite's deferred transactions can compete during read-to-write upgrades.
	// Serialize this archive's commits, with the same detached time budget also
	// bounding the wait. This gate never serializes planning or physical work.
	s.commitOnce.Do(func() { s.commitSlot = make(chan struct{}, 1) })
	select {
	case s.commitSlot <- struct{}{}:
		defer func() { <-s.commitSlot }()
	case <-ctx.Done():
		return "", ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	err = s.store.Commit(ctx, eventlog.CommitRequest{ExpectedVersion: 0,
		State: eventlog.AggregateState{AggregateID: id, Version: 1, Data: wire, UpdatedAt: now},
		Events: []eventlog.DomainEvent{{EventID: id + "/rejected", AggregateID: id,
			AggregateType: "planning_attempt", AggregateVersion: 1, EventType: "PLANNING_ATTEMPT_REJECTED",
			IdempotencyKey: id, CorrelationID: id, Actor: "local-agent/goal", OccurredAt: now,
			Payload: map[string]any{"planningAttemptId": id, "sourceTraceSHA256": attempt.SourceTraceSHA256,
				"traceAvailable": attempt.TraceAvailable, "executable": false}}}})
	if err != nil {
		return "", err
	}
	return id, nil
}

func (s *planningAttemptStore) PlanningAttempt(ctx context.Context, id string) (capability.PlanningAttempt, error) {
	if !planningAttemptIDPattern.MatchString(id) {
		return capability.PlanningAttempt{}, capability.ErrPlanningAttemptNotFound
	}
	if s == nil || s.store == nil {
		return capability.PlanningAttempt{}, errors.New("planning attempt archive unavailable")
	}
	state, found, err := s.store.LoadState(ctx, id)
	if err != nil {
		return capability.PlanningAttempt{}, err
	}
	if !found {
		return capability.PlanningAttempt{}, capability.ErrPlanningAttemptNotFound
	}
	var attempt capability.PlanningAttempt
	if err := json.Unmarshal(state.Data, &attempt); err != nil || state.Version != 1 ||
		attempt.ID != id || attempt.SchemaVersion != capability.PlanningAttemptVersion ||
		attempt.Scope != (agentcontext.Scope{RobotID: attempt.RobotID}) ||
		agentcontext.Hash(attempt.OriginalSourceTrace) != attempt.SourceTraceSHA256 {
		return capability.PlanningAttempt{}, capability.ErrPlanningAttemptCorrupt
	}
	var trace []capability.PlanningStep
	if err := json.Unmarshal([]byte(attempt.OriginalSourceTrace), &trace); err != nil || trace == nil ||
		attempt.TraceAvailable != (len(trace) > 0) {
		return capability.PlanningAttempt{}, capability.ErrPlanningAttemptCorrupt
	}
	for _, round := range trace {
		if round.Context != nil && round.Context.Scope != attempt.Scope {
			return capability.PlanningAttempt{}, capability.ErrPlanningAttemptCorrupt
		}
	}
	return attempt, nil
}
