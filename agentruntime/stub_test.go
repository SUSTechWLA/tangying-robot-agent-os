package agentruntime_test

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// stubAgent is a configurable Agent used to test the runtime itself. It is
// deliberately dumb: the runtime's behaviour must be observable without any real
// agent's logic getting in the way.
type stubAgent struct {
	name          string
	version       string
	capabilities  []agentcontract.Capability
	subscriptions []string
	permission    agentcontract.Permission
	health        agentcontract.Health

	mu        sync.Mutex
	received  []agentcontract.Event
	failNext  bool
	executed  int
	shutdowns int
}

func newStubAgent(name string) *stubAgent {
	return &stubAgent{
		name: name, version: "test",
		capabilities: []agentcontract.Capability{agentcontract.CapabilityObservation},
		permission:   agentcontract.ReadOnlyPermission(),
		health:       agentcontract.Health{Status: agentcontract.HealthHealthy},
	}
}

func (a *stubAgent) Name() string                                { return a.name }
func (a *stubAgent) Version() string                             { return a.version }
func (a *stubAgent) Capabilities() []agentcontract.Capability    { return a.capabilities }
func (a *stubAgent) Subscriptions() []string                     { return a.subscriptions }
func (a *stubAgent) Permissions() agentcontract.Permission       { return a.permission }
func (a *stubAgent) Health(context.Context) agentcontract.Health { return a.health }

func (a *stubAgent) OnEvent(_ context.Context, event agentcontract.Event) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.received = append(a.received, event)
	if a.failNext {
		a.failNext = false
		return errStubFailure
	}
	return nil
}

func (a *stubAgent) Execute(context.Context, agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.executed++
	return agentcontract.ExecutionOutcome{TaskID: "stub"}, nil
}

func (a *stubAgent) Shutdown(context.Context) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.shutdowns++
	return nil
}

func (a *stubAgent) events() []agentcontract.Event {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]agentcontract.Event(nil), a.received...)
}

func (a *stubAgent) topics() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	topics := make([]string, 0, len(a.received))
	for _, event := range a.received {
		topics = append(topics, event.Topic)
	}
	return topics
}

// collect makes the agent record everything it is shown directly, so a test can
// observe delivery without installing an extra subscriber.
//
// It takes the same lock as the accessors: the agent is written from the
// runtime's delivery goroutine and read from the test goroutine, and an
// unsynchronised slice append is a data race the race detector reports rather
// than a flake that hides.
func (a *stubAgent) collect() *stubAgent {
	return a
}

// seen reports the recorded topics, taking the agent's lock. It is the
// race-free form of topics() for tests that read while the runtime is running.
func (a *stubAgent) seen(topic string) bool {
	for _, recorded := range a.topics() {
		if recorded == topic {
			return true
		}
	}
	return false
}

var errStubFailure = stubError("stub agent failed to handle the event")

type stubError string

func (e stubError) Error() string { return string(e) }

// waitFor polls until condition holds or the deadline passes. Polling rather
// than sleeping a fixed time keeps the tests both fast and reliable: a fixed
// sleep is either slower than it needs to be or flaky.
func waitFor(t *testing.T, what string, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(2 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func mustRegistry(t *testing.T, config agentruntime.Config, agents ...agentcontract.Agent) *agentruntime.Registry {
	t.Helper()
	registry, err := agentruntime.NewRegistry(config)
	if err != nil {
		t.Fatalf("new registry: %v", err)
	}
	for _, agent := range agents {
		if err := registry.Register(agent); err != nil {
			t.Fatalf("register %s: %v", agent.Name(), err)
		}
	}
	return registry
}

func mustOrchestrator(t *testing.T, config agentruntime.Config, agents ...agentcontract.Agent) (*agentruntime.Orchestrator, *agentruntime.AgentRuntime) {
	t.Helper()
	registry := mustRegistry(t, config, agents...)
	runtime := agentruntime.New()
	orchestrator := agentruntime.NewOrchestrator(runtime, registry)
	// The delivery loop polls the runtime's queues, so it has to be told when
	// there is something to collect. Production wires this the same way, in the
	// code that builds both objects.
	runtime.SetPublishObserver(orchestrator.Wake)
	return orchestrator, runtime
}

// configFor builds a configuration enabling exactly the given agents.
//
// Tests must not start from DefaultConfig and then hand it a different set of
// agents: enabling an agent that is not registered is refused at startup, so
// such a mismatch would surface as a configuration error rather than as the
// behaviour under test. Deriving the configuration from the agents keeps each
// test's intent explicit about which agents are running.
func configFor(agents ...agentcontract.Agent) agentruntime.Config {
	names := make([]string, 0, len(agents))
	for _, agent := range agents {
		names = append(names, agent.Name())
	}
	return agentruntime.Config{Enabled: names}
}

// startOrchestrator builds, starts and registers cleanup for an orchestrator
// over exactly the given agents, with its configuration derived from them.
func startOrchestrator(t *testing.T, agents ...agentcontract.Agent) (*agentruntime.Orchestrator, *agentruntime.AgentRuntime) {
	t.Helper()
	return startOrchestratorWith(t, configFor(agents...), agents...)
}

// fastTick is the interval tests use when they depend on the evaluation loop.
//
// Release of a deferred proposal happens at an evaluation tick, which is
// deliberately infrequent in production. A test that waits on a real five-second
// tick would be both slow and flaky, so it shortens the interval rather than
// asserting on a wall-clock delay.
const fastTick = agentruntime.Duration(50 * time.Millisecond)

// startOrchestratorFast starts an orchestrator whose evaluation loop ticks
// quickly, for tests that wait on the safe-point release.
func startOrchestratorFast(t *testing.T, agents ...agentcontract.Agent) (*agentruntime.Orchestrator, *agentruntime.AgentRuntime) {
	t.Helper()
	config := configFor(agents...)
	config.HealthInterval = fastTick
	return startOrchestratorWith(t, config, agents...)
}

// startOrchestratorWith is startOrchestrator with an explicit configuration, for
// tests that need to vary a runtime setting such as the health interval.
func startOrchestratorWith(t *testing.T, config agentruntime.Config, agents ...agentcontract.Agent) (*agentruntime.Orchestrator, *agentruntime.AgentRuntime) {
	t.Helper()
	orchestrator, runtime := mustOrchestrator(t, config, agents...)
	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatalf("start orchestrator: %v", err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = orchestrator.Shutdown(ctx)
		runtime.Close()
	})
	return orchestrator, runtime
}
