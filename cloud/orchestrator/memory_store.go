package orchestrator

import (
	"context"
	"sync"
)

type MemoryStore struct {
	mu    sync.RWMutex
	tasks map[string]*Task
}

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
	clone.Intent.Object.Attributes = cloneStrings(task.Intent.Object.Attributes)
	return &clone
}

func cloneStrings(input map[string]string) map[string]string {
	output := make(map[string]string, len(input))
	for key, value := range input {
		output[key] = value
	}
	return output
}
