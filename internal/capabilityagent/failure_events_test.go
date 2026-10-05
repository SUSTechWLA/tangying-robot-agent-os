package capabilityagent

import (
	"context"
	"errors"
	"testing"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type transportFailureProvider struct {
	*fakeProvider
	tool string
}

func (p transportFailureProvider) CallService(ctx context.Context, req *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if req.Name == p.tool {
		return nil, status.Error(codes.Unavailable, "connection interrupted")
	}
	return p.fakeProvider.CallService(ctx, req)
}

func TestCapabilityFailuresPublishClassifiedActivityAndPreservePhysicalUncertainty(t *testing.T) {
	for _, tc := range []struct {
		name, tool, step, phase string
		physical, verification  bool
	}{
		{name: "read transport", tool: "calibration.get", step: "rev-1-cap-01", phase: "dispatch"},
		{name: "write transport", tool: "mapping.build", step: "rev-1-cap-02", phase: "dispatch", physical: true},
		{name: "completion evidence", tool: "mapping.build", step: "rev-1-cap-02", phase: "verification", physical: true, verification: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := &fakeProvider{badEvidence: tc.verification}
			e, task := setupGoal(t, f)
			if !tc.verification {
				e.Provider = transportFailureProvider{fakeProvider: f, tool: tc.tool}
			}
			_, err := e.Run(context.Background(), task, nil, nil)
			if err == nil {
				t.Fatal("failure disappeared")
			}
			if tc.physical && !tc.verification && !errors.Is(err, ErrOutcomeUnknown) {
				t.Fatalf("physical uncertainty lost: %v", err)
			}
			current, err := e.Tasks.Get(context.Background(), task.ID)
			if err != nil {
				t.Fatal(err)
			}
			failures, activities := 0, 0
			for _, event := range current.Events {
				if event.StepID != tc.step {
					continue
				}
				if event.Type == "CAPABILITY_FAILED" {
					failures++
					if event.Payload["tool"] != tc.tool || event.Payload["phase"] != tc.phase || event.Payload["mutatesWorld"] != tc.physical || event.Payload["outcomeUnknown"] != tc.physical {
						t.Fatalf("wrong failure evidence: %#v", event.Payload)
					}
				}
				if event.Type == "TOOL_ACTIVITY" && event.Payload["activityStatus"] == "FAILED" {
					activities++
					if event.Payload["code"] == "" || event.Payload["error"] == "" || event.Payload["commandId"] != task.ID+"/"+tc.step {
						t.Fatalf("incomplete activity: %#v", event.Payload)
					}
				}
			}
			if failures != 1 || activities != 1 {
				t.Fatalf("failures=%d activities=%d", failures, activities)
			}
			state, err := e.Store.StepStatus(context.Background(), task.ID, tc.step)
			if err != nil {
				t.Fatal(err)
			}
			want := middleware.StepFailed
			if tc.physical {
				want = middleware.StepStarted
			}
			if state != want {
				t.Fatalf("step state=%v want=%v", state, want)
			}
		})
	}
}

func TestRejectedCapabilityRetainsProviderCodeWithoutUnknownOutcome(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	e.Provider = rejectedProvider{fakeProvider: f, known: true}
	_, err := e.Run(context.Background(), task, nil, nil)
	var serviceErr *ServiceError
	if !errors.Is(err, ErrRejected) || !errors.As(err, &serviceErr) || serviceErr.Code != "PRECONDITION" {
		t.Fatalf("provider identity lost: %v", err)
	}
	current, _ := e.Tasks.Get(context.Background(), task.ID)
	for _, event := range current.Events {
		if event.Type == "CAPABILITY_FAILED" {
			if event.Payload["code"] != "PRECONDITION" || event.Payload["outcomeUnknown"] != false {
				t.Fatalf("false uncertainty: %#v", event.Payload)
			}
			return
		}
	}
	t.Fatal("missing failure event")
}

type verificationRefusalProvider struct{ *fakeProvider }

func (p verificationRefusalProvider) CallService(ctx context.Context, req *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if req.Name == "mapping.status" && p.polls > 0 {
		return &robotv1.ServiceResponse{Ok: false, Code: "READ_FORBIDDEN", Message: "verification read refused", Result: fields(map[string]any{"outcome": "REJECTED"})}, nil
	}
	return p.fakeProvider.CallService(ctx, req)
}

func TestVerificationReadRefusalDoesNotProveWriteNonAdmission(t *testing.T) {
	f := &fakeProvider{}
	e, task := setupGoal(t, f)
	e.Provider = verificationRefusalProvider{fakeProvider: f}
	if _, err := e.Run(context.Background(), task, nil, nil); err == nil {
		t.Fatal("verification rejection disappeared")
	}
	current, _ := e.Tasks.Get(context.Background(), task.ID)
	for _, event := range current.Events {
		if event.Type != "CAPABILITY_FAILED" {
			continue
		}
		if event.Payload["phase"] != "verification" || event.Payload["outcomeUnknown"] != true || event.Payload["rejected"] != false {
			t.Fatalf("verification read refusal misrepresented as original write rejection: %#v", event.Payload)
		}
		state, _ := e.Store.StepStatus(context.Background(), task.ID, "rev-1-cap-02")
		if state != middleware.StepStarted {
			t.Fatalf("unknown physical result became retryable: %v", state)
		}
		return
	}
	t.Fatal("missing failure event")
}
