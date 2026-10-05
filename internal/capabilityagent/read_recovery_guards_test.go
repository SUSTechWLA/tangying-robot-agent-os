package capabilityagent

import (
	"context"
	"errors"
	"testing"
	"time"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type recoveryGuardProvider struct {
	*interruptedReadProvider
	list func(context.Context) (*robotv1.ServiceCatalog, error)
}

func (p *recoveryGuardProvider) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	return p.list(ctx)
}

func TestReadRecoveryRefusesChangedBindingOrCancellationAfterDiagnosis(t *testing.T) {
	for _, change := range []string{"catalog revision", "robot identity", "task cancelled"} {
		t.Run(change, func(t *testing.T) {
			base := &fakeProvider{}
			executor, task := setupGoal(t, base)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			diagnosed := false
			catalogCalls := 0
			provider := &recoveryGuardProvider{
				interruptedReadProvider: &interruptedReadProvider{
					fakeProvider: base, failure: status.Error(codes.Unavailable, "temporary read outage"),
				},
				list: func(callCtx context.Context) (*robotv1.ServiceCatalog, error) {
					catalogCalls++
					if !diagnosed {
						t.Fatal("recovery catalogue checked before diagnosis")
					}
					deadline, ok := callCtx.Deadline()
					if !ok || time.Until(deadline) > 30*time.Second {
						t.Fatal("recovery catalogue request lacks its 30-second upper bound")
					}
					if err := callCtx.Err(); err != nil {
						return nil, err
					}
					catalog, err := base.ListServices(callCtx)
					if err != nil {
						return nil, err
					}
					switch change {
					case "catalog revision":
						catalog.Services[0].Description = "new provider capability revision"
					case "robot identity":
						catalog.RobotId = "replacement-robot"
					}
					return catalog, nil
				},
			}
			executor.Provider = provider
			executor.RecoverRead = func(context.Context, string, string, string, error) error {
				diagnosed = true
				if change == "task cancelled" {
					cancel()
				}
				return nil
			}
			_, err := executor.callRecoverableRead(ctx, task.ID, "rev-1-cap-01", "heterogeneous-unit",
				"calibration.get", task.ID+"/rev-1-cap-01", map[string]any{}, false)
			if err == nil || !diagnosed || provider.calls != 1 || catalogCalls != 1 {
				t.Fatalf("unsafe retry: err=%v diagnosed=%t calls=%d catalog=%d", err, diagnosed, provider.calls, catalogCalls)
			}
			if change == "task cancelled" && !errors.Is(err, context.Canceled) {
				t.Fatalf("cancellation was lost: %v", err)
			}
			persisted, err := executor.Tasks.Get(context.Background(), task.ID)
			if err != nil {
				t.Fatal(err)
			}
			for _, event := range persisted.Events {
				if event.Type == "CAPABILITY_READ_RETRY" {
					t.Fatalf("invalid binding acquired durable retry authority: %+v", event)
				}
			}
		})
	}
}

func TestCataloguePreservesAShorterCallerDeadline(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	parentDeadline, _ := ctx.Deadline()
	called := false
	provider := &recoveryGuardProvider{list: func(callCtx context.Context) (*robotv1.ServiceCatalog, error) {
		called = true
		deadline, ok := callCtx.Deadline()
		if !ok || !deadline.Equal(parentDeadline) {
			t.Fatalf("catalogue extended caller deadline: got=%v want=%v", deadline, parentDeadline)
		}
		return (&fakeProvider{}).ListServices(callCtx)
	}}
	if _, _, err := Catalogue(ctx, provider); err != nil || !called {
		t.Fatalf("catalogue read failed: called=%t err=%v", called, err)
	}
}
