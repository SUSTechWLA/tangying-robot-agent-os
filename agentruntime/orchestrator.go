package agentruntime

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

// Orchestrator runs the enabled agents: it routes events to them, decides what
// happens when two of them want incompatible things, refuses requests that
// exceed what an agent declared it may do, and owns their lifecycle.
//
// # What it deliberately does not do
//
// It does not decide whether a task is complete, whether a step may be retried,
// or whether a physical action may be dispatched. Those decisions already have
// owners — the closed-loop contract, the failure classifier, and the existing
// approval/lease/fencing gates — and a second decision-maker with a different
// opinion is how a system starts contradicting itself.
//
// The one decision this type owns is ordering: when an observer proposes
// something while the robot is mid-action, the proposal waits. That is a
// scheduling decision, and scheduling is the orchestrator's job.
type Orchestrator struct {
	runtime  *AgentRuntime
	registry *Registry
	config   Config

	// Telemetry reads the latest observation, shared with the agents that need
	// it. Optional.
	Telemetry func(ctx context.Context, taskID string) (telemetry.Snapshot, error)
	// Memory is the three-layer memory handed to agents that want it.
	Memory agentcontract.Memory
	// Now is the clock. Tests set it; production leaves it nil.
	Now func() time.Time

	mu        sync.Mutex
	agentSubs map[string]Subscription
	// supervisorSub receives every event, whatever any agent subscribed to.
	//
	// The orchestrator's own bookkeeping — which actions are in flight, which
	// proposals must wait — has to be complete. Deriving it from the union of
	// agent subscriptions would make arbitration depend on agent configuration:
	// turning off an observer would silently disable the rule that protects a
	// moving robot.
	supervisorSub Subscription
	health        map[string]agentcontract.Health
	// physicalHolds counts the physical actions currently in flight, per task.
	// It is a count rather than a flag because two steps of one task can overlap
	// in a way the orchestrator cannot rule out, and a flag would clear the hold
	// on the first completion while the second action was still running.
	physicalHolds map[string]int
	// deferred holds recovery proposals that arrived while a task was mid-action.
	deferred []agentcontract.Event
	// registered records the agent names started, for reporting.
	registered []string
	// publishers records which of them were handed the bus, so "this agent has
	// nothing to say" can be read off a log line instead of inferred from a
	// silence.
	publishers []string

	started  bool
	stopped  bool
	done     chan struct{}
	stopOnce sync.Once
	// wake lets a producer nudge the delivery loop.
	//
	// It is deliberately never closed. A channel that senders use must not be
	// closed by a shutdown running concurrently with them: the send would panic
	// on a closed channel, and the check-then-send window cannot be closed by a
	// mutex alone. Stopping is signalled by stop instead, and wake is left open
	// so a late nudge is harmless.
	wake chan struct{}
	// stop is closed once on shutdown and never sent on.
	stop chan struct{}
}

// NewOrchestrator builds an orchestrator over a registry and a runtime.
func NewOrchestrator(runtime *AgentRuntime, registry *Registry) *Orchestrator {
	return &Orchestrator{
		runtime:       runtime,
		registry:      registry,
		config:        registry.Config(),
		health:        map[string]agentcontract.Health{},
		agentSubs:     map[string]Subscription{},
		physicalHolds: map[string]int{},
		done:          make(chan struct{}),
		wake:          make(chan struct{}, 1),
		stop:          make(chan struct{}),
	}
}

// ErrOrchestratorNotStarted is returned by operations that need a running
// orchestrator.
var ErrOrchestratorNotStarted = errors.New("agent orchestrator is not started")

// Start validates the configuration, subscribes every enabled agent, announces
// them, and starts the delivery and evaluation loops.
//
// Validation happens before any agent is started, so a configuration that cannot
// be honoured leaves the system exactly as it was rather than half-started.
func (o *Orchestrator) Start(ctx context.Context) error {
	o.mu.Lock()
	if o.stopped {
		o.mu.Unlock()
		return agentcontract.ErrShutdown
	}
	if o.started {
		o.mu.Unlock()
		return nil
	}
	o.mu.Unlock()

	if err := o.registry.Validate(); err != nil {
		return err
	}
	enabled := o.registry.EnabledAgents()
	names := o.registry.Enabled()

	// Hand the bus to every agent that declared it publishes. This is done here,
	// by the runtime, and not by the code that built the agent.
	//
	// The reason is a defect only running the system could reveal: the
	// composition root set a publishing sink on one agent by hand, the observing
	// agent was registered without one, and so every finding it made reached the
	// alert store and never reached the bus. The recovery agent that listens for
	// those findings never woke, while the console still displayed the findings
	// and every agent still reported healthy. Unit tests passed throughout,
	// because they injected the sink themselves — they were exercising a wiring
	// production did not have.
	//
	// Injection at start is what makes a mute agent impossible to ship by
	// accident: an agent that implements Publisher is given the bus whether or
	// not whoever registered it remembered to.
	publishers := make([]string, 0, len(enabled))
	for _, agent := range enabled {
		publisher, ok := agent.(agentcontract.Publisher)
		if !ok {
			continue
		}
		publisher.SetPublish(o.runtime.Publish)
		publishers = append(publishers, agent.Name())
	}
	o.mu.Lock()
	o.publishers = publishers
	o.mu.Unlock()

	// The orchestrator observes everything itself, so its bookkeeping and its
	// arbitration do not depend on which agents happen to be enabled.
	supervisor, err := o.runtime.Subscribe("orchestrator", supervisorPatterns(), o.config.QueueCapacity)
	if err != nil {
		return fmt.Errorf("subscribe orchestrator: %w", err)
	}
	o.mu.Lock()
	o.supervisorSub = supervisor
	o.mu.Unlock()

	// Subscribe first, then announce, so an agent never misses the events that
	// describe the runtime it is part of.
	for _, agent := range enabled {
		patterns := agent.Subscriptions()
		if len(patterns) == 0 {
			continue
		}
		subscription, err := o.runtime.Subscribe(agent.Name(), patterns, o.config.QueueCapacity)
		if err != nil {
			return fmt.Errorf("subscribe agent %s: %w", agent.Name(), err)
		}
		o.mu.Lock()
		o.agentSubs[agent.Name()] = subscription
		o.mu.Unlock()
	}

	o.mu.Lock()
	o.started = true
	o.registered = names
	o.mu.Unlock()

	for _, agent := range enabled {
		descriptor := agentcontract.Describe(agent)
		o.runtime.Publish(ctx, agentcontract.Event{
			Topic: agentcontract.TopicAgentRegistered, Agent: descriptor.Name,
			AgentVersion: descriptor.Version, Priority: agentcontract.PriorityLow,
			OccurredAt: o.now().UTC(),
			Payload: agentcontract.RegisteredPayload{
				Version: descriptor.Version, Capabilities: descriptor.Capabilities,
				Permissions: descriptor.Permissions, EnabledAgents: names,
			}.Encode(),
		})
	}

	go o.deliver(ctx)
	go o.evaluate(ctx)
	return nil
}

// deliver routes events to the agents that subscribed to them.
func (o *Orchestrator) deliver(ctx context.Context) {
	defer close(o.done)
	for {
		// Drain before waiting. A signal sent between Subscribe and the first
		// receive below would otherwise sit in the runtime's buffer while the
		// non-blocking send saw a full channel and discarded its own signal —
		// the loop would then block forever with events pending. Draining first
		// makes startup ordering irrelevant instead of load-bearing.
		o.drain(ctx)
		if o.isStopped() {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-o.stop:
			return
		case <-o.wake:
		}
	}
}

// drain moves every pending event from every agent subscription to that agent.
//
// It reads from the subscriptions rather than being pushed to, so a slow agent
// cannot stall the orchestrator: the runtime has already bounded what each
// subscription holds, and this loop simply takes what is there.
func (o *Orchestrator) drain(ctx context.Context) {
	o.mu.Lock()
	supervisor := o.supervisorSub
	subscriptions := make(map[string]Subscription, len(o.agentSubs))
	for name, subscription := range o.agentSubs {
		subscriptions[name] = subscription
	}
	o.mu.Unlock()

	// Supervisor first: hold tracking and arbitration must be up to date before
	// any agent is shown the event, so an agent never reacts to a state the
	// orchestrator has not accounted for yet.
	if supervisor != nil {
		for {
			event, ok := collect(ctx, supervisor)
			if !ok {
				break
			}
			o.supervise(ctx, event)
		}
	}

	for name, subscription := range subscriptions {
		agent, ok := o.registry.Discover(name)
		if !ok {
			continue
		}
		for {
			event, ok := collect(ctx, subscription)
			if !ok {
				break
			}
			o.handle(ctx, agent, event)
		}
	}
}

// handle decides what one event means for the runtime, then gives it to the
// agent it was routed to.
func (o *Orchestrator) handle(ctx context.Context, agent agentcontract.Agent, event agentcontract.Event) {
	if o.holdsProposal(event) {
		return
	}
	if err := agent.OnEvent(ctx, event); err != nil {
		// An agent's reaction failing is that agent's problem, not the task's.
		// It is recorded and the event is considered handled: propagating it
		// would let an observer break execution.
		o.publishAgentError(ctx, agent, event, err)
	}
}

// supervise does the orchestrator's own work on one event: tracking what is in
// flight, and holding back a recovery proposal while a robot is moving.
//
// It runs for every event regardless of which agents subscribed, so arbitration
// is a property of the runtime rather than of an agent's configuration.
func (o *Orchestrator) supervise(ctx context.Context, event agentcontract.Event) {
	o.trackPhysicalAction(event)
	// The supervisor is where a proposal is held back, not the agent that made
	// it: an agent that could police itself would make the rule advisory.
	o.deferIfPhysicalActionInFlight(ctx, event)
}

// holdsProposal reports whether an event must not be shown to agents yet.
// A held proposal is invisible to agents until the safe point, so an observer
// cannot act on advice that the action in flight may invalidate.
func (o *Orchestrator) holdsProposal(event agentcontract.Event) bool {
	if event.Topic != agentcontract.TopicOpsRecoveryProposed || event.TaskID == "" {
		return false
	}
	return o.PhysicalActionInFlight(event.TaskID)
}

// collect takes one event from a subscription, reporting false when there is
// nothing pending or the subscription has ended.
//
// The timeout is what makes this a poll rather than a block: the delivery loop
// serves several subscriptions and must not park on one that is idle.
func collect(ctx context.Context, subscription Subscription) (agentcontract.Event, bool) {
	receiveContext, cancel := context.WithTimeout(ctx, 20*time.Millisecond)
	defer cancel()
	event, err := subscription.Receive(receiveContext)
	if err != nil {
		return agentcontract.Event{}, false
	}
	return event, true
}

// supervisorPatterns is the subscription that gives the orchestrator a complete
// view: every namespace the runtime can publish in.
func supervisorPatterns() []string {
	patterns := make([]string, 0, len(agentcontract.NamespacePrefixes))
	for _, namespace := range agentcontract.NamespacePrefixes {
		patterns = append(patterns, namespace+"*")
	}
	return patterns
}

// trackPhysicalAction maintains the per-task count of actions in flight.
//
// A physical action is in flight from the moment it is dispatched until it
// reports a terminal status. That window is exactly the one in which the robot
// is moving and nobody yet knows the result, which is why nothing may preempt it
// and why a recovery proposal arriving inside it must wait.
func (o *Orchestrator) trackPhysicalAction(event agentcontract.Event) {
	if event.Topic != agentcontract.TopicActionExecuted || event.TaskID == "" {
		return
	}
	status, _ := event.Payload["activityStatus"].(string)
	o.mu.Lock()
	defer o.mu.Unlock()
	switch status {
	case "SENDING", "RUNNING":
		o.physicalHolds[event.TaskID]++
	case "CONFIRMED", "FAILED":
		if hold := o.physicalHolds[event.TaskID]; hold > 1 {
			o.physicalHolds[event.TaskID] = hold - 1
		} else {
			delete(o.physicalHolds, event.TaskID)
		}
	}
}

// PhysicalActionInFlight reports whether a task currently has an action whose
// outcome is not yet known.
func (o *Orchestrator) PhysicalActionInFlight(taskID string) bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.physicalHolds[taskID] > 0
}

// deferIfPhysicalActionInFlight holds a recovery proposal that arrived while the
// task was mid-action, and reports whether it was held.
//
// Deferring is not rejecting. The proposal is kept and delivered once the action
// reaches a terminal status, because the condition it describes may still be
// true then. What must not happen is a recovery suggestion reaching an operator
// as an instruction while the robot is still moving: the outcome of the action
// in flight is exactly what would change the advice.
func (o *Orchestrator) deferIfPhysicalActionInFlight(ctx context.Context, event agentcontract.Event) bool {
	if event.Topic != agentcontract.TopicOpsRecoveryProposed || event.TaskID == "" {
		return false
	}
	if !o.PhysicalActionInFlight(event.TaskID) {
		return false
	}
	o.mu.Lock()
	o.deferred = append(o.deferred, event)
	o.mu.Unlock()
	o.publishDeferral(ctx, event, event.Agent)
	return true
}

// publishDeferral records that a proposal was held back and why. A deferral with
// no record would be indistinguishable from a proposal that was dropped.
func (o *Orchestrator) publishDeferral(ctx context.Context, event agentcontract.Event, agentName string) {
	payload := clonePayload(event.Payload)
	payload["deferredReason"] = "PHYSICAL_ACTION_IN_FLIGHT"
	payload["deferredBy"] = "orchestrator"
	o.runtime.Publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicOpsRecoveryDeferred, TaskID: event.TaskID, StepID: event.StepID,
		Agent: "orchestrator", Priority: agentcontract.PriorityHigh,
		OccurredAt: o.now().UTC(), CausationID: event.ID, CorrelationID: event.CorrelationID,
		Payload: payload,
	})
}

// releaseDeferred delivers proposals that were held back, for tasks that are no
// longer mid-action.
func (o *Orchestrator) releaseDeferred(ctx context.Context) {
	o.mu.Lock()
	held := o.deferred
	o.deferred = nil
	inFlight := make(map[string]bool, len(o.physicalHolds))
	for taskID := range o.physicalHolds {
		inFlight[taskID] = true
	}
	o.mu.Unlock()

	for _, event := range held {
		if inFlight[event.TaskID] {
			// Still moving: keep holding it rather than delivering advice that
			// the action in flight may invalidate.
			o.mu.Lock()
			o.deferred = append(o.deferred, event)
			o.mu.Unlock()
			continue
		}
		// The original identity was consumed when the proposal first arrived, so
		// republishing it verbatim would be suppressed as a duplicate and the
		// proposal would be lost rather than deferred. Releasing it is a new
		// delivery of the same proposal, and it therefore gets a new identity;
		// the correlation id keeps it recognisable as the same story.
		released := event
		released.ID = ""
		released.CausationID = event.ID
		o.runtime.Publish(ctx, released)
	}
}

// evaluate runs each observing agent's evaluation tick and releases held
// proposals at safe points.
func (o *Orchestrator) evaluate(ctx context.Context) {
	interval := o.config.HealthInterval.Or(DefaultHealthInterval)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	// Evaluate once before waiting for the first tick.
	//
	// The ticker alone left the supervisor blind for a whole interval after
	// every start — five seconds in which a task that had already failed, a
	// latched emergency stop and a robot that could not localise were all
	// invisible. That is the restart case the durable sweep exists to cover, so
	// waiting an interval before asking the question defeats it. Evaluating
	// immediately costs one pass over stores that are already open, and it makes
	// "started" and "watching" the same moment rather than five seconds apart.
	o.evaluateOnce(ctx)

	for {
		select {
		case <-ctx.Done():
			return
		case <-o.stop:
			return
		case <-ticker.C:
		}
		o.evaluateOnce(ctx)
	}
}

// evaluateOnce is one pass: release what was held, ask each observer to look,
// then report health.
func (o *Orchestrator) evaluateOnce(ctx context.Context) {
	o.releaseDeferred(ctx)
	for _, agent := range o.registry.EnabledAgents() {
		observer, ok := agent.(Observer)
		if !ok {
			continue
		}
		observer.Observe(ctx)
	}
	o.publishHealthChanges(ctx)
}

// Observer is an agent that does its work on a tick rather than in reaction to
// one event.
//
// It is declared here rather than on the contract because it describes how the
// orchestrator schedules an agent, not what an agent is. The TaskAgent executes
// when a host asks it to and does not implement this.
type Observer interface {
	Observe(ctx context.Context) []Finding
}

// DefaultHealthInterval is how often agent health is checked and observers are
// asked to evaluate. It is a compromise between noticing a problem quickly and
// not flooding the event stream with a status that has not changed.
const DefaultHealthInterval = 5 * time.Second

// publishHealthChanges emits agent.health_changed for agents whose health moved.
func (o *Orchestrator) publishHealthChanges(ctx context.Context) {
	for _, agent := range o.registry.EnabledAgents() {
		health := agent.Health(ctx)
		name := agent.Name()
		o.mu.Lock()
		previous, known := o.health[name]
		o.health[name] = health
		o.mu.Unlock()
		if known && previous.Status == health.Status {
			continue
		}
		o.runtime.Publish(ctx, agentcontract.Event{
			Topic: agentcontract.TopicAgentHealthChanged, Agent: name,
			AgentVersion: agent.Version(), Priority: agentcontract.PriorityLow,
			OccurredAt: o.now().UTC(),
			Payload: agentcontract.HealthChangedPayload{
				Previous: previous.Status, Current: health.Status, ReasonCode: health.ReasonCode,
			}.Encode(),
		})
	}
}

// Shutdown stops every agent and the orchestrator's own loops.
//
// It is bounded by the caller's context and never cancels work a task depends
// on: stopping an observer must not stop a task. Each agent's Shutdown is called
// even if an earlier one failed, because a stuck agent must not prevent the
// others from releasing what they hold.
func (o *Orchestrator) Shutdown(ctx context.Context) error {
	var shutdownErr error
	o.stopOnce.Do(func() {
		o.mu.Lock()
		o.stopped = true
		subscriptions := o.agentSubs
		supervisor := o.supervisorSub
		o.agentSubs = map[string]Subscription{}
		o.mu.Unlock()
		close(o.stop)
		if supervisor != nil {
			_ = supervisor.Close()
		}
		for _, subscription := range subscriptions {
			_ = subscription.Close()
		}
	})
	var failures []string
	for _, agent := range o.registry.EnabledAgents() {
		if err := agent.Shutdown(ctx); err != nil {
			failures = append(failures, fmt.Sprintf("%s: %v", agent.Name(), err))
		}
	}
	if len(failures) > 0 {
		sort.Strings(failures)
		shutdownErr = fmt.Errorf("agent shutdown failed: %s", strings.Join(failures, "; "))
	}
	// Wait for the loops, but never past the caller's deadline: shutdown must be
	// bounded even when an agent misbehaves.
	select {
	case <-o.done:
	case <-ctx.Done():
		if shutdownErr == nil {
			shutdownErr = ctx.Err()
		}
	}
	return shutdownErr
}

// AgentNames reports the enabled agents in startup order.
func (o *Orchestrator) AgentNames() []string {
	o.mu.Lock()
	defer o.mu.Unlock()
	return append([]string(nil), o.registered...)
}

// Publishers reports which enabled agents were handed the bus at start.
//
// It exists so a runtime where an agent turned out to be mute can be diagnosed
// from its own startup log, rather than by reading the composition root and
// noticing an assignment that is not there.
func (o *Orchestrator) Publishers() []string {
	o.mu.Lock()
	defer o.mu.Unlock()
	return append([]string(nil), o.publishers...)
}

// DeferredProposals reports how many recovery proposals are currently held back.
func (o *Orchestrator) DeferredProposals() int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return len(o.deferred)
}

func (o *Orchestrator) isStopped() bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.stopped
}

func (o *Orchestrator) now() time.Time {
	if o.Now != nil {
		return o.Now()
	}
	return time.Now()
}

// Wake nudges the delivery loop. It is what the runtime calls after publishing,
// and it is exported so the code that builds the runtime and the orchestrator
// can connect the two.
//
// It never blocks and is safe to call after shutdown: a nudge that arrives too
// late is simply ignored, and a publisher must never wait on the orchestrator.
func (o *Orchestrator) Wake() {
	o.mu.Lock()
	stopped := o.stopped
	o.mu.Unlock()
	if stopped {
		return
	}
	select {
	case o.wake <- struct{}{}:
	default:
		// A nudge is already pending, which is all this is for.
	}
}
