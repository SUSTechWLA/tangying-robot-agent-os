package tasks

import (
	"context"
	"errors"
	"sort"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type MemoryStore struct {
	mu             sync.RWMutex
	tasks          map[string]*Task
	revisions      map[string]map[uint64]*TaskRevision
	revisionEvents map[string]map[uint64][]RevisionLifecycleEvent
}

var _ Repository = (*MemoryStore)(nil)

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		tasks:          map[string]*Task{},
		revisions:      map[string]map[uint64]*TaskRevision{},
		revisionEvents: map[string]map[uint64][]RevisionLifecycleEvent{},
	}
}

func (s *MemoryStore) Create(_ context.Context, task *Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tasks[task.ID] = NormalizeLegacyTask(cloneTask(task))
	return nil
}

func (s *MemoryStore) CreateWithRevision(_ context.Context, task *Task, revision *TaskRevision) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if task == nil || revision == nil || task.ID == "" || revision.TaskID != task.ID || revision.Revision == 0 {
		return errors.New("task and initial revision identity are required")
	}
	if s.tasks[task.ID] != nil {
		return ErrRevisionConflict
	}
	storedTask := NormalizeLegacyTask(cloneTask(task))
	s.tasks[task.ID] = storedTask
	s.revisions[task.ID] = map[uint64]*TaskRevision{revision.Revision: cloneRevision(revision)}
	status := storedTask.RevisionState
	if status == "" {
		status = RevisionActive
	}
	s.revisionEvents[task.ID] = map[uint64][]RevisionLifecycleEvent{
		revision.Revision: {{Sequence: 1, Revision: revision.Revision, Status: status, OccurredAt: revision.CreatedAt}},
	}
	return nil
}

func (s *MemoryStore) Get(_ context.Context, id string) (*Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	task := s.tasks[id]
	if task == nil {
		return nil, ErrTaskNotFound
	}
	return NormalizeLegacyTask(cloneTask(task)), nil
}

func (s *MemoryStore) Update(_ context.Context, task *Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.tasks[task.ID] == nil {
		return ErrTaskNotFound
	}
	s.tasks[task.ID] = NormalizeLegacyTask(cloneTask(task))
	return nil
}

func (s *MemoryStore) List(_ context.Context) ([]*Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	tasks := make([]*Task, 0, len(s.tasks))
	for _, task := range s.tasks {
		tasks = append(tasks, NormalizeLegacyTask(cloneTask(task)))
	}
	return tasks, nil
}

func (s *MemoryStore) CommitRevision(_ context.Context, commit RevisionCommit) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	current := s.tasks[commit.TaskID]
	if current == nil {
		return ErrTaskNotFound
	}
	if commit.NewRevision != nil {
		for _, existing := range s.revisions[commit.TaskID] {
			if existing.IdempotencyKey != commit.NewRevision.IdempotencyKey {
				continue
			}
			if RevisionContentEqual(existing, commit.NewRevision) {
				return nil
			}
			return ErrIdempotencyConflict
		}
	}
	if current.AggregateVersion != commit.ExpectedAggregateVersion {
		return ErrRevisionConflict
	}
	if commit.Task == nil || commit.Task.ID != commit.TaskID ||
		commit.Task.AggregateVersion != commit.ExpectedAggregateVersion+1 {
		return ErrRevisionConflict
	}
	if commit.NewRevision != nil {
		if commit.NewRevision.TaskID != commit.TaskID || commit.NewRevision.Revision == 0 {
			return ErrRevisionConflict
		}
		if s.revisions[commit.TaskID] == nil {
			s.revisions[commit.TaskID] = map[uint64]*TaskRevision{}
		}
		if existing := s.revisions[commit.TaskID][commit.NewRevision.Revision]; existing != nil {
			return ErrRevisionConflict
		}
		s.revisions[commit.TaskID][commit.NewRevision.Revision] = cloneRevision(commit.NewRevision)
	}
	eventsToAppend := commit.LifecycleEvents
	if len(eventsToAppend) == 0 && commit.LifecycleEvent.Revision != 0 {
		eventsToAppend = []RevisionLifecycleEvent{commit.LifecycleEvent}
	}
	if len(eventsToAppend) == 0 {
		return ErrRevisionConflict
	}
	if s.revisionEvents[commit.TaskID] == nil {
		s.revisionEvents[commit.TaskID] = map[uint64][]RevisionLifecycleEvent{}
	}
	for _, candidate := range eventsToAppend {
		event := cloneRevisionEvent(candidate)
		if event.Revision == 0 || s.revisions[commit.TaskID][event.Revision] == nil {
			return ErrRevisionConflict
		}
		events := s.revisionEvents[commit.TaskID][event.Revision]
		if event.Sequence == 0 {
			event.Sequence = uint64(len(events) + 1)
		}
		s.revisionEvents[commit.TaskID][event.Revision] = append(events, event)
	}
	storedTask := NormalizeLegacyTask(cloneTask(commit.Task))
	if commit.TaskEvent != nil {
		taskEvent := *commit.TaskEvent
		taskEvent.Payload = cloneAnyMap(commit.TaskEvent.Payload)
		storedTask.Events = append(storedTask.Events, taskEvent)
	}
	s.tasks[commit.TaskID] = storedTask
	return nil
}

func (s *MemoryStore) Revision(_ context.Context, taskID string, revision uint64) (RevisionRecord, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.revisionLocked(taskID, revision)
}

func (s *MemoryStore) ListRevisions(_ context.Context, taskID string) ([]RevisionRecord, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.tasks[taskID] == nil {
		return nil, ErrTaskNotFound
	}
	keys := make([]uint64, 0, len(s.revisions[taskID]))
	for revision := range s.revisions[taskID] {
		keys = append(keys, revision)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	records := make([]RevisionRecord, 0, len(keys))
	for _, revision := range keys {
		record, err := s.revisionLocked(taskID, revision)
		if err != nil {
			return nil, err
		}
		records = append(records, record)
	}
	return records, nil
}

func (s *MemoryStore) revisionLocked(taskID string, revision uint64) (RevisionRecord, error) {
	stored := s.revisions[taskID][revision]
	if stored == nil {
		return RevisionRecord{}, ErrRevisionNotFound
	}
	events := cloneRevisionEvents(s.revisionEvents[taskID][revision])
	status := RevisionStatus("")
	if len(events) > 0 {
		status = events[len(events)-1].Status
	}
	return RevisionRecord{Revision: *cloneRevision(stored), Status: status, Events: events}, nil
}

func cloneTask(task *Task) *Task {
	clone := *task
	clone.Events = append([]TaskEvent(nil), task.Events...)
	clone.Intent = cloneIntent(task.Intent)
	if task.Plan != nil {
		plan := cloneBundle(*task.Plan)
		clone.Plan = &plan
	}
	return &clone
}

func cloneRevision(revision *TaskRevision) *TaskRevision {
	if revision == nil {
		return nil
	}
	clone := *revision
	clone.Intent = cloneIntent(revision.Intent)
	if revision.Plan != nil {
		plan := cloneBundle(*revision.Plan)
		clone.Plan = &plan
	}
	clone.ChangeSet.Retained = append([]string(nil), revision.ChangeSet.Retained...)
	clone.ChangeSet.Changed = append([]string(nil), revision.ChangeSet.Changed...)
	clone.ChangeSet.Added = append([]string(nil), revision.ChangeSet.Added...)
	clone.ChangeSet.Paused = append([]string(nil), revision.ChangeSet.Paused...)
	clone.Steps = append([]RevisionStep(nil), revision.Steps...)
	for index := range clone.Steps {
		clone.Steps[index].HarnessEvidenceIDs = append([]string(nil), revision.Steps[index].HarnessEvidenceIDs...)
	}
	return &clone
}

func cloneRevisionEvent(event RevisionLifecycleEvent) RevisionLifecycleEvent {
	event.Payload = cloneAnyMap(event.Payload)
	return event
}

func cloneRevisionEvents(events []RevisionLifecycleEvent) []RevisionLifecycleEvent {
	clone := make([]RevisionLifecycleEvent, len(events))
	for index, event := range events {
		clone[index] = cloneRevisionEvent(event)
	}
	return clone
}

func cloneBundle(bundle orchestration.Bundle) orchestration.Bundle {
	bundle.Plans = append([]taskgraph.TaskPlan(nil), bundle.Plans...)
	for planIndex := range bundle.Plans {
		bundle.Plans[planIndex].Steps = append([]taskgraph.SkillStep(nil), bundle.Plans[planIndex].Steps...)
		for stepIndex := range bundle.Plans[planIndex].Steps {
			step := &bundle.Plans[planIndex].Steps[stepIndex]
			step.DependsOn = append([]string(nil), step.DependsOn...)
			step.Arguments = cloneAnyMap(step.Arguments)
		}
	}
	bundle.Rejections = append([]string(nil), bundle.Rejections...)
	return bundle
}

func cloneAnyMap(input map[string]any) map[string]any {
	if input == nil {
		return nil
	}
	output := make(map[string]any, len(input))
	for key, value := range input {
		switch typed := value.(type) {
		case map[string]any:
			output[key] = cloneAnyMap(typed)
		default:
			output[key] = typed
		}
	}
	return output
}

func cloneIntent(intent manipulation.Intent) manipulation.Intent {
	intent.Object.Attributes = cloneStrings(intent.Object.Attributes)
	intent.Destination.Attributes = cloneStrings(intent.Destination.Attributes)
	if len(intent.Sequence) > 0 {
		intent.Sequence = append([]manipulation.Intent(nil), intent.Sequence...)
		for index := range intent.Sequence {
			intent.Sequence[index] = cloneIntent(intent.Sequence[index])
		}
	}
	return intent
}

func cloneStrings(input map[string]string) map[string]string {
	output := make(map[string]string, len(input))
	for key, value := range input {
		output[key] = value
	}
	return output
}
