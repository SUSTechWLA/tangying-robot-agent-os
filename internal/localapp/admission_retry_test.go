package localapp

import (
	"context"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
)

func TestHTTPApprovalQueueFullRetryPreservesConsentAndRunsOnce(t *testing.T) {
	service, runner, robot := newTestRuntime(t)
	queue := memory.NewQueue[string](1)
	if err := queue.Enqueue(context.Background(), "capacity-blocker"); err != nil {
		t.Fatal(err)
	}
	app := New(service, runner, queue)
	handler := console.NewServer(service, app, console.WithSessionToken("admission-fixture")).Handler()
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	approve := func() int {
		req := httptest.NewRequest("POST", "/v1/tasks/"+task.ID+"/approve", strings.NewReader("{}"))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set(console.SessionHeaderName, "admission-fixture")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		return w.Code
	}
	if status := approve(); status != 409 {
		t.Fatalf("queue full status=%d", status)
	}
	first, _ := service.Get(context.Background(), task.ID)
	if !first.Approved || first.State != taskgraph.StateReady || robot.calls() != 0 {
		t.Fatalf("approval/admission conflated: %+v calls=%d", first, robot.calls())
	}
	if _, err := queue.Dequeue(context.Background()); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if status := approve(); status != 200 {
				t.Errorf("retry status=%d", status)
			}
		}()
	}
	wg.Wait()
	retried, _ := service.Get(context.Background(), task.ID)
	approvals := 0
	for _, event := range retried.Events {
		if event.Type == "TASK_APPROVED" {
			approvals++
		}
	}
	if approvals != 1 || !retried.ApprovedAt.Equal(first.ApprovedAt) || retried.ApprovedBy != first.ApprovedBy {
		t.Fatalf("consent overwritten: %+v", retried)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	app.Start(ctx)
	waitForState(t, service, task.ID, taskgraph.StateSucceeded)
	// The final event is appended after the terminal transition. Wait for that
	// committed tail before checking that a rejected approval adds no events.
	until := time.Now().Add(time.Second)
	for {
		current, _ := service.Get(context.Background(), task.ID)
		complete := false
		for _, event := range current.Events {
			complete = complete || event.Type == "LOCAL_RUN_FINISHED"
		}
		if complete {
			break
		}
		if time.Now().After(until) {
			t.Fatal("final execution audit did not commit")
		}
		time.Sleep(time.Millisecond)
	}
	if got := robot.calls(); got != 7 {
		t.Fatalf("duplicate physical task: calls=%d want7", got)
	}
	before, _ := service.Get(context.Background(), task.ID)
	if status := approve(); status != 400 {
		t.Fatalf("terminal approval=%d", status)
	}
	after, _ := service.Get(context.Background(), task.ID)
	if len(after.Events) != len(before.Events) || !after.ApprovedAt.Equal(first.ApprovedAt) {
		t.Fatal("terminal task audit changed")
	}
	cancel()
	deadline, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	if err := app.Wait(deadline); err != nil {
		t.Fatal(err)
	}
}
