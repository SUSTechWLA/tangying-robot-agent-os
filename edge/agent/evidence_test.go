package agent_test

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type evidenceRobot struct {
	recordingRobot
	captures   int
	original   bool
	failVerify bool
}

func (r *evidenceRobot) Invoke(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	result, err := r.recordingRobot.Invoke(ctx, command)
	result.ObservationID = "runtime-receipt/" + command.StepID
	if r.original {
		// A real runtime stamps the capture it returns. Without an acquisition
		// time the command cannot prove it changed the world, so the closed-loop
		// gate would refuse it.
		observedAt := time.Now().UTC()
		result.Evidence = &telemetry.Snapshot{
			ObservedAt: observedAt,
			Reconstruction: &robotcontract.Reconstruction{
				ObservationID: result.ObservationID, SourceID: "test-robot/scene",
				ObservedAtUnixMS: observedAt.UnixMilli(),
			},
			Frame: []byte("verification-rgb"), DepthFrame: []byte("verification-depth"),
		}
	}
	if r.failVerify && string(command.Capability) == "verify_placement" {
		result.Success, result.Code = false, "PLACEMENT_UNSTABLE"
	}
	return result, err
}

func TestFailedVerificationRetainsItsOriginalEvidence(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "failed-evidence", Intent: parsed, Approved: true, CurrentRevision: 1}
	robot := &evidenceRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, original: true, failVerify: true}
	runner := agent.NewRunner(store, robot, robot)
	var failedSaved bool
	runner.Telemetry = func(_ context.Context, value telemetry.Snapshot) error {
		if value.Reconstruction.ObservationID == "runtime-receipt/verify_place" {
			failedSaved = true
		}
		return nil
	}
	var failedEvent *tasks.TaskEvent
	runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
		if event.Payload["activityStatus"] == "FAILED" {
			failedEvent = &event
		}
		return nil
	}
	if _, err := runner.Run(t.Context(), task); err == nil {
		t.Fatal("unstable placement accepted")
	}
	if !failedSaved || failedEvent == nil {
		t.Fatalf("failed verification image was lost: saved=%v event=%v", failedSaved, failedEvent)
	}
	ids, _ := failedEvent.Payload["evidenceIds"].([]string)
	if len(ids) != 1 || ids[0] != failedEvent.Payload["receiptObservationId"] || failedEvent.Payload["evidenceSource"] != "command_observation" {
		t.Fatalf("failure does not identify its verification capture: %+v", failedEvent)
	}
}

func TestCommandEvidenceIsSavedWithoutPollingAnUnrelatedPostToolFrame(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	task := &tasks.Task{ID: "exact-evidence", Intent: parsed, Approved: true, CurrentRevision: 2}
	robot := &evidenceRobot{recordingRobot: recordingRobot{counts: map[string]int{}}, original: true}
	runner := agent.NewRunner(store, robot, robot)
	var saved []telemetry.Snapshot
	runner.Telemetry = func(_ context.Context, value telemetry.Snapshot) error {
		if value.StepID != "revision-2/grounded" {
			saved = append(saved, value)
		}
		return nil
	}
	runner.TaskEvents = func(_ context.Context, _ string, event tasks.TaskEvent) error {
		if event.Payload["activityStatus"] == "CONFIRMED" {
			ids, _ := event.Payload["evidenceIds"].([]string)
			if len(ids) != 1 || ids[0] != event.Payload["receiptObservationId"] || event.Payload["evidenceSource"] != "command_observation" {
				t.Errorf("verification was associated with a different capture: %+v", event.Payload)
			}
		}
		return nil
	}
	if _, err := runner.Run(t.Context(), task); err != nil {
		t.Fatal(err)
	}
	if robot.captures != 1 {
		t.Errorf("polled %d new frames; only grounding should poll", robot.captures)
	}
	if len(saved) != 7 {
		t.Fatalf("saved %d command observations", len(saved))
	}
	for _, snapshot := range saved {
		if string(snapshot.Frame) != "verification-rgb" || string(snapshot.DepthFrame) != "verification-depth" || snapshot.TaskID != task.ID || snapshot.TaskRevision != 2 {
			t.Fatalf("original capture or task identity replaced: %+v", snapshot)
		}
	}
}

func (r *evidenceRobot) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	r.captures++
	return telemetry.Snapshot{TaskID: "wrong-provider-task", StepID: "wrong-provider-step", TaskRevision: 99,
		Reconstruction: &robotcontract.Reconstruction{ObservationID: fmt.Sprintf("capture-%d", r.captures)},
		Frame:          []byte("color"), DepthFrame: []byte("depth"),
	}, nil
}

func TestConfirmedEvidenceUsesSamePersistedPostToolCaptureAndTaskRevision(t *testing.T) {
	for _, persistFails := range []bool{false, true} {
		t.Run(fmt.Sprint(persistFails), func(t *testing.T) {
			store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			parsed, _ := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
			task := &tasks.Task{ID: "task-evidence", Intent: parsed, Approved: true, CurrentRevision: 3}
			robot := &evidenceRobot{recordingRobot: recordingRobot{counts: map[string]int{}}}
			runner := agent.NewRunner(store, robot, robot)
			saved := map[string]string{}
			runner.Telemetry = func(_ context.Context, snapshot telemetry.Snapshot) error {
				if snapshot.TaskID != task.ID || snapshot.TaskRevision != 3 {
					t.Fatalf("wrong task attribution: %+v", snapshot)
				}
				if persistFails {
					return errors.New("evidence disk unavailable")
				}
				saved[snapshot.StepID] = snapshot.Reconstruction.ObservationID
				return nil
			}
			confirmed := 0
			runner.TaskEvents = func(_ context.Context, taskID string, event tasks.TaskEvent) error {
				if event.Payload["activityStatus"] != "CONFIRMED" {
					return nil
				}
				confirmed++
				if taskID != task.ID || event.Payload["receiptObservationId"] != "runtime-receipt/"+event.StepID {
					t.Fatalf("lost independent receipt identity: %+v", event)
				}
				ids, _ := event.Payload["evidenceIds"].([]string)
				if persistFails {
					if len(ids) != 0 {
						t.Fatalf("unpersisted image claimed as history: %v", ids)
					}
				} else if len(ids) != 1 || ids[0] != saved["revision-3/"+event.StepID] {
					t.Fatalf("event does not reference same post-tool capture: event=%+v saved=%v", event, saved)
				}
				return nil
			}
			if _, err := runner.Run(context.Background(), task); err != nil {
				t.Fatal(err)
			}
			if confirmed != 7 {
				t.Fatalf("confirmed steps=%d", confirmed)
			}
		})
	}
}
