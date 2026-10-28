package edgeagent

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
)

type stopRequestRecorder struct {
	*missionRobot
	requests []*robotv1.ServiceRequest
}

func (p *stopRequestRecorder) CallService(ctx context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.requests = append(p.requests, request)
	return p.missionRobot.CallService(ctx, request)
}

func TestScopedProviderReadBudgetAllowsOnlyExactOwnedStop(t *testing.T) {
	ctx := context.Background()
	robot := &stopRequestRecorder{missionRobot: &missionRobot{}}
	contract, _ := objectiveFixture(t, robot.missionRobot, 1)
	_, catalogue, err := capabilityagent.Catalogue(ctx, robot)
	if err != nil {
		t.Fatal(err)
	}
	reads, authorityChecks := 0, 0
	provider := scopedProvider{Provider: robot, contract: contract, catalog: catalogue,
		before: func(context.Context) error { authorityChecks++; return nil },
		read: func() error {
			reads++
			return fmt.Errorf("%w: objective read budget exhausted", capabilityagent.ErrNeedsInput)
		},
	}
	request := func(service, owner, operation string, lease uint32) *robotv1.ServiceRequest {
		id := ""
		if service == "navigation.cancel" {
			id = owner + "/cancel"
		}
		return &robotv1.ServiceRequest{RobotId: contract.RobotID, Name: service,
			RequestId: id, OperationOwnerId: owner, OperationId: operation,
			OperationLeaseMs: lease, Parameters: fields(map[string]any{})}
	}
	if _, err := provider.CallService(ctx, request("mission.status", "command-1", "operation-1", 30000)); !errors.Is(err, capabilityagent.ErrNeedsInput) {
		t.Fatalf("ordinary status escaped the durable read budget: %v", err)
	}
	boundedCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	stopCtx, err := capability.WithOwnedStop(boundedCtx, capability.OwnedStop{RobotID: contract.RobotID,
		OwnerID: "command-1", OperationID: "operation-1", StatusService: "mission.status", CancelService: "navigation.cancel"})
	if err != nil {
		t.Fatal(err)
	}
	for _, service := range []string{"mission.status", "navigation.cancel"} {
		if _, err := provider.CallService(stopCtx, request(service, "command-1", "operation-1", 0)); err != nil {
			t.Fatalf("exhausted budget prevented owned %s: %v", service, err)
		}
	}
	if reads != 1 || authorityChecks != 1 || len(robot.requests) != 2 {
		t.Fatalf("stop consumed ordinary authority/budget: reads=%d authority=%d requests=%d", reads, authorityChecks, len(robot.requests))
	}
	for _, invalid := range []*robotv1.ServiceRequest{
		request("mission.status", "other-command", "operation-1", 0),
		request("mission.status", "command-1", "other-operation", 0),
		request("mission.status", "command-1", "operation-1", 30000),
		request("navigation.cancel", "other-command", "operation-1", 0),
	} {
		if _, err := provider.CallService(stopCtx, invalid); err == nil {
			t.Fatalf("invalid stop request bypassed budget/authority: %v", invalid)
		}
	}
	if len(robot.requests) != 2 || len(robot.writes) != 0 {
		t.Fatalf("stop scope admitted a foreign or lease-renewing request: requests=%d writes=%v", len(robot.requests), robot.writes)
	}
}
