package tasks_test

import (
	"context"
	"fmt"
	"sync"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestConcurrentToolReceiptsPreserveEveryAuditEvent(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	const count = 64
	start := make(chan struct{})
	errors := make(chan error, count)
	var group sync.WaitGroup
	for index := 0; index < count; index++ {
		group.Add(1)
		go func(index int) {
			defer group.Done()
			<-start
			_, err := service.AppendEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "TOOL_RECEIPT", Message: fmt.Sprint(index)})
			errors <- err
		}(index)
	}
	close(start)
	group.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}
	stored, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(stored.Events) != count+1 {
		t.Fatalf("lost receipts: got %d", len(stored.Events))
	}
	seen := map[string]bool{}
	for index, event := range stored.Events {
		if event.Sequence != uint64(index+1) {
			t.Fatalf("sequence=%d index=%d", event.Sequence, index)
		}
		if event.Type == "TOOL_RECEIPT" {
			seen[event.Message] = true
		}
	}
	if len(seen) != count {
		t.Fatalf("unique receipts=%d", len(seen))
	}
}
