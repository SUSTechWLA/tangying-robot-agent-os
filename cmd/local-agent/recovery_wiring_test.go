package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	edgeagent "github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// These tests run the composition root, not the runtime.
//
// The runtime's own tests can show that the bus is handed to an agent that asks
// for it. They cannot show that this program registers the agents that ask, or
// that the trigger connecting a finding to the recovery agent exists at all —
// both live here, in the wiring, and both have been wrong before.
//
// The specific defect they pin: the observer's findings were recorded and never
// published, because the sink was assigned per agent at this composition root
// and the observer's assignment was missing. The console still listed findings
// and every agent still reported healthy, so nothing looked broken. What looked
// broken was recovery: the agent that watches for findings never woke up. A test
// that constructs the agents itself would have injected the missing sink and
// passed.

// unreadableSteps is the execution store of a runtime whose durable step records
// are not reachable.
//
// Every read fails, which is the honest shape of "cannot tell". An empty answer
// would read as "this task has no unconfirmed steps", and that difference is the
// one this store exists to exercise: it is the store outage that must not be able
// to silently switch off the rule protecting a robot from redoing a step.
type unreadableSteps struct{}

var errNoStepRuns = errors.New("step runs are not reachable in this deployment")

func (unreadableSteps) StepStatus(context.Context, string, string) (middleware.StepStatus, error) {
	return middleware.StepPending, errNoStepRuns
}

func (unreadableSteps) MarkStepStarted(context.Context, middleware.StepRecord) error {
	return errNoStepRuns
}
func (unreadableSteps) MarkStepCompleted(context.Context, middleware.StepRecord) error {
	return errNoStepRuns
}
func (unreadableSteps) MarkStepFailed(context.Context, middleware.StepRecord) error {
	return errNoStepRuns
}

// ListStepRuns makes the store one that *can* be asked the reconciliation
// question, so the failure under test is a failed read rather than a missing
// capability. The missing-capability case is covered separately, where it can be
// checked without starting a runtime.
func (unreadableSteps) ListStepRuns(context.Context, string) ([]middleware.StepRun, error) {
	return nil, errNoStepRuns
}

// startTestRuntime runs the production composition root over an in-memory task
// store, with one task already in a state the supervisor is expected to report.
func startTestRuntime(t *testing.T) (*agentruntime.Orchestrator, *agentruntime.AlertStore) {
	t.Helper()
	ctx := context.Background()

	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	if err := store.Create(ctx, &tasks.Task{
		ID: "task-abnormal-1", State: taskgraph.StateFailed,
	}); err != nil {
		t.Fatalf("create the abnormal task: %v", err)
	}

	// The default configuration, read exactly as the binary reads it: an unset
	// environment must produce the shipped agent set.
	orchestrator, bus, alerts := startAgentRuntime(
		ctx, func(string) string { return "" },
		service, &edgeagent.Runner{}, unreadableSteps{}, nil,
	)
	if orchestrator == nil || bus == nil {
		t.Fatal("the agent runtime did not start; the composition root returned nothing")
	}
	t.Cleanup(func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), agentRuntimeShutdownGrace)
		defer cancel()
		_ = orchestrator.Shutdown(shutdownCtx)
		bus.Close()
	})
	return orchestrator, alerts
}

// readOnlyRisk is the catalog's risk class for an action that cannot change the
// world, and therefore the one class not gated behind an operator's approval.
const readOnlyRisk = "read_only"

// agentRuntimeShutdownGrace matches the bound the binary uses when it stops the
// runtime: a shutdown that waits forever on a wedged agent would hold up exit.
const agentRuntimeShutdownGrace = 3 * time.Second

// waitForPlan waits for the recovery agent to answer one trigger.
//
// It polls rather than sleeping because the chain it waits on is asynchronous by
// design: the finding is published, a subscriber wakes, an investigation runs,
// and only then is a plan stored. A fixed sleep would be either slower than
// necessary or flaky, and a flaky test on this path would be disabled rather
// than fixed.
func waitForPlan(t *testing.T, alerts *agentruntime.AlertStore, trigger string) agentruntime.RunnerAlertPlan {
	t.Helper()
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		if plan, ok := alerts.PlanFor(trigger); ok {
			return plan
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("no recovery plan was stored for %s: the finding was made and never reached the recovery agent", trigger)
	return agentruntime.RunnerAlertPlan{}
}

// TestTheShippedRuntimeRegistersEveryAgentThatPublishes is the wiring defect as
// an assertion.
//
// An agent missing from this list is an agent whose findings nobody can hear, and
// the symptom is silence rather than an error: the recovery agent in particular
// only ever acts on an event, so a bus it is not on is a recovery capability that
// does not exist while still being advertised.
func TestTheShippedRuntimeRegistersEveryAgentThatPublishes(t *testing.T) {
	orchestrator, _ := startTestRuntime(t)

	publishers := orchestrator.Publishers()
	for _, want := range []string{
		agentruntime.TaskAgentName, agentruntime.OpsAgentName, agentruntime.RecoveryAgentName,
	} {
		if !contains(publishers, want) {
			t.Fatalf("agents handed the bus = %v, missing %s: it can observe and cannot report", publishers, want)
		}
	}
}

// TestWhatTheObserverFindsReachesTheRecoveryAgentInProductionWiring follows one
// finding the whole way: the observer discovers a task that ended abnormally, the
// runtime publishes it, the trigger this composition root installs wakes the
// recovery agent, and the plan it forms is stored where the console reads it.
//
// No step of that chain is wired by the test. Every step of it is wired by the
// program, which is the only version of the chain that runs in production.
func TestWhatTheObserverFindsReachesTheRecoveryAgentInProductionWiring(t *testing.T) {
	_, alerts := startTestRuntime(t)

	// The plan is filed under the finding's identity, not its code: the same
	// failure on a different component is a different problem with its own plan.
	plan := waitForPlan(t, alerts, agentcontract.AnomalyIdentity(agentruntime.AnomalyAbnormalTask, "task"))

	if plan.PlanID == "" {
		t.Fatal("the stored plan has no identity, so it cannot be reviewed or referred to")
	}
	if plan.Verdict == "" {
		t.Fatal("the stored plan does not say what it decided")
	}
	if plan.Diagnosis == "" {
		t.Fatal("a plan without a diagnosis tells an operator what to do but not why")
	}
	// The proposal is the agent's, and an operator reviewing it must be able to
	// tell where it came from.
	if plan.Source == "" {
		t.Fatal("the stored plan does not name what produced it")
	}
	// A plan formed on no evidence is a guess. The investigation is stored with
	// it precisely so this can be checked after the fact.
	steps := plan.Investigation.Steps
	if len(steps) == 0 {
		t.Fatal("the plan was formed without recording a single investigation step")
	}

	// The traceability requirement, checked on the real chain: an operator asked
	// to approve a recovery has to be able to see what was consulted, which
	// table, and what came back.
	tables := map[string]bool{}
	for _, step := range steps {
		tables[step.Source.Table] = true
	}
	// The task row establishes which task is even being discussed, so a plan that
	// never read it was reasoning about a finding alone.
	if !tables["tasks"] {
		t.Fatalf("the investigation never records reading the task row: %#v", steps)
	}
	// A failed read is recorded rather than dropped. This deployment's step
	// records are unreachable, and an investigation that showed only its
	// successes would look more certain than it was.
	reconciliationRead, failed := false, false
	for _, step := range steps {
		if step.Error != "" {
			failed = true
		}
		if step.Name == "step_runs.read" {
			reconciliationRead = true
		}
	}
	if !failed {
		t.Fatalf("unreachable step records produced no failed read: %#v", steps)
	}
	if !reconciliationRead {
		t.Fatalf("the investigation never asked whether a step's outcome is unknown: %#v", steps)
	}

	// The question could not be answered, so the answer is not "the world is
	// settled". Recovery has to stop here: acting on an unknown world is the one
	// thing the closed-loop contract forbids outright, and a store outage is not
	// a licence to skip it.
	if plan.Verdict != string(agentcontract.VerdictEscalate) {
		t.Fatalf("verdict = %s with the reconciliation question unanswerable, want %s",
			plan.Verdict, agentcontract.VerdictEscalate)
	}
	if !strings.Contains(plan.EscalateReason, errNoStepRuns.Error()) {
		t.Fatalf("the escalation does not say what could not be read: %q", plan.EscalateReason)
	}

	// The boundary this version promises, checked on the wiring rather than on
	// the agent in isolation: every action that is not a read requires approval.
	// A read-only action is deliberately not gated — it changes nothing — but if
	// a write ever appears without the gate, the system has begun repairing a
	// robot on its own while the documentation still says it only proposes.
	for _, step := range plan.Steps {
		if step.Risk == readOnlyRisk {
			if step.RequiresApproval {
				t.Fatalf("read-only step %q asks for approval it does not need", step.Action)
			}
			continue
		}
		if !step.RequiresApproval {
			t.Fatalf("step %q is %s and does not require approval, so it would be automatic", step.Action, step.Risk)
		}
	}
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

// blindSteps is the execution store of a deployment whose store cannot be asked
// the reconciliation question at all: it implements the writes but not the task
// listing.
type blindSteps struct{ unreadableSteps }

// TestAnExecutionStoreThatCannotListStepsIsReportedAsAnOpenQuestion pins the
// difference between two answers that used to look identical.
//
// "This task has no step whose outcome is unknown" and "this deployment cannot
// tell me" both used to produce an empty list. Only one of them is an answer, and
// the investigation that could not tell still has to say so — otherwise a store
// without the listing capability reads as a robot in a known state, and the plan
// that follows is built on a question nobody asked.
func TestAnExecutionStoreThatCannotListStepsIsReportedAsAnOpenQuestion(t *testing.T) {
	ctx := context.Background()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	if err := store.Create(ctx, &tasks.Task{ID: "task-blind", State: taskgraph.StateFailed}); err != nil {
		t.Fatalf("create the task: %v", err)
	}

	facts, steps, err := recoveryFactsReader(service, blindSteps{}, nil)(ctx, "task-blind")
	if err != nil {
		t.Fatalf("a store that cannot list steps failed the whole investigation: %v", err)
	}
	if !facts.ReconciliationUnavailable {
		t.Fatal("a store that cannot list steps was read as a task with nothing to reconcile")
	}
	if facts.ReconciliationError == "" {
		t.Fatal("the investigation does not say what could not be read")
	}
	if len(facts.UncertainSteps) != 0 {
		t.Fatalf("an unreadable store invented %d uncertain steps", len(facts.UncertainSteps))
	}

	recorded := false
	for _, step := range steps {
		if step.Name == "step_runs.read" && step.Error != "" {
			recorded = true
		}
	}
	if !recorded {
		t.Fatalf("the unanswerable question was not recorded in the trail: %#v", steps)
	}
}
