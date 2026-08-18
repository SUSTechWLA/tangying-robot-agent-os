// Package runtime defines the stable boundary between the Agent and the
// Robot Runtime. Agent code depends on these semantic types; the gRPC robot
// protocol, ROS 2 topics/actions, vendor SDKs and hardware buses remain
// implementation details behind implementations of this contract.
package runtime

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
)

var (
	ErrRobotNotReady         = errors.New("robot is not ready for physical execution")
	ErrCapabilityUnknown     = errors.New("robot does not advertise required capability")
	ErrCapabilityUnavailable = errors.New("robot capability is currently unavailable")
	ErrSkillStreamClosed     = errors.New("robot skill stream closed without a terminal event")
	ErrSkillCommandExpired   = errors.New("robot skill command deadline has already passed")
)

// SkillResult is the normalized, semantic outcome of one capability
// invocation. It deliberately contains no ROS topic/service/action handles.
type SkillResult struct {
	Success                bool
	Code                   string
	Message                string
	ObservationID          string
	VerificationConfidence float64
}

// Capability describes what a Robot Runtime can do, whether it is currently
// available, and the execution properties the Agent may rely on.
type Capability struct {
	Name             string
	Description      string
	SafetyLevel      string
	Available        bool
	Blockers         []string
	Cancellable      bool
	Recoverable      bool
	DefaultTimeout   time.Duration
	InputParameters  []string
	OutputParameters []string
}

// Snapshot is a low-rate semantic view of the Robot Runtime. It contains no
// raw camera, LiDAR, IMU or joint-state data.
type Snapshot struct {
	RobotID         string
	Adapter         string
	SoftwareVersion string
	Ready           bool
	Blockers        []string
	Capabilities    []Capability
}

func (s Snapshot) Capability(name string) (Capability, bool) {
	for _, capability := range s.Capabilities {
		if capability.Name == name {
			return capability, true
		}
	}
	return Capability{}, false
}

func (s Snapshot) CapabilityNames() []string {
	names := make([]string, 0, len(s.Capabilities))
	for _, capability := range s.Capabilities {
		names = append(names, capability.Name)
	}
	return names
}

// CanExecute returns nil when the runtime advertises capability as currently
// available. Callers must still send the command through the deterministic
// Safety Supervisor; this is a planning/availability check, not an
// authorization.
func (s Snapshot) CanExecute(skill string) error {
	if skill == "" {
		return fmt.Errorf("%w: empty skill", ErrCapabilityUnknown)
	}
	capability, ok := s.Capability(skill)
	if !ok {
		return fmt.Errorf("%w: %s (available: %s)", ErrCapabilityUnknown, skill, strings.Join(s.CapabilityNames(), ", "))
	}
	if !capability.Available {
		return fmt.Errorf("%w: %s (%s)", ErrCapabilityUnavailable, skill, strings.Join(capability.Blockers, ", "))
	}
	return nil
}

func (s Snapshot) PhysicalReady() bool {
	return s.Ready && len(s.Blockers) == 0
}

// CapabilityProvider is implemented by Robot Runtime clients that can refresh
// the current capability/availability view.
type CapabilityProvider interface {
	Snapshot(context.Context) (Snapshot, error)
}
