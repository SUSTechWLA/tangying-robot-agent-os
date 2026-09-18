package recoveryexec_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

// fakeRobot is a robot that declares services and answers calls.
type fakeRobot struct {
	services []*robotv1.ServiceDefinition
	answers  map[string]*robotv1.ServiceResponse
	// err fails the catalogue read; callErr fails one call. They are separate
	// because they exercise different paths, and conflating them is how a test
	// ends up asserting on a nil tool.
	err      error
	callErr  error
	calls    []string
	lastArgs map[string]any
}

func (f *fakeRobot) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	if f.err != nil {
		return nil, f.err
	}
	return &robotv1.ServiceCatalog{RobotId: "xlerobot-test", Services: f.services}, nil
}

func (f *fakeRobot) CallService(_ context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	f.calls = append(f.calls, request.GetName())
	if f.callErr != nil {
		return nil, f.callErr
	}
	if request.GetParameters() != nil {
		f.lastArgs = request.GetParameters().AsMap()
	}
	if answer, ok := f.answers[request.GetName()]; ok {
		return answer, nil
	}
	return &robotv1.ServiceResponse{Ok: true}, nil
}

func service(name, description string, available, mutates bool) *robotv1.ServiceDefinition {
	schema, _ := structpb.NewStruct(map[string]any{
		"type": "object",
		"properties": map[string]any{
			"mapId": map[string]any{"type": "string"},
		},
	})
	return &robotv1.ServiceDefinition{
		Name: name, Description: description, Available: available,
		MutatesWorld: mutates, InputSchema: schema,
	}
}

func freshEvidence() recoveryexec.EvidenceSource {
	return func(context.Context) *closedloop.Evidence {
		return &closedloop.Evidence{
			ObservationID: "obs-after", ObservedAt: time.Now().UTC(),
			Freshness: "FRESH", SourceID: "robot-1/head-rgbd", Confidence: 0.9,
		}
	}
}

// A declared service becomes a tool, with the description and parameters the robot
// published — the model chooses from the robot's own words, not a paraphrase.
func TestDeclaredServicesBecomeTools(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{
		service("mapping.activate", "加载一张已保存的地图", true, false),
	}}
	registry := recoveryexec.RobotServices(robot, nil)
	tool, ok := registry.Lookup("mapping.activate")
	if !ok {
		t.Fatal("a declared, available service was not offered")
	}
	if tool.Description != "加载一张已保存的地图" {
		t.Fatalf("description = %q", tool.Description)
	}
	if len(tool.Parameters) != 1 || tool.Parameters[0] != "mapId" {
		t.Fatalf("parameters = %v, want the declared schema's properties", tool.Parameters)
	}
}

// An unavailable service is not offered. Offering it would be offering a choice
// that cannot succeed.
func TestUnavailableServicesAreNotOffered(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{
		service("calibration.run", "运行标定", false, false),
	}}
	if _, ok := recoveryexec.RobotServices(robot, nil).Lookup("calibration.run"); ok {
		t.Fatal("an unavailable service was offered as a tool")
	}
}

// And neither is an undeclared one.
func TestUnknownServicesAreNotOffered(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{service("mapping.status", "状态", true, false)}}
	registry := recoveryexec.RobotServices(robot, nil)
	if _, ok := registry.Lookup("mapping.self_destruct"); ok {
		t.Fatal("an undeclared service was offered")
	}
}

// The robot's own `mutates_world` decides the level. Nothing here guesses a level
// the robot did not state.
func TestTheRobotsOwnDeclarationDecidesTheLevel(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{
		service("mapping.status", "读状态", true, false),
		service("mapping.move", "移动机器人", true, true),
	}}
	registry := recoveryexec.RobotServices(robot, nil)
	readTool, _ := registry.Lookup("mapping.status")
	moveTool, _ := registry.Lookup("mapping.move")
	if readTool.MutatesWorld || moveTool.MutatesWorld == false {
		t.Fatalf("mutatesWorld was not taken from the declaration: %+v %+v", readTool, moveTool)
	}
	if readTool.SafetyLevel != "local_side_effect" {
		t.Fatalf("a non-mutating service got level %q", readTool.SafetyLevel)
	}
	if moveTool.SafetyLevel != "physical_motion" {
		t.Fatalf("a mutating service got level %q", moveTool.SafetyLevel)
	}
}

// The heart of it: a call that changes the world is confirmed by an observation
// taken after it, because the service itself carries none.
//
// Read literally, a mutating service call is always an unknown outcome and the
// recovery loop would stop after the first one. The gate is not relaxed here; the
// observation it asks for is supplied.
func TestAMutatingCallIsGivenPostCallEvidence(t *testing.T) {
	robot := &fakeRobot{
		services: []*robotv1.ServiceDefinition{service("mapping.move", "移动机器人", true, true)},
		answers: map[string]*robotv1.ServiceResponse{
			"mapping.move": {Ok: true, Message: "移动完成"},
		},
	}
	tool, _ := recoveryexec.RobotServices(robot, freshEvidence()).Lookup("mapping.move")
	result, err := tool.Call(context.Background(), map[string]any{"mapId": "scan-1"})
	if err != nil {
		t.Fatalf("call: %v", err)
	}
	if !result.Success {
		t.Fatalf("result = %+v", result)
	}
	if result.Evidence == nil {
		t.Fatal("a mutating call came back with no post-call observation, so the gate must refuse it")
	}
	if result.Evidence.ObservationID != "obs-after" {
		t.Fatalf("evidence = %+v", result.Evidence)
	}
	// And the gate accepts it, which is the whole point of supplying it.
	decision := closedloop.Gate(closedloop.Declaration{Manifest: true},
		time.Now().Add(-time.Second), result.Evidence)
	if err := decision.Require(); err != nil {
		t.Fatalf("the gate refused a call that carried fresh evidence: %v", err)
	}
}

// A read-only call is not asked for evidence: there is no world change to confirm.
func TestAReadOnlyCallIsNotAskedForEvidence(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{service("mapping.status", "读状态", true, false)}}
	asked := false
	tool, _ := recoveryexec.RobotServices(robot, func(context.Context) *closedloop.Evidence {
		asked = true
		return nil
	}).Lookup("mapping.status")
	if _, err := tool.Call(context.Background(), nil); err != nil {
		t.Fatalf("call: %v", err)
	}
	if asked {
		t.Fatal("an observation was taken for a call that changed nothing")
	}
}

// An observation source that fails leaves the evidence nil, which the gate reads as
// "not confirmed". An unavailable observation must not be able to complete a
// physical write.
func TestAFailedObservationLeavesTheCallUnconfirmed(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{service("mapping.move", "移动", true, true)}}
	tool, _ := recoveryexec.RobotServices(robot, func(context.Context) *closedloop.Evidence { return nil }).
		Lookup("mapping.move")
	result, _ := tool.Call(context.Background(), nil)
	if result.Evidence != nil {
		t.Fatalf("evidence = %+v, want none", result.Evidence)
	}
	decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, time.Now().Add(-time.Second), result.Evidence)
	if decision.Require() == nil {
		t.Fatal("the gate accepted a mutating call with no observation")
	}
}

// An emergency stop during the call means the tool physically did nothing, so the
// observation cannot confirm anything.
func TestAnEmergencyStopObservationIsNotUsableForClosure(t *testing.T) {
	snapshot := telemetry.Snapshot{
		RobotID: "robot-1", ObservedAt: time.Now().UTC(),
		EmergencyStopped: true, Anomalies: []string{"EMERGENCY_STOP_LATCHED"},
	}
	evidence := recoveryexec.EvidenceFromSnapshot(snapshot, "receipt-1", time.Now().UTC())
	if evidence.Freshness != "UNKNOWN" {
		t.Fatalf("freshness = %q, want UNKNOWN", evidence.Freshness)
	}
	decision := closedloop.Gate(closedloop.Declaration{Manifest: true}, time.Now().Add(-time.Second), evidence)
	if decision.Require() == nil {
		t.Fatal("the gate accepted evidence taken during an emergency stop")
	}
}

// A failure the robot reports keeps its code and message, so the loop classifies it
// and the operator sees the robot's own sentence.
func TestAServiceFailureKeepsItsCode(t *testing.T) {
	robot := &fakeRobot{
		services: []*robotv1.ServiceDefinition{service("mapping.activate", "加载地图", true, false)},
		answers: map[string]*robotv1.ServiceResponse{
			"mapping.activate": {Ok: false, Code: "NAV_MAP_NOT_READY", Message: "没有可用的地图"},
		},
	}
	tool, _ := recoveryexec.RobotServices(robot, nil).Lookup("mapping.activate")
	result, _ := tool.Call(context.Background(), nil)
	if result.Success || result.Code != "NAV_MAP_NOT_READY" || result.Message != "没有可用的地图" {
		t.Fatalf("result = %+v", result)
	}
	if class := closedloop.Classify(result.Code); class != closedloop.Perception {
		t.Fatalf("class = %s", class)
	}
}

// A transport failure carries no code, so whether the robot acted is unknown. It is
// reported as an unknown outcome rather than as a retryable transport error.
func TestATransportFailureIsReportedAsUnknown(t *testing.T) {
	robot := &fakeRobot{
		services: []*robotv1.ServiceDefinition{service("mapping.activate", "加载地图", true, true)},
		callErr:  errors.New("connection refused"),
	}
	tool, ok := recoveryexec.RobotServices(robot, freshEvidence()).Lookup("mapping.activate")
	if !ok {
		t.Fatal("the service was not offered; this test is about a failing call, not a failing catalogue")
	}
	result, err := tool.Call(context.Background(), nil)
	if err != nil {
		t.Fatalf("a transport failure must be a result, not an error: %v", err)
	}
	if result.Success {
		t.Fatal("a transport failure reported success")
	}
	if class := closedloop.Classify(result.Code); class != closedloop.UnknownOutcome {
		t.Fatalf("class = %s, want UNKNOWN_OUTCOME", class)
	}
}

// A catalogue that cannot be read offers nothing, rather than offering everything.
func TestAnUnreadableCatalogueOffersNothing(t *testing.T) {
	robot := &fakeRobot{err: errors.New("robot unreachable")}
	if _, ok := recoveryexec.RobotServices(robot, nil).Lookup("mapping.activate"); ok {
		t.Fatal("a tool was offered while the catalogue was unreadable")
	}
}

// --- local operations ---------------------------------------------------------

// Local operations are offered by the names the recovery catalogue refers to, and
// one that is not wired is simply absent so the action reports it by name.
func TestLocalOperationsBecomeToolsByName(t *testing.T) {
	registry := recoveryexec.LocalTools{
		ReadTelemetry: func(context.Context) (actionloop.Result, error) {
			return actionloop.Result{Success: true}, nil
		},
		ResumeTask: func(context.Context, map[string]any) (actionloop.Result, error) {
			return actionloop.Result{Success: true}, nil
		},
	}.Registry()
	if _, ok := registry.Lookup("telemetry.read"); !ok {
		t.Fatal("telemetry.read was not offered")
	}
	if _, ok := registry.Lookup("task.resume"); !ok {
		t.Fatal("task.resume was not offered")
	}
	// Not wired, so not offered — and that is how the executor reports it missing.
	for _, name := range []string{"execution.read-history", "runtime.reconnect", "recover_to_safe_pose"} {
		if _, ok := registry.Lookup(name); ok {
			t.Fatalf("%s was offered without being wired", name)
		}
	}
}

// A physical local operation carries the physical level, so it goes through the
// scope check like any other physical call.
func TestAPhysicalLocalOperationIsMarkedPhysical(t *testing.T) {
	registry := recoveryexec.LocalTools{
		ResumeTask: func(context.Context, map[string]any) (actionloop.Result, error) {
			return actionloop.Result{Success: true}, nil
		},
		SafePose: func(context.Context, map[string]any) (actionloop.Result, error) {
			return actionloop.Result{Success: true}, nil
		},
	}.Registry()
	resume, _ := registry.Lookup("task.resume")
	if resume.SafetyLevel != "physical_motion" {
		t.Fatalf("task.resume level = %q", resume.SafetyLevel)
	}
	pose, _ := registry.Lookup("recover_to_safe_pose")
	if pose.SafetyLevel != "physical_motion" || !pose.MutatesWorld {
		t.Fatalf("recover_to_safe_pose = %+v", pose)
	}
}

// The two surfaces share one namespace without either knowing about the other.
func TestServicesAndLocalOperationsCombine(t *testing.T) {
	robot := &fakeRobot{services: []*robotv1.ServiceDefinition{service("mapping.status", "状态", true, false)}}
	combined := recoveryexec.Combined(
		recoveryexec.RobotServices(robot, nil),
		recoveryexec.LocalTools{ReadTelemetry: func(context.Context) (actionloop.Result, error) {
			return actionloop.Result{Success: true}, nil
		}}.Registry(),
	)
	if _, ok := combined.Lookup("mapping.status"); !ok {
		t.Fatal("a robot service was not found")
	}
	if _, ok := combined.Lookup("telemetry.read"); !ok {
		t.Fatal("a local operation was not found")
	}
	if _, ok := combined.Lookup("nothing.at.all"); ok {
		t.Fatal("an unknown name resolved")
	}
}

// The full path: an approved action reaches a real service through the adapter.
func TestAnApprovedActionReachesTheRobotsService(t *testing.T) {
	robot := &fakeRobot{
		services: []*robotv1.ServiceDefinition{service("mapping.activate", "加载一张已保存的地图", true, false)},
		answers: map[string]*robotv1.ServiceResponse{
			"mapping.activate": {Ok: true, Message: "已加载"},
		},
	}
	decider := &scripted{decisions: []actionloop.Decision{
		{Tool: "mapping.activate", Arguments: map[string]any{"mapId": "scan-1"}, Reason: "加载地图"},
		{Done: true},
	}}
	executor := recoveryexec.Executor{
		Registry: recoveryexec.Combined(recoveryexec.RobotServices(robot, freshEvidence())),
		Observer: observer(), Verify: verified(), Decider: decider,
		Approve: func(context.Context, recoveryexec.ApprovalRequest) (bool, error) { return true, nil },
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.activate")})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !result.Executed || !result.Verified {
		t.Fatalf("result = %+v", result)
	}
	if len(robot.calls) != 1 || robot.calls[0] != "mapping.activate" {
		t.Fatalf("robot calls = %v", robot.calls)
	}
	// The arguments the model chose reached the robot.
	if robot.lastArgs["mapId"] != "scan-1" {
		t.Fatalf("robot received %v", robot.lastArgs)
	}
}

// A call that was in flight when the answer stopped coming is the unknown case,
// and only for a call that changes the world.
//
// The classification table maps the transport codes to TRANSIENT — "the connection
// is down, repeating is the right move". That is true of a call that never left and
// false of one that was delivered, and a code-to-class table has only the code to
// go on. This adapter knows whether the tool mutates, so it can tell them apart
// using two codes the table already defines.
func TestAnInFlightFailureIsUnknownOnlyForAMutatingCall(t *testing.T) {
	for name, testCase := range map[string]struct {
		mutates bool
		err     error
		want    string
	}{
		"mutating call, deadline exceeded":  {true, status.Error(codes.DeadlineExceeded, "no answer"), "EXECUTION_OUTCOME_UNKNOWN"},
		"mutating call, cancelled":          {true, status.Error(codes.Canceled, "cancelled"), "EXECUTION_OUTCOME_UNKNOWN"},
		"mutating call, connection refused": {true, status.Error(codes.Unavailable, "refused"), "RPC_UNAVAILABLE"},
		"read call, deadline exceeded":      {false, status.Error(codes.DeadlineExceeded, "no answer"), "RPC_DEADLINE_EXCEEDED"},
		"read call, connection refused":     {false, status.Error(codes.Unavailable, "refused"), "RPC_UNAVAILABLE"},
	} {
		t.Run(name, func(t *testing.T) {
			robot := &fakeRobot{
				services: []*robotv1.ServiceDefinition{service("x.call", "调用", true, testCase.mutates)},
				callErr:  testCase.err,
			}
			tool, ok := recoveryexec.RobotServices(robot, freshEvidence()).Lookup("x.call")
			if !ok {
				t.Fatal("the service was not offered")
			}
			result, err := tool.Call(context.Background(), nil)
			if err != nil {
				t.Fatalf("a failed call must be a result, not an error: %v", err)
			}
			if result.Code != testCase.want {
				t.Fatalf("code = %q, want %q", result.Code, testCase.want)
			}
		})
	}
}
