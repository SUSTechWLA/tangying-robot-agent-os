package queue

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
)

func TestUnregisteredRobotCannotSilentlyLoseTask(t *testing.T) {
	router := NewRouter(time.Second)
	if err := router.Enqueue(context.Background(), "task-1", []string{"unregistered-robot"}); err == nil {
		t.Fatal("task to an unregistered robot was silently dropped")
	}
}

// Logical routing at a thousand registered identities is cheap to exercise
// without claiming a production Redis/MySQL throughput number.
func TestThousandRobotRosterKeepsTasksIsolated(t *testing.T) {
	router := NewRouter(10 * time.Millisecond)
	defer router.Close()
	for i := 0; i < 1000; i++ {
		router.Add(fmt.Sprintf("robot-%04d", i), memory.NewQueue[string](2))
	}
	for i := 0; i < 1000; i++ {
		robotID := fmt.Sprintf("robot-%04d", i)
		if err := router.Enqueue(context.Background(), fmt.Sprintf("task-%04d", i), []string{robotID}); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < 1000; i++ {
		robotID := fmt.Sprintf("robot-%04d", i)
		got, err := router.Dequeue(context.Background(), robotID)
		if err != nil || got != fmt.Sprintf("task-%04d", i) {
			t.Fatalf("robot %s received %q: %v", robotID, got, err)
		}
	}
}
