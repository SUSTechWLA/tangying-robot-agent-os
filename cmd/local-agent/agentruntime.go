package main

import (
	"context"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	edgeagent "github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/autorecovery"
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
	autoRecovery *autorecovery.Supervisor,
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

	// The recovery agent proposes; it holds no execution port. Its facts come
	// from the same stores the observer reads, and every read it makes is
	// recorded in its investigation trail so a plan can be reviewed.
	recovery := agentruntime.NewRecoveryAgent(agentruntime.DefaultRecoveryCatalog())
	recovery.ReadFacts = recoveryFactsReader(service, store, telemetrySource)
	recovery.RememberPlan = runnerAlerts.RememberPlan
	if err := registry.Register(recovery); err != nil {
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
	// The ledger records transitions, not observations.
	//
	// An observer re-reports a standing condition for as long as it stands, which
	// a live console needs and an append-only record cannot absorb: on a real
	// deployment 99.3% of the ledger was observer output, one condition repeated
	// 1,410 times. The filter sits here, on the durable write only, so the bus —
	// and therefore the console, the recovery trigger and every other subscriber —
	// still sees every report. See agentruntime/ledgerfilter.go.
	ledgerFilter := agentruntime.NewLedgerFilter(0)
	runtime.SetEventSink(func(ctx context.Context, event agentcontract.Event) {
		if !ledgerFilter.Admit(event) {
			return
		}
		durable, ok := agentruntime.TaskEventFromEvent(event)
		if !ok {
			return
		}
		if _, err := service.AppendEvent(ctx, event.TaskID, durable); err != nil {
			// Persistence of an agent's report must never disturb execution.
			log.Printf("task %s: agent event %s not recorded: %v", event.TaskID, event.Topic, err)
		}
	})

	// Recovery is triggered by a finding, not by a tick: an investigation should
	// start when something was found, and use the state at that moment.
	//
	// The trigger is a subscriber rather than a call inside the observing agent,
	// so the observing agent stays read-only and the two roles remain separable —
	// the same reason agents do not call each other directly anywhere else here.
	recoverySub, err := runtime.Subscribe("recovery-trigger", []string{agentcontract.TopicOpsAnomalyDetected}, 0)
	if err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}
	go func() {
		for {
			event, receiveErr := recoverySub.Receive(ctx)
			if receiveErr != nil {
				return
			}
			finding, ok := findingFromEvent(event)
			if !ok {
				continue
			}
			plan := recovery.Recover(ctx, finding)
			// The automatic pass runs the plan's read-only steps. It is here,
			// after the plan exists and before anything else, because "notice a
			// problem" and "start looking into it" should not require a person to
			// be watching the console.
			//
			// Recover returns nil for a finding it has already planned, which is
			// what keeps a condition that fires four thousand times from being
			// investigated four thousand times.
			if autoRecovery != nil {
				if _, err := autoRecovery.Run(ctx, plan); err != nil {
					log.Printf("automatic recovery did not run: %v", err)
				}
			}
		}
	}()

	// The orchestrator polls the runtime's per-subscriber queues, so it has to
	// be told when there is something to collect.
	runtime.SetPublishObserver(orchestrator.Wake)

	// The automatic pass reports through the runtime, the same way agents are
	// handed their publisher by the runtime rather than by the composition root:
	// a sink assigned at a call site is a sink one call site can forget.
	if autoRecovery != nil {
		autoRecovery.Record = automaticRecoveryRecorder(runtime)
	}

	// The executing agent publishes through the bus as well as the ledger, so a
	// live observer reacts to a dispatch immediately rather than at the next
	// ledger read.
	//
	// Nothing here assigns that sink. Every agent that publishes — the runner,
	// the observer, the recovery proposer — is handed the bus by the runtime at
	// start, because a sink assigned per agent at the composition root is one a
	// new agent can silently be shipped without. That happened: the observer's
	// findings were recorded and never published.

	if err := orchestrator.Start(ctx); err != nil {
		log.Printf("agent runtime not started: %v", err)
		return nil, nil, runnerAlerts
	}
	// The publisher list is logged because "this agent has nothing to say" and
	// "this agent cannot say anything" look identical from the outside.
	log.Printf("agent runtime started: enabled=%v publishers=%v disabled=%v",
		orchestrator.AgentNames(), orchestrator.Publishers(), registry.Disabled())
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

// recoveryFactsReader gathers the evidence a recovery investigation starts from.
//
// Each read appends a trail step naming the store, the table and the selection,
// because the plan this feeds is only reviewable if the reads behind it are
// visible. The trail steps are returned rather than written to a log: they belong
// to the investigation, and they are published with the plan.
//
// A failed read is recorded as a failed step and does not stop the investigation.
// An investigation that hid its failures would appear more certain than it was,
// and a partial answer is still worth publishing.
func recoveryFactsReader(
	service *tasks.Service,
	store middleware.ExecutionStore,
	telemetrySource func(ctx context.Context, taskID string) (telemetry.Snapshot, error),
) func(ctx context.Context, taskID string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
	return func(ctx context.Context, taskID string) (agentruntime.RecoveryFacts, []agentruntime.TrailStep, error) {
		facts := agentruntime.RecoveryFacts{TaskID: taskID}
		steps := make([]agentruntime.TrailStep, 0, 4)

		if taskID != "" {
			task, err := service.Get(ctx, taskID)
			if err != nil {
				steps = append(steps, agentruntime.TrailStep{
					Kind: agentruntime.TrailQuery, Name: "tasks.read",
					Summary: "读取任务当前状态失败",
					Source:  agentruntime.DataSource{Kind: "sqlite", Table: "tasks", Query: "id = " + taskID},
					Error:   err.Error(),
				})
			} else {
				facts.TaskState = string(task.State)
				steps = append(steps, agentruntime.TrailStep{
					Kind: agentruntime.TrailQuery, Name: "tasks.read",
					Summary: "读取任务当前状态",
					Source:  agentruntime.DataSource{Kind: "sqlite", Table: "tasks", Query: "id = " + taskID},
					Rows:    1, Findings: map[string]any{"state": string(task.State)},
				})
			}

			// "Are there steps whose outcome is unknown" is the single question
			// recovery must not get wrong, so all three of its answers are
			// recorded: yes, no, and could-not-tell. The third one is the one
			// that used to disappear — a store without the list capability was
			// skipped in silence, and the investigation then read as though the
			// question had been asked and answered.
			if reader, ok := store.(middleware.ExecutionReader); ok {
				runs, err := reader.ListStepRuns(ctx, taskID)
				if err != nil {
					facts.ReconciliationUnavailable = true
					facts.ReconciliationError = err.Error()
					steps = append(steps, agentruntime.TrailStep{
						Kind: agentruntime.TrailQuery, Name: "step_runs.read",
						Summary: "读取执行记录失败；无法确认是否存在结果未知的物理步骤",
						Source:  agentruntime.DataSource{Kind: "sqlite", Table: "step_runs", Query: "task_id = " + taskID},
						Error:   err.Error(),
					})
				} else {
					uncertain := make([]agentcontract.StepRecord, 0)
					for _, run := range runs {
						if run.Status != middleware.StepStarted {
							continue
						}
						uncertain = append(uncertain, agentcontract.StepRecord{
							TaskID: run.TaskID, StepID: run.StepID,
							Capability: run.Capability, SafetyLevel: run.SafetyLevel,
							Status: string(run.Status),
						})
					}
					facts.UncertainSteps = uncertain
					steps = append(steps, agentruntime.TrailStep{
						Kind: agentruntime.TrailQuery, Name: "step_runs.read",
						Summary: "读取该任务的执行记录，统计结果未知的物理步骤",
						Source:  agentruntime.DataSource{Kind: "sqlite", Table: "step_runs", Query: "task_id = " + taskID},
						Rows:    len(runs),
						Findings: map[string]any{
							"uncertainSteps": len(uncertain),
						},
					})
				}
			} else {
				facts.ReconciliationUnavailable = true
				facts.ReconciliationError = "该部署的执行存储不支持按任务列出步骤记录"
				steps = append(steps, agentruntime.TrailStep{
					Kind: agentruntime.TrailQuery, Name: "step_runs.read",
					Summary: "该部署的执行存储不支持按任务列出步骤；无法确认是否存在结果未知的物理步骤",
					Source:  agentruntime.DataSource{Kind: "sqlite", Table: "step_runs", Query: "task_id = " + taskID},
					Error:   facts.ReconciliationError,
				})
			}
		}

		if telemetrySource != nil {
			snapshot, err := telemetrySource(ctx, taskID)
			if err != nil {
				steps = append(steps, agentruntime.TrailStep{
					Kind: agentruntime.TrailQuery, Name: "telemetry.read",
					Summary: "读取机器人观测失败",
					Source:  agentruntime.DataSource{Kind: "runtime", Table: "telemetry snapshot"},
					Error:   err.Error(),
				})
			} else {
				if snapshot.Faults != nil {
					facts.FaultSeverity = snapshot.Faults.Severity
					for _, fault := range snapshot.Faults.Faults {
						facts.FaultCodes = append(facts.FaultCodes, fault.Code)
						if fault.UserInstruction != "" {
							facts.RecommendedActions = append(facts.RecommendedActions, fault.UserInstruction)
						}
					}
				}
				steps = append(steps, agentruntime.TrailStep{
					Kind: agentruntime.TrailQuery, Name: "telemetry.read",
					Summary: "读取机器人观测与故障台账",
					Source:  agentruntime.DataSource{Kind: "runtime", Table: "telemetry snapshot"},
					Rows:    1,
					Findings: map[string]any{
						"faultCount": len(facts.FaultCodes),
						"severity":   snapshot.Faults.WorstSeverity(),
						"emergency":  snapshot.EmergencyStopped,
					},
				})
			}
		}

		return facts, steps, nil
	}
}

// findingFromEvent reads an observed anomaly back into the finding shape the
// recovery agent reasons over.
//
// The observer publishes a payload, not a Go value, so this is the inverse of
// that encoding. A payload that cannot be read is skipped rather than guessed at:
// an investigation built on a half-understood finding would publish a plan whose
// reasoning does not match the problem.
// automaticRecoveryRecorder writes the automatic pass's decisions into the task
// ledger.
//
// A skipped step is recorded, not omitted. The distinction a reader needs is
// between "the system looked at this and decided not to touch it, because it
// would move the robot" and "the system never considered it" — and a ledger that
// kept only the runs cannot tell those apart.
//
// The task comes from the plan, so a robot-level plan is not filed under a task
// it does not describe; the projection drops an empty task id, which is the same
// rule agent.registered follows.
func automaticRecoveryRecorder(runtime *agentruntime.AgentRuntime) func(
	context.Context, string, string, autorecovery.StepOutcome,
) {
	return func(ctx context.Context, taskID, planID string, outcome autorecovery.StepOutcome) {
		summary := ""
		payload := agentcontract.RecoveryExecutedPayload{
			PlanID: planID, ActionID: outcome.ActionID,
			Executed: outcome.Ran && outcome.Result.Executed,
			Verified: outcome.Ran && outcome.Result.Verified,
			// The agent is "recovery", but the initiator was not a person. It is
			// left false so that "the system decided this was safe to do alone"
			// cannot later be read as "somebody approved it".
			OperatorApproved: false,
			OccurredAt:       time.Now().UTC(),
		}
		switch {
		case !outcome.Ran:
			summary = fmt.Sprintf("系统自动处理时没有执行 %s：%s", outcome.ActionID, outcome.SkipReason)
			payload.Verification = outcome.SkipReason
		case outcome.Error != "":
			summary = fmt.Sprintf("自动执行 %s 时出错：%s", outcome.ActionID, outcome.Error)
			payload.Failed = outcome.Error
		default:
			summary = "自动" + recoveryExecutionSummary(outcome.Result)
			payload.Verification = outcome.Result.Verification
		}
		payload.Summary = summary
		runtime.Publish(ctx, agentcontract.Event{
			TaskID: taskID, Topic: agentcontract.TopicOpsRecoveryExecuted,
			Agent: "recovery", OccurredAt: payload.OccurredAt,
			Priority: agentcontract.PriorityNormal,
			Payload:  payload.Encode(),
		})
	}
}

func findingFromEvent(event agentcontract.Event) (agentruntime.Finding, bool) {
	code, _ := event.Payload["code"].(string)
	if code == "" {
		return agentruntime.Finding{}, false
	}
	finding := agentruntime.Finding{
		TaskID:    event.TaskID,
		Code:      code,
		Component: stringOrEmpty(event.Payload["component"]),
		Message:   stringOrEmpty(event.Payload["message"]),
		Severity:  stringOrEmpty(event.Payload["severity"]),
		Evidence:  stringSlice(event.Payload["evidence"]),
		Facts:     map[string]any{},
	}
	if facts, ok := event.Payload["facts"].(map[string]any); ok {
		finding.Facts = facts
	}
	// The facts are what let the catalog match: a finding is named in the agent
	// vocabulary while catalog entries are keyed by what actually failed.
	if retryForbidden, ok := event.Payload["automaticRetryForbidden"].(bool); ok {
		finding.AutomaticRetryForbidden = retryForbidden
	}
	return finding, true
}

func stringOrEmpty(value any) string {
	text, _ := value.(string)
	return text
}

func stringSlice(value any) []string {
	items, ok := value.([]any)
	if !ok {
		if typed, ok := value.([]string); ok {
			return typed
		}
		return nil
	}
	result := make([]string, 0, len(items))
	for _, item := range items {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}
