package agent

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type availabilityProbe struct {
	reads int
	info  func(int) runtime.Snapshot
}

func (p *availabilityProbe) Info(ctx context.Context) (runtime.Snapshot, error) {
	p.reads++
	return p.info(p.reads), ctx.Err()
}

func (*availabilityProbe) Invoke(context.Context, runtime.Command) (runtime.Result, error) {
	panic("availability check must never dispatch a physical command")
}

func availableProbeSnapshot() runtime.Snapshot {
	return runtime.Snapshot{Adapter: "custom", Ready: true,
		Capabilities: []runtime.Capability{{Name: "arm.move", Available: true}}}
}

func availabilityPlan() taskgraph.TaskPlan {
	return taskgraph.TaskPlan{Steps: []taskgraph.SkillStep{{Skill: "arm.move", SafetyLevel: "physical_motion"}}}
}

func TestCapabilityAdmissionWaitsForActualRecoveryBeforeDispatch(t *testing.T) {
	probe := &availabilityProbe{info: func(read int) runtime.Snapshot {
		s := availableProbeSnapshot()
		if read == 1 {
			s.Capabilities[0].Available = false
			s.Capabilities[0].Blockers = []string{"SENSOR_NOT_READY"}
		}
		return s
	}}
	r := &Runner{invoker: probe}
	s, err := r.checkRuntimeCapabilities(context.Background(), availabilityPlan(), "custom")
	if err != nil || probe.reads != 2 || !s.Capabilities[0].Available {
		t.Fatalf("snapshot=%+v reads=%d err=%v", s, probe.reads, err)
	}
}

func TestCapabilityAdmissionDoesNotWaitForUnknownSkillsOrIdentityMismatch(t *testing.T) {
	for _, identity := range []bool{false, true} {
		probe := &availabilityProbe{info: func(int) runtime.Snapshot {
			s := availableProbeSnapshot()
			if !identity {
				s.Capabilities = nil
			}
			return s
		}}
		requested := "custom"
		want := runtime.ErrCapabilityUnknown
		if identity {
			requested, want = "gazebo", runtime.ErrAdapterMismatch
		}
		_, err := (&Runner{invoker: probe}).checkRuntimeCapabilities(context.Background(), availabilityPlan(), requested)
		if !errors.Is(err, want) || probe.reads != 1 {
			t.Fatalf("reads=%d err=%v", probe.reads, err)
		}
	}
}

func TestCapabilityAdmissionHonorsCancellationAndPersistentFailure(t *testing.T) {
	probe := &availabilityProbe{info: func(int) runtime.Snapshot {
		s := availableProbeSnapshot()
		s.Ready, s.Blockers = false, []string{"CALIBRATION_REQUIRED"}
		return s
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	_, err := (&Runner{invoker: probe}).checkRuntimeCapabilities(ctx, availabilityPlan(), "custom")
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	probe.reads = 0
	_, err = (&Runner{invoker: probe}).checkRuntimeCapabilities(ctx, availabilityPlan(), "custom")
	if !errors.Is(err, context.DeadlineExceeded) || probe.reads != 0 {
		t.Fatalf("reads=%d err=%v", probe.reads, err)
	}
	// Without an earlier caller deadline the bounded readiness wait still fails.
	_, err = (&Runner{invoker: probe}).checkRuntimeCapabilities(context.Background(), availabilityPlan(), "custom")
	if !errors.Is(err, runtime.ErrRobotNotReady) {
		t.Fatal(err)
	}
}
