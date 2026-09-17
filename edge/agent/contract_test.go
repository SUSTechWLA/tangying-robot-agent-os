package agent_test

import (
	"context"
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The execution Agent is now reachable through the common Agent interface. These
// tests pin the parts of that interface a runtime relies on before it starts
// anything, and the parts a safety reviewer relies on afterwards: what the agent
// says it is, what it admits it is allowed to do, and whether it refuses work
// instead of guessing when it cannot read the task it was asked to run.
//
// Execution behaviour itself is covered by the existing edge/agent tests
// (closure, evidence, recovery, dispatch deadline). Nothing here re-tests the
// closed loop through a second path, because a second path is how the two would
// eventually disagree.

type taskReaderStub struct {
	tasks map[string]*tasks.Task
	err   error
}

func (r taskReaderStub) Get(_ context.Context, id string) (*tasks.Task, error) {
	if r.err != nil {
		return nil, r.err
	}
	task, ok := r.tasks[id]
	if !ok {
		return nil, tasks.ErrTaskNotFound
	}
	return task, nil
}

func parsedIntent(t *testing.T) manipulation.Intent {
	t.Helper()
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func executeTask(t *testing.T, id string) *tasks.Task {
	t.Helper()
	return &tasks.Task{
		ID: id, Intent: parsedIntent(t), State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
}

func TestRunnerIsANativeAgent(t *testing.T) {
	runner := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{})

	var contract agentcontract.Agent = runner
	if contract.Name() != agent.TaskAgentName {
		t.Fatalf("agent name = %q, want %q", contract.Name(), agent.TaskAgentName)
	}
	if contract.Version() == "" {
		t.Fatal("agent version must not be empty")
	}
	// The name is a configuration key and an event attribution key, so it is
	// part of the deployed interface rather than an internal detail.
	if agent.TaskAgentName != "task" {
		t.Fatalf("TaskAgentName = %q, want the configured name %q", agent.TaskAgentName, "task")
	}
}

func TestTaskAgentDeclaresWorldMutationPermission(t *testing.T) {
	permission := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{}).Permissions()
	if permission.ReadOnly {
		t.Fatal("the execution agent cannot be read-only: it is the agent that drives hardware")
	}
	if !permission.MayMutateWorld() {
		t.Fatalf("permission %#v does not permit the world mutation this agent exists to perform", permission)
	}
	if !permission.MayMutateTaskState() {
		t.Fatalf("permission %#v does not permit the task state changes execution requires", permission)
	}
}

func TestTaskAgentCapabilitiesCoverTheToolCatalog(t *testing.T) {
	capabilities := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{}).Capabilities()
	declared := make(map[agentcontract.Capability]bool, len(capabilities))
	for _, capability := range capabilities {
		if declared[capability] {
			t.Fatalf("capability %q declared twice", capability)
		}
		declared[capability] = true
	}
	if !declared[agentcontract.CapabilityTaskExecution] {
		t.Fatal("task.execution capability is not declared")
	}
	// Every catalog skill must be reachable, otherwise the declaration would
	// understate what this agent can actually dispatch.
	for _, manifest := range manipulation.Catalog() {
		if !declared[agentcontract.Capability(manifest.Name)] {
			t.Fatalf("catalog skill %q is missing from the declared capabilities", manifest.Name)
		}
	}
}

func TestTaskAgentSubscribesToNothing(t *testing.T) {
	// An executor that also subscribed would sit in a delivery path it never
	// needed, where a slow executor can only lose events.
	if subscriptions := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{}).Subscriptions(); len(subscriptions) != 0 {
		t.Fatalf("subscriptions = %#v, want none", subscriptions)
	}
}

func TestTaskAgentHealthReportsUnverifiedPreconditions(t *testing.T) {
	store := newExecutionStoreSpy()
	invoker := &invokerSpy{}

	healthy := agent.NewRunner(store, grounderStub{}, invoker)
	if health := healthy.Health(context.Background()); health.Status != agentcontract.HealthHealthy {
		t.Fatalf("health = %#v, want HEALTHY", health)
	}
	if health := healthy.Health(context.Background()); health.CheckedAt.IsZero() {
		t.Fatal("health report must say when it was produced")
	}

	// Without durable step records a world mutation cannot be reconciled, so
	// this is unhealthy rather than degraded.
	noStore := agent.NewRunner(nil, grounderStub{}, invoker)
	health := noStore.Health(context.Background())
	if health.Status != agentcontract.HealthUnhealthy {
		t.Fatalf("health without a store = %#v, want UNHEALTHY", health)
	}
	if health.ReasonCode == "" {
		t.Fatal("an unhealthy report must carry a reason code, not just a status")
	}

	// Missing execution ports mean it cannot run, which is degraded: the store
	// can still describe what happened before.
	noInvoker := agent.NewRunner(store, grounderStub{}, nil)
	if health := noInvoker.Health(context.Background()); health.Status != agentcontract.HealthDegraded {
		t.Fatalf("health without an invoker = %#v, want DEGRADED", health)
	}
}

func TestTaskAgentShutdownStopsAcceptingWork(t *testing.T) {
	runner := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{})
	runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{"task-1": executeTask(t, "task-1")}}
	if err := runner.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	// Shutdown is called at least twice in a real lifecycle: once by the
	// runtime and once by whatever deferred it. It must be idempotent.
	if err := runner.Shutdown(context.Background()); err != nil {
		t.Fatalf("second shutdown: %v", err)
	}
	if health := runner.Health(context.Background()); health.Status != agentcontract.HealthStopped {
		t.Fatalf("health after shutdown = %#v, want STOPPED", health)
	}
	if _, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: "task-1"}); !errors.Is(err, agentcontract.ErrShutdown) {
		t.Fatalf("execute after shutdown = %v, want ErrShutdown", err)
	}
}

func TestTaskAgentExecuteRefusesWhenItCannotKnowTheTask(t *testing.T) {
	runner := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{})

	if _, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{}); err == nil {
		t.Fatal("execute without a task id must be refused")
	}
	// No reader means the outcome state could only be invented, which is worse
	// than refusing: a runtime that cannot read the state cannot report it.
	if _, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: "task-1"}); err == nil {
		t.Fatal("execute without a task reader must be refused")
	}
	runner.Tasks = taskReaderStub{err: errors.New("store unavailable")}
	if _, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: "task-1"}); err == nil {
		t.Fatal("execute must fail when the task cannot be read")
	}
}

func TestTaskAgentExecuteRefusesARevisionItWasNotAskedToRun(t *testing.T) {
	runner := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{})
	runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{"task-1": executeTask(t, "task-1")}}
	_, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: "task-1", TaskRevision: 4})
	if !errors.Is(err, agent.ErrResumeBindingChanged) {
		t.Fatalf("execute at a stale revision = %v, want ErrResumeBindingChanged", err)
	}
}

func TestTaskAgentExecuteRunsThroughTheClosedLoopAndReportsTheState(t *testing.T) {
	store := newExecutionStoreSpy()
	invoker := &invokerSpy{}
	runner := agent.NewRunner(store, grounderStub{}, invoker)
	task := executeTask(t, "task-execute")
	runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{task.ID: task}}

	outcome, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: task.ID})
	if err != nil {
		t.Fatal(err)
	}
	if outcome.TaskID != task.ID {
		t.Fatalf("outcome task id = %q, want %q", outcome.TaskID, task.ID)
	}
	if len(outcome.CompletedSteps) == 0 {
		t.Fatal("a completed run must report the steps it completed")
	}
	// The state is read back from the authority, not inferred from the error.
	if outcome.State != string(task.State) {
		t.Fatalf("outcome state = %q, want the task's state %q", outcome.State, task.State)
	}
	if outcome.ReasonCode != "TASK_EXECUTION_COMPLETED" {
		t.Fatalf("reason code = %q, want TASK_EXECUTION_COMPLETED", outcome.ReasonCode)
	}
	if !store.completed["pick"] {
		t.Fatalf("the run did not go through the execution store: %#v", store.completed)
	}
}

func TestTaskAgentExecuteMapsRefusalsToStableReasonCodes(t *testing.T) {
	// Reason codes reach the durable event stream and operator guidance, so they
	// must not move when an error message is reworded. This exercises the
	// mapping through a real refusal: an unapproved task cannot run a physical
	// step, and the code must name approval rather than a generic failure.
	runner := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{})
	task := executeTask(t, "task-unapproved")
	task.Approved = false
	runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{task.ID: task}}

	outcome, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: task.ID})
	if err == nil {
		t.Fatal("an unapproved task ran a physical step")
	}
	if !errors.Is(err, agent.ErrApprovalRequired) {
		t.Fatalf("execute error = %v, want ErrApprovalRequired", err)
	}
	if outcome.ReasonCode != "APPROVAL_REQUIRED" {
		t.Fatalf("reason code = %q, want APPROVAL_REQUIRED", outcome.ReasonCode)
	}
}

func TestTaskAgentExecutePublishesActionAndEvidenceEvents(t *testing.T) {
	store := newExecutionStoreSpy()
	runner := agent.NewRunner(store, grounderStub{}, &invokerSpy{})
	task := executeTask(t, "task-events")
	runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{task.ID: task}}

	var published []agentcontract.Event
	runner.Events = func(_ context.Context, event agentcontract.Event) {
		published = append(published, event)
	}

	if _, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: task.ID}); err != nil {
		t.Fatal(err)
	}
	statuses := map[string]int{}
	topics := map[string]int{}
	for _, event := range published {
		topics[event.Topic]++
		if event.Topic != agentcontract.TopicActionExecuted {
			continue
		}
		status, _ := event.Payload["activityStatus"].(string)
		statuses[status]++
		if event.Agent != agent.TaskAgentName {
			t.Fatalf("event %#v is not attributed to the task agent", event)
		}
		if event.TaskID != task.ID {
			t.Fatalf("event %#v is not attributed to the task", event)
		}
		if !agentcontract.KnownTopic(event.Topic) {
			t.Fatalf("event %#v uses an unknown topic", event)
		}
	}
	if topics[agentcontract.TopicActionExecuted] == 0 {
		t.Fatal("no action.executed event was published")
	}
	// Every dispatch announces that it was sent and that it was running. These
	// two are what an observer uses to know a physical action is in flight.
	if statuses["SENDING"] == 0 || statuses["RUNNING"] == 0 {
		t.Fatalf("dispatch transitions = %#v, want SENDING and RUNNING", statuses)
	}
	if statuses["CONFIRMED"] == 0 {
		t.Fatalf("dispatch transitions = %#v, want a CONFIRMED step", statuses)
	}
	if topics[agentcontract.TopicEvidenceCollected] == 0 {
		t.Fatal("no evidence.collected event was published for a confirmed step")
	}
}

func TestTaskAgentPublishesNothingWithoutASink(t *testing.T) {
	// A nil sink is the pre-runtime behaviour and must stay the behaviour: the
	// agent runtime is an added observer, never a required step.
	runner := agent.NewRunner(newExecutionStoreSpy(), grounderStub{}, &invokerSpy{})
	task := executeTask(t, "task-no-sink")
	runner.Tasks = taskReaderStub{tasks: map[string]*tasks.Task{task.ID: task}}
	if _, err := runner.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: task.ID}); err != nil {
		t.Fatalf("execution changed because no event sink is installed: %v", err)
	}
}
