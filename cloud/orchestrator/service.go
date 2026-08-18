package orchestrator

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type Task struct {
	ID             string                `json:"id"`
	Request        string                `json:"request"`
	Adapter        string                `json:"adapter"`
	Intent         manipulation.Intent   `json:"intent"`
	Plan           *orchestration.Bundle `json:"plan,omitempty"`
	State          taskgraph.TaskState   `json:"state"`
	Approved       bool                  `json:"approved"`
	LeaseID        string                `json:"leaseId,omitempty"`
	LeasedTo       string                `json:"leasedTo,omitempty"`
	LeaseExpiresAt time.Time             `json:"leaseExpiresAt,omitempty"`
	Events         []TaskEvent           `json:"events,omitempty"`
	CreatedAt      time.Time             `json:"createdAt"`
	UpdatedAt      time.Time             `json:"updatedAt"`
}

type TaskEvent struct {
	Sequence   uint64         `json:"sequence"`
	Type       string         `json:"type"`
	StepID     string         `json:"stepId,omitempty"`
	Message    string         `json:"message,omitempty"`
	Payload    map[string]any `json:"payload,omitempty"`
	OccurredAt time.Time      `json:"occurredAt"`
}

type Claim struct {
	Task       *Task     `json:"task,omitempty"`
	LeaseID    string    `json:"leaseId,omitempty"`
	LeaseUntil time.Time `json:"leaseUntil,omitempty"`
}

type Service struct {
	store     Store
	parser    intent.Parser
	planner   orchestration.Planner
	telemetry *TelemetryHub
	mu        sync.Mutex
	now       func() time.Time
}

func NewService(store Store, parser intent.Parser, planners ...orchestration.Planner) *Service {
	service := &Service{
		store:     store,
		parser:    parser,
		telemetry: NewTelemetryHub(),
		now:       time.Now,
	}
	if len(planners) > 0 && planners[0] != nil {
		service.planner = planners[0]
	} else {
		service.planner = orchestration.DeterministicPlanner{}
	}
	return service
}

func (s *Service) Create(ctx context.Context, request, adapter string) (*Task, error) {
	parsed, err := s.parser.Parse(request)
	if err != nil {
		return nil, err
	}
	if adapter == "" {
		adapter = "mujoco"
	}
	planBundle, err := s.planner.Plan(request, parsed)
	if err != nil {
		planBundle = orchestration.Bundle{
			Source:     orchestration.SourceDeterministic,
			Rejections: []string{err.Error()},
		}
	}
	now := s.now().UTC()
	task := &Task{
		ID:        newID("task"),
		Request:   request,
		Adapter:   adapter,
		Intent:    parsed,
		Plan:      &planBundle,
		State:     taskgraph.StateReady,
		CreatedAt: now,
		UpdatedAt: now,
	}
	task.Events = append(task.Events, TaskEvent{Sequence: 1, Type: "TASK_CREATED", OccurredAt: now})
	if err := s.store.Create(ctx, task); err != nil {
		return nil, err
	}
	return task, nil
}

func (s *Service) Get(ctx context.Context, id string) (*Task, error) {
	return s.store.Get(ctx, id)
}

func (s *Service) List(ctx context.Context) ([]*Task, error) { return s.store.List(ctx) }

func (s *Service) PublishTelemetry(_ context.Context, snapshot telemetry.Snapshot) {
	s.telemetry.Publish(snapshot)
}

func (s *Service) TelemetryLatest(adapter string) (telemetry.Snapshot, bool) {
	return s.telemetry.Latest(adapter)
}

func (s *Service) TelemetryHistory(adapter string, limit int) []telemetry.Snapshot {
	return s.telemetry.History(adapter, limit)
}

func (s *Service) TelemetryAdapters() []string {
	return s.telemetry.Adapters()
}

func (s *Service) OrchestrationMetrics(ctx context.Context) orchestration.Metrics {
	tasks, err := s.store.List(ctx)
	if err != nil {
		return orchestration.Metrics{}
	}
	records := make([]orchestration.TaskRecord, 0, len(tasks))
	for _, task := range tasks {
		record := orchestration.TaskRecord{State: string(task.State), Sequence: len(task.Intent.Sequence) > 1}
		if task.Plan != nil {
			record.Source = task.Plan.Source
			record.Attempts = task.Plan.Attempts
			record.AcceptedCandidates = task.Plan.AcceptedCandidates
			record.Rejections = task.Plan.Rejections
		}
		records = append(records, record)
	}
	return orchestration.CalculateMetrics(records)
}

func (s *Service) Claim(ctx context.Context, agentID string, duration time.Duration) (Claim, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	tasks, err := s.store.List(ctx)
	if err != nil {
		return Claim{}, err
	}
	now := s.now().UTC()
	for _, task := range tasks {
		if terminal(task.State) || (task.LeaseID != "" && task.LeaseExpiresAt.After(now)) {
			continue
		}
		task.LeaseID = newID("lease")
		task.LeasedTo = agentID
		task.LeaseExpiresAt = now.Add(duration)
		task.UpdatedAt = now
		if err := s.store.Update(ctx, task); err != nil {
			return Claim{}, err
		}
		return Claim{Task: task, LeaseID: task.LeaseID, LeaseUntil: task.LeaseExpiresAt}, nil
	}
	return Claim{}, nil
}

// RenewLease extends an active task lease. Keeping the lease alive is required
// for long-running compound tasks; the local agent stops execution when renewal
// fails so another agent cannot claim and repeat physical steps.
func (s *Service) RenewLease(ctx context.Context, leaseID, agentID string, duration time.Duration) (time.Time, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	tasks, err := s.store.List(ctx)
	if err != nil {
		return time.Time{}, err
	}
	now := s.now().UTC()
	for _, task := range tasks {
		if task.LeaseID != leaseID || task.LeasedTo != agentID {
			continue
		}
		if terminal(task.State) {
			return time.Time{}, ErrLeaseNotFound
		}
		task.LeaseExpiresAt = now.Add(duration)
		task.UpdatedAt = now
		if err := s.store.Update(ctx, task); err != nil {
			return time.Time{}, err
		}
		return task.LeaseExpiresAt, nil
	}
	return time.Time{}, ErrLeaseNotFound
}

func (s *Service) Approve(ctx context.Context, taskID string) (*Task, error) {
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	task.Approved = true
	task.UpdatedAt = s.now().UTC()
	s.appendEvent(task, "TASK_APPROVED", "", "")
	return task, s.store.Update(ctx, task)
}

func (s *Service) Transition(ctx context.Context, taskID string, target taskgraph.TaskState, reason string) error {
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		return err
	}
	if target != taskgraph.StateSafetyStopped && !taskgraph.CanTransition(task.State, target) {
		return fmt.Errorf("invalid task transition %s -> %s", task.State, target)
	}
	if terminal(task.State) {
		return errors.New("terminal task cannot transition")
	}
	task.State = target
	task.UpdatedAt = s.now().UTC()
	s.appendEvent(task, "STATE_CHANGED", "", reason)
	return s.store.Update(ctx, task)
}

func (s *Service) AppendEvent(ctx context.Context, taskID string, event TaskEvent) (*Task, error) {
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	event.Sequence = uint64(len(task.Events) + 1)
	if event.OccurredAt.IsZero() {
		event.OccurredAt = s.now().UTC()
	}
	task.Events = append(task.Events, event)
	task.UpdatedAt = event.OccurredAt
	return task, s.store.Update(ctx, task)
}

func (s *Service) appendEvent(task *Task, eventType, stepID, message string) {
	task.Events = append(task.Events, TaskEvent{
		Sequence: uint64(len(task.Events) + 1), Type: eventType, StepID: stepID,
		Message: message, OccurredAt: s.now().UTC(),
	})
}

func terminal(state taskgraph.TaskState) bool {
	return state == taskgraph.StateSucceeded || state == taskgraph.StateCancelled || state == taskgraph.StateFailed
}

func newID(prefix string) string {
	var bytes [12]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		panic(err)
	}
	return prefix + "-" + hex.EncodeToString(bytes[:])
}
