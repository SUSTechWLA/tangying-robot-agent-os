package recoveryexec_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Match the run-002 failure shape: a 335-event ledger contains many raw/direct
// mirrors with the same event IDs but different command parameters/fence fields,
// followed by a physical pick failure. The conflicts must remain unresolved.
func conflictLedger335() (tasks.Task, []tasks.RevisionRecord) {
	task := tasks.Task{ID: "task-79f7e1765770f7b7de3e61db", Request: "巡检五处，抓放杯子并逐步复验；未知动作禁止重放", CurrentRevision: 1,
		State: taskgraph.StateRecoverableFailure, Intent: manipulation.Intent{RobotID: "gazebo-home_furnished"}}
	for commandIndex := 0; commandIndex < 25; commandIndex++ {
		step := fmt.Sprintf("rev-1-cap-%02d-verify_arrival_00", commandIndex+1)
		command := task.ID + "/revision/1/step/" + step
		for transition, status := range []string{"SENDING", "RUNNING", "AWAITING_EVIDENCE", "CONFIRMED"} {
			if commandIndex == 24 && status == "CONFIRMED" {
				status = "FAILED"
			}
			id := fmt.Sprintf("%s/action/fact-%d-%d", task.ID, commandIndex, transition)
			for _, kind := range []string{"action.executed", "TOOL_ACTIVITY"} {
				payload := map[string]any{"eventId": id, "commandId": command, "robotId": task.Intent.RobotID, "taskRevision": 1,
					"stepId": step, "toolName": "navigation.navigate", "activityStatus": status, "mutatesWorld": true}
				if kind == "TOOL_ACTIVITY" {
					payload["arguments"], payload["fencingToken"] = map[string]any{"goalPose": []float64{1, 2, 0, 1, 0, 0, 0}}, 0
				} else {
					payload["arguments"] = nil
				}
				if status == "FAILED" {
					payload["error"] = "GRASP_TARGET_UNREACHABLE"
				}
				task.Events = append(task.Events, tasks.TaskEvent{Type: kind, StepID: step, Payload: payload})
			}
			task.Events = append(task.Events, tasks.TaskEvent{Type: "agent.permission_denied", Payload: map[string]any{"reasonCode": "EVENT_ID_CONFLICT", "rejectedEventId": id}})
		}
	}
	for len(task.Events) < 335 {
		task.Events = append(task.Events, tasks.TaskEvent{Type: "ops.root_cause_hypothesis", Payload: map[string]any{"summary": "读取现状，不重做未知动作"}})
	}
	for i := range task.Events {
		task.Events[i].Sequence = uint64(i + 1)
	}
	revision := tasks.RevisionRecord{Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 1, Request: task.Request, Intent: task.Intent}, Status: tasks.RevisionActive}
	return task, []tasks.RevisionRecord{revision}
}

func exerciseLayeredLedgerRecovery(t *testing.T, task tasks.Task, revisions []tasks.RevisionRecord) {
	t.Helper()
	t.Setenv("TANGYING_CONTEXT_MAX_BYTES", "32768")
	t.Setenv("TANGYING_MODEL_REQUEST_MAX_BYTES", "65536")
	d := tasks.ContextForRevisions(task, revisions, "recovery", time.Unix(1700000000, 0))
	// Prove this fixture exercises the original cause, not just a large optional
	// diagnostic paragraph: putting full variants back in guard must fail.
	old := d
	old.Records = append([]agentcontext.Record(nil), d.Records...)
	for i := range old.Records {
		if strings.HasPrefix(old.Records[i].ID, "memory:action:") {
			old.Records[i].Kind = "guard"
		}
	}
	if _, err := agentcontext.ProjectManaged(old, "recovery", agentcontext.DefaultBudget()); !errors.Is(err, agentcontext.ErrContextBudget) {
		t.Fatalf("fixture does not reproduce the required-state overflow: %v", err)
	}
	for _, actionID := range []string{"execution.read-history", "observe.re-read"} {
		t.Run(actionID, func(t *testing.T) {
			reads := 0
			// Use the production stepRunDetail field shape and a realistic long
			// task count, not a tiny success stub that conceals second-round size.
			var steps []any
			for i := 0; i < 35; i++ {
				steps = append(steps, map[string]any{"stepId": fmt.Sprintf("rev-1-cap-%02d-verify_arrival_00", i),
					"status": "STARTED", "capability": "navigation.navigate", "safetyLevel": "physical_motion"})
			}
			read := func(ctx context.Context) (actionloop.Result, error) {
				reads++
				if recoveryexec.TaskIDFrom(ctx) != task.ID {
					t.Fatal("diagnostic widened or lost task scope")
				}
				if actionID == "observe.re-read" {
					return actionloop.Result{Success: true, Message: "已读取遥测"}, nil
				}
				return actionloop.Result{Success: true, Message: "已读取 35 条执行记录", Detail: map[string]any{"stepCount": len(steps), "steps": steps}}, nil
			}
			registry := recoveryexec.LocalTools{ReadTelemetry: read, ReadHistory: func(ctx context.Context, args map[string]any) (actionloop.Result, error) {
				if len(args) != 0 {
					t.Fatal("diagnostic invented query parameters")
				}
				return read(ctx)
			}}.Registry()
			executor := recoveryexec.Executor{Registry: registry, Verify: recoveryexec.VerifyReadOnly(registry),
				DeciderProvider: func() actionloop.Decider { t.Fatal("parameterless read requested a model"); return nil },
				Observer: observerFunc(func(context.Context, string) (actionloop.Observation, error) {
					return actionloop.Observation{Context: &d, Summary: fmt.Sprintf("任务 %s 当前状态 %s，原始要求：%s", task.ID, task.State, task.Request), EvidenceIDs: []string{task.ID}}, nil
				}),
			}
			before, _ := json.Marshal(d)
			result, err := executor.Execute(context.Background(), recoveryexec.Request{TaskID: task.ID, PlanID: "diagnostic-only", Action: action(t, actionID)})
			if err != nil || reads != 2 || !result.Executed || !result.Verified || len(result.Rounds) != 2 {
				t.Fatalf("335-event context blocked diagnostic/finish/independent reread: reads=%d executed=%v verified=%v rounds=%d err=%v", reads, result.Executed, result.Verified, len(result.Rounds), err)
			}
			after, _ := json.Marshal(d)
			if string(before) != string(after) {
				t.Fatal("diagnostic rewrote durable source context")
			}
			for _, round := range result.Rounds {
				p := round.Context
				if p == nil || len(p.Text) > 32768 || len(p.Artifacts) == 0 || p.Scope.TaskID != task.ID {
					t.Fatal("missing bounded, scoped and retrievable source")
				}
				t.Logf("%s round=%d context_bytes=%d archived_records=%d", actionID, round.Round, len(p.Text), p.Compaction.ArchivedRecords)
				var view agentcontext.Document
				if err := json.Unmarshal([]byte(p.Text), &view); err != nil {
					t.Fatal(err)
				}
				allSources := map[string]agentcontext.Record{}
				for _, r := range view.Records {
					allSources[r.ID] = r
				}
				for _, artifact := range p.Artifacts {
					if artifact.Kind != "records" {
						continue
					}
					data, err := artifact.SourceBytes()
					if err != nil {
						t.Fatal(err)
					}
					var records []agentcontext.Record
					if err := json.Unmarshal(data, &records); err != nil {
						t.Fatal(err)
					}
					for _, r := range records {
						allSources[r.ID] = r
					}
				}
				guards := 0
				for _, r := range view.Records {
					if !strings.HasPrefix(r.ID, "memory:command:") {
						continue
					}
					guards++
					var guard struct {
						Unresolved     bool     `json:"unresolved_dispatch"`
						RetryForbidden bool     `json:"automatic_retry_forbidden"`
						Sources        []string `json:"source_record_ids"`
					}
					if err := json.Unmarshal([]byte(r.Statement[strings.Index(r.Statement, "{"):]), &guard); err != nil {
						t.Fatal(err)
					}
					if r.Kind != "guard" || !guard.Unresolved || !guard.RetryForbidden || len(guard.Sources) == 0 {
						t.Fatal("unresolved execution boundary was weakened")
					}
					for _, id := range guard.Sources {
						if _, ok := allSources[id]; !ok {
							t.Fatalf("guard source %s is not retrievable", id)
						}
					}
				}
				if guards < 20 {
					t.Fatalf("fixture lost disputed physical commands: %d", guards)
				}
				for _, source := range d.Records {
					got, ok := allSources[source.ID]
					left, _ := json.Marshal(source)
					right, _ := json.Marshal(got)
					if !ok || string(left) != string(right) {
						t.Fatalf("original source %s was silently dropped or summarized", source.ID)
					}
				}
			}
		})
	}
}

func Test335EventRecoveryKeepsUnknownGuardsAndReadsWithoutLargerBudget(t *testing.T) {
	task, revisions := conflictLedger335()
	exerciseLayeredLedgerRecovery(t, task, revisions)
}

// Opt-in read-only replay of the unmodified acceptance artifact. The synthetic
// fixture above always runs in CI; this replay ties the same assertions to the
// actual task without making a checkout depend on untracked incident files.
func TestRun002ActualRecoveryContextReplay(t *testing.T) {
	path := os.Getenv("TANGYING_RUN002_CONTEXT_FIXTURE")
	if path == "" {
		t.Skip("set TANGYING_RUN002_CONTEXT_FIXTURE to saved run-002/final-task.json")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var task tasks.Task
	if err := json.Unmarshal(raw, &task); err != nil {
		t.Fatal(err)
	}
	if task.ID != "task-79f7e1765770f7b7de3e61db" || len(task.Events) != 335 {
		t.Fatal("wrong actual-run fixture")
	}
	revisionRaw, err := os.ReadFile(filepath.Join(filepath.Dir(path), "revisions.json"))
	if err != nil {
		t.Fatal(err)
	}
	var history struct {
		Revisions []tasks.RevisionRecord `json:"revisions"`
	}
	if err := json.Unmarshal(revisionRaw, &history); err != nil {
		t.Fatal(err)
	}
	t.Logf("actual_source_sha256=%s revisions_sha256=%s", agentcontext.Hash(string(raw)), agentcontext.Hash(string(revisionRaw)))
	exerciseLayeredLedgerRecovery(t, task, history.Revisions)
}
