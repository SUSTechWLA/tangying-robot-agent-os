package agentruntime_test

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

func TestOrchestratorRoutesEventsToSubscribers(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	ops.subscriptions = []string{agentcontract.TopicAgentAll}

	_, _ = startOrchestratorFast(t, task, ops)

	// The first health announcement arrives on an evaluation tick, so wait for
	// it specifically rather than for "some events", which the registration
	// announcements alone would already satisfy.
	waitFor(t, "the observer to be told about both agents", func() bool {
		return containsString(ops.topics(), agentcontract.TopicAgentRegistered) &&
			containsString(ops.topics(), agentcontract.TopicAgentHealthChanged)
	})
	topics := ops.topics()
	// One announcement per enabled agent, and only for the namespace this
	// observer actually asked for.
	for _, wanted := range []string{agentcontract.TopicAgentRegistered, agentcontract.TopicAgentHealthChanged} {
		if !containsString(topics, wanted) {
			t.Fatalf("observer topics = %#v, missing %q", topics, wanted)
		}
	}
	for _, topic := range topics {
		if !agentcontract.Matches(agentcontract.TopicAgentAll, topic) {
			t.Fatalf("observer received %q, which it never subscribed to", topic)
		}
	}
}

// Starting publishes agent.registered for each enabled agent, once. A replay has
// to be able to tell which agents were running when a task executed.
func TestOrchestratorAnnouncesRegisteredAgents(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	ops.subscriptions = []string{agentcontract.TopicAgentAll}

	_, _ = startOrchestratorFast(t, task, ops)

	waitFor(t, "the registration announcement", func() bool {
		return len(ops.events()) > 0
	})
	event := ops.events()[0]
	if event.Topic != agentcontract.TopicAgentRegistered {
		t.Fatalf("first event topic = %q", event.Topic)
	}
	// No task id: the ledger is per task, so a runtime-wide fact is broadcast
	// and not filed under a task it does not describe.
	if event.TaskID != "" {
		t.Fatalf("a runtime-wide event carries task id %q", event.TaskID)
	}
	enabled, _ := event.Payload["enabledAgents"].([]any)
	if len(enabled) != 2 {
		t.Fatalf("enabledAgents = %#v, want both agents named", event.Payload["enabledAgents"])
	}
}

func TestOrchestratorRefusesToStartWithUnregisteredEnabledAgent(t *testing.T) {
	registry := mustRegistry(t, agentruntime.Config{Enabled: []string{"task", "missing"}}, newStubAgent("task"))
	runtime := agentruntime.New()
	defer runtime.Close()
	orchestrator := agentruntime.NewOrchestrator(runtime, registry)

	if err := orchestrator.Start(context.Background()); err == nil {
		t.Fatal("startup succeeded with an enabled agent that does not exist")
	}
	// Validation happens before anything is started, so a bad configuration
	// leaves the system as it was rather than half-started.
	if names := orchestrator.AgentNames(); len(names) != 0 {
		t.Fatalf("agents were started despite the configuration error: %#v", names)
	}
}

// --- the physical-critical arbitration -------------------------------------

// A recovery proposal must not reach a decision while the robot is mid-action.
// The outcome of the action in flight is exactly what would change the advice,
// so acting on it early means acting on a state that is already stale.
func TestRecoveryProposalIsDeferredWhileAnActionIsInFlight(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	ops.subscriptions = []string{agentcontract.TopicOpsAll}

	orchestrator, runtime := startOrchestratorFast(t, task, ops)

	// A physical action starts and has not reported a terminal status.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: "task-1",
		Payload: agentcontract.ActionPayload{
			ToolName: "manipulation.pick", ActivityStatus: "SENDING", StepID: "pick",
		}.Encode(),
	})
	waitFor(t, "the action to be tracked as in flight", func() bool {
		return orchestrator.PhysicalActionInFlight("task-1")
	})

	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsRecoveryProposed, TaskID: "task-1", ID: "proposal-1",
		Payload: agentcontract.RecoveryProposalPayload{
			ProposalID: "p1", Action: "re-observe", AutomationLevel: agentruntime.AutomationAdvisory,
			RequiresApproval: true,
		}.Encode(),
	})

	waitFor(t, "the proposal to be held back", func() bool {
		return orchestrator.DeferredProposals() == 1
	})

	// The proposal must not have been delivered while the action is in flight.
	for _, event := range ops.events() {
		if event.Topic == agentcontract.TopicOpsRecoveryProposed {
			t.Fatalf("a recovery proposal was delivered mid-action: %#v", event.Payload)
		}
	}
	// The deferral is recorded, because a deferral with no trace is
	// indistinguishable from a proposal that was dropped.
	waitFor(t, "the deferral to be recorded", func() bool {
		return containsString(ops.topics(), agentcontract.TopicOpsRecoveryDeferred)
	})
	var deferral agentcontract.Event
	for _, event := range ops.events() {
		if event.Topic == agentcontract.TopicOpsRecoveryDeferred {
			deferral = event
		}
	}
	if deferral.Payload["deferredReason"] != "PHYSICAL_ACTION_IN_FLIGHT" {
		t.Fatalf("deferral reason = %#v", deferral.Payload["deferredReason"])
	}

	// Once the action reaches a terminal status, the safe point has arrived and
	// the proposal is released.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: "task-1",
		Payload: agentcontract.ActionPayload{
			ToolName: "manipulation.pick", ActivityStatus: "CONFIRMED", StepID: "pick",
		}.Encode(),
	})
	waitFor(t, "the action to leave flight", func() bool {
		return !orchestrator.PhysicalActionInFlight("task-1")
	})
	waitFor(t, "the proposal to be released at the safe point", func() bool {
		return containsString(ops.topics(), agentcontract.TopicOpsRecoveryProposed)
	})
}

// A proposal about a task that is not moving is routed immediately. The
// deferral is about physical safety, not about throttling observers.
func TestRecoveryProposalIsNotDeferredWhenNothingIsMoving(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	ops.subscriptions = []string{agentcontract.TopicOpsAll}

	orchestrator, runtime := startOrchestrator(t, task, ops)

	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsRecoveryProposed, TaskID: "task-quiet",
		Payload: agentcontract.RecoveryProposalPayload{
			ProposalID: "p1", Action: "re-observe", AutomationLevel: agentruntime.AutomationAdvisory,
			RequiresApproval: true,
		}.Encode(),
	})
	waitFor(t, "the proposal to be routed", func() bool {
		return containsString(ops.topics(), agentcontract.TopicOpsRecoveryProposed)
	})
	if orchestrator.DeferredProposals() != 0 {
		t.Fatalf("a proposal was deferred with nothing in flight: %d held", orchestrator.DeferredProposals())
	}
}

// The count of actions in flight must survive overlapping dispatches: a single
// flag would clear the hold on the first completion while the second action was
// still running, which is precisely when a proposal must keep waiting.
func TestPhysicalActionHoldSurvivesOverlappingDispatches(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	orchestrator, runtime := startOrchestrator(t, task)

	dispatch := func(status string) {
		runtime.Publish(context.Background(), agentcontract.Event{
			Topic: agentcontract.TopicActionExecuted, TaskID: "task-1",
			Payload: agentcontract.ActionPayload{ToolName: "manipulation.pick", ActivityStatus: status}.Encode(),
		})
	}
	dispatch("SENDING")
	dispatch("RUNNING")
	waitFor(t, "both dispatches to be tracked", func() bool {
		return orchestrator.PhysicalActionInFlight("task-1")
	})
	dispatch("CONFIRMED")
	if !orchestrator.PhysicalActionInFlight("task-1") {
		t.Fatal("one completion cleared the hold while another action was still in flight")
	}
	dispatch("FAILED")
	waitFor(t, "the hold to clear after both terminal statuses", func() bool {
		return !orchestrator.PhysicalActionInFlight("task-1")
	})
}

// --- the permission gate ---------------------------------------------------

// The acceptance criterion, stated directly: the observing agent cannot execute
// a world mutation, and the refusal is recorded.
func TestOpsAgentCannotRequestAWorldMutation(t *testing.T) {
	ops := agentruntime.NewOpsAgent()
	// The refusal is published on the bus, so it is observed the way any other
	// event is: by subscribing to the namespace it lands in.
	watcher := newStubAgent("watcher")
	watcher.subscriptions = []string{agentcontract.TopicAgentAll}
	task := newStubAgent(agentruntime.TaskAgentName)
	orchestrator, _ := startOrchestrator(t, task, ops, watcher)

	decision, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: agentruntime.OpsAgentName, Capability: "manipulation.pick",
		TaskID: "task-1", MutatesWorld: true, Reason: "retry the pick",
	})
	if err == nil {
		t.Fatal("the observer was allowed to request a world mutation")
	}
	if !errors.Is(err, agentruntime.ErrPermissionDenied) {
		t.Fatalf("error = %v, want ErrPermissionDenied", err)
	}
	if decision.Allowed {
		t.Fatal("the decision allowed the mutation")
	}
	if decision.ReasonCode != agentruntime.RefusalReadOnly {
		t.Fatalf("reason code = %q, want %q", decision.ReasonCode, agentruntime.RefusalReadOnly)
	}
	// A refusal that leaves no trace is indistinguishable from an agent that
	// never asked.
	waitFor(t, "the refusal to be recorded", func() bool {
		return containsString(watcher.topics(), agentcontract.TopicAgentPermissionDenied)
	})
}

func TestOpsAgentCannotRequestATaskStateChange(t *testing.T) {
	ops := agentruntime.NewOpsAgent()
	task := newStubAgent(agentruntime.TaskAgentName)
	orchestrator, _ := startOrchestrator(t, task, ops)

	decision, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: agentruntime.OpsAgentName, TaskID: "task-1", MutatesTaskState: true,
	})
	if err == nil || decision.Allowed {
		t.Fatal("the observer was allowed to change task state")
	}
	if decision.ReasonCode != agentruntime.RefusalReadOnly {
		t.Fatalf("reason code = %q", decision.ReasonCode)
	}
}

// The executing agent's declaration is the counterpart: it may do the thing the
// observer may not. A gate that refused everything would satisfy the safety
// requirement and break the product.
func TestTaskAgentMayRequestAWorldMutation(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	task.capabilities = []agentcontract.Capability{
		agentcontract.CapabilityTaskExecution, agentcontract.Capability("manipulation.pick"),
	}
	task.permission = agentcontract.Permission{MutatesWorld: true, MutatesTaskState: true}
	orchestrator, _ := startOrchestrator(t, task)

	decision, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: agentruntime.TaskAgentName, Capability: "manipulation.pick",
		TaskID: "task-1", MutatesWorld: true, MutatesTaskState: true,
	})
	if err != nil {
		t.Fatalf("the executing agent was refused its own declared work: %v", err)
	}
	if !decision.Allowed {
		t.Fatalf("decision = %#v", decision)
	}
}

func TestPermissionGateRefusesUndeclaredCapabilityAndUnknownAgent(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	task.capabilities = []agentcontract.Capability{agentcontract.CapabilityTaskExecution}
	task.permission = agentcontract.Permission{MutatesWorld: true, MutatesTaskState: true}
	orchestrator, runtime := mustOrchestrator(t, configFor(task), task)
	defer runtime.Close()

	undeclared, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: agentruntime.TaskAgentName, Capability: "manipulation.teleport", MutatesWorld: true,
	})
	if err == nil {
		t.Fatal("an undeclared capability was allowed")
	}
	if undeclared.ReasonCode != agentruntime.RefusalUndeclaredCapability {
		t.Fatalf("reason code = %q", undeclared.ReasonCode)
	}

	unknown, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: "ghost", MutatesWorld: true,
	})
	if err == nil {
		t.Fatal("an unregistered agent was allowed to act")
	}
	if unknown.ReasonCode != agentruntime.RefusalUnknownAgent {
		t.Fatalf("reason code = %q", unknown.ReasonCode)
	}
}

// A request that asks for nothing is refused rather than silently allowed: an
// allowed no-op would make the gate's answer meaningless.
func TestPermissionGateRefusesAnEmptyRequest(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	orchestrator, runtime := mustOrchestrator(t, configFor(task), task)
	defer runtime.Close()
	decision, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: agentruntime.TaskAgentName,
	})
	if err == nil || decision.Allowed {
		t.Fatal("a request that changes nothing was allowed")
	}
	if decision.ReasonCode != agentruntime.RefusalNoMutation {
		t.Fatalf("reason code = %q", decision.ReasonCode)
	}
}

func TestPermissionGateRequiresApprovalWhenDeclared(t *testing.T) {
	guarded := newStubAgent("guarded")
	guarded.capabilities = []agentcontract.Capability{agentcontract.Capability("guarded.act")}
	guarded.permission = agentcontract.Permission{
		MutatesWorld: true, MutatesTaskState: true, RequiresApproval: true,
	}
	orchestrator, runtime := mustOrchestrator(t, configFor(guarded), guarded)
	defer runtime.Close()

	without, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: "guarded", Capability: "guarded.act", MutatesWorld: true,
	})
	if err == nil {
		t.Fatal("a request needing approval was allowed without one")
	}
	if without.ReasonCode != agentruntime.RefusalApprovalMissing {
		t.Fatalf("reason code = %q", without.ReasonCode)
	}

	with, err := orchestrator.RequestMutation(context.Background(), agentruntime.MutationRequest{
		Agent: "guarded", Capability: "guarded.act", MutatesWorld: true, ApprovalPresent: true,
	})
	if err != nil || !with.Allowed {
		t.Fatalf("an approved request was refused: %v %#v", err, with)
	}
}

// --- lifecycle -------------------------------------------------------------

func TestOrchestratorShutdownStopsEveryAgentAndIsIdempotent(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	orchestrator, runtime := mustOrchestrator(t, configFor(task, ops), task, ops)
	defer runtime.Close()
	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatalf("starting twice is not an error: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := orchestrator.Shutdown(ctx); err != nil {
		t.Fatalf("shutdown: %v", err)
	}
	// Shutdown must be bounded by the caller's deadline even if an agent
	// misbehaves, and calling it twice must be safe.
	second, cancelSecond := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancelSecond()
	if err := orchestrator.Shutdown(second); err != nil {
		t.Fatalf("second shutdown: %v", err)
	}
	task.mu.Lock()
	shutdowns := task.shutdowns
	task.mu.Unlock()
	if shutdowns != 2 {
		t.Fatalf("the agent was shut down %d times, want once per Shutdown call", shutdowns)
	}
}

// An observer whose event handling fails must not stop the runtime: an agent's
// reaction failing is that agent's problem, not the task's.
func TestAgentEventHandlingFailureIsRecordedNotPropagated(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	broken := newStubAgent("broken")
	broken.subscriptions = []string{agentcontract.TopicOpsAll, agentcontract.TopicAgentAll}
	broken.failNext = true

	orchestrator, runtime := startOrchestrator(t, task, broken)
	// The orchestrator must survive an agent that fails, so a later event is
	// still delivered to it.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: "task-1",
	})
	waitFor(t, "the first event to be delivered", func() bool {
		return len(broken.events()) >= 1
	})
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: "task-1", ID: "second",
	})
	waitFor(t, "delivery to continue after the handling failure", func() bool {
		return len(broken.events()) >= 2
	})
	// The failure is reported as a health change so it is visible rather than
	// swallowed.
	waitFor(t, "the handling failure to be recorded", func() bool {
		for _, event := range broken.events() {
			if event.Payload["reasonCode"] == "EVENT_HANDLING_FAILED" {
				return true
			}
		}
		return false
	})
	_ = orchestrator
}

func TestOrchestratorHealthChangesArePublishedOncePerChange(t *testing.T) {
	task := newStubAgent(agentruntime.TaskAgentName)
	watcher := newStubAgent("watcher")
	watcher.subscriptions = []string{agentcontract.TopicAgentAll}

	config := configFor(task, watcher)
	config.HealthInterval = agentruntime.Duration(50 * time.Millisecond)
	_, _ = startOrchestratorWith(t, config, task, watcher)

	waitFor(t, "the first health report", func() bool {
		return containsString(watcher.topics(), agentcontract.TopicAgentHealthChanged)
	})
	// Let several evaluation ticks pass.
	time.Sleep(220 * time.Millisecond)
	changes := 0
	for _, topic := range watcher.topics() {
		if topic == agentcontract.TopicAgentHealthChanged {
			changes++
		}
	}
	// Two agents, one change each. A health report per tick would be dozens.
	if changes > 4 {
		t.Fatalf("health changed was published %d times over several ticks; "+
			"health must be announced on change, not on every check", changes)
	}
}

// eventCollector records what a real agent publishes, since OpsAgent reports
// through a sink rather than exposing what it saw.
//
// It locks because the agent publishes from the runtime's delivery goroutine
// while the test reads from its own: an unsynchronised append here is a data
// race the race detector reports, not a flake that quietly passes.
type eventCollector struct {
	mu     sync.Mutex
	events []agentcontract.Event
}

// collectors maps an agent to the collector installed on it, so a test that
// forgot to keep the reference can still read back what was published.
var collectors = struct {
	mu sync.Mutex
	m  map[agentcontract.Agent]*eventCollector
}{m: map[agentcontract.Agent]*eventCollector{}}

// recorderFor returns the collector installed on an agent, installing one if the
// test did not.
func recorderFor(agent agentcontract.Agent) *eventCollector {
	collectors.mu.Lock()
	defer collectors.mu.Unlock()
	if collector, ok := collectors.m[agent]; ok {
		return collector
	}
	collector := &eventCollector{}
	collectors.m[agent] = collector
	return collector
}

func (c *eventCollector) sink(_ context.Context, event agentcontract.Event) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.events = append(c.events, event)
}

func (c *eventCollector) all() []agentcontract.Event {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]agentcontract.Event(nil), c.events...)
}

func (c *eventCollector) topics() []string {
	topics := []string{}
	for _, event := range c.all() {
		topics = append(topics, event.Topic)
	}
	return topics
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
