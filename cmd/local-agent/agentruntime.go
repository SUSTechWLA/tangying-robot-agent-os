package main

import (
	"context"
	"log"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	edgeagent "github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// This file is the composition root of the agent runtime: the one place that
// knows both what agents exist and how they are hosted.
//
// Keeping it here is what lets the runtime stay ignorant of the agents it runs.
// agentruntime defines the interface, the bus, the registry and the
// orchestrator; it does not import the execution agent or the observing agent,
// so adding EvalAgent, ExperienceAgent or EscalationAgent later means adding a
// file here and a name in configuration rather than editing the runtime.

// agentRuntimeConfig reads which agents to run.
//
// The default is the two agents this version ships. The value is a documented
// deployment parameter: TANGYING_AGENTS=task disables observation without
// disabling execution, which is the configuration an operator uses to establish
// whether an observation is changing behaviour.
func agentRuntimeConfig(getenv func(string) string) agentruntime.Config {
	config := agentruntime.DefaultConfig()
	if getenv == nil {
		return config
	}
	configured := getenv("TANGYING_AGENTS")
	if configured == "" {
		return config
	}
	names := make([]string, 0, 2)
	for _, name := range strings.Split(configured, ",") {
		name = strings.TrimSpace(name)
		if name != "" {
			names = append(names, name)
		}
	}
	if len(names) == 0 {
		// An explicitly empty value is more likely a mistake than a request to
		// run nothing, and running nothing would look identical to running
		// fine. The default is kept and the mistake is reported.
		log.Printf("TANGYING_AGENTS is set but names no agent; using the default %v", config.Enabled)
		return config
	}
	config.Enabled = names
	return config
}

// startAgentRuntime builds and starts the multi-agent runtime over the already
// constructed local stack.
//
// Every failure here is reported and returned as nil, nil rather than aborting
// startup. The agent runtime observes execution; it is not part of it, and a
// system that refused to run a task because an observer could not be started
// would have made observability part of the safety path — the exact inversion
// the rest of this repository avoids.
func startAgentRuntime(
	ctx context.Context,
	getenv func(string) string,
	service *tasks.Service,
	runner *edgeagent.Runner,
	store middleware.ExecutionStore,
	telemetrySource func(ctx context.Context, taskID string) (telemetry.Snapshot, error),
) (*agentruntime.Orchestrator, *agentruntime.AgentRuntime, *agentruntime.AlertStore) {
	config := agentRuntimeConfig(getenv)

	// The store exists before the registry because the observer writes to it on
	// every evaluation, including the first.
	runnerAlerts := agentruntime.NewAlertStore(0)

	registry, err := agentruntime.NewRegistry(config)
	if err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}

	// The execution agent is the existing runner, which is a native Agent. It
	// is registered, not rewired: the local execution lifecycle keeps calling it
	// exactly as before.
	runner.Tasks = service
	if err := registry.Register(runner); err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}

	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = telemetrySource
	observer.Memory = agentruntime.NewMemory(store)
	observer.RunnerAlerts = runnerAlerts
	// The durable task index is what makes the supervisor useful across a
	// restart: without it, a process that starts after a failure sees a clean,
	// quiet robot and reports nothing.
	observer.History = taskHistory{service: service}
	if err := registry.Register(observer); err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}

	if err := registry.Validate(); err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}

	runtime := agentruntime.New()
	orchestrator := agentruntime.NewOrchestrator(runtime, registry)
	orchestrator.Telemetry = telemetrySource
	orchestrator.Memory = agentruntime.NewMemory(store)

	// The two directions of the bridge between the durable ledger and the agent
	// event stream:
	//
	//   ledger -> bus: a state change or a tool dispatch recorded by execution
	//   is projected into the agent vocabulary, so an observer sees execution
	//   without execution having to know an observer exists.
	//
	//   agent -> ledger: an event an agent publishes is appended to the same
	//   per-task ledger, which is what makes an observer's findings appear in
	//   the ordinary task replay.
	//
	// The same fact can travel both ways, so the runtime suppresses a repeat of
	// an identity it has already delivered. Without that, every action would
	// appear twice in a replay.
	service.ObserveEvents(func(ctx context.Context, taskID string, event tasks.TaskEvent) {
		projected, ok := agentruntime.ProjectTaskEvent(taskID, event)
		if !ok {
			return
		}
		runtime.Publish(ctx, projected)
	})
	runtime.SetEventSink(func(ctx context.Context, event agentcontract.Event) {
		durable, ok := agentruntime.TaskEventFromEvent(event)
		if !ok {
			return
		}
		if _, err := service.AppendEvent(ctx, event.TaskID, durable); err != nil {
			// Persistence of an agent's report must never disturb execution.
			log.Printf("task %s: agent event %s not recorded: %v", event.TaskID, event.Topic, err)
		}
	})

	// The orchestrator polls the runtime's per-subscriber queues, so it has to
	// be told when there is something to collect.
	runtime.SetPublishObserver(orchestrator.Wake)

	// The executing agent publishes through the bus as well as the ledger, so a
	// live observer reacts to a dispatch immediately rather than at the next
	// ledger read.
	runner.Events = func(ctx context.Context, event agentcontract.Event) {
		runtime.Publish(ctx, event)
	}

	if err := orchestrator.Start(ctx); err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}
	log.Printf("agent runtime started: enabled=%v disabled=%v",
		orchestrator.AgentNames(), registry.Disabled())
	return orchestrator, runtime, runnerAlerts
}

// taskHistory adapts the task service to the observer's read-only task index.
//
// It is where "abnormal" is defined, and the definition is deliberately narrow.
// A cancelled task was an operator's decision and a paused one is waiting, so
// neither is a failure: reporting them would train an operator to ignore the
// supervisor. What counts is a task the system could not finish on its own.
type taskHistory struct {
	service *tasks.Service
}

func (h taskHistory) TaskIDs(ctx context.Context) ([]string, error) {
	all, err := h.service.List(ctx)
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(all))
	for _, task := range all {
		if task != nil && task.ID != "" {
			ids = append(ids, task.ID)
		}
	}
	return ids, nil
}

func (h taskHistory) Abnormal(ctx context.Context) ([]string, error) {
	all, err := h.service.List(ctx)
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0)
	for _, task := range all {
		if task != nil && needsAttention(task.State) {
			ids = append(ids, task.ID)
		}
	}
	return ids, nil
}

// Events reads one task's ledger for the supervisor.
//
// The projection is narrow on purpose: the contract carries what a supervisor
// needs, not the task package's whole event type, so the runtime does not depend
// on the store it is observing.
func (h taskHistory) Events(ctx context.Context, taskID string) ([]agentcontract.TaskEvent, error) {
	task, err := h.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	events := make([]agentcontract.TaskEvent, 0, len(task.Events))
	for _, event := range task.Events {
		events = append(events, agentcontract.TaskEvent{Type: event.Type, Payload: event.Payload})
	}
	return events, nil
}

// needsAttention reports whether a task state means someone has to look.
//
// CANCELLED and PAUSED are excluded on purpose: the first was an operator's own
// decision and the second is a routine wait. Calling them abnormal would make the
// supervisor noisy, and a noisy supervisor is one nobody reads.
func needsAttention(state taskgraph.TaskState) bool {
	switch state {
	case taskgraph.StateFailed,
		taskgraph.StateRecoverableFailure,
		taskgraph.StateFailedSafe,
		taskgraph.StateSafetyStopped,
		taskgraph.StateWaitingUser,
		taskgraph.StateBlocked:
		return true
	default:
		return false
	}
}

// containsAgent reports whether a name is in an agent list. It is a plain scan
// because the list is two entries long and a map would be more code than it
// saves.
func containsAgent(names []string, wanted string) bool {
	for _, name := range names {
		if name == wanted {
			return true
		}
	}
	return false
}
