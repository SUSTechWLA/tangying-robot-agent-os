package capabilityagent

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

type stopScopeProvider struct {
	*fakeProvider
	t            *testing.T
	scoped       []*robotv1.ServiceRequest
	startsScoped bool
}

func (p *stopScopeProvider) CallService(ctx context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if scope, ok := capability.OwnedStopFromContext(ctx); ok {
		if !scope.Allows(ctx, r.RobotId, r.Name, r.RequestId, r.OperationId, r.OperationOwnerId, r.OperationLeaseMs, len(r.GetParameters().GetFields())) {
			p.t.Fatal("executor emitted invalid owned stop request")
		}
		p.scoped = append(p.scoped, r)
		if r.Name == "mapping.build" {
			p.startsScoped = true
		}
	}
	return p.fakeProvider.CallService(ctx, r)
}

func TestExecutorStopMintsExactScopeWithoutRenewingMotionLease(t *testing.T) {
	f := &fakeProvider{keepRunning: true}
	e, task := setupGoal(t, f)
	provider := &stopScopeProvider{fakeProvider: f, t: t}
	e.Provider = provider
	e.OperationTimeout = time.Millisecond
	_, err := e.Run(context.Background(), task, nil, nil)
	if err == nil || errors.Is(err, ErrOutcomeUnknown) || f.cancels != 1 {
		t.Fatalf("owned cancellation not confirmed: %v cancels=%d", err, f.cancels)
	}
	if provider.startsScoped || len(provider.scoped) != 3 {
		t.Fatalf("scoped calls=%d startsScoped=%v", len(provider.scoped), provider.startsScoped)
	}
	for _, r := range provider.scoped {
		if r.OperationLeaseMs != 0 || r.OperationId != "owned-session" || r.OperationOwnerId == "" {
			t.Fatal("stop request lost identity or extended motion lease")
		}
	}
}
