package capabilityagent

import (
	"context"
	"errors"
	"testing"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type interruptedReadProvider struct {
	*fakeProvider
	calls      int
	failure    error
	persistent bool
	ids        []string
}

func (p *interruptedReadProvider) CallService(ctx context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls++
	p.ids = append(p.ids, r.RequestId)
	if p.persistent || p.calls == 1 {
		return nil, p.failure
	}
	return p.fakeProvider.CallService(ctx, r)
}

func TestReadRecoveryRequiresDiagnosisAndRetainsDurableBudget(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	p := &interruptedReadProvider{fakeProvider: f, failure: status.Error(codes.Unavailable, "injected transport loss")}
	e.Provider = p
	diagnostics := 0
	e.RecoverRead = func(ctx context.Context, taskID, stepID, tool string, cause error) error {
		diagnostics++
		if taskID != task.ID || tool != "calibration.get" {
			t.Fatal("wrong diagnostic binding")
		}
		current, _ := e.Tasks.Get(ctx, taskID)
		found := false
		for _, event := range current.Events {
			if event.Type == "CAPABILITY_FAILED" && event.StepID == stepID {
				found = true
			}
		}
		if !found {
			t.Fatal("diagnosis started before durable failure")
		}
		return nil
	}
	ctx := context.Background()
	identity := task.ID + "/rev-1-cap-01"
	result, err := e.callRecoverableRead(ctx, task.ID, "rev-1-cap-01", "heterogeneous-unit", "calibration.get", identity, map[string]any{}, false)
	if err != nil || result["revision"] != "cal-1" || p.calls != 2 || diagnostics != 1 || p.ids[0] != p.ids[1] {
		t.Fatalf("result=%v err=%v calls=%d diagnostics=%d ids=%v", result, err, p.calls, diagnostics, p.ids)
	}
	// A recreated executor cannot gain a fresh recovery allowance.
	p.persistent = true
	copy := *e
	_, err = copy.callRecoverableRead(ctx, task.ID, "rev-1-cap-01", "heterogeneous-unit", "calibration.get", identity, map[string]any{}, false)
	if err == nil || diagnostics != 1 || p.calls != 2 {
		t.Fatal("durable recovery budget reset")
	}
}

func TestReadRecoveryNeverRetriesWritesOrNonTransportErrors(t *testing.T) {
	for _, tc := range []struct {
		name    string
		mutates bool
		failure error
	}{
		{"write", true, status.Error(codes.Unavailable, "lost write reply")},
		{"permission", false, status.Error(codes.PermissionDenied, "denied")},
		{"unclassified", false, errors.New("unknown failure")},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := &fakeProvider{}
			e, task := setupGoal(t, f)
			p := &interruptedReadProvider{fakeProvider: f, failure: tc.failure}
			e.Provider = p
			e.RecoverRead = func(context.Context, string, string, string, error) error {
				t.Fatal("unsafe recovery requested")
				return nil
			}
			_, err := e.callRecoverableRead(context.Background(), task.ID, "rev-1-cap-01", "heterogeneous-unit", "calibration.get", task.ID+"/rev-1-cap-01", nil, tc.mutates)
			if err == nil || p.calls != 1 {
				t.Fatal("failure was hidden or replayed")
			}
		})
	}
}

func TestReadRecoveryEscalationDoesNotDispatchAgain(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	p := &interruptedReadProvider{fakeProvider: f, failure: status.Error(codes.Unavailable, "lost read")}
	e.Provider = p
	e.RecoverRead = func(context.Context, string, string, string, error) error {
		return errors.New("reconciliation unavailable")
	}
	_, err := e.callRecoverableRead(context.Background(), task.ID, "rev-1-cap-01", "heterogeneous-unit", "calibration.get", task.ID+"/rev-1-cap-01", nil, false)
	if err == nil || p.calls != 1 {
		t.Fatal("escalation replayed the read")
	}
}
