package main

import (
	"context"
	"fmt"
	"math"
	"net/url"
	"reflect"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
)

type localPolicySettings struct {
	Mode, Endpoint, RobotModel, TransformRevision, CalibrationRevision string
	Timeout                                                            time.Duration
}

func parseLocalPolicySettings(values map[string]string) (localPolicySettings, error) {
	s := localPolicySettings{Mode: strings.ToLower(strings.TrimSpace(values["LOCAL_POLICY_MODE"])),
		Endpoint: strings.TrimSpace(values["LOCAL_POLICY_ENDPOINT"]), RobotModel: strings.TrimSpace(values["LOCAL_POLICY_ROBOT_MODEL"]),
		TransformRevision: strings.TrimSpace(values["LOCAL_POLICY_TRANSFORM_REVISION"]), CalibrationRevision: strings.TrimSpace(values["LOCAL_POLICY_CALIBRATION_REVISION"]), Timeout: 10 * time.Second}
	if s.Mode == "" || s.Mode == "disabled" {
		if s.Endpoint != "" {
			return s, fmt.Errorf("LOCAL_POLICY_ENDPOINT requires LOCAL_POLICY_MODE=http")
		}
		return s, nil
	}
	if s.Mode != "http" {
		return s, fmt.Errorf("LOCAL_POLICY_MODE must be disabled or http; simulated policies cannot commission hardware")
	}
	if raw := strings.TrimSpace(values["LOCAL_POLICY_TIMEOUT"]); raw != "" {
		value, err := time.ParseDuration(raw)
		if err != nil || value <= 0 || value > time.Minute {
			return s, fmt.Errorf("LOCAL_POLICY_TIMEOUT must be positive and no greater than 1m")
		}
		s.Timeout = value
	}
	u, err := url.Parse(s.Endpoint)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || (u.Scheme != "https" && u.Scheme != "http") {
		return s, fmt.Errorf("LOCAL_POLICY_ENDPOINT must be an HTTP(S) service origin without credentials, query or fragment")
	}
	if u.Scheme == "http" && u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1" && u.Hostname() != "::1" {
		return s, fmt.Errorf("LOCAL_POLICY_ENDPOINT requires HTTPS outside loopback")
	}
	if s.RobotModel == "" || s.TransformRevision == "" || s.CalibrationRevision == "" {
		return s, fmt.Errorf("LOCAL_POLICY_ROBOT_MODEL, LOCAL_POLICY_TRANSFORM_REVISION and LOCAL_POLICY_CALIBRATION_REVISION must pin the commissioned configuration")
	}
	return s, nil
}

type localPolicyRuntime interface {
	Info(context.Context) (runtime.Snapshot, error)
	Telemetry(context.Context, string) (telemetry.Snapshot, error)
}

func localPolicyPreparer(settings localPolicySettings, connected runtime.Snapshot, robot localPolicyRuntime) (func(context.Context, runtime.Command, runtime.Snapshot) (map[string]any, error), error) {
	var provider policy.Provider
	if settings.Mode == "http" {
		var err error
		provider, err = policy.NewHTTPProvider(policy.HTTPConfig{Endpoint: settings.Endpoint, Timeout: settings.Timeout})
		if err != nil {
			return nil, err
		}
		if connected.RobotProfile != nil && settings.RobotModel != connected.RobotProfile.ModelID {
			return nil, fmt.Errorf("%w: local policy model differs from connected robot profile", policy.ErrPolicyIncompatible)
		}
	}
	return func(ctx context.Context, command runtime.Command, approved runtime.Snapshot) (map[string]any, error) {
		capability, ok := approved.Capability(string(command.Capability))
		if !ok || !containsParameter(capability.InputParameters, "action_chunk") {
			// Internally planned tools own their motion generation; never attach
			// an external trajectory simply because a sidecar was configured.
			return nil, nil
		}
		if command.RobotID != connected.RobotID || approved.RobotID != connected.RobotID || approved.Adapter != connected.Adapter || command.CatalogRevision != approved.CatalogRevision {
			return nil, fmt.Errorf("%w: local policy robot binding changed", policy.ErrPolicyIncompatible)
		}
		observer := &commissionedPolicyObserver{robot: robot, connected: connected, transform: settings.TransformRevision}
		preparer := worker.New(worker.Config{RobotID: connected.RobotID, Adapter: connected.Adapter,
			RobotModel: settings.RobotModel, TransformRevision: settings.TransformRevision, CalibrationRevision: settings.CalibrationRevision,
			Observer: observer, Policy: provider})
		prepared, err := preparer.PreparePolicyCommand(ctx, command, approved)
		if err != nil {
			return nil, err
		}
		current, err := robot.Info(ctx)
		if err != nil {
			return nil, err
		}
		if current.RobotID != approved.RobotID || current.Adapter != approved.Adapter || current.CatalogRevision != approved.CatalogRevision {
			return nil, fmt.Errorf("%w: local policy runtime binding changed during inference", policy.ErrPolicyIncompatible)
		}
		if !current.PhysicalReady() {
			return nil, runtime.ErrRobotNotReady
		}
		if err := current.CanExecute(string(command.Capability)); err != nil {
			return nil, err
		}
		metadata, ok := prepared.Parameters["policy_execution"].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("local policy did not prepare supported motion")
		}
		validUntil, err := time.Parse(time.RFC3339Nano, fmt.Sprint(metadata["validUntil"]))
		if err != nil {
			return nil, err
		}
		if !observer.validUntil.IsZero() && observer.validUntil.Before(validUntil) {
			validUntil = observer.validUntil
			metadata["validUntil"] = validUntil.Format(time.RFC3339Nano)
		}
		if !time.Now().Before(validUntil) {
			return nil, policy.ErrObservationStale
		}
		return map[string]any{"action_chunk": prepared.Parameters["action_chunk"], "policy_execution": prepared.Parameters["policy_execution"]}, nil
	}, nil
}

// A legacy entity list stamped at request time is insufficient to commission
// learned physical motion. Require the existing sensor/profile contract, using
// the capture timestamp and source budget rather than a fresh HTTP response.
type commissionedPolicyObserver struct {
	robot      localPolicyRuntime
	connected  runtime.Snapshot
	transform  string
	validUntil time.Time
}

func (o *commissionedPolicyObserver) Telemetry(ctx context.Context, taskID string) (telemetry.Snapshot, error) {
	s, err := o.robot.Telemetry(ctx, taskID)
	if err != nil {
		return s, err
	}
	if s.RobotID != o.connected.RobotID || s.Adapter != o.connected.Adapter || s.EmergencyStopped {
		return s, fmt.Errorf("%w: policy telemetry robot binding or stop state is invalid", policy.ErrPolicyIncompatible)
	}
	if s.Adapter == "gazebo" || s.Adapter == "mujoco" || s.Adapter == "robocasa" {
		return s, nil
	}
	if s.RobotProfile == nil || o.connected.RobotProfile == nil || s.Reconstruction == nil || !reflect.DeepEqual(s.RobotProfile, o.connected.RobotProfile) {
		return s, fmt.Errorf("%w: physical policy requires a stable robot profile and capture reconstruction", policy.ErrPolicyIncompatible)
	}
	r := s.Reconstruction
	if r.SourceType == "sim_ground_truth" || r.TransformRevision != o.transform {
		return s, fmt.Errorf("%w: physical policy requires a commissioned real observation source and transform", policy.ErrPolicyIncompatible)
	}
	if err := r.Validate(*s.RobotProfile, time.Now()); err != nil {
		return s, fmt.Errorf("%w: %v", policy.ErrObservationInvalid, err)
	}
	// Every declared joint must have a measured, finite position. Counters and
	// rewards in a legacy snapshot cannot stand in for proprioception.
	joints := s.RobotState["joints"]
	if joints == nil {
		joints = s.RobotState["joint_positions"]
	}
	if len(s.RobotProfile.Joints) == 0 {
		return s, fmt.Errorf("%w: physical policy has no declared joints", policy.ErrObservationUnavailable)
	}
	for _, joint := range s.RobotProfile.Joints {
		var value float64
		var present bool
		switch positions := joints.(type) {
		case map[string]any:
			value, present = positions[joint.Name].(float64)
		case map[string]float64:
			value, present = positions[joint.Name]
		}
		if !present || math.IsNaN(value) || math.IsInf(value, 0) || value < joint.Lower || value > joint.Upper {
			return s, fmt.Errorf("%w: physical policy lacks valid measured joint %s", policy.ErrObservationUnavailable, joint.Name)
		}
	}
	sensor, _ := s.RobotProfile.Sensor(r.SourceID)
	o.validUntil = time.UnixMilli(r.ObservedAtUnixMS).Add(time.Duration(sensor.MaxAgeMS) * time.Millisecond)
	return s, nil
}

func containsParameter(values []string, key string) bool {
	for _, value := range values {
		if value == key {
			return true
		}
	}
	return false
}
