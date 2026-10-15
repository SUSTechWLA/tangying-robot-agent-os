package edgeagent

// This campaign exercises the production edge Host and SQLite journals against
// a deterministic in-process service fixture. It measures software contracts,
// not Gazebo dynamics, real hardware, natural-language understanding or an LLM.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type campaignCall struct {
	Sequence   int            `json:"sequence"`
	Service    string         `json:"service"`
	Arguments  map[string]any `json:"arguments"`
	StartedAt  time.Time      `json:"startedAt"`
	DurationNS int64          `json:"durationNs"`
	Outcome    string         `json:"outcome"`
	Writes     int            `json:"cumulativeWrites"`
}

type campaignRobot struct {
	*missionRobot
	muCampaign sync.Mutex
	rejectOnce map[string]bool
	lostAt     int
	calls      []campaignCall
}

func (r *campaignRobot) CallService(ctx context.Context, q *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	r.muCampaign.Lock()
	defer r.muCampaign.Unlock()
	started := time.Now().UTC()
	outcome := "OK"
	var response *robotv1.ServiceResponse
	var err error
	args := q.Parameters.AsMap()
	target, _ := args["target"].(string)
	if q.Name == "navigation.goto" && args["route"] == "main" && r.rejectOnce[target] {
		delete(r.rejectOnce, target)
		outcome = "REJECTED_BEFORE_EFFECT"
		response = &robotv1.ServiceResponse{Ok: false, Code: "BLOCKED", Message: "commissioned main route is closed", Result: fields(map[string]any{"outcome": "REJECTED"})}
	} else {
		if q.Name == "navigation.goto" && r.lostAt > 0 && len(r.writes)+1 == r.lostAt {
			r.missionRobot.lost = true
		}
		response, err = r.missionRobot.CallService(ctx, q)
		if err != nil {
			outcome = "TRANSPORT_ERROR"
		}
	}
	r.calls = append(r.calls, campaignCall{Sequence: len(r.calls) + 1, Service: q.Name, Arguments: args, StartedAt: started, DurationNS: time.Since(started).Nanoseconds(), Outcome: outcome, Writes: len(r.writes)})
	return response, err
}

// The scripted decider receives no robot or store pointer. Its next chunk is
// derived only from the bounded progress projection supplied to the planner.
type campaignDecider struct {
	targets          []string
	chunk            int
	plans            int
	maxProgressBytes int
	maxRecent        int
	maxCompleted     int
	progress         []json.RawMessage
}

func (d *campaignDecider) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	var progress struct {
		CompletedCount int               `json:"completedCount"`
		CompletedHash  string            `json:"completedDigest"`
		Recent         []completedCall   `json:"recentCompleted"`
		Diagnosis      map[string]any    `json:"diagnosis"`
		Completion     []objective.Check `json:"completion"`
	}
	var raw string
	if request.Observation.Context != nil {
		for _, constraint := range request.Observation.Context.Constraints {
			if index := strings.Index(constraint, "{\"completedCount\""); index >= 0 {
				raw = constraint[index:]
			}
		}
	}
	if raw == "" {
		return actionloop.Decision{}, errors.New("scripted campaign lacks the host progress projection")
	}
	if err := json.Unmarshal([]byte(raw), &progress); err != nil {
		return actionloop.Decision{}, err
	}
	if len(progress.CompletedHash) != 64 || len(progress.Recent) > 8 || progress.CompletedCount < len(progress.Recent) {
		return actionloop.Decision{}, errors.New("invalid bounded progress projection")
	}
	d.plans++
	d.maxProgressBytes = max(d.maxProgressBytes, len(raw))
	d.maxRecent = max(d.maxRecent, len(progress.Recent))
	d.maxCompleted = max(d.maxCompleted, progress.CompletedCount)
	d.progress = append(d.progress, json.RawMessage(raw))
	start := progress.CompletedCount
	end := min(start+d.chunk, len(d.targets))
	if start >= end {
		return actionloop.Decision{}, errors.New("no remaining scripted target; completion cannot be fabricated")
	}
	rejectedTarget := ""
	if args, ok := progress.Diagnosis["arguments"].(map[string]any); ok {
		rejectedTarget, _ = args["target"].(string)
	}
	var calls []any
	for _, target := range d.targets[start:end] {
		route := "main"
		if target == rejectedTarget {
			route = "detour"
		}
		calls = append(calls, map[string]any{"tool": "navigation.goto", "arguments": map[string]any{"target": target, "route": route}})
	}
	return actionloop.Decision{Tool: "propose_capability_plan", Arguments: map[string]any{"calls": calls}}, nil
}

func campaignObjective(t *testing.T, robot *campaignRobot, label string, count, priorWrites int) (objective.Contract, *campaignDecider) {
	t.Helper()
	_, entries, err := capabilityagent.Catalogue(context.Background(), robot)
	if err != nil {
		t.Fatal(err)
	}
	targets := make([]string, count)
	enums := make([]any, count)
	for i := range targets {
		targets[i] = fmt.Sprintf("%s-station-%03d", label, i+1)
		enums[i] = targets[i]
	}
	c := objective.Contract{
		SchemaVersion: objective.Version, RobotID: "edge-test", CatalogRevision: capability.Fingerprint(entries),
		Goal:      "依次巡检以下所有站点，每站确认到达；主路明确拒绝时走已标定绕行路线，只处理剩余站点，最终核验累计到访次数：" + strings.Join(targets, "、"),
		Resources: []string{"robot"}, Effects: []string{"READ", "PHYSICAL_MOTION"},
		// The campaign measures logical horizon under variable CI/disk load;
		// tight execution deadline behavior has independent fault regressions.
		Budget:     objective.Budget{MaxPlans: 8, MaxSteps: 128, MaxReads: 1024, TimeoutSeconds: 600},
		ExpiresAt:  time.Now().UTC().Add(time.Hour),
		Completion: []objective.Check{{Tool: "mission.status", Arguments: map[string]any{}, Path: []string{"visits"}, Equals: priorWrites + count}},
		Grants: []objective.Grant{
			{Tool: "navigation.goto", Arguments: map[string]any{"type": "object", "properties": map[string]any{"target": map[string]any{"type": "string", "enum": enums}, "route": map[string]any{"type": "string", "enum": []any{"main", "detour"}}}, "required": []any{"target", "route"}, "additionalProperties": false}},
			{Tool: "mission.status", Arguments: map[string]any{"type": "object", "additionalProperties": false}},
		},
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	return c, &campaignDecider{targets: targets, chunk: 16}
}

func campaignHost(t *testing.T, robot *campaignRobot) (*Host, *tasks.Service, *sqlite.Store) {
	t.Helper()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "campaign.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	service := tasks.NewService(store, intent.NewDeterministicParser())
	return &Host{Executor: &capabilityagent.Executor{Provider: robot, Store: store, Tasks: service}}, service, store
}

func campaignTask(t *testing.T, service *tasks.Service, contract objective.Contract) *tasks.Task {
	t.Helper()
	task, err := service.CreateObjective(context.Background(), contract, "ros2")
	if err != nil {
		t.Fatal(err)
	}
	task, err = service.Approve(context.Background(), task.ID, "campaign-operator")
	if err != nil {
		t.Fatal(err)
	}
	return task
}

func campaignCheckpoint(t *testing.T, store *sqlite.Store, task *tasks.Task) checkpoint {
	t.Helper()
	state, found, err := store.LoadState(context.Background(), goalKey(task))
	if err != nil || !found {
		t.Fatalf("load campaign checkpoint: found=%v error=%v", found, err)
	}
	var cp checkpoint
	if err := json.Unmarshal(state.Data, &cp); err != nil {
		t.Fatal(err)
	}
	return cp
}

func campaignLog(t *testing.T, data map[string]any) {
	t.Helper()
	data["schemaVersion"] = "edge-robustness-campaign.v1"
	data["evidenceLevel"] = "scripted_in_process_contract_fixture"
	data["evidenceType"] = "software_contract"
	data["model"] = "scripted_decider_no_model_inference"
	data["recordType"] = "task"
	data["plannedStations"] = data["targetCount"]
	data["taskIndex"] = data["taskOrdinal"]
	data["physicalStarts"] = data["writes"]
	data["durationScope"] = "initial_run_checkpoint_and_restart"
	if ns, ok := data["durationNs"].(int64); ok {
		data["elapsedSeconds"] = float64(ns) / float64(time.Second)
	}
	switch data["scenario"] {
	case "persistent_single_robot_multi_task", "sqlite_close_reopen_verified_goal":
		if data["scenario"] == "persistent_single_robot_multi_task" {
			data["durationScope"] = "initial_host_run_only"
		}
		data["outcome"] = "FAILED_UNEXPECTED"
		if data["runError"] == "<nil>" && data["restartError"] == "<nil>" && data["complete"] == true && data["pending"] == false && data["writes"] == data["targetCount"] && data["restartWrites"] == 0 && data["restartPlans"] == 0 {
			data["outcome"] = "SUCCEEDED"
		}
	case "unknown_effect_after_long_confirmed_prefix":
		data["outcome"] = "FAILED_UNEXPECTED"
		if strings.Contains(fmt.Sprint(data["runError"]), capabilityagent.ErrOutcomeUnknown.Error()) && strings.Contains(fmt.Sprint(data["restartError"]), capabilityagent.ErrOutcomeUnknown.Error()) && data["confirmed"] == 24 && data["writes"] == 25 && data["restartWrites"] == 0 && data["restartPlans"] == 0 && data["resumable"] == false && data["complete"] == false && data["pending"] == true {
			data["outcome"] = "RECONCILIATION"
		}
	default:
		data["outcome"] = "FAILED_UNEXPECTED"
		if data["runError"] != "<nil>" && data["restartError"] != "<nil>" && data["complete"] == false && data["pending"] == false && data["restartWrites"] == 0 {
			data["outcome"] = "BOUNDED_STOP"
		}
	}
	if calls, ok := data["serviceCalls"].([]campaignCall); ok {
		seen := map[string]bool{}
		duplicates, priorWrites := 0, 0
		if before, ok := data["writesBefore"].(int); ok {
			priorWrites = before
		}
		for _, call := range calls {
			if call.Service == "navigation.goto" && call.Writes > priorWrites {
				target, _ := call.Arguments["target"].(string)
				if seen[target] {
					duplicates++
				}
				seen[target] = true
			}
			priorWrites = call.Writes
			step, err := json.Marshal(map[string]any{"schemaVersion": data["schemaVersion"], "evidenceLevel": data["evidenceLevel"], "evidenceType": "software_contract", "recordType": "step", "taskId": data["taskId"], "taskOrdinal": data["taskOrdinal"], "scenario": data["scenario"], "call": call})
			if err != nil {
				t.Fatal(err)
			}
			t.Logf("ROBOT_EXPERIMENT %s", step)
		}
		data["duplicateStarts"] = duplicates
		if duplicates != 0 {
			data["outcome"] = "FAILED_UNEXPECTED"
		}
		delete(data, "serviceCalls")
	}
	wire, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("ROBOT_EXPERIMENT %s", wire)
}

func TestSingleRobotCampaignSQLiteCloseReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "reopened-campaign.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	robot := &campaignRobot{missionRobot: &missionRobot{}, rejectOnce: map[string]bool{}}
	contract, decider := campaignObjective(t, robot, "reopen", 32, 0)
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task := campaignTask(t, service, contract)
	host := &Host{Planner: &capabilityagent.Planner{Provider: robot, Decider: decider}, Executor: &capabilityagent.Executor{Provider: robot, Store: store, Tasks: service}}
	started := time.Now().UTC()
	_, runErr := host.Run(context.Background(), task, nil, nil)
	cp := campaignCheckpoint(t, store, task)
	beforeWrites, beforePlans := len(robot.writes), decider.plans
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	service = tasks.NewService(store, intent.NewDeterministicParser())
	restoredTask, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	host = &Host{Planner: &capabilityagent.Planner{Provider: robot, Decider: decider}, Executor: &capabilityagent.Executor{Provider: robot, Store: store, Tasks: service}}
	_, restartErr := host.Run(context.Background(), restoredTask, nil, nil)
	after := campaignCheckpoint(t, store, restoredTask)
	campaignLog(t, map[string]any{"scenario": "sqlite_close_reopen_verified_goal", "taskOrdinal": 1, "taskId": task.ID, "startedAt": started, "durationNs": time.Since(started).Nanoseconds(), "budget": contract.Budget, "targetCount": 32, "writes": len(robot.writes), "plans": cp.Generation, "steps": cp.Steps, "reads": cp.Reads, "confirmed": len(cp.Completed), "complete": cp.Complete, "pending": cp.Pending, "restartWrites": len(robot.writes) - beforeWrites, "restartPlans": decider.plans - beforePlans, "runError": fmt.Sprint(runErr), "restartError": fmt.Sprint(restartErr), "checkpoint": cp, "checkpointAfterReopen": after, "serviceCalls": robot.calls})
	if runErr != nil || restartErr != nil || !cp.Complete || !after.Complete || cp.Pending || after.Pending || len(robot.writes) != 32 || decider.plans != 2 || beforeWrites != len(robot.writes) || capability.Fingerprint(cp.Proof) != capability.Fingerprint(after.Proof) {
		t.Fatalf("SQLite close/reopen lost proof or replayed actions: run=%v restart=%v writes=%d plans=%d", runErr, restartErr, len(robot.writes), decider.plans)
	}
}

func TestSingleRobotCampaignUnknownAfterLongConfirmedPrefix(t *testing.T) {
	robot := &campaignRobot{missionRobot: &missionRobot{}, rejectOnce: map[string]bool{}, lostAt: 25}
	host, service, store := campaignHost(t, robot)
	contract, decider := campaignObjective(t, robot, "unknown", 64, 0)
	task := campaignTask(t, service, contract)
	host.SetPlanner(&capabilityagent.Planner{Provider: robot, Decider: decider})
	started := time.Now().UTC()
	_, runErr := host.Run(context.Background(), task, nil, nil)
	cp := campaignCheckpoint(t, store, task)
	beforeWrites, beforePlans := len(robot.writes), decider.plans
	restarted := &Host{Planner: host.planner(), Executor: host.Executor}
	_, restartErr := restarted.Run(context.Background(), task, nil, nil)
	resumable, resumeErr := CanResumeGoal(context.Background(), store, task)
	campaignLog(t, map[string]any{"scenario": "unknown_effect_after_long_confirmed_prefix", "taskId": task.ID, "startedAt": started, "durationNs": time.Since(started).Nanoseconds(), "budget": contract.Budget, "targetCount": 64, "injectedResponseLossAtWrite": 25, "writes": len(robot.writes), "restartWrites": len(robot.writes) - beforeWrites, "restartPlans": decider.plans - beforePlans, "plans": cp.Generation, "steps": cp.Steps, "reads": cp.Reads, "confirmed": len(cp.Completed), "complete": cp.Complete, "pending": cp.Pending, "outcome": "reconciliation_required", "runError": fmt.Sprint(runErr), "restartError": fmt.Sprint(restartErr), "resumable": resumable, "resumeError": fmt.Sprint(resumeErr), "checkpoint": cp, "serviceCalls": robot.calls})
	if !errors.Is(runErr, capabilityagent.ErrOutcomeUnknown) || !errors.Is(restartErr, capabilityagent.ErrOutcomeUnknown) || resumeErr != nil || resumable || len(robot.writes) != 25 || len(cp.Completed) != 24 || cp.Steps != 25 || cp.Complete || !cp.Pending || decider.plans != beforePlans {
		t.Fatalf("uncertain long task replayed or falsely completed: run=%v restart=%v checkpoint=%+v writes=%d", runErr, restartErr, cp, len(robot.writes))
	}
}

func TestSingleRobotLongHorizonCampaign(t *testing.T) {
	robot := &campaignRobot{missionRobot: &missionRobot{}, rejectOnce: map[string]bool{}}
	host, service, store := campaignHost(t, robot)
	counts := []int{32, 48, 64, 96, 128}
	for ordinal, count := range counts {
		t.Run(fmt.Sprintf("persistent_task_%02d_%03d_steps", ordinal+1, count), func(t *testing.T) {
			label := fmt.Sprintf("m%02d", ordinal+1)
			beforeWrites, beforeCalls := len(robot.writes), len(robot.calls)
			contract, decider := campaignObjective(t, robot, label, count, beforeWrites)
			rejections := 0
			if count == 48 || count == 64 {
				for _, index := range []int{4, 22, 43} {
					robot.rejectOnce[decider.targets[index]] = true
					rejections++
				}
			}
			task := campaignTask(t, service, contract)
			host.SetPlanner(&capabilityagent.Planner{Provider: robot, Decider: decider})
			started := time.Now().UTC()
			_, runErr := host.Run(context.Background(), task, nil, nil)
			elapsed := time.Since(started)
			cp := campaignCheckpoint(t, store, task)
			replayedWrites, replayedPlans := 0, 0
			var replayErr error
			if runErr == nil {
				beforeReplayWrites, beforeReplayPlans := len(robot.writes), decider.plans
				// Reconstruct both transport-independent Host and task service over
				// the same persistent journal, while preserving this robot episode.
				rebuiltService := tasks.NewService(store, intent.NewDeterministicParser())
				host = &Host{Planner: &capabilityagent.Planner{Provider: robot, Decider: decider}, Executor: &capabilityagent.Executor{Provider: robot, Store: store, Tasks: rebuiltService}}
				_, replayErr = host.Run(context.Background(), task, nil, nil)
				replayedWrites, replayedPlans = len(robot.writes)-beforeReplayWrites, decider.plans-beforeReplayPlans
				service = rebuiltService
			}
			campaignLog(t, map[string]any{"scenario": "persistent_single_robot_multi_task", "taskOrdinal": ordinal + 1, "taskId": task.ID, "goal": contract.Goal, "budget": contract.Budget, "targetCount": count, "injectedRejections": rejections, "startedAt": started, "durationNs": elapsed.Nanoseconds(), "writesBefore": beforeWrites, "writes": len(robot.writes) - beforeWrites, "cumulativeWrites": len(robot.writes), "plans": cp.Generation, "steps": cp.Steps, "reads": cp.Reads, "confirmed": len(cp.Completed), "complete": cp.Complete, "pending": cp.Pending, "runError": fmt.Sprint(runErr), "restartError": fmt.Sprint(replayErr), "restartWrites": replayedWrites, "restartPlans": replayedPlans, "maxProgressBytes": decider.maxProgressBytes, "maxRecentCompleted": decider.maxRecent, "maxProjectedCompletedCount": decider.maxCompleted, "progressProjections": decider.progress, "checkpoint": cp, "serviceCalls": robot.calls[beforeCalls:]})
			if runErr != nil || replayErr != nil {
				t.Fatalf("campaign run=%v restart=%v", runErr, replayErr)
			}
			if !cp.Complete || cp.Pending || len(cp.Completed) != count || cp.Steps != count+rejections || len(robot.writes)-beforeWrites != count || replayedWrites != 0 || replayedPlans != 0 {
				t.Fatalf("campaign lost task or replayed a write: checkpoint=%+v", cp)
			}
			if decider.maxRecent != 8 || decider.maxCompleted <= 8 || decider.maxProgressBytes > 8000 {
				t.Fatalf("bounded context not exercised: recent=%d total=%d bytes=%d", decider.maxRecent, decider.maxCompleted, decider.maxProgressBytes)
			}
			seen := map[string]bool{}
			for index, target := range robot.writes[beforeWrites:] {
				if seen[target] {
					t.Fatalf("confirmed target repeated: %s", target)
				}
				if target != decider.targets[index] {
					t.Fatalf("target order changed at %d: got=%s want=%s", index, target, decider.targets[index])
				}
				seen[target] = true
			}
		})
	}
}

func TestSingleRobotCampaignBudgetBoundaries(t *testing.T) {
	for _, scenario := range []struct {
		name      string
		plans     int
		steps     int
		reads     int
		wantWrite int
		wantError string
	}{
		{name: "plan_budget_after_confirmed_prefix", plans: 1, steps: 128, reads: 1024, wantWrite: 16, wantError: "plan budget exhausted"},
		{name: "step_budget_rejects_oversized_next_chunk", plans: 8, steps: 31, reads: 1024, wantWrite: 16, wantError: "step budget exceeded"},
		{name: "read_budget_preserves_final_check", plans: 8, steps: 128, reads: 17, wantWrite: 16, wantError: "read budget cannot cover completion"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			robot := &campaignRobot{missionRobot: &missionRobot{}, rejectOnce: map[string]bool{}}
			host, service, store := campaignHost(t, robot)
			contract, decider := campaignObjective(t, robot, "budget", 32, 0)
			contract.Budget.MaxPlans, contract.Budget.MaxSteps, contract.Budget.MaxReads = scenario.plans, scenario.steps, scenario.reads
			task := campaignTask(t, service, contract)
			host.SetPlanner(&capabilityagent.Planner{Provider: robot, Decider: decider})
			started := time.Now().UTC()
			_, runErr := host.Run(context.Background(), task, nil, nil)
			cp := campaignCheckpoint(t, store, task)
			beforeWrites := len(robot.writes)
			restarted := &Host{Planner: host.planner(), Executor: host.Executor}
			_, restartErr := restarted.Run(context.Background(), task, nil, nil)
			after := campaignCheckpoint(t, store, task)
			campaignLog(t, map[string]any{"scenario": scenario.name, "taskId": task.ID, "startedAt": started, "durationNs": time.Since(started).Nanoseconds(), "budget": contract.Budget, "targetCount": 32, "writes": len(robot.writes), "restartWrites": len(robot.writes) - beforeWrites, "plans": cp.Generation, "steps": cp.Steps, "reads": cp.Reads, "complete": cp.Complete, "pending": cp.Pending, "outcome": "bounded_safe_stop", "runError": fmt.Sprint(runErr), "restartError": fmt.Sprint(restartErr), "checkpointBeforeRestart": cp, "checkpointAfterRestart": after, "serviceCalls": robot.calls})
			if runErr == nil || !strings.Contains(runErr.Error(), scenario.wantError) || restartErr == nil || len(robot.writes) != scenario.wantWrite || after.Complete || after.Pending || after.Reads > contract.Budget.MaxReads || after.Steps > contract.Budget.MaxSteps || after.Generation > contract.Budget.MaxPlans {
				t.Fatalf("budget not respected: run=%v restart=%v writes=%d checkpoint=%+v", runErr, restartErr, len(robot.writes), after)
			}
		})
	}
}
