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
	ErrProtocolIncompatible  = errors.New("robot runtime protocol is incompatible")
	ErrAdapterMismatch       = errors.New("task adapter does not match connected robot runtime")
	ErrRobotUnknown          = errors.New("robot runtime is not registered")
)

type CapabilityName string

const (
	CapabilityGetState      CapabilityName = "state.get"
	CapabilityObserveScene  CapabilityName = "observe_scene"
	CapabilityNavigate      CapabilityName = "navigation.navigate"
	CapabilityMoveArm       CapabilityName = "arm.move"
	CapabilityPick          CapabilityName = "manipulation.pick"
	CapabilityPlace         CapabilityName = "manipulation.place"
	CapabilityEmergencyStop CapabilityName = "safety.emergency_stop"
)

// SkillResult is the normalized, semantic outcome of one capability
// invocation. It deliberately contains no ROS topic/service/action handles.
type Result struct {
	Success                bool
	Code                   string
	Message                string
	ObservationID          string
	VerificationConfidence float64
}

// SkillResult remains an alias for callers migrating to the semantic Result.
type SkillResult = Result

// Command is the transport-neutral invocation sent to a Robot Runtime. It has
// no protobuf, ROS 2 or hardware SDK types; only the transport adapter maps it
// to the wire protocol.
type Command struct {
	SchemaVersion  string
	CommandID      string
	TaskID         string
	RobotID        string
	Capability     CapabilityName
	TargetRef      string
	Parameters     map[string]any
	Deadline       time.Time
	Lease          time.Duration
	IdempotencyKey string
	SafetyProfile  string
	ApprovalID     string
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
	ProtocolVersion string
	RuntimeVersion  string
	Ready           bool
	Blockers        []string
	Capabilities    []Capability
}

func (s Snapshot) ValidateProtocol(expected string) error {
	expectedMajor, _, _ := strings.Cut(expected, ".")
	actualMajor, _, _ := strings.Cut(s.ProtocolVersion, ".")
	if expectedMajor == "" || actualMajor == "" || expectedMajor != actualMajor {
		return fmt.Errorf("%w: laptop=%s robot=%s", ErrProtocolIncompatible, expected, s.ProtocolVersion)
	}
	return nil
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

// InfoProvider is implemented by Robot Runtime clients that can refresh
// the current capability/availability view.
type InfoProvider interface {
	Info(context.Context) (Snapshot, error)
}

type Invoker interface {
	Invoke(context.Context, Command) (Result, error)
}

type Client interface {
	InfoProvider
	Invoker
	Cancel(context.Context, string, string) (bool, error)
	EmergencyStop(context.Context, string) error
}
