package console_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func localProgressEvent(step, tool, status string, revision uint64, captures ...string) tasks.TaskEvent {
	return tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: step, Payload: map[string]any{
		"stepId": step, "toolName": tool, "activityStatus": status, "taskRevision": revision, "evidenceIds": captures,
	}}
}

func readLocalProgress(t *testing.T, events []tasks.TaskEvent, revision uint64, count int, state taskgraph.TaskState) tasks.TaskExperience {
	t.Helper()
	ctx := context.Background()
	store := tasks.NewMemoryStore()
	parsed := manipulation.Intent{Action: manipulation.ActionPickAndPlace}
	steps := make([]tasks.RevisionStep, count)
	if count > 1 {
		parsed.Sequence = make([]manipulation.Intent, count)
	}
	for index := range steps {
		steps[index] = tasks.RevisionStep{StepID: tasks.StableStepID(index, "abc123"), IntentIndex: index, Action: manipulation.ActionPickAndPlace, Status: tasks.StepPending}
	}
	task := &tasks.Task{ID: "local-progress", CurrentRevision: revision, RevisionState: tasks.RevisionActive, Intent: parsed, State: state, Events: events}
	if err := store.CreateWithRevision(ctx, task, &tasks.TaskRevision{TaskID: task.ID, Revision: revision, Intent: parsed, Steps: steps}); err != nil {
		t.Fatal(err)
	}
	server := console.NewServer(tasks.NewService(store, intent.NewDeterministicParser()), &executorSpy{})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest("GET", "/v1/tasks/"+task.ID+"/experience", nil))
	if response.Code != 200 {
		t.Fatalf("experience: %d %s", response.Code, response.Body.String())
	}
	var view tasks.TaskExperience
	if err := json.Unmarshal(response.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	// The projection is read-only; it must not rewrite saved plans or evidence.
	saved, err := store.Revision(ctx, task.ID, revision)
	if err != nil {
		t.Fatal(err)
	}
	for _, step := range saved.Revision.Steps {
		if step.Status != tasks.StepPending || len(step.HarnessEvidenceIDs) != 0 {
			t.Fatalf("experience mutated saved revision: %#v", step)
		}
	}
	return view
}

func TestLocalExperienceReplaysActualNavigationIntoItsOwnSubtask(t *testing.T) {
	// Reduced durable events from mapping-continuous-3, through sequence 13:
	// two confirmed reads followed by task01-navigate RUNNING.
	wire, err := os.ReadFile("testdata/local_navigation_running_events.json")
	if err != nil {
		t.Fatal(err)
	}
	var events []tasks.TaskEvent
	if err := json.Unmarshal(wire, &events); err != nil {
		t.Fatal(err)
	}
	view := readLocalProgress(t, events, 1, 2, taskgraph.StateExecuting)
	if view.Steps[0].Status != tasks.StepRunning || view.Steps[1].Status != tasks.StepPending || view.Steps[0].StatusText != "正在执行" {
		t.Fatalf("navigation progress: %#v", view.Steps)
	}
	if view.Steps[0].EvidenceText == "等待环境证据" {
		t.Fatalf("running subtask still displayed as unstarted: %#v", view.Steps[0])
	}
}

func TestLocalExperienceRequiresEachFinalPlacementConfirmation(t *testing.T) {
	events := []tasks.TaskEvent{localProgressEvent("task01-pick", "manipulation.pick", "CONFIRMED", 1, "grasp")}
	assertStates := func(first, second tasks.StepStatus) {
		t.Helper()
		view := readLocalProgress(t, events, 1, 2, taskgraph.StateExecuting)
		if view.Steps[0].Status != first || view.Steps[1].Status != second {
			t.Fatalf("want %s/%s, got %#v", first, second, view.Steps)
		}
	}
	assertStates(tasks.StepRunning, tasks.StepPending)
	events = append(events, localProgressEvent("task01-place", "manipulation.place", "CONFIRMED", 1, "release"))
	assertStates(tasks.StepAwaitingEvidence, tasks.StepPending)
	events = append(events, localProgressEvent("task01-verify_place", "verify_placement", "CONFIRMED", 1))
	assertStates(tasks.StepAwaitingEvidence, tasks.StepPending)
	events = append(events, localProgressEvent("task01-verify_place", "verify_placement", "CONFIRMED", 1, "placement-1"))
	assertStates(tasks.StepSatisfied, tasks.StepPending)
	events = append(events, localProgressEvent("task02-navigate", "navigation.navigate", "RUNNING", 1))
	assertStates(tasks.StepSatisfied, tasks.StepRunning)
	// A resumed run refreshes read-only steps of the already completed first
	// subtask. This must not erase its confirmed placement or finish subtask 2.
	events = append(events, localProgressEvent("task01-observe/resume-read/after-65s", "observe_scene", "CONFIRMED", 1, "fresh-read"))
	assertStates(tasks.StepSatisfied, tasks.StepRunning)
	events = append(events, localProgressEvent("task02-verify_place", "verify_placement", "RUNNING", 1))
	assertStates(tasks.StepSatisfied, tasks.StepAwaitingEvidence)
	events = append(events, localProgressEvent("task02-verify_place", "verify_placement", "CONFIRMED", 1, "placement-2"))
	assertStates(tasks.StepSatisfied, tasks.StepSatisfied)
	view := readLocalProgress(t, events, 1, 2, taskgraph.StateSucceeded)
	if len(view.Professional.StepEvidence) != 0 {
		t.Fatalf("local camera capture was mislabeled as coordinator Harness evidence: %#v", view.Professional.StepEvidence)
	}
}

func TestLocalExperienceDoesNotPromoteOldOrUnrelatedEvidence(t *testing.T) {
	for _, test := range []struct {
		name  string
		event tasks.TaskEvent
	}{
		{"previous revision", localProgressEvent("task01-verify_place", "verify_placement", "CONFIRMED", 1, "old")},
		{"wrong tool", localProgressEvent("task01-verify_place", "observe_scene", "CONFIRMED", 2, "not-placement")},
		{"wrong subtask", localProgressEvent("task03-verify_place", "verify_placement", "CONFIRMED", 2, "other")},
		{"unscoped single task", localProgressEvent("verify_place", "verify_placement", "CONFIRMED", 2, "ambiguous")},
	} {
		t.Run(test.name, func(t *testing.T) {
			view := readLocalProgress(t, []tasks.TaskEvent{test.event}, 2, 2, taskgraph.StateSucceeded)
			for _, step := range view.Steps {
				if step.Status != tasks.StepPending {
					t.Fatalf("unrelated evidence or overall success promoted step: %#v", step)
				}
			}
		})
	}
}

func TestLocalExperienceShowsFailureAndSingleIntentProgress(t *testing.T) {
	events := []tasks.TaskEvent{localProgressEvent("navigate", "navigation.navigate", "FAILED", 1, "failure-observation")}
	view := readLocalProgress(t, events, 1, 1, taskgraph.StateRecoverableFailure)
	if view.Steps[0].Status != tasks.StepFailed {
		t.Fatalf("navigation failure was hidden: %#v", view.Steps)
	}
	events = append(events, localProgressEvent("verify_place/resume-read/after-65s", "verify_placement", "CONFIRMED", 1, "placement"))
	view = readLocalProgress(t, events, 1, 1, taskgraph.StateSucceeded)
	if view.Steps[0].Status != tasks.StepSatisfied {
		t.Fatalf("single-intent final verification not projected: %#v", view.Steps)
	}
}
