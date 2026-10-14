package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/fleetcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/encoding/protojson"
)

type objectiveStack struct {
	worker   *Worker
	service  *tasks.Service
	coord    *coordinator.Coordinator
	server   *httptest.Server
	operator string
	contract objective.Contract
	robot    *serviceRobot
}

func newObjectiveStack(t *testing.T) *objectiveStack {
	t.Helper()
	ctx := context.Background()
	robot := &serviceRobot{}
	catalog, err := robot.ListServices(ctx)
	if err != nil {
		t.Fatal(err)
	}
	wire, _ := protojson.Marshal(catalog)
	reg := registry.New(registry.NewMemoryStore())
	_, err = reg.Register(ctx, registry.Device{RobotID: catalog.RobotId, Adapter: "ros2", ServiceCatalog: wire}, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	// No cloud goal planner is installed. The cloud stores only the envelope.
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	coord := coordinator.New(service).WithResourceLeases(lease.NewMemoryManager(), time.Minute)
	authentication, err := auth.New(auth.Options{OperatorUser: "operator", OperatorPass: "test-pass", DeviceCredentials: map[string]string{catalog.RobotId: "device-secret", "foreign-robot": "other-secret"}})
	if err != nil {
		t.Fatal(err)
	}
	token, _, err := authentication.Login(ctx, "operator", "test-pass")
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(fleet.NewServer(service, nil, fleet.WithAuthenticator(authentication), fleet.WithRegistry(reg), fleet.WithCoordinator(coord)).Handler())
	t.Cleanup(server.Close)
	cloud, err := cloudclient.New(cloudclient.Config{BaseURL: server.URL, RobotID: catalog.RobotId, DeviceToken: "device-secret"})
	if err != nil {
		t.Fatal(err)
	}
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "edge.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	_, entries, err := capabilityagent.Catalogue(ctx, robot)
	if err != nil {
		t.Fatal(err)
	}
	empty := map[string]any{"type": "object", "additionalProperties": false}
	c := objective.Contract{SchemaVersion: objective.Version, RobotID: catalog.RobotId, Goal: "检查标定，建图", CatalogRevision: capability.Fingerprint(entries), Effects: []string{"READ", "PHYSICAL_MOTION", "ARTIFACT_WRITE"}, Resources: []string{"robot"}, Budget: objective.Budget{MaxPlans: 2, MaxSteps: 8, MaxReads: 64, TimeoutSeconds: 60}, ExpiresAt: time.Now().UTC().Add(time.Hour), Grants: []objective.Grant{
		{Tool: "calibration.get", Arguments: empty},
		{Tool: "mapping.build", Arguments: map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"explore"}}}, "required": []any{"mode"}, "additionalProperties": false}},
		{Tool: "mapping.status", Arguments: empty},
	}, Completion: []objective.Check{{Tool: "mapping.status", Arguments: map[string]any{}, Path: []string{"activeMap", "mapRevision"}, Equals: "hash-1"}}}
	return &objectiveStack{worker: New(Config{RobotID: catalog.RobotId, Adapter: "ros2", Cloud: cloud, Runtime: robot, ExecutionStore: store}), service: service, coord: coord, server: server, operator: token, contract: c, robot: robot}
}
func (s *objectiveStack) create(t *testing.T) *tasks.Task {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"objective": s.contract, "adapter": "ros2", "idempotencyKey": "goal-request-001"})
	q, _ := http.NewRequest("POST", s.server.URL+"/v1/objectives", bytes.NewReader(body))
	q.Header.Set("Authorization", "Bearer "+s.operator)
	response, err := http.DefaultClient.Do(q)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 201 {
		wire, _ := io.ReadAll(response.Body)
		t.Fatalf("objective API status=%d body=%s", response.StatusCode, wire)
	}
	var task tasks.Task
	if err = json.NewDecoder(response.Body).Decode(&task); err != nil {
		t.Fatal(err)
	}
	return &task
}
func TestCloudDelegatesGoalToEdgeAndVerifiesChildEvidence(t *testing.T) {
	s := newObjectiveStack(t)
	task := s.create(t)
	if len(task.Plan.Capabilities.Calls) != 1 || task.Plan.Capabilities.Calls[0].Tool != objective.Tool || task.Approved {
		t.Fatal("cloud planned child tools or auto-approved delegation")
	}
	if duplicate := s.create(t); duplicate.ID != task.ID {
		t.Fatal("HTTP goal retry created another parent")
	}
	if _, err := s.coord.NextIntent(context.Background(), task.ID, s.contract.RobotID); err == nil {
		t.Fatal("unapproved objective claimed")
	}
	if _, err := s.service.Approve(context.Background(), task.ID, "test-operator"); err != nil {
		t.Fatal(err)
	}
	if err := s.worker.processTask(context.Background(), task.ID); err != nil {
		t.Fatal(err)
	}
	final, err := s.service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if final.State != "SUCCEEDED" || s.robot.starts != 1 {
		t.Fatalf("state=%s actions=%d", final.State, s.robot.starts)
	}
	snapshot, _ := s.coord.Snapshot(context.Background(), task.ID)
	if len(snapshot.Intents) != 1 || snapshot.Intents[0].FencingToken == 0 {
		t.Fatal("parent goal did not retain robot lease")
	}
	planSeen, proofSeen := false, false
	for _, e := range final.Events {
		planSeen = planSeen || e.Type == "EDGE_PLAN_CREATED"
		proofSeen = proofSeen || (e.Type == "CAPABILITY_VERIFIED" && e.Payload["tool"] == objective.Tool)
	}
	if !planSeen || !proofSeen {
		t.Fatal("edge plan lineage or objective proof missing")
	}
	if err = s.worker.processTask(context.Background(), task.ID); err != nil || s.robot.starts != 1 {
		t.Fatalf("goal replayed: %v", err)
	}
	t.Logf("HTTP operator/device auth; coarse_goal=%q parent_nodes=1 edge_steps=2 map=hash-1 physical_starts=1 duplicate_replays=0", s.contract.Goal)
}
func TestObjectiveAPIDeniesDeviceCreationAndInvalidContracts(t *testing.T) {
	s := newObjectiveStack(t)
	for _, mode := range []string{"device", "effects", "catalog", "scope", "extra-json"} {
		t.Run(mode, func(t *testing.T) {
			c, _ := objective.Decode(s.contract.Call())
			if mode == "effects" {
				c.Effects = []string{"READ"}
			}
			if mode == "catalog" {
				c.CatalogRevision = capability.Fingerprint("wrong")
			}
			if mode == "scope" {
				c.Resources = []string{"shared-object"}
			}
			wire, _ := json.Marshal(map[string]any{"objective": c, "adapter": "ros2"})
			if mode == "extra-json" {
				wire = append(wire, []byte(" {}")...)
			}
			q, _ := http.NewRequest("POST", s.server.URL+"/v1/objectives", bytes.NewReader(wire))
			if mode == "device" {
				q.Header.Set("X-Device-Token", "device-secret")
				q.Header.Set("X-Robot-Id", c.RobotID)
			} else {
				q.Header.Set("Authorization", "Bearer "+s.operator)
			}
			response, err := http.DefaultClient.Do(q)
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if response.StatusCode < 400 {
				t.Fatalf("invalid mode %s accepted", mode)
			}
		})
	}
}
func TestObjectiveCompletionRejectsInventedFinalProof(t *testing.T) {
	s := newObjectiveStack(t)
	task := s.create(t)
	_, _ = s.service.Approve(context.Background(), task.ID, "test-operator")
	node, err := s.coord.NextIntent(context.Background(), task.ID, s.contract.RobotID)
	if err != nil {
		t.Fatal(err)
	}
	events := cloudEvents{cloud: s.worker.config.Cloud, task: task, node: node, robot: s.contract.RobotID}
	_, err = events.AppendEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "CAPABILITY_VERIFIED", StepID: node.StepID, Payload: map[string]any{"tool": objective.Tool, "evidence": map[string]any{"objectiveDigest": capability.Fingerprint(s.contract), "checks": []any{map[string]any{"satisfied": true}}}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.coord.CompleteIntent(context.Background(), task.ID, node.Index, s.contract.RobotID); !errors.Is(err, coordinator.ErrIntentUnverifiable) {
		t.Fatalf("forged proof accepted: %v", err)
	}
}

type lostGoalCloud struct {
	TaskCloud
	lost  bool
	after bool
}

func (c *lostGoalCloud) RenewIntentRevision(ctx context.Context, id string, node *fleetcontract.IntentNode, robot string) error {
	return c.TaskCloud.(claimRenewer).RenewIntentRevision(ctx, id, node, robot)
}
func (c *lostGoalCloud) AppendEvent(ctx context.Context, id, kind, step, message string, payload map[string]any) error {
	if kind == "CAPABILITY_VERIFIED" && payload["tool"] == objective.Tool && !c.lost {
		c.lost = true
		if c.after {
			if err := c.TaskCloud.AppendEvent(ctx, id, kind, step, message, payload); err != nil {
				return err
			}
		}
		return errors.New("lost objective result response")
	}
	return c.TaskCloud.AppendEvent(ctx, id, kind, step, message, payload)
}
func TestObjectiveLostReceiptResumesAfterWorkerRestartWithoutActionReplay(t *testing.T) {
	for _, after := range []bool{false, true} {
		t.Run(map[bool]string{false: "before-commit", true: "after-commit"}[after], func(t *testing.T) {
			s := newObjectiveStack(t)
			task := s.create(t)
			_, _ = s.service.Approve(context.Background(), task.ID, "test-operator")
			s.worker.config.Cloud = &lostGoalCloud{TaskCloud: s.worker.config.Cloud, after: after}
			if err := s.worker.processTask(context.Background(), task.ID); !errors.Is(err, ErrCompletionPending) {
				t.Fatalf("lost receipt did not remain pending: %v", err)
			}
			if s.robot.starts != 1 {
				t.Fatal("goal did not execute exactly once")
			}
			restarted := New(s.worker.config)
			if err := restarted.processTask(context.Background(), task.ID); err != nil {
				t.Fatal(err)
			}
			final, _ := s.service.Get(context.Background(), task.ID)
			if final.State != "SUCCEEDED" || s.robot.starts != 1 {
				t.Fatalf("restart state=%s physical starts=%d", final.State, s.robot.starts)
			}
		})
	}
}

func TestDelegatedCancellationStopsOwnedOperationBeforeCloudTerminalState(t *testing.T) {
	s := newObjectiveStack(t)
	s.robot.keepRunning = true
	task := s.create(t)
	_, _ = s.service.Approve(context.Background(), task.ID, "test-operator")
	done := make(chan error, 1)
	go func() { done <- s.worker.processTask(context.Background(), task.ID) }()
	deadline := time.Now().Add(5 * time.Second)
	for {
		current, _ := s.service.Get(context.Background(), task.ID)
		started := false
		for _, e := range current.Events {
			if e.Type == "EDGE_CHILD_EVENT" && e.Payload["kind"] == "CAPABILITY_PROGRESS" {
				started = true
			}
		}
		if started {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("delegated operation never started")
		}
		time.Sleep(time.Millisecond)
	}
	if _, err := s.coord.AppendTaskEvent(context.Background(), task.ID, tasks.TaskEvent{Type: "CANCEL_REQUESTED"}); err != nil {
		t.Fatal(err)
	}
	s.worker.cancelCurrent(context.Background(), "operator cancellation requested")
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled objective succeeded")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("owned operation failed to stop")
	}
	final, _ := s.service.Get(context.Background(), task.ID)
	if final.State != "CANCELLED" {
		t.Fatalf("cancel state=%s", final.State)
	}
	s.robot.Lock()
	stopped := s.robot.stopped
	s.robot.Unlock()
	if !stopped {
		t.Fatal("cloud cancellation preceded owned stop")
	}
}
