package tasks

import (
	"context"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type MemoryStore struct {
	mu    sync.RWMutex
	tasks map[string]*Task
}

var _ Repository = (*MemoryStore)(nil)

func NewMemoryStore() *MemoryStore { return &MemoryStore{tasks: map[string]*Task{}} }

func (s *MemoryStore) Create(_ context.Context, task *Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tasks[task.ID] = cloneTask(task)
	return nil
}

func (s *MemoryStore) Get(_ context.Context, id string) (*Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	task := s.tasks[id]
	if task == nil {
		return nil, ErrTaskNotFound
	}
	return cloneTask(task), nil
}

func (s *MemoryStore) Update(_ context.Context, task *Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.tasks[task.ID] == nil {
		return ErrTaskNotFound
	}
	s.tasks[task.ID] = cloneTask(task)
	return nil
}

func (s *MemoryStore) List(_ context.Context) ([]*Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	tasks := make([]*Task, 0, len(s.tasks))
	for _, task := range s.tasks {
		tasks = append(tasks, cloneTask(task))
	}
	return tasks, nil
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
