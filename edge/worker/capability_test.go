package worker

import (
	"context"
	"errors"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/structpb"
)

type serviceRobot struct {
	sync.Mutex
	starts, renewals     int
	keepRunning, stopped bool
}

func capFields(v map[string]any) *structpb.Struct {
	s, err := structpb.NewStruct(v)
	if err != nil {
		panic(err)
	}
	return s
}
func (r *serviceRobot) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	empty := capFields(map[string]any{"type": "object", "additionalProperties": false})
	read := capFields(map[string]any{"version": "1", "effects": []any{"READ"}})
	write := capFields(map[string]any{"version": "1", "effects": []any{"PHYSICAL_MOTION", "ARTIFACT_WRITE"}, "resources": []any{"robot"}, "operation": map[string]any{"leaseSupported": true, "statusService": "mapping.status", "cancelService": "mapping.cancel", "identityPath": "operationId", "statusIdentityPath": "operationId", "statePath": "state", "running": []any{"exploring"}, "success": []any{"completed"}, "failure": []any{"cancelled"}}, "verification": map[string]any{"service": "mapping.status", "required": []any{"activeMap.mapId", "activeMap.mapRevision", "activeMap.calibrationRevision"}}})
	return &robotv1.ServiceCatalog{RobotId: "heterogeneous-unit", Services: []*robotv1.ServiceDefinition{
		{Name: "calibration.get", Available: true, InputSchema: empty, Contract: read},
		{Name: "mapping.build", Available: true, MutatesWorld: true, InputSchema: capFields(map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"explore"}}}, "additionalProperties": false}), Contract: write},
		{Name: "mapping.status", Available: true, InputSchema: empty, Contract: read},
		{Name: "mapping.cancel", Available: true, MutatesWorld: true, InputSchema: empty},
	}}, nil
}
func (r *serviceRobot) CallService(_ context.Context, q *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	r.Lock()
	defer r.Unlock()
	result := map[string]any{"revision": "calibration-1"}
	if q.Name == "mapping.build" {
		r.starts++
		if q.OperationLeaseMs == 0 {
			return nil, errors.New("missing operation lease")
		}
		result = map[string]any{"operationId": "owned-operation", "state": "exploring"}
	}
	if q.Name == "mapping.cancel" {
		r.stopped = true
		result = map[string]any{"state": "cancelled"}
	}
	if q.Name == "mapping.status" {
		if q.OperationId != "" {
			r.renewals++
		}
		result = map[string]any{"operationId": "owned-operation", "state": "completed", "activeMap": map[string]any{"mapId": "measured-map", "mapRevision": "hash-1", "calibrationRevision": "calibration-1"}}
		if r.keepRunning && !r.stopped {
			result["state"] = "exploring"
		}
		if r.stopped {
			result["state"] = "cancelled"
		}
	}
	return &robotv1.ServiceResponse{Ok: true, Result: capFields(result)}, nil
}
func (*serviceRobot) Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error) {
	return manipulation.GroundedTask{}, errors.New("not a navigation fixture")
}
func (*serviceRobot) Invoke(context.Context, runtime.Command) (runtime.Result, error) {
	return runtime.Result{}, errors.New("not a navigation fixture")
}
func (*serviceRobot) Info(context.Context) (runtime.Snapshot, error) {
	return runtime.Snapshot{RobotID: "heterogeneous-unit", Adapter: "gazebo"}, nil
}
func (*serviceRobot) Cancel(context.Context, string, string) (bool, error) { return true, nil }
func (*serviceRobot) EmergencyStop(context.Context, string) error          { return nil }
func (*serviceRobot) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	return telemetry.Snapshot{}, nil
}

// Real HTTP auth, registry, planner, coordinator, SQLite journal and shared
// executor. The provider is a contract fixture; this is not hardware acceptance.
func TestCloudCapabilityGoalUsesLeasedEdgeExecutorAndDurableProviderEvidence(t *testing.T) {
	ctx := context.Background()
	robot := &serviceRobot{}
	reg := registry.New(registry.NewMemoryStore())
	catalog, _ := robot.ListServices(ctx)
	wire, _ := protojson.Marshal(catalog)
	if _, err := reg.Register(ctx, registry.Device{RobotID: catalog.RobotId, ServiceCatalog: wire}, time.Minute); err != nil {
		t.Fatal(err)
	}
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.SetGoalPlanner(&fleet.CapabilityPlanner{Registry: reg, Parser: intent.NewDeterministicParser()})
	task, err := service.Create(ctx, "heterogeneous-unit: 检查标定，建图", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	coord := coordinator.New(service).WithResourceLeases(lease.NewMemoryManager(), time.Minute)
	if _, err := coord.NextIntent(ctx, task.ID, catalog.RobotId); err == nil {
		t.Fatal("unapproved task claim was accepted")
	}
	task, err = service.Approve(ctx, task.ID, "operator")
	if err != nil {
		t.Fatal(err)
	}
	authentication, err := auth.New(auth.Options{OperatorUser: "operator", OperatorPass: "test-pass", DeviceCredentials: map[string]string{catalog.RobotId: "device-secret"}})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(fleet.NewServer(service, nil, fleet.WithAuthenticator(authentication), fleet.WithRegistry(reg), fleet.WithCoordinator(coord)).Handler())
	defer server.Close()
	cloud, err := cloudclient.New(cloudclient.Config{BaseURL: server.URL, RobotID: catalog.RobotId, DeviceToken: "device-secret"})
	if err != nil {
		t.Fatal(err)
	}
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "execution.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	worker := New(Config{RobotID: catalog.RobotId, Adapter: "gazebo", Cloud: cloud, Runtime: robot, ExecutionStore: store})
	if err := worker.processTask(ctx, task.ID); err != nil {
		t.Fatal(err)
	}
	final, _ := service.Get(ctx, task.ID)
	if final.State != "SUCCEEDED" || robot.starts != 1 || robot.renewals == 0 {
		t.Fatalf("state=%s starts=%d renewals=%d", final.State, robot.starts, robot.renewals)
	}
	snapshot, err := coord.Snapshot(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	for _, node := range snapshot.Intents {
		if node.VerificationBasis != "PROVIDER_CONTRACT_EDGE_VERIFIED" || node.FencingToken == 0 {
			t.Fatalf("unverified claim: %#v", node)
		}
	}
	runs, err := store.ListStepRuns(ctx, task.ID)
	if err != nil || len(runs) != 2 {
		t.Fatalf("durable runs=%d err=%v", len(runs), err)
	}
	if err := worker.processTask(ctx, task.ID); err != nil || robot.starts != 1 {
		t.Fatalf("completed task replayed start: %v starts=%d", err, robot.starts)
	}
}

func TestCloudCancellationWaitsForOwnedStopEvidence(t *testing.T) {
	ctx := context.Background()
	robot := &serviceRobot{keepRunning: true}
	reg := registry.New(registry.NewMemoryStore())
	catalog, _ := robot.ListServices(ctx)
	wire, _ := protojson.Marshal(catalog)
	_, _ = reg.Register(ctx, registry.Device{RobotID: catalog.RobotId, ServiceCatalog: wire}, time.Minute)
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.SetGoalPlanner(&fleet.CapabilityPlanner{Registry: reg, Parser: intent.NewDeterministicParser()})
	task, err := service.Create(ctx, "heterogeneous-unit: 检查标定，建图", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	task, err = service.Approve(ctx, task.ID, "operator")
	if err != nil {
		t.Fatal(err)
	}
	coord := coordinator.New(service).WithResourceLeases(lease.NewMemoryManager(), time.Minute)
	authentication, err := auth.New(auth.Options{OperatorUser: "operator", OperatorPass: "test-pass", DeviceCredentials: map[string]string{catalog.RobotId: "device-secret"}})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(fleet.NewServer(service, nil, fleet.WithAuthenticator(authentication), fleet.WithRegistry(reg), fleet.WithCoordinator(coord)).Handler())
	defer server.Close()
	cloud, err := cloudclient.New(cloudclient.Config{BaseURL: server.URL, RobotID: catalog.RobotId, DeviceToken: "device-secret"})
	if err != nil {
		t.Fatal(err)
	}
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "execution.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	worker := New(Config{RobotID: catalog.RobotId, Adapter: "gazebo", Cloud: cloud, Runtime: robot, ExecutionStore: store})
	done := make(chan error, 1)
	go func() { done <- worker.processTask(ctx, task.ID) }()
	deadline := time.Now().Add(5 * time.Second)
	for {
		current, _ := service.Get(ctx, task.ID)
		ready := false
		for _, e := range current.Events {
			ready = ready || e.Type == "CAPABILITY_PROGRESS"
		}
		if ready {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("operation did not start")
		}
		time.Sleep(time.Millisecond)
	}
	if _, err := coord.AppendTaskEvent(ctx, task.ID, tasks.TaskEvent{Type: "CANCEL_REQUESTED"}); err != nil {
		t.Fatal(err)
	}
	worker.cancelCurrent(ctx, "operator cancellation requested")
	select {
	case err := <-done:
		t.Logf("cancel result: %v", err)
		if err == nil {
			t.Fatal("cancelled goal reported success")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("stop not reconciled")
	}
	final, _ := service.Get(ctx, task.ID)
	if final.State != "CANCELLED" {
		t.Fatalf("state=%s events=%+v", final.State, final.Events)
	}
	confirmed := false
	for _, e := range final.Events {
		confirmed = confirmed || e.Type == "CAPABILITY_STOP_CONFIRMED"
	}
	if !confirmed {
		t.Fatal("cancel lacked stop evidence")
	}
}
