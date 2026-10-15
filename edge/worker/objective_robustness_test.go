package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/fleetcontract"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/edgeagent"
)

type interruptedGoalDecider struct{}

func (interruptedGoalDecider) Decide(context.Context, actionloop.Request) (actionloop.Decision, error) {
	return actionloop.Decision{}, errors.New("injected process exit at planning boundary")
}

type lostStartRobot struct{ *serviceRobot }

func (r lostStartRobot) CallService(ctx context.Context, q *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	response, err := r.serviceRobot.CallService(ctx, q)
	if q.Name == "mapping.build" && err == nil {
		return nil, errors.New("injected transport loss after operation started")
	}
	return response, err
}

// A restart may resume a checkpoint without an in-flight child. The next
// failure still needs the same coordinator classification as an initial run.
func TestObjectiveResumedClaimReportsUnknownOutcome(t *testing.T) {
	s := newObjectiveStack(t)
	task := s.create(t)
	ctx := context.Background()
	task, _ = s.service.Approve(ctx, task.ID, "operator")
	node, err := s.coord.NextIntent(ctx, task.ID, s.contract.RobotID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.worker.acceptContext(ctx, task, node); err != nil {
		t.Fatal(err)
	}
	s.worker.config.GoalDecider = interruptedGoalDecider{}
	if err = s.worker.runCapabilityIntent(ctx, task, node); err == nil {
		t.Fatal("planning interruption did not fire")
	}
	store, _ := s.worker.contextStore()
	if safe, err := edgeagent.CanResumeGoal(ctx, store, task); err != nil || !safe || s.robot.starts != 0 {
		t.Fatalf("not at safe restart boundary: safe=%v starts=%d err=%v", safe, s.robot.starts, err)
	}
	config := s.worker.config
	config.GoalDecider = nil
	config.Runtime = lostStartRobot{s.robot}
	restarted := New(config)
	err = restarted.processTask(ctx, task.ID)
	if !errors.Is(err, ErrHandoffUncertain) {
		t.Errorf("resumed unknown write did not retain handoff classification: %v", err)
	}
	snapshot, _ := s.coord.Snapshot(ctx, task.ID)
	if len(snapshot.Intents) != 1 || snapshot.Intents[0].Status != "UNKNOWN_OUTCOME" {
		t.Fatalf("cloud did not learn unknown outcome after restart: %+v", snapshot.Intents)
	}
	if err = restarted.processTask(ctx, task.ID); !errors.Is(err, ErrHandoffUncertain) || s.robot.starts != 1 {
		t.Fatalf("unknown write replayed: starts=%d err=%v", s.robot.starts, err)
	}
	wire, _ := json.Marshal(map[string]any{"recordType": "task", "scenario": "restart_then_unknown_write", "taskId": task.ID, "evidenceType": "software_http_contract", "physicalStarts": s.robot.starts, "duplicateStarts": 0, "expectedOutcome": "UNKNOWN_OUTCOME", "outcome": snapshot.Intents[0].Status})
	t.Logf("ROBOT_EXPERIMENT %s", wire)
}

type lossyProgressCloud struct {
	TaskCloud
	advisoryDropped int
	proofLost       bool
	afterCommit     bool
}

func (c *lossyProgressCloud) RenewIntentRevision(ctx context.Context, id string, node *fleetcontract.IntentNode, robot string) error {
	return c.TaskCloud.(claimRenewer).RenewIntentRevision(ctx, id, node, robot)
}
func (c *lossyProgressCloud) AppendEvent(ctx context.Context, id, kind, step, message string, payload map[string]any) error {
	if kind == "EDGE_CHILD_EVENT" && payload["kind"] != "CAPABILITY_VERIFIED" {
		c.advisoryDropped++
		return errors.New("injected advisory event loss")
	}
	if kind == "CAPABILITY_VERIFIED" && payload["tool"] == "edge.goal" && !c.proofLost {
		c.proofLost = true
		if c.afterCommit {
			if err := c.TaskCloud.AppendEvent(ctx, id, kind, step, message, payload); err != nil {
				return err
			}
		}
		return errors.New("injected final proof response loss")
	}
	return c.TaskCloud.AppendEvent(ctx, id, kind, step, message, payload)
}

func TestObjectiveSequentialCloudReceiptCampaign(t *testing.T) {
	s := newObjectiveStack(t)
	ctx := context.Background()
	cloud := s.worker.config.Cloud
	for index := 0; index < 12; index++ {
		started := time.Now()
		task, err := s.service.CreateObjectiveOnce(ctx, s.contract, "ros2", fmt.Sprintf("receipt-%02d", index), "campaign-operator")
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.service.Approve(ctx, task.ID, "operator"); err != nil {
			t.Fatal(err)
		}
		loss := &lossyProgressCloud{TaskCloud: cloud, afterCommit: index%2 == 1}
		s.worker.config.Cloud = loss
		before := s.robot.starts
		if err = s.worker.processTask(ctx, task.ID); !errors.Is(err, ErrCompletionPending) {
			t.Fatalf("task %d did not retain completed goal: %v", index, err)
		}
		if !loss.proofLost || loss.advisoryDropped == 0 {
			t.Fatal("faults were not actually injected")
		}
		// Same device and SQLite, reconstructed transport and execution host.
		s.worker = New(s.worker.config)
		if err = s.worker.processTask(ctx, task.ID); err != nil {
			t.Fatal(err)
		}
		final, _ := s.service.Get(ctx, task.ID)
		if final.State != "SUCCEEDED" || s.robot.starts-before != 1 {
			t.Fatalf("task=%d state=%s starts=%d", index, final.State, s.robot.starts-before)
		}
		if err = s.worker.processTask(ctx, task.ID); err != nil || s.robot.starts-before != 1 {
			t.Fatal("completion replayed operation")
		}
		var proof map[string]any
		for _, e := range final.Events {
			if e.Type == "CAPABILITY_VERIFIED" && e.Payload["tool"] == "edge.goal" {
				proof, _ = e.Payload["evidence"].(map[string]any)
			}
		}
		row := map[string]any{"recordType": "task", "scenario": "sequential_cloud_receipt_loss", "evidenceType": "software_http_contract", "taskIndex": index, "taskId": task.ID, "objectiveDigest": capability.Fingerprint(s.contract), "proof": proof, "physicalStarts": s.robot.starts - before, "cumulativeStarts": s.robot.starts, "advisoryEventsDropped": loss.advisoryDropped, "proofResponseLostAfterCommit": loss.afterCommit, "duplicateStarts": 0, "workerRebuilds": 1, "outcome": final.State, "durationScope": "execution_lost_receipt_restart_and_replay", "elapsedSeconds": time.Since(started).Seconds()}
		wire, _ := json.Marshal(row)
		t.Logf("ROBOT_EXPERIMENT %s", wire)
	}
}
