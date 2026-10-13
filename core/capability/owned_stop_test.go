package capability

import (
	"context"
	"testing"
	"time"
)

func TestOwnedStopScopeOnlyPermitsExactZeroLeaseRequests(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	ctx, err := WithOwnedStop(ctx, OwnedStop{RobotID: "robot-a", OperationID: "op-1", OwnerID: "task-1/call-1", StatusService: "mapping.status", CancelService: "mapping.cancel"})
	if err != nil {
		t.Fatal(err)
	}
	scope, ok := OwnedStopFromContext(ctx)
	if !ok {
		t.Fatal("missing scope")
	}
	for _, service := range []string{"mapping.status", "mapping.cancel"} {
		id := ""
		if service == "mapping.cancel" {
			id = "task-1/call-1/cancel"
		}
		if !scope.Allows(ctx, "robot-a", service, id, "op-1", "task-1/call-1", 0, 0) {
			t.Fatal("valid scoped request rejected")
		}
	}
	for _, bad := range []struct {
		robot, service, id, operation, owner string
		lease                                uint32
		parameters                           int
	}{
		{"robot-b", "mapping.status", "", "op-1", "task-1/call-1", 0, 0},
		{"robot-a", "mapping.build", "", "op-1", "task-1/call-1", 0, 0},
		{"robot-a", "mapping.cancel", "wrong", "op-1", "task-1/call-1", 0, 0},
		{"robot-a", "mapping.status", "", "op-2", "task-1/call-1", 0, 0},
		{"robot-a", "mapping.status", "", "op-1", "another-owner", 0, 0},
		{"robot-a", "mapping.status", "", "op-1", "task-1/call-1", 30000, 0},
		{"robot-a", "mapping.status", "", "op-1", "task-1/call-1", 0, 1},
	} {
		if scope.Allows(ctx, bad.robot, bad.service, bad.id, bad.operation, bad.owner, bad.lease, bad.parameters) {
			t.Fatalf("invalid request allowed: %+v", bad)
		}
	}
	if scope.Allows(context.WithoutCancel(ctx), "robot-a", "mapping.status", "", "op-1", "task-1/call-1", 0, 0) {
		t.Fatal("removing deadline retained authority")
	}
	cancel()
	if scope.Allows(ctx, "robot-a", "mapping.status", "", "op-1", "task-1/call-1", 0, 0) {
		t.Fatal("cancelled scope retained authority")
	}
}

func TestOwnedStopScopeRequiresFreshBoundedContext(t *testing.T) {
	scope := OwnedStop{RobotID: "robot-a", OperationID: "op-1", OwnerID: "owner", StatusService: "status", CancelService: "cancel"}
	if _, err := WithOwnedStop(context.Background(), scope); err == nil {
		t.Fatal("unbounded stop authority")
	}
	long, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	if _, err := WithOwnedStop(long, scope); err == nil {
		t.Fatal("long stop authority")
	}
	short, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	scope.OperationID = ""
	if _, err := WithOwnedStop(short, scope); err == nil {
		t.Fatal("missing operation identity")
	}
}
