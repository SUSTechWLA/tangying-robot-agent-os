package worker

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Preserve the memory manager's lease semantics while exercising the uint64
// magnitude used by the distributed production lease manager.
type largeTokenLeases struct{ lease.Manager }

const testFenceOffset uint64 = 1791514885123456789

func (m largeTokenLeases) Acquire(ctx context.Context, resource, owner string, ttl time.Duration) (lease.Grant, error) {
	g, err := m.Manager.Acquire(ctx, resource, owner, ttl)
	g.Token += testFenceOffset
	return g, err
}
func (m largeTokenLeases) Renew(ctx context.Context, resource, owner string, token uint64, ttl time.Duration) (lease.Grant, error) {
	g, err := m.Manager.Renew(ctx, resource, owner, token-testFenceOffset, ttl)
	g.Token += testFenceOffset
	return g, err
}
func (m largeTokenLeases) Transfer(ctx context.Context, resource, from, to string, token uint64, ttl time.Duration) (lease.Grant, error) {
	g, err := m.Manager.Transfer(ctx, resource, from, to, token-testFenceOffset, ttl)
	g.Token += testFenceOffset
	return g, err
}
func (m largeTokenLeases) Validate(ctx context.Context, resource, owner string, token uint64) error {
	return m.Manager.Validate(ctx, resource, owner, token-testFenceOffset)
}
func (m largeTokenLeases) Release(ctx context.Context, resource, owner string, token uint64) error {
	return m.Manager.Release(ctx, resource, owner, token-testFenceOffset)
}

// This is a real HTTP/SQLite protocol test, not Gazebo or physical acceptance.
// Execution has already succeeded when the injected network fault occurs.
func TestCompletionResponseLostAfterRevisionActivationSurvivesBothRestarts(t *testing.T) {
	ctx := context.Background()
	cloudPath, edgePath := filepath.Join(t.TempDir(), "cloud.db"), filepath.Join(t.TempDir(), "edge.db")
	cloudDB, err := sqlite.Open(cloudPath)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cloudDB.Close() }()
	edgeDB, err := sqlite.Open(edgePath)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = edgeDB.Close() }()
	service := tasks.NewService(cloudDB, intent.NewDeterministicParser())
	newCoordinator := func() *coordinator.Coordinator {
		return coordinator.NewWithStore(service, time.Minute, cloudDB).
			WithResourceLeases(largeTokenLeases{lease.NewMemoryManager()}, time.Minute).
			WithCatalogLookup(func(context.Context, string) (string, error) { return "fixture-catalog", nil })
	}
	coord := newCoordinator()
	task, err := service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err == nil {
		task, err = service.Approve(ctx, task.ID, "test-operator")
	}
	if err != nil {
		t.Fatal(err)
	}
	authentication, err := auth.New(auth.Options{OperatorUser: "operator", OperatorPass: "test-pass", DeviceCredentials: map[string]string{"robot-1": "test-device-secret"}})
	if err != nil {
		t.Fatal(err)
	}
	var dropResponse atomic.Bool
	dropResponse.Store(true)
	startServer := func() *httptest.Server {
		handler := fleet.NewServer(service, nil, fleet.WithAuthenticator(authentication), fleet.WithCoordinator(coord)).Handler()
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if strings.HasSuffix(r.URL.Path, "/complete") && dropResponse.CompareAndSwap(true, false) {
				recorded := httptest.NewRecorder()
				handler.ServeHTTP(recorded, r)
				if recorded.Code != http.StatusOK {
					t.Errorf("completion before response loss: %d %s", recorded.Code, recorded.Body)
				}
				connection, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				_ = connection.Close()
				return
			}
			handler.ServeHTTP(w, r)
		}))
	}
	server := startServer()
	defer func() { server.Close() }()
	newClient := func() *cloudclient.Client {
		client, err := cloudclient.New(cloudclient.Config{BaseURL: server.URL, RobotID: "robot-1", DeviceToken: "test-device-secret"})
		if err != nil {
			t.Fatal(err)
		}
		return client
	}
	client := newClient()
	node, err := client.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || node == nil {
		t.Fatalf("claim=%v err=%v", node, err)
	}
	if node.FencingToken <= 1<<53 {
		t.Fatal("test requires the production-sized uint64 fence")
	}
	task, _ = client.GetTask(ctx, task.ID)
	worker := New(Config{RobotID: "robot-1", Cloud: client, ExecutionStore: edgeDB})
	if _, err := worker.acceptContext(ctx, task, node); err != nil {
		t.Fatal(err)
	}
	proposal, err := coord.ProposeRevision(ctx, tasks.ProposeRevisionCommand{TaskID: task.ID, ExpectedRevision: 1,
		Request: "让1号机器人把蓝色杯子放进左侧收纳盒", IdempotencyKey: "next-revision", Creator: "test-operator"})
	if err != nil {
		t.Fatal(err)
	}
	confirmed, err := coord.ConfirmRevision(ctx, task.ID, proposal.Revision.Revision, 1, "confirm-next")
	if err != nil || confirmed.Status != tasks.RevisionWaitingSafePoint {
		t.Fatalf("confirmation=%v err=%v", confirmed, err)
	}
	if err := worker.markContextExecuted(ctx, task.ID, node); err != nil {
		t.Fatal(err)
	}
	if err := worker.completeContext(ctx, task.ID, node); !errors.Is(err, ErrCompletionPending) {
		t.Fatalf("lost HTTP completion err=%v", err)
	}
	snapshot, err := coord.Snapshot(ctx, task.ID)
	if err != nil || snapshot.TaskRevision != 2 || snapshot.Intents[0].Status != coordinator.StatusReady {
		t.Fatalf("activated revision=%+v err=%v", snapshot, err)
	}
	_, saved, found, err := worker.loadContext(ctx, task.ID)
	if err != nil || !found || saved.Phase != contextExecuted {
		t.Fatalf("pending local checkpoint=%+v err=%v", saved, err)
	}

	// Neither process retains its in-memory task/intent/checkpoint state.
	server.Close()
	if err := cloudDB.Close(); err != nil {
		t.Fatal(err)
	}
	if err := edgeDB.Close(); err != nil {
		t.Fatal(err)
	}
	cloudDB, err = sqlite.Open(cloudPath)
	if err != nil {
		t.Fatal(err)
	}
	edgeDB, err = sqlite.Open(edgePath)
	if err != nil {
		t.Fatal(err)
	}
	service = tasks.NewService(cloudDB, intent.NewDeterministicParser())
	coord = newCoordinator()
	server = startServer()
	client = newClient()
	runtime := &learnedRuntime{}
	worker = New(Config{RobotID: "robot-1", Cloud: client, Runtime: runtime, ExecutionStore: edgeDB})
	for range 2 {
		if err := worker.dispatchContextOutbox(ctx); err != nil {
			t.Fatal(err)
		}
	}
	_, saved, found, err = worker.loadContext(ctx, task.ID)
	if err != nil || !found || saved.Phase != contextCompleted || len(runtime.commands) != 0 {
		t.Fatalf("replayed checkpoint=%+v physical calls=%d err=%v", saved, len(runtime.commands), err)
	}
	entries, err := edgeDB.ClaimOutbox(ctx, 10)
	if err != nil || len(entries) != 0 {
		t.Fatalf("unacked completions=%v err=%v", entries, err)
	}
	snapshot, err = coord.Snapshot(ctx, task.ID)
	if err != nil || snapshot.TaskRevision != 2 || snapshot.Intents[0].Status != coordinator.StatusReady {
		t.Fatalf("old receipt advanced new graph: %+v err=%v", snapshot, err)
	}
	// Compatibility is explicit: without the basis, an old revision must not
	// borrow its replacement node's identity or receipt.
	legacy := *node
	legacy.ContextBasis = nil
	if err := client.CompleteIntentRevision(ctx, task.ID, &legacy, "robot-1"); err == nil {
		t.Fatal("legacy completion advanced or acknowledged a superseded revision")
	}
	next, err := client.NextIntent(ctx, task.ID, "robot-1")
	if err != nil || next == nil || next.TaskRevision != 2 {
		t.Fatalf("new revision remains blocked: next=%+v err=%v", next, err)
	}
}
