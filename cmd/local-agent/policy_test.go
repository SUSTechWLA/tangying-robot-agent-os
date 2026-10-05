package main

import (
	"context"
	"encoding/json"
	"errors"
	"maps"
	"math"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
)

func localPolicyTestValues() map[string]string {
	return map[string]string{"LOCAL_POLICY_MODE": "http", "LOCAL_POLICY_ENDPOINT": "http://127.0.0.1:8899",
		"LOCAL_POLICY_ROBOT_MODEL": "test-arm", "LOCAL_POLICY_TRANSFORM_REVISION": "camera-cal-1",
		"LOCAL_POLICY_CALIBRATION_REVISION": "actuator-cal-1"}
}

func TestParseLocalPolicySettingsDisabledAndDefault(t *testing.T) {
	for _, mode := range []string{"", "disabled", " DISABLED "} {
		settings, err := parseLocalPolicySettings(map[string]string{"LOCAL_POLICY_MODE": mode})
		if err != nil || settings.Timeout != 10*time.Second || settings.Endpoint != "" {
			t.Fatalf("mode %q: settings=%+v error=%v", mode, settings, err)
		}
		if _, err := parseLocalPolicySettings(map[string]string{"LOCAL_POLICY_MODE": mode, "LOCAL_POLICY_ENDPOINT": "http://localhost:8899"}); err == nil {
			t.Fatalf("mode %q silently ignored a configured endpoint", mode)
		}
	}
}

func TestParseLocalPolicySettingsAcceptsCommissionedOriginsAndBoundedTimeout(t *testing.T) {
	for _, endpoint := range []string{"http://localhost:8899", "http://127.0.0.1:8899", "http://[::1]:8899", "https://policy.example.test", "https://policy.example.test/"} {
		for _, timeout := range []string{"", "250ms", "1m"} {
			values := localPolicyTestValues()
			values["LOCAL_POLICY_MODE"], values["LOCAL_POLICY_ENDPOINT"], values["LOCAL_POLICY_TIMEOUT"] = " HTTP ", endpoint, timeout
			settings, err := parseLocalPolicySettings(values)
			if err != nil || settings.Mode != "http" || settings.Endpoint != endpoint || settings.RobotModel != "test-arm" || settings.TransformRevision != "camera-cal-1" || settings.CalibrationRevision != "actuator-cal-1" {
				t.Fatalf("%s / %q: settings=%+v error=%v", endpoint, timeout, settings, err)
			}
			expected := 10 * time.Second
			if timeout != "" {
				expected, _ = time.ParseDuration(timeout)
			}
			if settings.Timeout != expected {
				t.Fatalf("timeout=%s, want %s", settings.Timeout, expected)
			}
		}
	}
}

func TestParseLocalPolicySettingsRejectsUnsafeOrIncompleteConfiguration(t *testing.T) {
	cases := map[string]map[string]string{
		"simulated mode":      {"LOCAL_POLICY_MODE": "deterministic"},
		"remote plaintext":    {"LOCAL_POLICY_ENDPOINT": "http://policy.example.test"},
		"loopback lookalike":  {"LOCAL_POLICY_ENDPOINT": "http://localhost.example.test"},
		"credentials":         {"LOCAL_POLICY_ENDPOINT": "https://user:secret@policy.example.test"},
		"query":               {"LOCAL_POLICY_ENDPOINT": "https://policy.example.test?token=secret"},
		"fragment":            {"LOCAL_POLICY_ENDPOINT": "https://policy.example.test#other"},
		"path":                {"LOCAL_POLICY_ENDPOINT": "https://policy.example.test/other"},
		"missing endpoint":    {"LOCAL_POLICY_ENDPOINT": ""},
		"missing model":       {"LOCAL_POLICY_ROBOT_MODEL": ""},
		"missing transform":   {"LOCAL_POLICY_TRANSFORM_REVISION": ""},
		"missing calibration": {"LOCAL_POLICY_CALIBRATION_REVISION": " "},
		"zero timeout":        {"LOCAL_POLICY_TIMEOUT": "0s"},
		"negative timeout":    {"LOCAL_POLICY_TIMEOUT": "-1s"},
		"too long timeout":    {"LOCAL_POLICY_TIMEOUT": "61s"},
		"malformed timeout":   {"LOCAL_POLICY_TIMEOUT": "soon"},
	}
	for name, overrides := range cases {
		t.Run(name, func(t *testing.T) {
			values := localPolicyTestValues()
			maps.Copy(values, overrides)
			if _, err := parseLocalPolicySettings(values); err == nil {
				t.Fatal("invalid configuration accepted")
			}
		})
	}
}

// This fake has no hardware transport. Invoke counts ensure preparation itself
// does not dispatch a physical command, even when an HTTP sidecar returns data.
type localPolicyTestRuntime struct {
	mu                               sync.Mutex
	current                          runtime.Snapshot
	observation                      telemetry.Snapshot
	infoError, observationError      error
	infoCalls, observeCalls, invokes int
	observedTask                     string
	infoWaitUntil                    time.Time
}

func (r *localPolicyTestRuntime) Info(context.Context) (runtime.Snapshot, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.infoCalls++
	if delay := time.Until(r.infoWaitUntil); delay > 0 {
		time.Sleep(delay)
	}
	return r.current, r.infoError
}

func (r *localPolicyTestRuntime) Telemetry(_ context.Context, task string) (telemetry.Snapshot, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.observeCalls++
	r.observedTask = task
	return r.observation, r.observationError
}

func (r *localPolicyTestRuntime) Invoke(context.Context, runtime.Command) (runtime.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.invokes++
	return runtime.Result{}, errors.New("test must never dispatch")
}

func localPolicyTestFixture(t *testing.T) (localPolicySettings, runtime.Snapshot, runtime.Command, *localPolicyTestRuntime, policy.Manifest) {
	t.Helper()
	profile := &robotcontract.Profile{SchemaVersion: "robot.profile.v1", RobotID: "arm-local", AdapterID: "xlerobot", AdapterVersion: "1", ModelID: "test-arm", Embodiment: "arm",
		Joints:       []robotcontract.Joint{{Name: "shoulder", Kind: "revolute", Unit: "rad", Lower: -2, Upper: 2}},
		EndEffectors: []robotcontract.EndEffector{{ID: "hand", Kind: "gripper", JointNames: []string{"shoulder"}}},
		Sensors:      []robotcontract.Sensor{{SourceID: "head", SourceType: "rgbd_camera", FrameID: "camera", TransformRevision: "camera-cal-1", MaxAgeMS: 1000}},
		ActionLimits: map[string]robotcontract.ActionLimit{"shoulder.pos": {Min: -2, Max: 2, Unit: "rad"}},
		Tools:        []string{"observe_scene", "emergency_stop", "manipulation.pick"}}
	if err := profile.Validate(); err != nil {
		t.Fatal(err)
	}
	connected := runtime.Snapshot{RobotID: profile.RobotID, Adapter: profile.AdapterID, RobotProfile: profile, Ready: true, CatalogRevision: "catalog-1",
		Capabilities: []runtime.Capability{{Name: "manipulation.pick", Available: true, MutatesWorld: true, InputParameters: []string{"targetRef", "action_chunk"}}}}
	command := runtime.Command{CommandID: "task-local/rev-1/pick", TaskID: "task-local", TaskRevision: 1, StepID: "pick", RobotID: profile.RobotID,
		Capability: runtime.CapabilityPick, TargetRef: "cup-1", CatalogRevision: connected.CatalogRevision, Parameters: map[string]any{"grasp": "left"}}
	now := time.Now().UTC()
	scene := &robotcontract.Reconstruction{SchemaVersion: "scene.reconstruction.v1", RobotID: profile.RobotID, ObservationID: "capture-local", SourceID: "head", SourceType: "rgbd_camera", SourceFrameID: "camera", FrameID: "world", TransformRevision: "camera-cal-1", ObservedAtUnixMS: now.UnixMilli(), Sequence: 1, Units: "m",
		Entities: []robotcontract.Entity{{EntityID: "cup-1", Category: "cup", Pose: []float64{0, 0, 0, 1, 0, 0, 0}, Confidence: .99}}, Points: [][]float64{{0, 0, 0}}}
	if err := scene.Validate(*profile, now); err != nil {
		t.Fatal(err)
	}
	observation := telemetry.Snapshot{RobotID: profile.RobotID, Adapter: profile.AdapterID, TaskID: command.TaskID, TaskRevision: 1, ObservedAt: now,
		RobotProfile: profile, Reconstruction: scene, RobotState: map[string]any{"joint_positions": map[string]any{"shoulder": .1}},
		Entities: []telemetry.Entity{{EntityID: "cup-1", Category: "cup", Confidence: .99}}}
	manifest := policy.Manifest{SchemaVersion: "policy.manifest.v1", PolicyID: "commissioned-test-policy", Version: "1", Framework: policy.FrameworkImitation,
		ArtifactSHA256: strings.Repeat("a", 64), Capabilities: []string{"manipulation.pick"}, RobotModels: []string{profile.ModelID}, Adapters: []string{profile.AdapterID},
		ObservationSchema: "policy.observation.v1", RequiredObservationSources: []string{"scene", "proprioception"}, MaxObservationAge: time.Second,
		ActionSchema: "xlerobot.named-joints.v1", MaxActionChunkLength: 2, ActionBounds: map[string]policy.ActionBound{"shoulder.pos": {Minimum: -1, Maximum: 1}},
		TransformRevision: "camera-cal-1", CalibrationRevision: "actuator-cal-1"}
	settings, err := parseLocalPolicySettings(localPolicyTestValues())
	if err != nil {
		t.Fatal(err)
	}
	return settings, connected, command, &localPolicyTestRuntime{current: connected, observation: observation}, manifest
}

type localPolicyTestSidecar struct {
	manifest                  policy.Manifest
	manifestCalls, inferCalls atomic.Int32
	allCalls                  atomic.Int32
	requests                  chan policy.InferenceRequest
	mutateResult              func(*policy.InferenceResult)
	afterInfer                func()
	manifestDrift             bool
	status                    int
}

func (s *localPolicyTestSidecar) serve(t *testing.T) *httptest.Server {
	t.Helper()
	s.requests = make(chan policy.InferenceRequest, 4)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.allCalls.Add(1)
		if s.status != 0 {
			w.WriteHeader(s.status)
			return
		}
		switch r.URL.Path {
		case "/v1/manifest":
			if r.Method != http.MethodGet {
				t.Errorf("manifest method=%s", r.Method)
			}
			current := s.manifest
			if s.manifestCalls.Add(1) > 1 && s.manifestDrift {
				current.Version = "changed-during-inference"
			}
			_ = json.NewEncoder(w).Encode(current)
		case "/v1/infer":
			if r.Method != http.MethodPost {
				t.Errorf("inference method=%s", r.Method)
			}
			var input policy.InferenceRequest
			if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
				t.Error(err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			s.inferCalls.Add(1)
			s.requests <- input
			result := policy.InferenceResult{SchemaVersion: "policy.inference.result.v1", RequestID: input.RequestID, CommandID: input.CommandID, ManifestRevision: input.ManifestRevision,
				InferenceID: "inference-local", ObservationID: input.Observation.ObservationID, Actions: []policy.Action{{Values: map[string]float64{"shoulder.pos": .25}}}}
			if s.mutateResult != nil {
				s.mutateResult(&result)
			}
			if s.afterInfer != nil {
				s.afterInfer()
			}
			_ = json.NewEncoder(w).Encode(result)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(server.Close)
	return server
}

func TestLocalPolicyPreparerUsesHTTPAndReturnsBoundedIdentityPinnedDecision(t *testing.T) {
	settings, connected, command, robot, manifest := localPolicyTestFixture(t)
	sidecar := &localPolicyTestSidecar{manifest: manifest}
	settings.Endpoint = sidecar.serve(t).URL
	prepare, err := localPolicyPreparer(settings, connected, robot)
	if err != nil {
		t.Fatal(err)
	}
	patch, err := prepare(context.Background(), command, connected)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(patch["action_chunk"], []any{map[string]any{"shoulder.pos": .25}}) {
		t.Fatalf("actions=%#v", patch)
	}
	input := <-sidecar.requests
	if input.CommandID != command.CommandID || input.TaskID != command.TaskID || input.TaskRevision != 1 || input.StepID != command.StepID || input.RobotID != connected.RobotID || input.TargetRef != "cup-1" {
		t.Fatalf("identity lost: %+v", input)
	}
	if input.Observation.Adapter != "xlerobot" || input.Observation.TransformRevision != settings.TransformRevision || input.Observation.CalibrationRevision != settings.CalibrationRevision {
		t.Fatalf("unpinned observation: %+v", input.Observation)
	}
	evidence, ok := patch["policy_execution"].(map[string]any)
	if !ok || evidence["observationId"] != input.Observation.ObservationID || evidence["manifestRevision"] != input.ManifestRevision || evidence["artifactSha256"] != manifest.ArtifactSHA256 {
		t.Fatalf("audit binding=%#v", evidence)
	}
	if robot.observedTask != command.TaskID || robot.invokes != 0 || robot.infoCalls == 0 || sidecar.inferCalls.Load() != 1 {
		t.Fatalf("unexpected IO: robot=%+v infer=%d", robot, sidecar.inferCalls.Load())
	}
	if _, changed := command.Parameters["action_chunk"]; changed {
		t.Fatal("preparation mutated caller command")
	}
}

func TestLocalPolicyPreparerRejectsUnsafeOrUnboundSidecarResults(t *testing.T) {
	cases := map[string]func(*policy.InferenceResult){
		"outside bounds":      func(r *policy.InferenceResult) { r.Actions[0].Values["shoulder.pos"] = 1.1 },
		"unknown actuator":    func(r *policy.InferenceResult) { r.Actions[0].Values = map[string]float64{"uncommissioned.pos": .1} },
		"too many actions":    func(r *policy.InferenceResult) { r.Actions = append(r.Actions, r.Actions[0], r.Actions[0]) },
		"foreign command":     func(r *policy.InferenceResult) { r.CommandID = "other-task/pick" },
		"foreign observation": func(r *policy.InferenceResult) { r.ObservationID = "stale-capture" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			settings, connected, command, robot, manifest := localPolicyTestFixture(t)
			sidecar := &localPolicyTestSidecar{manifest: manifest, mutateResult: mutate}
			settings.Endpoint = sidecar.serve(t).URL
			prepare, err := localPolicyPreparer(settings, connected, robot)
			if err != nil {
				t.Fatal(err)
			}
			patch, err := prepare(context.Background(), command, connected)
			expected := policy.ErrActionUnsafe
			if strings.HasPrefix(name, "foreign") {
				expected = policy.ErrInferenceIdentity
			}
			if !errors.Is(err, expected) || patch != nil || robot.invokes != 0 || sidecar.inferCalls.Load() != 1 {
				t.Fatalf("unsafe result admitted: patch=%#v error=%v dispatched=%d", patch, err, robot.invokes)
			}
		})
	}
}

func TestLocalPolicyPreparerRejectsStaleOrForeignPhysicalTelemetryBeforeInference(t *testing.T) {
	cases := map[string]func(*telemetry.Snapshot){
		"stale capture": func(o *telemetry.Snapshot) {
			o.Reconstruction.ObservedAtUnixMS = time.Now().Add(-2 * time.Second).UnixMilli()
		},
		"foreign robot":   func(o *telemetry.Snapshot) { o.RobotID = "other-robot" },
		"foreign adapter": func(o *telemetry.Snapshot) { o.Adapter = "mujoco" },
		"missing profile": func(o *telemetry.Snapshot) { o.RobotProfile = nil },
		"changed profile": func(o *telemetry.Snapshot) {
			changed := *o.RobotProfile
			changed.AdapterVersion = "different-runtime"
			o.RobotProfile = &changed
		},
		"missing reconstruction": func(o *telemetry.Snapshot) { o.Reconstruction = nil },
		"unknown sensor":         func(o *telemetry.Snapshot) { o.Reconstruction.SourceID = "unregistered" },
		"simulated source": func(o *telemetry.Snapshot) {
			o.Reconstruction.SourceType = "sim_ground_truth"
			o.RobotProfile.Sensors[0].SourceType = "sim_ground_truth"
		},
		"changed transform":         func(o *telemetry.Snapshot) { o.Reconstruction.TransformRevision = "camera-cal-2" },
		"counter instead of joints": func(o *telemetry.Snapshot) { o.RobotState = map[string]any{"step_count": 123.0} },
		"missing joint": func(o *telemetry.Snapshot) {
			o.RobotState = map[string]any{"joint_positions": map[string]any{"other": .1}}
		},
		"nonfinite joint": func(o *telemetry.Snapshot) {
			o.RobotState = map[string]any{"joint_positions": map[string]any{"shoulder": math.NaN()}}
		},
		"out of range joint": func(o *telemetry.Snapshot) {
			o.RobotState = map[string]any{"joint_positions": map[string]any{"shoulder": 2.1}}
		},
		"emergency stopped": func(o *telemetry.Snapshot) { o.EmergencyStopped = true },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			settings, connected, command, robot, manifest := localPolicyTestFixture(t)
			mutate(&robot.observation)
			sidecar := &localPolicyTestSidecar{manifest: manifest}
			settings.Endpoint = sidecar.serve(t).URL
			prepare, err := localPolicyPreparer(settings, connected, robot)
			if err != nil {
				t.Fatal(err)
			}
			patch, err := prepare(context.Background(), command, connected)
			if err == nil || patch != nil || sidecar.inferCalls.Load() != 0 || robot.invokes != 0 {
				t.Fatalf("bad telemetry admitted: patch=%#v error=%v infer=%d", patch, err, sidecar.inferCalls.Load())
			}
		})
	}
}

func TestLocalPolicyPreparerRechecksCatalogAndAvailabilityAfterInference(t *testing.T) {
	cases := map[string]func(*runtime.Snapshot){
		"catalog changed": func(s *runtime.Snapshot) { s.CatalogRevision = "catalog-2" },
		"robot changed":   func(s *runtime.Snapshot) { s.RobotID = "other" },
		"adapter changed": func(s *runtime.Snapshot) { s.Adapter = "mujoco" },
		"not ready":       func(s *runtime.Snapshot) { s.Ready = false },
		"blocker":         func(s *runtime.Snapshot) { s.Blockers = []string{"ESTOP_LATCHED"} },
		"tool unavailable": func(s *runtime.Snapshot) {
			s.Capabilities = []runtime.Capability{{Name: "manipulation.pick", Available: false}}
		},
		"tool removed": func(s *runtime.Snapshot) { s.Capabilities = nil },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			settings, connected, command, robot, manifest := localPolicyTestFixture(t)
			sidecar := &localPolicyTestSidecar{manifest: manifest, afterInfer: func() { robot.mu.Lock(); defer robot.mu.Unlock(); mutate(&robot.current) }}
			settings.Endpoint = sidecar.serve(t).URL
			prepare, err := localPolicyPreparer(settings, connected, robot)
			if err != nil {
				t.Fatal(err)
			}
			patch, err := prepare(context.Background(), command, connected)
			if err == nil || patch != nil || robot.invokes != 0 || sidecar.inferCalls.Load() != 1 {
				t.Fatalf("runtime drift admitted: patch=%#v error=%v infer=%d", patch, err, sidecar.inferCalls.Load())
			}
		})
	}
}

func TestLocalPolicyPreparerRejectsStaleCommandCatalogBeforeInference(t *testing.T) {
	settings, connected, command, robot, manifest := localPolicyTestFixture(t)
	sidecar := &localPolicyTestSidecar{manifest: manifest}
	settings.Endpoint = sidecar.serve(t).URL
	command.CatalogRevision = "old-catalog"
	prepare, err := localPolicyPreparer(settings, connected, robot)
	if err != nil {
		t.Fatal(err)
	}
	patch, err := prepare(context.Background(), command, connected)
	if err == nil || patch != nil || sidecar.inferCalls.Load() != 0 || robot.invokes != 0 {
		t.Fatalf("stale command admitted: patch=%#v error=%v", patch, err)
	}
}

func TestLocalPolicyPreparerRejectsUnpinnedPhysicalOrDeterministicManifest(t *testing.T) {
	cases := map[string]func(*policy.Manifest){
		"deterministic": func(m *policy.Manifest) {
			m.Framework = policy.FrameworkDeterministic
			m.ArtifactSHA256 = "deterministic:test"
		},
		"wildcard models":      func(m *policy.Manifest) { m.RobotModels = nil },
		"wildcard adapters":    func(m *policy.Manifest) { m.Adapters = nil },
		"unpinned transform":   func(m *policy.Manifest) { m.TransformRevision = "" },
		"unpinned calibration": func(m *policy.Manifest) { m.CalibrationRevision = "" },
		"no proprioception":    func(m *policy.Manifest) { m.RequiredObservationSources = []string{"scene"} },
		"no scene":             func(m *policy.Manifest) { m.RequiredObservationSources = []string{"proprioception"} },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			settings, connected, command, robot, manifest := localPolicyTestFixture(t)
			mutate(&manifest)
			sidecar := &localPolicyTestSidecar{manifest: manifest}
			settings.Endpoint = sidecar.serve(t).URL
			prepare, err := localPolicyPreparer(settings, connected, robot)
			if err != nil {
				t.Fatal(err)
			}
			patch, err := prepare(context.Background(), command, connected)
			if err == nil || patch != nil || sidecar.inferCalls.Load() != 0 || robot.invokes != 0 {
				t.Fatalf("uncommissioned manifest admitted: patch=%#v error=%v", patch, err)
			}
		})
	}
}

func TestLocalPolicyPreparerInternallyPlannedToolNeverCallsSidecar(t *testing.T) {
	settings, connected, command, robot, manifest := localPolicyTestFixture(t)
	connected.Capabilities[0].InputParameters = []string{"targetRef"}
	connected.RobotProfile.InternallyPlannedTools = []string{"manipulation.pick"}
	sidecar := &localPolicyTestSidecar{manifest: manifest, status: http.StatusServiceUnavailable}
	settings.Endpoint = sidecar.serve(t).URL
	prepare, err := localPolicyPreparer(settings, connected, robot)
	if err != nil {
		t.Fatal(err)
	}
	patch, err := prepare(context.Background(), command, connected)
	if err != nil || patch != nil || sidecar.allCalls.Load() != 0 || robot.observeCalls != 0 || robot.invokes != 0 {
		t.Fatalf("internal tool consulted external policy: patch=%#v error=%v", patch, err)
	}
}

func TestLocalPolicyPreparerDisabledFailsRequiredPolicyBeforeMotion(t *testing.T) {
	_, connected, command, robot, _ := localPolicyTestFixture(t)
	prepare, err := localPolicyPreparer(localPolicySettings{}, connected, robot)
	if err != nil {
		t.Fatal(err)
	}
	patch, err := prepare(context.Background(), command, connected)
	if !errors.Is(err, worker.ErrPolicyRequired) || patch != nil || robot.invokes != 0 {
		t.Fatalf("disabled required policy: patch=%#v error=%v", patch, err)
	}
}

func TestLocalPolicyPreparerUnavailableManifestDriftAndTimeoutNeverAuthorizeMotion(t *testing.T) {
	for _, fault := range []string{"unavailable", "manifest drift", "timeout"} {
		t.Run(fault, func(t *testing.T) {
			settings, connected, command, robot, manifest := localPolicyTestFixture(t)
			sidecar := &localPolicyTestSidecar{manifest: manifest}
			var expected error
			switch fault {
			case "unavailable":
				sidecar.status, expected = http.StatusServiceUnavailable, policy.ErrProviderUnavailable
			case "manifest drift":
				sidecar.manifestDrift, expected = true, policy.ErrManifestDrift
			case "timeout":
				settings.Timeout, expected = 5*time.Millisecond, policy.ErrProviderTimeout
				sidecar.afterInfer = func() { time.Sleep(30 * time.Millisecond) }
			}
			settings.Endpoint = sidecar.serve(t).URL
			prepare, err := localPolicyPreparer(settings, connected, robot)
			if err != nil {
				t.Fatal(err)
			}
			patch, err := prepare(context.Background(), command, connected)
			if !errors.Is(err, expected) || patch != nil || robot.invokes != 0 {
				t.Fatalf("fault=%s patch=%#v error=%v", fault, patch, err)
			}
		})
	}
}

func TestLocalPolicyPreparerRejectsObservationExpiringDuringRuntimeRecheck(t *testing.T) {
	for _, tighterBudget := range []string{"manifest", "sensor"} {
		t.Run(tighterBudget, func(t *testing.T) {
			settings, connected, command, robot, manifest := localPolicyTestFixture(t)
			const budget = 500 * time.Millisecond
			if tighterBudget == "manifest" {
				manifest.MaxObservationAge = budget
			} else {
				connected.RobotProfile.Sensors[0].MaxAgeMS = budget.Milliseconds()
			}
			robot.infoWaitUntil = time.UnixMilli(robot.observation.Reconstruction.ObservedAtUnixMS).Add(budget + 20*time.Millisecond)
			sidecar := &localPolicyTestSidecar{manifest: manifest}
			settings.Endpoint = sidecar.serve(t).URL
			prepare, err := localPolicyPreparer(settings, connected, robot)
			if err != nil {
				t.Fatal(err)
			}
			patch, err := prepare(context.Background(), command, connected)
			if !errors.Is(err, policy.ErrObservationStale) || patch != nil || sidecar.inferCalls.Load() != 1 || robot.infoCalls != 1 || robot.invokes != 0 {
				t.Fatalf("expired %s budget admitted: patch=%#v error=%v infer=%d rechecks=%d", tighterBudget, patch, err, sidecar.inferCalls.Load(), robot.infoCalls)
			}
		})
	}
}
