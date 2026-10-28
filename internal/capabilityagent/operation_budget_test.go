package capabilityagent

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

type exhaustedOperationProvider struct {
	*fakeProvider
	t             *testing.T
	stopFailure   string
	ordinaryPolls int
	stopRequests  int
}

func (p *exhaustedOperationProvider) CallService(ctx context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if scope, stopping := capability.OwnedStopFromContext(ctx); stopping {
		if !scope.Allows(ctx, request.RobotId, request.Name, request.RequestId, request.OperationId, request.OperationOwnerId, request.OperationLeaseMs, len(request.GetParameters().GetFields())) {
			p.t.Fatal("budget recovery exceeded the exact owned stop scope")
		}
		p.stopRequests++
		if request.Name == p.stopFailure {
			return nil, errors.New("injected owned stop service unavailable")
		}
		if p.stopFailure == "foreign-operation" && request.Name == "mapping.status" {
			return &robotv1.ServiceResponse{Ok: true, Result: fields(map[string]any{"sessionId": "foreign-session", "state": "exploring"})}, nil
		}
		return p.fakeProvider.CallService(ctx, request)
	}
	if request.Name == "mapping.status" {
		p.ordinaryPolls++
		return nil, fmt.Errorf("%w: objective read budget exhausted", ErrNeedsInput)
	}
	return p.fakeProvider.CallService(ctx, request)
}

func TestOperationReadBudgetExhaustionStopsWithoutFurtherPolling(t *testing.T) {
	for _, failure := range []string{"", "mapping.status", "mapping.cancel", "foreign-operation"} {
		name := failure
		if name == "" {
			name = "confirmed"
		}
		t.Run(name, func(t *testing.T) {
			robot := &fakeProvider{keepRunning: true}
			executor, task := setupGoal(t, robot)
			provider := &exhaustedOperationProvider{fakeProvider: robot, t: t, stopFailure: failure}
			executor.Provider = provider
			executor.StartIndex, executor.EndIndex = 1, 2
			executor.OperationTimeout = time.Hour
			// The context is a watchdog for the old polling behavior, not the
			// mechanism under test. A permanent budget refusal must finish before
			// this expires and must not issue another ordinary status request.
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			_, err := executor.Run(ctx, task, nil, nil)
			if ctx.Err() != nil || provider.ordinaryPolls != 1 || robot.starts != 1 {
				t.Fatalf("budget refusal was polled until timeout: polls=%d starts=%d context=%v error=%v", provider.ordinaryPolls, robot.starts, ctx.Err(), err)
			}
			if failure == "" {
				if !errors.Is(err, ErrNeedsInput) || errors.Is(err, ErrOutcomeUnknown) || robot.cancels != 1 || provider.stopRequests != 3 {
					t.Fatalf("owned stop lost budget cause: cancels=%d scoped_requests=%d error=%v", robot.cancels, provider.stopRequests, err)
				}
			} else if !errors.Is(err, ErrOutcomeUnknown) {
				t.Fatalf("unconfirmed stop lost unknown classification: %v", err)
			}
			if failure != "" && robot.cancels != 0 {
				t.Fatalf("failed ownership/cancel request reached an unauthorized stop: cancels=%d", robot.cancels)
			}
			final, getErr := executor.Tasks.Get(context.Background(), task.ID)
			if getErr != nil {
				t.Fatal(getErr)
			}
			confirmed := false
			for _, event := range final.Events {
				confirmed = confirmed || event.Type == "CAPABILITY_STOP_CONFIRMED"
			}
			if confirmed != (failure == "") {
				t.Fatalf("stop evidence differs from observation: confirmed=%v failure=%q", confirmed, failure)
			}
			t.Logf("budget_refusals=%d owned_stop_requests=%d confirmed=%v error=%v", provider.ordinaryPolls, provider.stopRequests, confirmed, err)
		})
	}
}
