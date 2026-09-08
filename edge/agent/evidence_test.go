package agent_test

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"testing"

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
	captures int
}

func (r *evidenceRobot) Invoke(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	result, err := r.recordingRobot.Invoke(ctx, command)
	result.ObservationID = "runtime-receipt/" + command.StepID
	return result, err
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
