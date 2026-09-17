package agentruntime_test

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// These tests exist because of a defect that every existing test passed.
//
// The composition root assigned the bus to the executing agent by hand and to no
// one else, so the observing agent's findings reached the alert store and never
// reached the bus. The recovery agent subscribed to those findings never woke,
// while the console kept listing findings and every agent kept reporting healthy.
// The unit tests could not see it: each one assigned `Publish` itself before
// asserting, so they verified a wiring that production did not have.
//
// The rule these tests enforce is therefore not "the observer publishes". It is
// "the runtime publishes for the agent, and nothing outside the runtime has to
// remember to". A test that wires the sink by hand cannot enforce that, so none
// of the tests below assigns a sink.

// publishingAgent is a stub that declares the optional Publisher capability.
// It records what it was given rather than what it was told, because "was I given
// a bus at all" is the question the production defect got wrong.
type publishingAgent struct {
	*stubAgent

	mu        sync.Mutex
	publish   func(ctx context.Context, event agentcontract.Event)
	installed int
}

func newPublishingAgent(name string) *publishingAgent {
	agent := &publishingAgent{stubAgent: newStubAgent(name)}
	agent.capabilities = []agentcontract.Capability{agentcontract.CapabilityObservation}
	return agent
}

// SetPublish implements agentcontract.Publisher.
func (a *publishingAgent) SetPublish(publish func(ctx context.Context, event agentcontract.Event)) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.publish = publish
	a.installed++
}

func (a *publishingAgent) sink() func(ctx context.Context, event agentcontract.Event) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.publish
}

func (a *publishingAgent) installCount() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.installed
}

// emit publishes through whatever the runtime gave it, and reports whether it
// had anything to publish through at all.
func (a *publishingAgent) emit(ctx context.Context, event agentcontract.Event) bool {
	sink := a.sink()
	if sink == nil {
		return false
	}
	sink(ctx, event)
	return true
}

// TestTheRuntimeHandsTheBusToEveryAgentThatPublishes is the regression test for
// the wiring defect. Nothing in this test assigns a sink; if the runtime stops
// injecting one, the agent is handed nothing and the assertion fails.
func TestTheRuntimeHandsTheBusToEveryAgentThatPublishes(t *testing.T) {
	agent := newPublishingAgent("publisher")
	orchestrator, runtime := startOrchestrator(t, agent)
	_ = runtime

	if agent.installCount() != 1 {
		t.Fatalf("the runtime installed the bus %d times, want exactly once", agent.installCount())
	}
	if agent.sink() == nil {
		t.Fatal("an agent that declares it publishes was started without a bus")
	}
	if got := orchestrator.Publishers(); len(got) != 1 || got[0] != "publisher" {
		t.Fatalf("the runtime reports publishers %v, want [publisher]", got)
	}
}

// TestAnAgentGivenTheBusByTheRuntimeActuallyReachesSubscribers closes the loop:
// being handed a sink is not the point, delivery is. Without this, injection
// could be "fixed" by assigning a function that goes nowhere.
func TestAnAgentGivenTheBusByTheRuntimeActuallyReachesSubscribers(t *testing.T) {
	agent := newPublishingAgent("publisher")
	_, runtime := startOrchestrator(t, agent)

	seen := make(chan agentcontract.Event, 4)
	subscription, err := runtime.Subscribe("watcher", []string{agentcontract.TopicOpsAnomalyDetected}, 0)
	if err != nil {
		t.Fatalf("subscribe watcher: %v", err)
	}
	t.Cleanup(func() { _ = subscription.Close() })
	go func() {
		for {
			event, receiveErr := subscription.Receive(context.Background())
			if receiveErr != nil {
				return
			}
			seen <- event
		}
	}()

	if !agent.emit(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, Agent: "publisher",
		OccurredAt: time.Now().UTC(),
		Payload:    map[string]any{"code": "ANOMALY_TEST", "component": "test"},
	}) {
		t.Fatal("the agent was never given a bus, so nothing could be delivered")
	}

	select {
	case event := <-seen:
		if event.Topic != agentcontract.TopicOpsAnomalyDetected {
			t.Fatalf("delivered topic %s, want %s", event.Topic, agentcontract.TopicOpsAnomalyDetected)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("an event published by an agent was not delivered to a subscriber")
	}
}

// TestAnAgentThatOnlyObservesIsNotForcedToPublish pins the other half of the
// decision. Publishing is an optional capability rather than a method on the
// Agent interface, so an agent that has nothing to say is not required to
// implement a port it will never use.
func TestAnAgentThatOnlyObservesIsNotForcedToPublish(t *testing.T) {
	silent := newStubAgent("silent")
	orchestrator, _ := startOrchestrator(t, silent)

	if _, ok := agentcontract.Agent(silent).(agentcontract.Publisher); ok {
		t.Fatal("the test stub must not implement Publisher for this test to mean anything")
	}
	if got := orchestrator.Publishers(); len(got) != 0 {
		t.Fatalf("a non-publishing agent was reported as a publisher: %v", got)
	}
}

// TestTheObserverReachesTheBusThroughTheRuntime is the production defect itself,
// with the real observing agent.
//
// The observer is given a durable task that ended abnormally and is never
// assigned a sink. On the old wiring it found the task, recorded it, and said
// nothing on the bus; the recovery agent listening for that report could not
// have seen it. Nothing here reproduces the composition root, because the
// composition root is exactly what must not be needed.
func TestTheObserverReachesTheBusThroughTheRuntime(t *testing.T) {
	observer := agentruntime.NewOpsAgent()
	observer.History = staticHistory{tasks: []string{"task-abnormal"}, abnormal: []string{"task-abnormal"}}
	observer.RunnerAlerts = agentruntime.NewAlertStore(0)

	_, runtime := startOrchestratorFast(t, observer)

	received := make(chan agentcontract.Event, 8)
	subscription, err := runtime.Subscribe("recovery-trigger", []string{agentcontract.TopicOpsAnomalyDetected}, 0)
	if err != nil {
		t.Fatalf("subscribe recovery trigger: %v", err)
	}
	t.Cleanup(func() { _ = subscription.Close() })
	go func() {
		for {
			event, receiveErr := subscription.Receive(context.Background())
			if receiveErr != nil {
				return
			}
			received <- event
		}
	}()

	waitFor(t, "the observer's finding to reach the bus", func() bool {
		return len(received) > 0
	})

	event := <-received
	if event.Topic != agentcontract.TopicOpsAnomalyDetected {
		t.Fatalf("delivered topic %s, want %s", event.Topic, agentcontract.TopicOpsAnomalyDetected)
	}
	if event.Agent != agentruntime.OpsAgentName {
		t.Fatalf("event attributed to %q, want %q", event.Agent, agentruntime.OpsAgentName)
	}
	// The consumer of this event is the recovery agent, and it can only reason
	// about a payload it can read back into a finding. A report with no code
	// would be delivered and then skipped, which looks identical to silence.
	if code, _ := event.Payload["code"].(string); code == "" {
		t.Fatalf("the report carries no code, so no consumer could act on it: %#v", event.Payload)
	}
}

// observingStub counts the evaluation passes it is given.
type observingStub struct {
	*stubAgent

	mu    sync.Mutex
	looks int
}

func newObservingStub(name string) *observingStub {
	return &observingStub{stubAgent: newStubAgent(name)}
}

func (a *observingStub) Observe(context.Context) []agentruntime.Finding {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.looks++
	return nil
}

func (a *observingStub) looksTaken() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.looks
}

// TestTheFirstEvaluationDoesNotWaitForTheFirstTick is the restart case.
//
// The evaluation loop used to wait one full interval before looking, so for five
// seconds after every start a task that had already failed, a latched emergency
// stop and a robot that could not localise were all invisible — and the durable
// sweep that exists precisely to catch those is only useful if it runs. With the
// interval set to an hour, this test fails unless the first pass happens at
// start.
func TestTheFirstEvaluationDoesNotWaitForTheFirstTick(t *testing.T) {
	observer := newObservingStub("observer")
	config := configFor(observer)
	config.HealthInterval = agentruntime.Duration(time.Hour)

	orchestrator, runtime := mustOrchestrator(t, config, observer)
	runtime.SetPublishObserver(orchestrator.Wake)
	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatalf("start orchestrator: %v", err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = orchestrator.Shutdown(ctx)
		runtime.Close()
	})

	waitFor(t, "the first evaluation to run at start", func() bool {
		return observer.looksTaken() > 0
	})
}
