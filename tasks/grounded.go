package tasks

import (
	"context"
	"encoding/json"
	"sort"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

// Rebuild the model projection from the existing durable task event store so
// process restart cannot silently restore raw telemetry as model authority.
func (s *Service) groundedWorld(adapter string) orchestration.World {
	world := orchestration.World{GroundedOnly: true}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	tasks, err := s.store.List(ctx)
	if err != nil {
		return world
	}
	var events []TaskEvent
	for _, task := range tasks {
		if task.Adapter != adapter {
			continue
		}
		for _, event := range task.Events {
			if event.Type != "STATE_REPORT" {
				continue
			}
			raw, ok := event.Payload["state_report_json"].(string)
			if !ok {
				continue
			}
			var header closedloop.GroundedReport
			if json.Unmarshal([]byte(raw), &header) != nil {
				continue
			}
			if _, err := closedloop.ParseGroundedReport(raw, header.ActionID, header.ActionName, task.ID); err == nil {
				events = append(events, event)
			}
		}
	}
	sort.SliceStable(events, func(i, j int) bool { return events[i].OccurredAt.Before(events[j].OccurredAt) })
	if len(events) > 3 {
		events = events[len(events)-3:]
	}
	for _, event := range events {
		world.StateReports = append(world.StateReports, event.Payload["state_report_json"].(string))
	}
	return world
}
