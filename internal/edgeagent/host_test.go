package edgeagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
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
	"google.golang.org/protobuf/types/known/structpb"
)

func fields(v map[string]any) *structpb.Struct {
	s, err := structpb.NewStruct(v)
	if err != nil {
		panic(err)
	}
	return s
}

type missionRobot struct {
	mu               sync.Mutex
	visited          map[string]any
	writes           []string
	last             string
	blocked          string
	lost             bool
	verificationLost bool
}

func (*missionRobot) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	empty := fields(map[string]any{"type": "object", "additionalProperties": false})
	return &robotv1.ServiceCatalog{RobotId: "edge-test", Services: []*robotv1.ServiceDefinition{
		{Name: "navigation.goto", Available: true, MutatesWorld: true, InputSchema: fields(map[string]any{"type": "object", "properties": map[string]any{"target": map[string]any{"type": "string"}, "route": map[string]any{"type": "string", "enum": []any{"main", "detour"}}}, "required": []any{"target", "route"}, "additionalProperties": false}), Contract: fields(map[string]any{"version": "1", "effects": []any{"PHYSICAL_MOTION"}, "resources": []any{"robot"}, "verification": map[string]any{"service": "mission.status", "required": []any{"lastTarget"}, "match": map[string]any{"lastTarget": "arguments.target"}}})},
		{Name: "mission.status", Available: true, InputSchema: empty, Contract: fields(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": []any{"visited", "lastTarget"}})},
	}}, nil
}
func (r *missionRobot) CallService(ctx context.Context, q *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if r.visited == nil {
		r.visited = map[string]any{}
	}
	if q.Name == "mission.status" && r.verificationLost {
		return nil, errors.New("verification sensor unavailable")
	}
	if q.Name == "navigation.goto" {
		args := q.Parameters.AsMap()
		target, _ := args["target"].(string)
		if target == r.blocked && args["route"] == "main" {
			return &robotv1.ServiceResponse{Ok: false, Code: "BLOCKED", Message: "main route closed; use commissioned detour", Result: fields(map[string]any{"outcome": "REJECTED"})}, nil
		}
		r.writes = append(r.writes, target)
		r.last = target
		r.visited[target] = true
		if r.lost {
			return nil, errors.New("lost physical response after movement")
		}
	}
	return &robotv1.ServiceResponse{Ok: true, Result: fields(map[string]any{"lastTarget": r.last, "visited": r.visited, "visits": len(r.writes)})}, nil
}

type routeDecider struct {
	rounds  int
	targets []string
	bad     string
	repeat  bool
}

func (d *routeDecider) Decide(_ context.Context, r actionloop.Request) (actionloop.Decision, error) {
	d.rounds++
	start := 0
	if d.rounds > 1 {
		start = 4
		if d.repeat {
			start = 0
		}
	}
	var calls []any
	for i := start; i < len(d.targets); i++ {
		route := "main"
		if d.rounds > 1 && i == 4 {
			route = "detour"
		}
		target := d.targets[i]
		if d.bad != "" {
			target = d.bad
		}
		calls = append(calls, map[string]any{"tool": "navigation.goto", "arguments": map[string]any{"target": target, "route": route}})
	}
	if d.rounds > 1 && !strings.Contains(fmt.Sprint(r.Observation.Context), "BLOCKED") {
		return actionloop.Decision{}, fmt.Errorf("replan lacks durable diagnosis")
	}
	return actionloop.Decision{Tool: "propose_capability_plan", Arguments: map[string]any{"calls": calls}}, nil
}
func objectiveFixture(t *testing.T, r *missionRobot, n int) (objective.Contract, *routeDecider) {
	t.Helper()
	_, entries, err := capabilityagent.Catalogue(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	var targets []string
	var enums []any
	var checks []objective.Check
	for i := 1; i <= n; i++ {
		target := fmt.Sprintf("station-%02d", i)
		targets = append(targets, target)
		enums = append(enums, target)
		checks = append(checks, objective.Check{Tool: "mission.status", Arguments: map[string]any{}, Path: []string{"visited", target}, Equals: true})
	}
	c := objective.Contract{SchemaVersion: objective.Version, RobotID: "edge-test", Goal: "巡检所有指定站点，遇到主路封闭时使用已标定绕行路线，完成后核验每个站点的记录。", CatalogRevision: capability.Fingerprint(entries), Resources: []string{"robot"}, Effects: []string{"READ", "PHYSICAL_MOTION"}, Budget: objective.Budget{MaxPlans: 3, MaxSteps: 32, MaxReads: 128, TimeoutSeconds: 60}, ExpiresAt: time.Now().UTC().Add(time.Hour), Completion: checks, Grants: []objective.Grant{
		{Tool: "navigation.goto", Arguments: map[string]any{"type": "object", "properties": map[string]any{"target": map[string]any{"type": "string", "enum": enums}, "route": map[string]any{"type": "string", "enum": []any{"main", "detour"}}}, "required": []any{"target", "route"}, "additionalProperties": false}},
		{Tool: "mission.status", Arguments: map[string]any{"type": "object", "additionalProperties": false}},
	}}
	return c, &routeDecider{targets: targets}
}
func hostFixture(t *testing.T, r *missionRobot, c objective.Contract, d actionloop.Decider) (*Host, *tasks.Task, *sqlite.Store) {
	t.Helper()
	ctx := context.Background()
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "edge.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	service := tasks.NewService(store, intent.NewDeterministicParser())
	task, err := service.CreateObjective(ctx, c, "ros2")
	if err != nil {
		t.Fatal(err)
	}
	task, err = service.Approve(ctx, task.ID, "test-operator")
	if err != nil {
		t.Fatal(err)
	}
	host := &Host{Planner: &capabilityagent.Planner{Provider: r, Decider: d}, Executor: &capabilityagent.Executor{Provider: r, Store: store, Tasks: service}}
	return host, task, store
}

func TestDelegatedLongGoalReplansRemainingStepsAndPersistsLineage(t *testing.T) {
	r := &missionRobot{blocked: "station-05"}
	c, d := objectiveFixture(t, r, 11)
	host, task, store := hostFixture(t, r, c, d)
	if _, err := host.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatal(err)
	}
	if len(r.writes) != 11 || d.rounds != 2 {
		t.Fatalf("writes=%v planning rounds=%d", r.writes, d.rounds)
	}
	seen := map[string]bool{}
	for _, target := range r.writes {
		if seen[target] {
			t.Fatal("confirmed write replayed")
		}
		seen[target] = true
	}
	final, err := host.Tasks.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	foundRecovery := false
	for _, e := range final.Events {
		if e.Type == "EDGE_RECOVERY_DECISION" && e.Payload["diagnosedBy"] == "ops" && e.Payload["role"] == "recovery" {
			foundRecovery = true
		}
	}
	if !foundRecovery {
		t.Fatal("diagnosis and recovery lineage missing")
	}
	s, _, _ := store.LoadState(context.Background(), goalKey(task))
	var cp checkpoint
	_ = json.Unmarshal(s.Data, &cp)
	if !cp.Complete || cp.Pending || cp.Generation != 2 || cp.Steps != 12 || len(cp.Completed) != 11 {
		t.Fatalf("checkpoint=%+v", cp)
	}
	// A new host reconstructs the goal from SQLite; it only retransmits evidence.
	restarted := &Host{Planner: host.Planner, Executor: host.Executor}
	if _, err = restarted.Run(context.Background(), task, nil, nil); err != nil || len(r.writes) != 11 || d.rounds != 2 {
		t.Fatalf("restart replayed actions: %v", err)
	}
	t.Logf("goal=%q stations=11 confirmed_writes=11 rejected_attempts=1 local_plans=2 steps=%d reads=%d restart_replays=0", c.Goal, cp.Steps, cp.Reads)
	trace, _ := json.Marshal(map[string]any{"goal": c.Goal, "executedTargets": r.writes, "checkpoint": cp, "events": final.Events})
	t.Logf("trace=%s", trace)
}
func TestDelegatedUnknownPhysicalResultNeverReplansOrReplays(t *testing.T) {
	r := &missionRobot{lost: true}
	c, d := objectiveFixture(t, r, 1)
	host, task, store := hostFixture(t, r, c, d)
	_, err := host.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrOutcomeUnknown) {
		t.Fatalf("unknown result=%v", err)
	}
	if safe, _ := CanResumeGoal(context.Background(), store, task); safe {
		t.Fatal("uncertain checkpoint resumed")
	}
	_, err = host.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrOutcomeUnknown) || len(r.writes) != 1 || d.rounds != 1 {
		t.Fatalf("unknown action replayed: %v", err)
	}
}

type failedRecovery struct{}

func (failedRecovery) Propose(context.Context, Finding) (string, error) {
	return "", errors.New("replacement recovery component unavailable")
}
func TestDelegatedRecoveryPluginCannotHideUnknownOutcome(t *testing.T) {
	r := &missionRobot{lost: true}
	c, d := objectiveFixture(t, r, 1)
	host, task, _ := hostFixture(t, r, c, d)
	host.Recovery = failedRecovery{}
	_, err := host.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrOutcomeUnknown) || len(r.writes) != 1 {
		t.Fatalf("replacement policy hid uncertain movement: %v", err)
	}
}
func TestDelegatedLostVerificationAfterWriteRequiresReconciliation(t *testing.T) {
	r := &missionRobot{verificationLost: true}
	c, d := objectiveFixture(t, r, 1)
	host, task, _ := hostFixture(t, r, c, d)
	_, err := host.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrOutcomeUnknown) || len(r.writes) != 1 || d.rounds != 1 {
		t.Fatalf("unverified write was not quarantined: %v", err)
	}
}
func TestDelegatedEnvelopeRejectsModelTargetOutsideScope(t *testing.T) {
	r := &missionRobot{}
	c, d := objectiveFixture(t, r, 1)
	d.bad = "unauthorized-area"
	host, task, _ := hostFixture(t, r, c, d)
	if _, err := host.Run(context.Background(), task, nil, nil); err == nil || len(r.writes) != 0 {
		t.Fatalf("outside scope executed: %v", err)
	}
}
func TestDelegatedReplanCannotRepeatConfirmedWrites(t *testing.T) {
	r := &missionRobot{blocked: "station-05"}
	c, d := objectiveFixture(t, r, 11)
	d.repeat = true
	host, task, _ := hostFixture(t, r, c, d)
	_, err := host.Run(context.Background(), task, nil, nil)
	if err == nil || !strings.Contains(err.Error(), "repeats confirmed write") || len(r.writes) != 4 {
		t.Fatalf("replan replayed past movement: %v writes=%v", err, r.writes)
	}
}
func TestDelegatedPlanBudgetSurvivesHostRestart(t *testing.T) {
	r := &missionRobot{blocked: "station-05"}
	c, d := objectiveFixture(t, r, 11)
	c.Budget.MaxPlans = 1
	host, task, _ := hostFixture(t, r, c, d)
	_, err := host.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrNeedsInput) {
		t.Fatalf("budget did not stop replan: %v", err)
	}
	restarted := &Host{Planner: host.Planner, Executor: host.Executor}
	_, err = restarted.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrNeedsInput) || d.rounds != 1 || len(r.writes) != 4 {
		t.Fatalf("restart reset budget: %v rounds=%d writes=%d", err, d.rounds, len(r.writes))
	}
}
func TestDelegatedIntentionalRepeatRequiresApprovedExecutionQuota(t *testing.T) {
	r := &missionRobot{}
	c, d := objectiveFixture(t, r, 1)
	c.Grants[0].MaxExecutions = 2
	c.Completion = []objective.Check{{Tool: "mission.status", Arguments: map[string]any{}, Path: []string{"visits"}, Equals: 2}}
	d.targets = append(d.targets, d.targets[0])
	host, task, _ := hostFixture(t, r, c, d)
	if _, err := host.Run(context.Background(), task, nil, nil); err != nil || len(r.writes) != 2 {
		t.Fatalf("explicit repeat failed: %v writes=%v", err, r.writes)
	}
}
func TestDelegatedModelHTTPPlanReceivesAuthorizedSchemaAndCompletion(t *testing.T) {
	r := &missionRobot{}
	c, _ := objectiveFixture(t, r, 1)
	seen := make(chan string, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		wire, _ := io.ReadAll(q.Body)
		select {
		case seen <- string(wire):
		default:
		}
		args, _ := json.Marshal(map[string]any{"calls": []any{map[string]any{"tool": "navigation.goto", "arguments": map[string]any{"target": "station-01", "route": "main"}}}})
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{"role": "assistant", "tool_calls": []any{map[string]any{"function": map[string]any{"name": "propose_capability_plan", "arguments": string(args)}}}}}}})
	}))
	defer server.Close()
	d := &actionloop.LLMDecider{BaseURL: server.URL + "/v1", Model: "scripted-http-fixture", Client: server.Client()}
	host, task, _ := hostFixture(t, r, c, d)
	if _, err := host.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatal(err)
	}
	body := <-seen
	if !strings.Contains(body, "station-01") || !strings.Contains(body, "completion") || strings.Contains(body, "unauthorized-area") || len(r.writes) != 1 {
		t.Fatal("model schema/completion projection differs from authority")
	}
}
func TestDelegatedBudgetsAndCancellationPreventNewMovement(t *testing.T) {
	for _, mode := range []string{"steps", "reads", "expired", "cancelled", "unapproved", "catalog"} {
		t.Run(mode, func(t *testing.T) {
			r := &missionRobot{}
			c, d := objectiveFixture(t, r, 2)
			if mode == "steps" {
				c.Budget.MaxSteps = 1
			}
			if mode == "reads" {
				c.Budget.MaxReads = 1
			}
			host, task, _ := hostFixture(t, r, c, d)
			if mode == "expired" {
				var decoded map[string]any
				wire, _ := json.Marshal(c)
				_ = json.Unmarshal(wire, &decoded)
				decoded["expiresAt"] = time.Now().Add(-time.Hour).Format(time.RFC3339Nano)
				task.Plan.Capabilities.Calls[0].Arguments["objective"] = decoded
			}
			if mode == "unapproved" {
				task.Approved = false
			}
			if mode == "catalog" {
				task.Plan.Capabilities.CatalogRevision = strings.Repeat("0", 64)
			}
			before := func(context.Context) error {
				if mode == "cancelled" {
					return context.Canceled
				}
				return nil
			}
			if _, err := host.Run(context.Background(), task, before, nil); err == nil {
				t.Fatal("invalid authority/budget accepted")
			}
			if mode != "reads" && len(r.writes) != 0 {
				t.Fatalf("movement after refusal: %v", r.writes)
			}
		})
	}
}
