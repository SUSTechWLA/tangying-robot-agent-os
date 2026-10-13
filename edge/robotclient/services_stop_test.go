package robotclient

import (
	"context"
	"errors"
	"net"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

type stopServiceServer struct {
	robotv1.UnimplementedRobotRuntimeServer
	failInfo  atomic.Bool
	changed   atomic.Bool
	infoCalls atomic.Int32
	requests  chan *robotv1.ServiceRequest
}

func (s *stopServiceServer) GetRuntimeInfo(context.Context, *robotv1.GetRuntimeInfoRequest) (*robotv1.RuntimeInfo, error) {
	s.infoCalls.Add(1)
	if s.failInfo.Load() {
		return nil, status.Error(codes.Unavailable, "info unavailable during shutdown")
	}
	revision := "catalog-1"
	if s.changed.Load() {
		revision = "catalog-2"
	}
	return &robotv1.RuntimeInfo{RobotId: "robot-a", Adapter: "fixture", AdapterVersion: "1", ProtocolVersion: "1.0", CatalogRevision: revision}, nil
}
func (s *stopServiceServer) CallService(_ context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	s.requests <- r
	return &robotv1.ServiceResponse{Ok: true, Code: "OK"}, nil
}

func stopClient(t *testing.T, pinned bool) (*Client, *stopServiceServer) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s := &stopServiceServer{requests: make(chan *robotv1.ServiceRequest, 10)}
	server := grpc.NewServer()
	robotv1.RegisterRobotRuntimeServer(server, s)
	go server.Serve(listener)
	t.Cleanup(server.Stop)
	configuration := Config{Address: listener.Addr().String(), DevInsecure: true, ExpectedRobotID: "robot-a"}
	if pinned {
		configuration.ContractPinPath = filepath.Join(t.TempDir(), "pin.json")
	}
	c, err := New(configuration)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	return c, s
}

func stopScope(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	ctx, err := capability.WithOwnedStop(ctx, capability.OwnedStop{RobotID: "robot-a", OperationID: "op-1", OwnerID: "task-1/call-1", StatusService: "mapping.status", CancelService: "mapping.cancel"})
	if err != nil {
		t.Fatal(err)
	}
	return ctx
}
func stopRequest(name string) *robotv1.ServiceRequest {
	r := &robotv1.ServiceRequest{RobotId: "robot-a", Name: name, OperationId: "op-1", OperationOwnerId: "task-1/call-1", Parameters: &structpb.Struct{}}
	if name == "mapping.cancel" {
		r.RequestId = "task-1/call-1/cancel"
	}
	return r
}

func TestOwnedStopSurvivesRuntimeInfoFailureAndContractChange(t *testing.T) {
	for _, failure := range []string{"unavailable", "changed"} {
		t.Run(failure, func(t *testing.T) {
			client, server := stopClient(t, true)
			if _, err := client.Info(t.Context()); err != nil {
				t.Fatal(err)
			}
			if failure == "unavailable" {
				server.failInfo.Store(true)
			} else {
				server.changed.Store(true)
			}
			if _, err := client.CallService(t.Context(), stopRequest("mapping.status")); err == nil {
				t.Fatal("regular call bypassed failed admission")
			}
			before := server.infoCalls.Load()
			ctx := stopScope(t)
			for _, name := range []string{"mapping.status", "mapping.cancel", "mapping.status"} {
				if _, err := client.CallService(ctx, stopRequest(name)); err != nil {
					t.Fatal(err)
				}
			}
			if server.infoCalls.Load() != before {
				t.Fatal("owned stop still queried unavailable Info")
			}
			if len(server.requests) != 3 {
				t.Fatal("owned stop requests did not reach Runtime")
			}
			for range 3 {
				r := <-server.requests
				if r.OperationLeaseMs != 0 || r.OperationId != "op-1" || r.OperationOwnerId != "task-1/call-1" {
					t.Fatal("stop renewed motion or lost ownership")
				}
			}
		})
	}
}

func TestOwnedStopCannotBypassWithUnrelatedRequest(t *testing.T) {
	client, server := stopClient(t, true)
	if _, err := client.Info(t.Context()); err != nil {
		t.Fatal(err)
	}
	server.changed.Store(true)
	ctx := stopScope(t)
	for name, mutate := range map[string]func(*robotv1.ServiceRequest){
		"robot":          func(r *robotv1.ServiceRequest) { r.RobotId = "robot-b" },
		"start":          func(r *robotv1.ServiceRequest) { r.Name = "mapping.build" },
		"unrelated-stop": func(r *robotv1.ServiceRequest) { r.Name = "navigation.cancel" },
		"operation":      func(r *robotv1.ServiceRequest) { r.OperationId = "op-2" },
		"owner":          func(r *robotv1.ServiceRequest) { r.OperationOwnerId = "other-owner" },
		"lease":          func(r *robotv1.ServiceRequest) { r.OperationLeaseMs = 30000 },
		"parameters":     func(r *robotv1.ServiceRequest) { r.Parameters, _ = structpb.NewStruct(map[string]any{"restart": true}) },
		"request-id":     func(r *robotv1.ServiceRequest) { r.RequestId = "new-command" },
	} {
		t.Run(name, func(t *testing.T) {
			r := stopRequest("mapping.cancel")
			mutate(r)
			if _, err := client.CallService(ctx, r); !errors.Is(err, ErrContractChanged) {
				t.Fatalf("invalid scoped request admitted: %v", err)
			}
		})
	}
	if len(server.requests) != 0 {
		t.Fatal("rejected request reached Runtime")
	}
}

func TestOwnedStopRequiresPriorAdmissionEvenWithoutPin(t *testing.T) {
	for _, pinned := range []bool{false, true} {
		client, server := stopClient(t, pinned)
		ctx := stopScope(t)
		if _, err := client.CallService(ctx, stopRequest("mapping.cancel")); !errors.Is(err, ErrContractChanged) {
			t.Fatalf("cold client admitted stop: %v", err)
		}
		if _, err := client.Info(t.Context()); err != nil {
			t.Fatal(err)
		}
		server.failInfo.Store(true)
		if _, err := client.CallService(ctx, stopRequest("mapping.cancel")); err != nil {
			t.Fatal(err)
		}
		if len(server.requests) != 1 {
			t.Fatal("admitted client stop missing")
		}
	}
}
