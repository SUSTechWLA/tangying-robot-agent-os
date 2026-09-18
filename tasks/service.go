package tasks

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type Task struct {
	ID       string                `json:"id"`
	Request  string                `json:"request"`
	Adapter  string                `json:"adapter"`
	Intent   manipulation.Intent   `json:"intent"`
	Plan     *orchestration.Bundle `json:"plan,omitempty"`
	State    taskgraph.TaskState   `json:"state"`
	Approved bool                  `json:"approved"`
	// ApprovedBy and ApprovedAt record who consented and when.
	//
	// The boolean alone could say that a task was approved but never by whom, so
	// "who let this run" had no answer anywhere in the system — the one question
	// an approval exists to make answerable. The value names the surface that
	// consented (a console session, the fleet API), not a person: the console has
	// no accounts to name one with, and inventing an identity there would be a
	// weaker second system beside the fleet's real auth.
	ApprovedBy       string         `json:"approvedBy,omitempty"`
	ApprovedAt       time.Time      `json:"approvedAt,omitempty"`
	CurrentRevision  uint64         `json:"currentRevision"`
	AggregateVersion uint64         `json:"aggregateVersion"`
	RevisionState    RevisionStatus `json:"revisionState"`
	Events           []TaskEvent    `json:"events,omitempty"`
	CreatedAt        time.Time      `json:"createdAt"`
	UpdatedAt        time.Time      `json:"updatedAt"`
}

type TaskEvent struct {
	Sequence   uint64         `json:"sequence"`
	Type       string         `json:"type"`
	StepID     string         `json:"stepId,omitempty"`
	Message    string         `json:"message,omitempty"`
	Payload    map[string]any `json:"payload,omitempty"`
	OccurredAt time.Time      `json:"occurredAt"`
}

type Service struct {
	// Serialize read/modify/write mutations so tool receipts, operator actions,
	// and revision commits cannot overwrite each other's event sequence/state.
	mu      sync.Mutex
	store   Repository
	planner orchestration.Planner
	// parser is read on every task creation and replaced when the operator
	// changes the model configuration, so it is behind its own lock rather than
	// the mutation lock: parsing does not touch task state, and holding the
	// mutation lock through a model call would serialize every task behind the
	// slowest network request.
	parserMu  sync.RWMutex
	parser    intent.Parser
	telemetry *TelemetryHub
	now       func() time.Time
	// observerMu guards eventObserver, which is set once at composition time
	// and read on every event.
	observerMu    sync.Mutex
	eventObserver EventObserver
}

// EventObserver is called after a task event has been appended and persisted.
// It exists so the Agent runtime can learn about lifecycle changes without every
// caller of Transition or AppendEvent having to remember to announce them.
//
// It is deliberately advisory. The change has already been committed and the
// observer's error is not returned to the caller: an observer that could fail a
// write would make observability part of the safety path, which is the opposite
// of the rule the rest of this repository follows.
type EventObserver func(ctx context.Context, taskID string, event TaskEvent)

// ObserveEvents installs the event observer. Passing nil removes it. It is
// meant to be called once, during composition.
func (s *Service) ObserveEvents(observer EventObserver) {
	s.observerMu.Lock()
	defer s.observerMu.Unlock()
	s.eventObserver = observer
}

func (s *Service) observer() EventObserver {
	s.observerMu.Lock()
	defer s.observerMu.Unlock()
	return s.eventObserver
}

// notifyEvent reports one committed event. It is called with no service lock
// held so an observer may read back the task it just heard about without
// deadlocking, which is exactly what the Agent runtime does with it.
func (s *Service) notifyEvent(ctx context.Context, taskID string, event TaskEvent) {
	current := s.observer()
	if current == nil {
		return
	}
	current(ctx, taskID, event)
}

// currentParser reads the parser in force right now.
func (s *Service) currentParser() intent.Parser {
	s.parserMu.RLock()
	defer s.parserMu.RUnlock()
	return s.parser
}

// SetParser replaces how requests are understood, without a restart.
//
// The model configuration is an operator setting, and needing to restart the agent
// to apply it makes the setting a deployment parameter pretending to be a setting.
// Replacing the parser takes effect on the next request; a request already being
// parsed finishes under the parser it started with, which is the honest
// behaviour — half-understanding one sentence under two grammars would be worse
// than either.
//
// A nil parser is refused rather than stored: a service that cannot parse anything
// would accept no task and report no reason.
func (s *Service) SetParser(parser intent.Parser) {
	if parser == nil {
		return
	}
	s.parserMu.Lock()
	defer s.parserMu.Unlock()
	s.parser = parser
}

func NewService(store Repository, parser intent.Parser, planners ...orchestration.Planner) *Service {
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

func NormalizeAdapter(adapter string) string {
	switch strings.ToLower(strings.TrimSpace(adapter)) {
	case "", "auto", "sim", "simulation":
		return "mujoco"
	case "direct", "xlerobot", "xlerobot_direct", "xlerobot-direct":
		return "xlerobot_direct"
	case "ros", "ros2", "xlerobot_ros2", "xlerobot-ros2":
		return "xlerobot_ros2"
	default:
		return strings.ToLower(strings.TrimSpace(adapter))
	}
}

func (s *Service) Create(ctx context.Context, request, adapter string) (*Task, error) {
	parsed, err := s.currentParser().Parse(request)
	if err != nil {
		return nil, err
	}
	adapter = NormalizeAdapter(adapter)
	// The world the plan has to be consistent with. Without it a plan can instruct
	// the robot to look for an object in a room it is not in, which always fails
	// and looks like a perception fault rather than a planning one.
	planBundle, err := s.planner.Plan(request, parsed, s.worldFor(adapter))
	if err != nil {
		planBundle = orchestration.Bundle{
			Source:     orchestration.SourceDeterministic,
			Rejections: []string{err.Error()},
		}
	}
	now := s.now().UTC()
	task := &Task{
		ID:               newID("task"),
		Request:          request,
		Adapter:          adapter,
		Intent:           parsed,
		Plan:             &planBundle,
		State:            taskgraph.StateReady,
		CurrentRevision:  1,
		AggregateVersion: 1,
		RevisionState:    RevisionActive,
		CreatedAt:        now,
		UpdatedAt:        now,
	}
	task.Events = append(task.Events, TaskEvent{Sequence: 1, Type: "TASK_CREATED", OccurredAt: now})
	revision := &TaskRevision{
		TaskID: task.ID, Revision: 1, Request: request, Understanding: understandingForIntent(parsed),
		Intent: parsed, Plan: &planBundle, Steps: buildRevisionSteps(parsed, 1, nil),
		RiskClass: "physical", ApprovalRequired: true, Creator: "task/create",
		IdempotencyKey: task.ID + "/create", CreatedAt: now,
	}
	if err := s.store.CreateWithRevision(ctx, task, revision); err != nil {
		return nil, err
	}
	return task, nil
}

func (s *Service) Get(ctx context.Context, id string) (*Task, error) {
	return s.store.Get(ctx, id)
}

func (s *Service) List(ctx context.Context) ([]*Task, error) { return s.store.List(ctx) }

func (s *Service) ListRevisions(ctx context.Context, taskID string) ([]RevisionRecord, error) {
	return s.store.ListRevisions(ctx, taskID)
}

func (s *Service) ProposeRevision(ctx context.Context, command ProposeRevisionCommand, basis RevisionBasis) (*RevisionRecord, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	command.TaskID = strings.TrimSpace(command.TaskID)
	command.Request = strings.TrimSpace(command.Request)
	command.IdempotencyKey = strings.TrimSpace(command.IdempotencyKey)
	if command.TaskID == "" || command.Request == "" || command.IdempotencyKey == "" {
		return nil, errors.New("task id, request, and idempotency key are required")
	}
	task, err := s.store.Get(ctx, command.TaskID)
	if err != nil {
		return nil, err
	}
	history, err := s.store.ListRevisions(ctx, command.TaskID)
	if err != nil {
		return nil, err
	}
	for index := range history {
		if history[index].Revision.IdempotencyKey != command.IdempotencyKey {
			continue
		}
		if history[index].Revision.Request != command.Request {
			return nil, ErrIdempotencyConflict
		}
		return &history[index], nil
	}
	if command.ExpectedRevision != task.CurrentRevision {
		return nil, &RevisionConflictError{Current: task}
	}
	current, err := s.store.Revision(ctx, task.ID, task.CurrentRevision)
	if err != nil {
		return nil, err
	}
	parsed, err := s.currentParser().Parse(command.Request)
	if err != nil {
		parsed, err = contextualRevisionIntent(task.Intent, command.Request)
		if err != nil {
			return nil, err
		}
	}
	// A revision is planned against the world as it is now, not as it was when the
	// task was created: the robot has moved since.
	planBundle, planErr := s.planner.Plan(command.Request, parsed, s.worldFor(task.Adapter))
	if planErr != nil {
		planBundle = orchestration.Bundle{Source: orchestration.SourceDeterministic, Rejections: []string{planErr.Error()}}
	}
	now := s.now().UTC()
	nextRevision := task.CurrentRevision + 1
	steps := buildRevisionSteps(parsed, nextRevision, current.Revision.Steps)
	revision := &TaskRevision{
		TaskID: task.ID, Revision: nextRevision, BaseRevision: task.CurrentRevision,
		ExpectedAggregateVersion: task.AggregateVersion, Request: command.Request,
		Understanding: understandingForIntent(parsed), Intent: parsed, Plan: &planBundle,
		Steps: steps, RiskClass: "physical", ApprovalRequired: true,
		Creator: strings.TrimSpace(command.Creator), IdempotencyKey: command.IdempotencyKey, CreatedAt: now,
	}
	revision.ChangeSet = BuildChangeSet(current, steps, basis)
	retained := make(map[string]struct{}, len(revision.ChangeSet.Retained))
	for _, stepID := range revision.ChangeSet.Retained {
		retained[stepID] = struct{}{}
	}
	for index := range revision.Steps {
		if _, ok := retained[revision.Steps[index].StepID]; ok {
			continue
		}
		revision.Steps[index].Status = StepPending
		revision.Steps[index].HarnessEvidenceIDs = nil
	}
	nextTask := *task
	nextTask.AggregateVersion = task.AggregateVersion + 1
	nextTask.RevisionState = RevisionProposed
	nextTask.UpdatedAt = now
	event := RevisionLifecycleEvent{Revision: nextRevision, Status: RevisionProposed, OccurredAt: now,
		Payload: map[string]any{"baseRevision": task.CurrentRevision, "idempotencyKey": command.IdempotencyKey}}
	taskEvent := TaskEvent{Sequence: uint64(len(task.Events) + 1), Type: "REVISION_PROPOSED", OccurredAt: now,
		Payload: map[string]any{"revision": nextRevision, "aggregateVersion": nextTask.AggregateVersion}}
	if err := s.store.CommitRevision(ctx, RevisionCommit{
		TaskID: task.ID, ExpectedAggregateVersion: task.AggregateVersion, Task: &nextTask,
		NewRevision: revision, LifecycleEvents: []RevisionLifecycleEvent{event}, TaskEvent: &taskEvent,
	}); err != nil {
		if errors.Is(err, ErrRevisionConflict) {
			latest, _ := s.store.Get(ctx, task.ID)
			return nil, &RevisionConflictError{Current: latest}
		}
		return nil, err
	}
	record, err := s.store.Revision(ctx, task.ID, nextRevision)
	return &record, err
}

func (s *Service) ConfirmRevision(ctx context.Context, command ConfirmRevisionCommand) (*RevisionRecord, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	task, err := s.store.Get(ctx, command.TaskID)
	if err != nil {
		return nil, err
	}
	record, err := s.store.Revision(ctx, command.TaskID, command.Revision)
	if err != nil {
		return nil, err
	}
	if record.Status == RevisionActive && task.CurrentRevision == command.Revision {
		return &record, nil
	}
	if command.ExpectedCurrentRevision != task.CurrentRevision {
		return nil, &RevisionConflictError{Current: task}
	}
	if record.Status != RevisionProposed && record.Status != RevisionWaitingApproval && record.Status != RevisionWaitingSafePoint {
		return nil, fmt.Errorf("revision %d cannot be confirmed from %s", command.Revision, record.Status)
	}
	if command.WaitForSafePoint || command.ApprovalRequired {
		status := RevisionWaitingSafePoint
		if command.ApprovalRequired && !command.WaitForSafePoint {
			status = RevisionWaitingApproval
		}
		return s.commitRevisionStatus(ctx, task, record, status, command.IdempotencyKey, false)
	}
	return s.commitRevisionStatus(ctx, task, record, RevisionActive, command.IdempotencyKey, true)
}

func (s *Service) ActivateWaitingRevision(ctx context.Context, taskID string, revision uint64, idempotencyKey string) (*RevisionRecord, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	record, err := s.store.Revision(ctx, taskID, revision)
	if err != nil {
		return nil, err
	}
	if record.Status == RevisionActive && task.CurrentRevision == revision {
		return &record, nil
	}
	if record.Status != RevisionWaitingSafePoint && record.Status != RevisionWaitingApproval {
		return nil, fmt.Errorf("revision %d is not waiting for activation", revision)
	}
	return s.commitRevisionStatus(ctx, task, record, RevisionActive, idempotencyKey, true)
}

func (s *Service) commitRevisionStatus(
	ctx context.Context,
	task *Task,
	record RevisionRecord,
	status RevisionStatus,
	idempotencyKey string,
	activate bool,
) (*RevisionRecord, error) {
	now := s.now().UTC()
	nextTask := *task
	nextTask.AggregateVersion = task.AggregateVersion + 1
	nextTask.RevisionState = status
	nextTask.UpdatedAt = now
	events := []RevisionLifecycleEvent{{Revision: record.Revision.Revision, Status: status, OccurredAt: now,
		Payload: map[string]any{"idempotencyKey": idempotencyKey}}}
	if activate {
		if task.CurrentRevision != record.Revision.Revision {
			events = append([]RevisionLifecycleEvent{{Revision: task.CurrentRevision, Status: RevisionSuperseded, OccurredAt: now,
				Payload: map[string]any{"supersededBy": record.Revision.Revision}}}, events...)
		}
		nextTask.CurrentRevision = record.Revision.Revision
		nextTask.Request = record.Revision.Request
		nextTask.Intent = cloneIntent(record.Revision.Intent)
		if record.Revision.Plan == nil {
			nextTask.Plan = nil
		} else {
			plan := cloneBundle(*record.Revision.Plan)
			nextTask.Plan = &plan
		}
	}
	taskEvent := TaskEvent{Sequence: uint64(len(task.Events) + 1), Type: "REVISION_STATUS_CHANGED", OccurredAt: now,
		Payload: map[string]any{"revision": record.Revision.Revision, "status": status, "aggregateVersion": nextTask.AggregateVersion}}
	if err := s.store.CommitRevision(ctx, RevisionCommit{
		TaskID: task.ID, ExpectedAggregateVersion: task.AggregateVersion, Task: &nextTask,
		LifecycleEvents: events, TaskEvent: &taskEvent,
	}); err != nil {
		if errors.Is(err, ErrRevisionConflict) {
			latest, _ := s.store.Get(ctx, task.ID)
			return nil, &RevisionConflictError{Current: latest}
		}
		return nil, err
	}
	updated, err := s.store.Revision(ctx, task.ID, record.Revision.Revision)
	return &updated, err
}

func contextualRevisionIntent(previous manipulation.Intent, request string) (manipulation.Intent, error) {
	destination, err := intent.ParseDestinationRevision(request)
	if err != nil {
		return manipulation.Intent{}, err
	}
	intents := previous.Tasks()
	if len(intents) == 0 {
		return manipulation.Intent{}, intent.ErrUnsupportedIntent
	}
	updated := make([]manipulation.Intent, len(intents))
	for index := range intents {
		updated[index] = cloneIntent(intents[index])
	}
	updated[len(updated)-1].Destination = destination
	result := updated[0]
	if len(updated) > 1 {
		result.Sequence = updated
	}
	return result, nil
}

func buildRevisionSteps(parsed manipulation.Intent, revision uint64, previous []RevisionStep) []RevisionStep {
	intents := parsed.Tasks()
	steps := make([]RevisionStep, 0, len(intents))
	for index, parsedIntent := range intents {
		resourceID := entityResourceID(parsedIntent.Object)
		postcondition := resourceID + " in " + parsedIntent.Destination.Category
		if parsedIntent.Action == manipulation.ActionHomeRoute {
			resourceID = "home-route"
			postcondition = "arrived at " + strings.Join(parsedIntent.RouteRooms, " -> ")
		}
		if parsedIntent.Destination.Relation != "" {
			postcondition += "/" + parsedIntent.Destination.Relation
		}
		if destination := entityResourceID(parsedIntent.Destination); destination != "" && destination != parsedIntent.Destination.Category {
			postcondition += "/" + destination
		}
		step := RevisionStep{IntroducedRevision: revision, IntentIndex: index, Action: parsedIntent.Action,
			RobotID: parsedIntent.RobotID, ResourceID: resourceID, RequiredPostcondition: postcondition, Status: StepPending}
		step.SemanticFingerprint = SemanticFingerprint(step)
		if index < len(previous) {
			step.StepID = previous[index].StepID
			if previous[index].SemanticFingerprint == step.SemanticFingerprint {
				step.IntroducedRevision = previous[index].IntroducedRevision
				step.Status = previous[index].Status
				step.HarnessEvidenceIDs = append([]string(nil), previous[index].HarnessEvidenceIDs...)
			}
		} else {
			step.StepID = StableStepID(index, step.SemanticFingerprint)
		}
		steps = append(steps, step)
	}
	return steps
}

func entityResourceID(selector manipulation.EntitySelector) string {
	color := strings.TrimSpace(selector.Attributes["color"])
	category := strings.TrimSpace(selector.Category)
	if color != "" && category != "" {
		return color + "-" + category
	}
	if category != "" {
		return category
	}
	keys := make([]string, 0, len(selector.Attributes))
	for key := range selector.Attributes {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, key+"="+selector.Attributes[key])
	}
	return strings.Join(parts, ",")
}

func understandingForIntent(parsed manipulation.Intent) string {
	intents := parsed.Tasks()
	parts := make([]string, 0, len(intents))
	for _, parsedIntent := range intents {
		if parsedIntent.Action == manipulation.ActionHomeManipulation {
			parts = append(parts, "家庭任务："+strings.Join(parsedIntent.RouteRooms, " → ")+"，将"+
				entityResourceID(parsedIntent.Object)+"放入"+entityResourceID(parsedIntent.Destination)+func() string {
				if parsedIntent.ReturnToStart {
					return "，并返回起点"
				}
				return ""
			}())
			continue
		}
		if parsedIntent.Action == manipulation.ActionHomeRoute {
			parts = append(parts, "巡检家庭路线："+strings.Join(parsedIntent.RouteRooms, " → ")+func() string {
				if parsedIntent.ReturnToStart {
					return "，并回到起点"
				}
				return ""
			}())
			continue
		}
		robot := "机器人"
		if parsedIntent.RobotID != "" {
			robot = strings.TrimPrefix(parsedIntent.RobotID, "robot-") + "号机器人"
		}
		destination := "指定位置"
		switch parsedIntent.Destination.Category {
		case manipulation.CategoryHandoffZone:
			destination = "交接区"
		case manipulation.CategoryTargetZone:
			destination = "目标区"
			if parsedIntent.Destination.Relation == "right_side" {
				destination = "右侧目标区"
			}
			if parsedIntent.Destination.Attributes["color"] == "blue" {
				destination = "右侧蓝色垫子"
			}
		case manipulation.CategoryStorageBin:
			destination = "收纳盒"
		case manipulation.CategoryDeliveryTray:
			destination = "交付托盘"
		}
		parts = append(parts, robot+"把"+entityResourceID(parsedIntent.Object)+"放到"+destination)
	}
	return strings.Join(parts, "，然后")
}

func (s *Service) PublishTelemetry(_ context.Context, snapshot telemetry.Snapshot) {
	s.telemetry.Publish(snapshot)
}

// worldFor is the runtime state a plan is formed against.
//
// It reads the latest telemetry for the task's own adapter and tolerates its
// absence: a task created before the robot has reported anything is planned
// against an unknown world, and the planner then says so rather than inventing a
// room. Refusing to create the task instead would make an unplugged robot a reason
// nobody can type a request.
func (s *Service) worldFor(adapter string) orchestration.World {
	snapshot, ok := s.telemetry.Latest(adapter)
	if !ok {
		return orchestration.World{}
	}
	return orchestration.WorldFrom(snapshot.RobotState)
}

func (s *Service) TelemetryLatest(adapter string) (telemetry.Snapshot, bool) {
	return s.telemetry.Latest(adapter)
}

// TelemetryUnfiled reports observations refused for naming no adapter. See
// TelemetryHub.Unfiled.
func (s *Service) TelemetryUnfiled() (uint64, time.Time) {
	return s.telemetry.Unfiled()
}

func (s *Service) TelemetryHistory(adapter string, limit int) []telemetry.Snapshot {
	return s.telemetry.History(adapter, limit)
}

func (s *Service) SceneFrame(adapter string) (SceneFrame, bool) {
	return s.telemetry.LatestFrame(adapter)
}

func (s *Service) SceneFrameIssue(adapter string) (SceneFrameIssue, bool) {
	return s.telemetry.LatestFrameIssue(adapter)
}

func (s *Service) DepthFrame(adapter string) (SceneFrame, bool) {
	return s.telemetry.LatestDepthFrame(adapter)
}

func (s *Service) DepthFrameIssue(adapter string) (SceneFrameIssue, bool) {
	return s.telemetry.LatestDepthFrameIssue(adapter)
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

// Approve records consent for a task and who gave it.
//
// The actor is required. An approval that cannot name its surface is the same
// statement as no approval at all, and the whole point of this call is that a
// physical action may proceed because something answerable said so.
func (s *Service) Approve(ctx context.Context, taskID, actor string) (*Task, error) {
	if strings.TrimSpace(actor) == "" {
		return nil, errors.New("approval requires the surface giving it")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	task.Approved = true
	task.ApprovedBy = strings.TrimSpace(actor)
	task.ApprovedAt = s.now().UTC()
	task.UpdatedAt = task.ApprovedAt
	s.appendEvent(task, "TASK_APPROVED", "", "批准来源："+task.ApprovedBy)
	return task, s.store.Update(ctx, task)
}

func (s *Service) Transition(ctx context.Context, taskID string, target taskgraph.TaskState, reason string) error {
	s.mu.Lock()
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		s.mu.Unlock()
		return err
	}
	if target != taskgraph.StateSafetyStopped && !taskgraph.CanTransition(task.State, target) {
		s.mu.Unlock()
		return fmt.Errorf("invalid task transition %s -> %s", task.State, target)
	}
	if terminal(task.State) {
		s.mu.Unlock()
		return errors.New("terminal task cannot transition")
	}
	previous := task.State
	task.State = target
	task.UpdatedAt = s.now().UTC()
	event := s.appendEvent(task, "STATE_CHANGED", "", reason)
	err = s.store.Update(ctx, task)
	s.mu.Unlock()
	if err == nil {
		// The previous state is carried on the event so an observer can report
		// both sides of the change. It is not reconstructable afterwards: by the
		// time anyone reads the task, the old state is gone.
		event.Payload = map[string]any{"previousState": string(previous), "state": string(target)}
		s.notifyEvent(ctx, taskID, event)
	}
	return err
}

func (s *Service) AppendEvent(ctx context.Context, taskID string, event TaskEvent) (*Task, error) {
	s.mu.Lock()
	task, err := s.store.Get(ctx, taskID)
	if err != nil {
		s.mu.Unlock()
		return nil, err
	}
	event.Sequence = uint64(len(task.Events) + 1)
	if event.OccurredAt.IsZero() {
		event.OccurredAt = s.now().UTC()
	}
	task.Events = append(task.Events, event)
	task.UpdatedAt = event.OccurredAt
	err = s.store.Update(ctx, task)
	s.mu.Unlock()
	if err != nil {
		return nil, err
	}
	// An event that reports a state change is announced even when it arrives as
	// an appended event rather than through Transition, so an observer never
	// sees a lifecycle change it was not told about.
	if event.Type == "STATE_CHANGED" {
		s.notifyEvent(ctx, taskID, event)
	}
	return task, nil
}

// appendEvent records one event on the task and returns it, so a caller that
// needs the assigned sequence number or the resolved timestamp does not have to
// guess them. Callers must hold the service lock.
func (s *Service) appendEvent(task *Task, eventType, stepID, message string) TaskEvent {
	event := TaskEvent{
		Sequence: uint64(len(task.Events) + 1), Type: eventType, StepID: stepID,
		Message: message, OccurredAt: s.now().UTC(),
	}
	task.Events = append(task.Events, event)
	return event
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
