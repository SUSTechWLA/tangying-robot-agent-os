package tasks

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
)

func TestObjectiveIdempotencyPreservesApprovalAndRejectsChangedEnvelope(t *testing.T) {
	c := objective.Contract{SchemaVersion: objective.Version, RobotID: "edge", Goal: "读取地图状态", CatalogRevision: strings.Repeat("a", 64), Grants: []objective.Grant{{Tool: "mapping.status", Arguments: map[string]any{"type": "object", "additionalProperties": false}}}, Effects: []string{"READ"}, Resources: []string{"robot"}, Completion: []objective.Check{{Tool: "mapping.status", Arguments: map[string]any{}, Path: []string{"ready"}, Equals: true}}, Budget: objective.Budget{MaxPlans: 1, MaxSteps: 2, MaxReads: 4, TimeoutSeconds: 60}, ExpiresAt: time.Now().Add(time.Hour)}
	repository := NewMemoryStore()
	service := NewService(repository, intent.NewDeterministicParser())
	ctx := context.Background()
	first, err := service.CreateObjectiveOnce(ctx, c, "ros2", "request-1", "cloud-user")
	if err != nil {
		t.Fatal(err)
	}
	_, err = service.Approve(ctx, first.ID, "operator")
	if err != nil {
		t.Fatal(err)
	}
	restarted := NewService(repository, intent.NewDeterministicParser())
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			same, err := restarted.CreateObjectiveOnce(ctx, c, "ros2", "request-1", "cloud-user")
			if err != nil || same.ID != first.ID || !same.Approved {
				t.Errorf("retry reset task: %v", err)
			}
		}()
	}
	wg.Wait()
	all, _ := restarted.List(ctx)
	if len(all) != 1 {
		t.Fatal("retry created duplicate objectives")
	}
	c.Goal = "different goal"
	if _, err = restarted.CreateObjectiveOnce(ctx, c, "ros2", "request-1", "cloud-user"); !errors.Is(err, ErrIdempotencyConflict) {
		t.Fatalf("changed envelope accepted: %v", err)
	}
}
