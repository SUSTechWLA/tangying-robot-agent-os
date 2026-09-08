package agent

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func TestDispatchAfterLongSafePauseKeepsBoundedLeaseAndCommandIdentity(t *testing.T) {
	now := time.Now()
	planned := runtime.Command{CommandID: "task/revision/2/step/pick", IdempotencyKey: "same-physical-action",
		ApprovalID: "approved", TaskRevision: 2, Deadline: now.Add(-10 * time.Minute), Lease: 15 * time.Second}
	dispatched := commandAtDispatch(context.Background(), planned, now)
	if dispatched.Deadline != now.Add(15*time.Second) {
		t.Fatalf("paused task did not receive its bounded dispatch budget: %v", dispatched.Deadline)
	}
	dispatched.Deadline = planned.Deadline
	if !reflect.DeepEqual(dispatched, planned) {
		t.Fatal("refreshing a dispatch budget changed the action identity, approval or lease")
	}
}
