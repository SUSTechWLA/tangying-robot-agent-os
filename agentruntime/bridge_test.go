package agentruntime_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	edgeagent "github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// This is the bridge that the product depends on, tested end to end: execution
// records a fact, the observing agent sees it, and what the observer concludes
// lands back in the same durable ledger a person replays.
//
// Both directions matter and neither is obvious from the two components read
// separately, so the wiring is exercised here rather than assumed.

// wireRuntime reproduces the composition the local agent performs, over an
// in-memory service.
func wireRuntime(t *testing.T, service *tasks.Service, runner *edgeagent.Runner, source func(context.Context, string) (telemetry.Snapshot, error)) (*agentruntime.Orchestrator, *agentruntime.AgentRuntime) {
	t.Helper()
	runtime := agentruntime.New()

	// The observer reports through the bus, so the bus must exist before the
	// agent does. This is the same ordering the composition root uses.
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = source
	observer.Publish = func(ctx context.Context, event agentcontract.Event) {
		runtime.Publish(ctx, event)
	}

	config := agentruntime.Config{
		Enabled:        []string{agentruntime.TaskAgentName, agentruntime.OpsAgentName},
		HealthInterval: fastTick,
	}
	registry := mustRegistry(t, config, runner, observer)
	orchestrator := agentruntime.NewOrchestrator(runtime, registry)

	// The two directions of the bridge, exactly as the composition root wires
	// them: an agent event is persisted into the ledger, and a ledger entry is
	// projected back onto the bus.
	runtime.SetEventSink(func(ctx context.Context, event agentcontract.Event) {
		durable, ok := agentruntime.TaskEventFromEvent(event)
		if !ok {
			return
		}
		if _, err := service.AppendEvent(ctx, event.TaskID, durable); err != nil {
			t.Errorf("append agent event: %v", err)
		}
	})
	service.ObserveEvents(func(ctx context.Context, taskID string, event tasks.TaskEvent) {
		projected, ok := agentruntime.ProjectTaskEvent(taskID, event)
		if !ok {
			return
		}
		runtime.Publish(ctx, projected)
	})
	runtime.SetPublishObserver(orchestrator.Wake)
	// The executing agent announces each dispatch on the bus as well as
	// recording it, which is what lets a live observer react immediately rather
	// than at the next ledger read.
	runner.Events = func(ctx context.Context, event agentcontract.Event) {
		runtime.Publish(ctx, event)
	}
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

func emergencyTelemetry(timeout time.Time) func(context.Context, string) (telemetry.Snapshot, error) {
	return func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{
			RobotID: "robot-local", Adapter: "local-runtime", SchemaVersion: "robot.v1",
			ObservedAt: time.Now().UTC(), EmergencyStopped: true,
			Anomalies: []string{"EMERGENCY_STOP_LATCHED"},
		}, nil
	}
}

// A task state change recorded by execution must reach the observing agent. This
// is the ledger -> bus direction, and it is what lets an observer exist without
// execution knowing about it.
func TestLedgerStateChangeReachesTheObserver(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-bridge", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}

	runner := edgeagent.NewRunner(nil, nil, nil)
	_, runtime := wireRuntime(t, service, runner, emergencyTelemetry(time.Now()))

	// Execution records the transition the way the local lifecycle does.
	if err := service.Transition(context.Background(), task.ID, taskgraph.StateObserving, "local execution started"); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "the observer to be told about the transition", func() bool {
		current, err := service.Get(context.Background(), task.ID)
		if err != nil {
			return false
		}
		for _, event := range current.Events {
			if event.Type == agentcontract.TopicStateTransition {
				return true
			}
		}
		return false
	})
	_ = runtime
}

// The observer -> ledger direction: what the observer concludes is written back
// into the same per-task ledger, which is what puts it in the replay.
func TestObserverFindingsAreRecordedInTheTaskLedger(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-findings", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}

	runner := edgeagent.NewRunner(nil, nil, nil)
	_, runtime := wireRuntime(t, service, runner, emergencyTelemetry(time.Now()))

	// An emergency stop is reported by the robot, observed by the agent, and
	// must end up in the ledger under the observer's own topic.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicStateTransition, TaskID: task.ID,
		Payload: agentcontract.StateTransitionPayload{From: "READY", To: "OBSERVING"}.Encode(),
	})

	waitFor(t, "the observer's finding to be recorded in the ledger", func() bool {
		current, err := service.Get(context.Background(), task.ID)
		if err != nil {
			return false
		}
		for _, event := range current.Events {
			if event.Type == agentcontract.TopicOpsAnomalyDetected {
				return true
			}
		}
		return false
	})
}

// The acceptance criterion, end to end: after a task has run, the ordinary
// replay shows events attributed to both agents.
func TestReplayShowsBothAgentsAfterExecution(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-replay-e2e", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}

	runner := edgeagent.NewRunner(nil, nil, nil)
	_, runtime := wireRuntime(t, service, runner, emergencyTelemetry(time.Now()))

	// The executing agent publishes a dispatch, and the observer sees an
	// emergency stop. Both routes into the ledger are exercised.
	runtime.Publish(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: task.ID, Agent: agentruntime.TaskAgentName,
		Payload: agentcontract.ActionPayload{
			ToolName: "manipulation.pick", ActivityStatus: "SENDING", StepID: "pick",
		}.Encode(),
	})

	waitFor(t, "both agents to appear in the ledger", func() bool {
		current, err := service.Get(context.Background(), task.ID)
		if err != nil {
			return false
		}
		agents := map[string]bool{}
		for _, event := range current.Events {
			if agent, ok := event.Payload["agent"].(string); ok && agent != "" {
				agents[agent] = true
			}
		}
		return agents[agentruntime.TaskAgentName] && agents[agentruntime.OpsAgentName]
	})

	current, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task:   current,
		Events: current.Events,
	})
	agents := map[string]int{}
	for _, event := range view.AgentEvents {
		agents[event.Agent]++
	}
	if agents[agentruntime.TaskAgentName] == 0 {
		t.Fatalf("the replay shows no task agent events: %#v", view.AgentEvents)
	}
	if agents[agentruntime.OpsAgentName] == 0 {
		t.Fatalf("the replay shows no observer events: %#v", view.AgentEvents)
	}
}

// The same fact arrives by two routes — an agent publishes it, and the ledger
// observer feeds it back — and must be delivered once. Without suppression every
// action would appear twice in a replay.
func TestAFactReachingTheBusTwiceIsDeliveredOnce(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-dedup", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}

	watcher := newStubAgent("watcher")
	watcher.subscriptions = []string{agentcontract.TopicActionAll}
	runner := edgeagent.NewRunner(nil, nil, nil)

	config := agentruntime.Config{Enabled: []string{agentruntime.TaskAgentName, "watcher"}, HealthInterval: fastTick}
	registry := mustRegistry(t, config, runner, watcher)
	runtime := agentruntime.New()
	orchestrator := agentruntime.NewOrchestrator(runtime, registry)
	runtime.SetEventSink(func(ctx context.Context, event agentcontract.Event) {
		durable, ok := agentruntime.TaskEventFromEvent(event)
		if !ok {
			return
		}
		if _, err := service.AppendEvent(ctx, event.TaskID, durable); err != nil {
			t.Errorf("append: %v", err)
		}
	})
	service.ObserveEvents(func(ctx context.Context, taskID string, event tasks.TaskEvent) {
		projected, ok := agentruntime.ProjectTaskEvent(taskID, event)
		if !ok {
			return
		}
		runtime.Publish(ctx, projected)
	})
	runtime.SetPublishObserver(orchestrator.Wake)
	if err := orchestrator.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = orchestrator.Shutdown(ctx)
		runtime.Close()
	})

	// Published with the identity the ledger projection will derive, which is
	// the case the suppression exists for.
	event := agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: task.ID,
		ID: task.ID + "#2", Agent: agentruntime.TaskAgentName,
		Payload: agentcontract.ActionPayload{
			ToolName: "manipulation.pick", ActivityStatus: "SENDING", StepID: "pick",
		}.Encode(),
	}
	runtime.Publish(context.Background(), event)

	waitFor(t, "the watcher to receive the dispatch", func() bool {
		return len(watcher.topics()) >= 1
	})
	// Give the ledger round trip time to complete and be projected back.
	time.Sleep(200 * time.Millisecond)

	dispatches := 0
	for _, topic := range watcher.topics() {
		if topic == agentcontract.TopicActionExecuted {
			dispatches++
		}
	}
	if dispatches != 1 {
		t.Fatalf("the dispatch was delivered %d times; one fact must be delivered once", dispatches)
	}
}

// The full chain the requirement describes: a fault happens, the supervisor
// diagnoses it, the diagnosis is recorded, and a person replaying the task sees
// the diagnosis and what to do about it.
//
// Every link is exercised, because each one has failed in a different way during
// development: the fault not being detected, the detection not being persisted,
// and the persistence not reaching the projection.
func TestFaultDiagnosisReachesTheReplayWithAdvice(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, nil)
	parsed, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	task := &tasks.Task{
		ID: "task-diagnosed", Intent: parsed, State: taskgraph.StateReady,
		Approved: true, CurrentRevision: 1,
	}
	if err := store.Create(context.Background(), task); err != nil {
		t.Fatal(err)
	}

	// A process was interrupted mid-action: the durable record has a physical
	// step that never reached a terminal state.
	execution := durableSteps{runs: map[string][]middleware.StepRun{
		task.ID: {{
			StepRecord: middleware.StepRecord{
				TaskID: task.ID, StepID: "pick",
				Capability: "manipulation.pick", SafetyLevel: "physical_motion",
			},
			Status: middleware.StepStarted,
		}},
	}}

	runner := edgeagent.NewRunner(nil, nil, nil)
	_, runtime := wireRuntime(t, service, runner, quietTelemetry())
	// The supervisor is rebuilt with the durable record, the way a restarted
	// process would be.
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = quietTelemetry()
	observer.Memory = agentruntime.NewMemory(execution)
	observer.History = staticHistory{tasks: []string{task.ID}, abnormal: []string{task.ID}}
	observer.Publish = func(ctx context.Context, event agentcontract.Event) {
		runtime.Publish(ctx, event)
	}
	observer.Observe(context.Background())

	waitFor(t, "the diagnosis to reach the ledger", func() bool {
		current, err := service.Get(context.Background(), task.ID)
		if err != nil {
			return false
		}
		for _, event := range current.Events {
			if event.Type == agentcontract.TopicOpsAnomalyDetected {
				return true
			}
		}
		return false
	})

	current, err := service.Get(context.Background(), task.ID)
	if err != nil {
		t.Fatal(err)
	}
	view := tasks.ProjectExperience(tasks.ExperienceInput{Task: current, Events: current.Events})

	var diagnosis *tasks.AgentEventView
	var hypothesis *tasks.AgentEventView
	for index := range view.AgentEvents {
		event := &view.AgentEvents[index]
		// The same condition produces an anomaly (what was seen) and a hypothesis
		// (what it means). They are identified differently on purpose: the anomaly
		// carries the rule code, while the hypothesis carries the failure class,
		// because the class is the classifier's authoritative output and not the
		// rule's private name for the finding.
		switch event.Topic {
		case agentcontract.TopicOpsAnomalyDetected:
			if event.Code == agentruntime.AnomalyUnverifiedMutation {
				diagnosis = event
			}
		case agentcontract.TopicOpsRootCauseHypothesis:
			if category, _ := event.Payload["category"].(string); category == "UNKNOWN_OUTCOME" {
				hypothesis = event
			}
		}
	}
	if diagnosis == nil {
		t.Fatalf("the replay does not show the diagnosis: %#v", view.AgentEvents)
	}
	if diagnosis.Agent != agentruntime.OpsAgentName {
		t.Fatalf("diagnosis attributed to %q, want the supervisor", diagnosis.Agent)
	}
	// The three things a person needs: what it is, that retrying is forbidden,
	// and what to actually do.
	if diagnosis.Severity != agentruntime.SeverityCritical {
		t.Fatalf("severity = %q; an unconfirmed physical action is critical", diagnosis.Severity)
	}
	if diagnosis.Summary == "" {
		t.Fatal("the replay shows a diagnosis with no readable summary")
	}
	// The explanation must also be visible, with its own evidence chain and a
	// stated certainty: a conclusion a reader cannot check is a claim.
	if hypothesis == nil {
		t.Fatalf("the replay shows no root cause hypothesis: %#v", view.AgentEvents)
	}
	if hypothesis.Confidence <= 0 {
		t.Fatal("the hypothesis does not state how certain it is")
	}
	if len(hypothesis.EvidenceIDs) == 0 && hypothesis.Payload["evidenceChain"] == nil {
		t.Fatal("the hypothesis carries no evidence chain")
	}
	// The advice lives on the explanation, not on the observation: the anomaly
	// says what was seen, the hypothesis says what it means and what to do. That
	// split is what lets a reader tell "this is the fact" from "this is the
	// conclusion".
	if !hypothesis.AutomaticRetryForbidden {
		t.Fatal("the retry prohibition did not survive the trip into the replay")
	}
	if len(hypothesis.RecommendedActions) == 0 {
		t.Fatal("the replay shows a diagnosis with no advice, which is what the rail exists to avoid")
	}
}
