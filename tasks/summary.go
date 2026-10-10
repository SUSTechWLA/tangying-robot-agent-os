package tasks

import (
	"context"
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

// Summary is a current index row, not execution evidence or a replacement for
// Get. SourceSequence excludes observer reports so diagnostics do not create
// their own new recovery episodes. EventSequence still exposes every append.
type Summary struct {
	ID               string              `json:"id"`
	State            taskgraph.TaskState `json:"state"`
	CurrentRevision  uint64              `json:"currentRevision"`
	AggregateVersion uint64              `json:"aggregateVersion"`
	EventSequence    uint64              `json:"eventSequence"`
	SourceSequence   uint64              `json:"sourceSequence"`
	UpdatedAt        time.Time           `json:"updatedAt"`
}

// SummaryReader avoids loading plans, model inputs and event payloads when a
// caller needs only the current task index. Existing repositories can fall back.
type SummaryReader interface {
	ListSummaries(context.Context) ([]Summary, error)
	GetSummary(context.Context, string) (Summary, error)
}

func (s *Service) ListSummaries(ctx context.Context) ([]Summary, error) {
	if reader, ok := s.store.(SummaryReader); ok {
		return reader.ListSummaries(ctx)
	}
	all, err := s.store.List(ctx)
	if err != nil {
		return nil, err
	}
	rows := make([]Summary, 0, len(all))
	for _, task := range all {
		if task != nil {
			rows = append(rows, summarize(task))
		}
	}
	return rows, nil
}

func (s *Service) GetSummary(ctx context.Context, id string) (Summary, error) {
	if reader, ok := s.store.(SummaryReader); ok {
		return reader.GetSummary(ctx, id)
	}
	task, err := s.store.Get(ctx, id)
	if err != nil {
		return Summary{}, err
	}
	return summarize(task), nil
}

func summarize(task *Task) Summary {
	row := Summary{ID: task.ID, State: task.State, CurrentRevision: task.CurrentRevision,
		AggregateVersion: task.AggregateVersion, UpdatedAt: task.UpdatedAt}
	for _, event := range task.Events {
		row.EventSequence = max(row.EventSequence, event.Sequence)
		if !strings.HasPrefix(event.Type, "ops.") && !strings.HasPrefix(event.Type, "agent.") {
			row.SourceSequence = max(row.SourceSequence, event.Sequence)
		}
	}
	return row
}

func (s *MemoryStore) ListSummaries(_ context.Context) ([]Summary, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rows := make([]Summary, 0, len(s.tasks))
	for _, task := range s.tasks {
		rows = append(rows, summarize(task))
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].ID < rows[j].ID })
	return rows, nil
}

func (s *MemoryStore) GetSummary(_ context.Context, id string) (Summary, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	task := s.tasks[id]
	if task == nil {
		return Summary{}, ErrTaskNotFound
	}
	return summarize(task), nil
}
