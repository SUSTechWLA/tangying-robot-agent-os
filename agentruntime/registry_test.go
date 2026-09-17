package agentruntime_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

func TestDefaultConfigEnablesTaskAndOps(t *testing.T) {
	config := agentruntime.DefaultConfig()
	if len(config.Enabled) != 2 {
		t.Fatalf("default enabled = %#v, want exactly two agents", config.Enabled)
	}
	if !config.Requires(agentruntime.TaskAgentName) || !config.Requires(agentruntime.OpsAgentName) {
		t.Fatalf("default enabled = %#v, want task and ops", config.Enabled)
	}
}

// Running only the execution agent is a supported configuration, not a test
// fixture: an operator debugging whether observation changes behaviour turns it
// off and changes nothing else.
func TestExecutableOnlyConfigIsSupported(t *testing.T) {
	config := agentruntime.ExecutableConfig()
	if config.Requires(agentruntime.OpsAgentName) {
		t.Fatalf("enabled = %#v, want the observer off", config.Enabled)
	}
	if !config.Requires(agentruntime.TaskAgentName) {
		t.Fatalf("enabled = %#v, want the executor on", config.Enabled)
	}

	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	orchestrator, runtime := mustOrchestrator(t, config, task, ops)
	defer runtime.Close()

	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = orchestrator.Shutdown(context.Background()) }()

	if names := orchestrator.AgentNames(); len(names) != 1 || names[0] != agentruntime.TaskAgentName {
		t.Fatalf("started agents = %#v, want only task", names)
	}
	// The disabled agent must not be subscribed at all: a registered but
	// disabled agent that still received events would be running by accident.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: "task-1",
	})
	if len(ops.events()) != 0 {
		t.Fatalf("a disabled agent received events: %#v", ops.topics())
	}
}

func TestConfigRejectsContradictoryValues(t *testing.T) {
	cases := []struct {
		name   string
		config agentruntime.Config
	}{
		{"empty name", agentruntime.Config{Enabled: []string{""}}},
		{"whitespace around a name", agentruntime.Config{Enabled: []string{" task"}}},
		{"the same agent twice", agentruntime.Config{Enabled: []string{"task", "task"}}},
		{"negative queue capacity", agentruntime.Config{Enabled: []string{"task"}, QueueCapacity: -1}},
	}
	for _, testCase := range cases {
		if _, err := agentruntime.NewRegistry(testCase.config); !errors.Is(err, agentruntime.ErrInvalidConfig) {
			t.Errorf("%s: error = %v, want ErrInvalidConfig", testCase.name, err)
		}
	}
}

// A configuration naming an agent that is not registered is refused rather than
// silently reduced. Starting with less than was asked for, and saying nothing,
// is how an operator comes to believe an observer is running when it is not.
func TestValidateRefusesEnabledButUnregisteredAgent(t *testing.T) {
	registry := mustRegistry(t, agentruntime.Config{Enabled: []string{"task", "eval"}}, newStubAgent("task"))
	err := registry.Validate()
	if !errors.Is(err, agentruntime.ErrUnknownAgent) {
		t.Fatalf("validate error = %v, want ErrUnknownAgent", err)
	}
	if !strings.Contains(err.Error(), "eval") {
		t.Fatalf("the error does not name the missing agent: %v", err)
	}
}

// The extension requirement, tested rather than asserted in prose: a third agent
// needs an implementation and a registration, and nothing else.
func TestRegisteringAFutureAgentNeedsNoCoreChange(t *testing.T) {
	// Stand-ins for the agents that are only interfaces today. They are stubs
	// here for the same reason they are interfaces in the product: the point
	// under test is that the runtime accepts them, not what they would do.
	evaluator := newStubAgent("eval")
	evaluator.capabilities = []agentcontract.Capability{agentcontract.CapabilityCompletionVerification}
	evaluator.subscriptions = []string{agentcontract.TopicTaskAll}

	experience := newStubAgent("experience")
	experience.capabilities = []agentcontract.Capability{agentcontract.CapabilityExperienceRecording}
	experience.subscriptions = []string{agentcontract.TopicOpsAll}

	escalation := newStubAgent("escalation")
	escalation.capabilities = []agentcontract.Capability{agentcontract.CapabilityEscalation}
	escalation.subscriptions = []string{agentcontract.TopicOpsAll}

	task := newStubAgent(agentruntime.TaskAgentName)
	ops := newStubAgent(agentruntime.OpsAgentName)
	config := agentruntime.Config{Enabled: []string{"task", "ops", "eval", "experience", "escalation"}}

	orchestrator, runtime := mustOrchestrator(t, config, task, ops, evaluator, experience, escalation)
	defer runtime.Close()
	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = orchestrator.Shutdown(context.Background()) }()

	if names := orchestrator.AgentNames(); len(names) != 5 {
		t.Fatalf("started agents = %#v, want all five", names)
	}
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: "task-1",
	})
	waitFor(t, "the future agents to receive the event", func() bool {
		return len(experience.events()) > 0 && len(escalation.events()) > 0
	})
	// The evaluator subscribes to task events only, so an ops event must not
	// reach it. This is what per-agent subscription routing is for.
	if len(evaluator.events()) != 0 {
		t.Fatalf("the evaluator received an event it did not subscribe to: %#v", evaluator.topics())
	}
}

func TestRegistryRefusesDuplicateAndEmptyNames(t *testing.T) {
	registry := mustRegistry(t, agentruntime.Config{Enabled: []string{"task"}}, newStubAgent("task"))
	if err := registry.Register(newStubAgent("task")); !errors.Is(err, agentruntime.ErrAgentAlreadyRegistered) {
		t.Fatalf("duplicate registration error = %v, want ErrAgentAlreadyRegistered", err)
	}
	if err := registry.Register(newStubAgent("")); err == nil {
		t.Fatal("an agent with an empty name was registered; its events could not be attributed")
	}
	if err := registry.Register(nil); err == nil {
		t.Fatal("a nil agent was registered")
	}
	// An agent that declares nothing is more likely a wiring mistake than a
	// deliberate participant.
	empty := newStubAgent("empty")
	empty.capabilities = nil
	if err := registry.Register(empty); err == nil {
		t.Fatal("an agent that declares no capabilities was registered")
	}
	// A subscription typo must fail at registration, not silently at runtime.
	typo := newStubAgent("typo")
	typo.subscriptions = []string{"ops.anomoly_detected"}
	if err := registry.Register(typo); err == nil {
		t.Fatal("an agent with a misspelled subscription was registered")
	}
}

func TestRegistryReportsRegisteredEnabledAndDisabled(t *testing.T) {
	config := agentruntime.Config{Enabled: []string{"task"}}
	registry := mustRegistry(t, config, newStubAgent("task"), newStubAgent("ops"))
	if names := registry.Registered(); len(names) != 2 {
		t.Fatalf("registered = %#v", names)
	}
	if enabled := registry.Enabled(); len(enabled) != 1 || enabled[0] != "task" {
		t.Fatalf("enabled = %#v", enabled)
	}
	// A shipped-but-off agent is reported, so an operator can see it exists
	// without running it.
	disabled := registry.Disabled()
	if len(disabled) != 1 || disabled[0] != "ops" {
		t.Fatalf("disabled = %#v, want ops", disabled)
	}
	if _, ok := registry.Discover("ops"); !ok {
		t.Fatal("a registered agent could not be discovered")
	}
	if _, ok := registry.Discover("missing"); ok {
		t.Fatal("discover found an agent that was never registered")
	}
}
