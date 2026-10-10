package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/autorecovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Bump when the automatic investigation strategy changes. Catalog changes have
// their own digest. Neither a fresh plan ID nor a process restart grants another
// pass over the same failure. A reservation is never physical retry authority.
const investigationPolicyVersion = "automatic-investigation.v1"

// The closure schema stays independently readable when the investigation policy
// changes; a policy upgrade must not render previous closing edges unreadable.
const investigationClosureVersion = "automatic-investigation-closure.v1"

type recoveryInvestigationJournal struct {
	store       eventlog.Store
	service     *tasks.Service
	catalogHash string
}

type investigationBasis struct {
	Policy        string                        `json:"policy"`
	AgentVersion  string                        `json:"agentVersion"`
	CatalogHash   string                        `json:"catalogHash"`
	TaskID        string                        `json:"taskId"`
	Trigger       string                        `json:"trigger"`
	Binding       agentcontract.RecoveryBinding `json:"binding"`
	TaskRevision  uint64                        `json:"taskRevision,omitempty"`
	TaskState     taskgraph.TaskState           `json:"taskState,omitempty"`
	SourceSeq     uint64                        `json:"sourceSequence,omitempty"`
	ClosedEventID string                        `json:"closedEventId,omitempty"`
	Facts         map[string]any                `json:"stableFacts,omitempty"`
}

type investigationClosure struct {
	Version    string    `json:"version"`
	TaskID     string    `json:"taskId"`
	Trigger    string    `json:"trigger"`
	EventID    string    `json:"eventId"`
	OccurredAt time.Time `json:"occurredAt"`
}

// Only the automatic subscriber calls this gate. Explicit operator recovery
// still reaches its existing approval boundary without this reservation.
func investigateRecovery(ctx context.Context, journal *recoveryInvestigationJournal, recovery *agentruntime.RecoveryAgent, automatic *autorecovery.Supervisor, finding agentruntime.Finding) error {
	if journal != nil {
		admitted, err := journal.reserve(ctx, &finding)
		if err != nil || !admitted {
			return err
		}
	}
	plan := recovery.Recover(ctx, finding)
	if automatic != nil {
		_, err := automatic.Run(ctx, plan)
		return err
	}
	return nil
}

func newRecoveryInvestigationJournal(service *tasks.Service, store any, catalog *agentruntime.RecoveryCatalog) *recoveryInvestigationJournal {
	atomic, _ := store.(eventlog.Store)
	actions := catalog.Actions()
	sort.Slice(actions, func(i, j int) bool { return actions[i].ID < actions[j].ID })
	digest, _ := contextcontract.Fingerprint(actions)
	return &recoveryInvestigationJournal{store: atomic, service: service, catalogHash: digest}
}

func investigationKey(prefix string, identity any) (string, error) {
	digest, err := contextcontract.Fingerprint(identity)
	return prefix + "/" + digest, err
}

// stableInvestigationFacts deliberately excludes ages, observation timestamps,
// counts, generated event IDs and new sensor frames. They are not new issues.
func stableInvestigationFacts(finding agentruntime.Finding) map[string]any {
	facts := map[string]any{}
	for _, key := range []string{"robotId", "adapter", "moduleId", "faultKind", "faultCode", "faultSeverity", "stepId", "capability", "phase"} {
		if value, ok := finding.Facts[key].(string); ok && value != "" {
			facts[key] = value
		}
	}
	if value, ok := finding.Facts["uncertainSteps"].(string); ok {
		steps := strings.Split(value, ",")
		sort.Strings(steps)
		facts["uncertainSteps"] = steps
	}
	// AbnormalTasks is already split into one Finding per task. Another task's
	// failure count must not restart investigation of this unchanged task.
	return facts
}

func (j *recoveryInvestigationJournal) reserve(ctx context.Context, finding *agentruntime.Finding) (bool, error) {
	if j.store == nil || j.catalogHash == "" {
		return false, errors.New("durable automatic investigation journal unavailable")
	}
	binding := finding.Binding
	binding.AnomalyEventID, binding.PlanEventID = "", ""
	if binding.TaskID != "" && binding.TaskID != finding.TaskID {
		return false, errors.New("investigation task binding conflict")
	}
	basis := investigationBasis{Policy: investigationPolicyVersion, AgentVersion: agentruntime.RecoveryAgentVersion,
		CatalogHash: j.catalogHash, TaskID: finding.TaskID, Trigger: finding.Identity(), Binding: binding}
	if binding.SourceEventID == "" {
		// This is a standing condition, not a proven failed command. Current
		// task coordinates partition the reservation without filling Binding.
		if finding.TaskID != "" {
			if j.service == nil {
				return false, errors.New("investigation task index unavailable")
			}
			summary, err := j.service.GetSummary(ctx, finding.TaskID)
			if err != nil {
				return false, err
			}
			basis.TaskRevision, basis.TaskState, basis.SourceSeq = summary.CurrentRevision, summary.State, summary.SourceSequence
		}
		basis.Facts = stableInvestigationFacts(*finding)
		_, closure, err := j.loadClosure(ctx, finding.TaskID, finding.Identity())
		if err != nil {
			return false, err
		}
		// An old queued detection cannot borrow a later closing edge and
		// consume the next occurrence's reservation. Missing/equal time cannot
		// establish reopening. Time is a gate, never part of episode identity.
		if closure.EventID != "" && !finding.ReportedAt.After(closure.OccurredAt) {
			return false, nil
		}
		basis.ClosedEventID = closure.EventID
	}
	key, err := investigationKey("automatic-investigation", basis)
	if err != nil {
		return false, err
	}
	wire, err := json.Marshal(basis)
	if err != nil {
		return false, err
	}
	state, found, err := j.store.LoadState(ctx, key)
	if err != nil {
		return false, err
	}
	if found {
		if state.Version != 1 || string(state.Data) != string(wire) {
			return false, errors.New("automatic investigation reservation conflict")
		}
		return false, nil
	}
	now := time.Now().UTC()
	err = j.store.Commit(ctx, eventlog.CommitRequest{ExpectedVersion: 0,
		State: eventlog.AggregateState{AggregateID: key, Version: 1, Data: wire, UpdatedAt: now},
		Events: []eventlog.DomainEvent{{EventID: key + "/reserved", AggregateID: key, AggregateType: "automatic_investigation", AggregateVersion: 1,
			EventType: "INVESTIGATION_RESERVED", IdempotencyKey: key, CorrelationID: finding.TaskID, CausationID: binding.SourceEventID,
			Actor: "local-agent", OccurredAt: now, Payload: map[string]any{"basisJSON": string(wire), "physicalRetryAllowed": false}}},
		Checkpoint: &eventlog.Checkpoint{AggregateID: key, Version: 1, EventCursor: key + "/reserved", Data: wire, CreatedAt: now}})
	if errors.Is(err, eventlog.ErrVersionConflict) {
		return false, nil // Another process reserved it before any investigation.
	}
	if err == nil {
		finding.InvestigationID = key
	}
	return err == nil, err
}

func (j *recoveryInvestigationJournal) loadClosure(ctx context.Context, taskID, trigger string) (eventlog.AggregateState, investigationClosure, error) {
	key, err := investigationKey("automatic-investigation-closure", []string{taskID, trigger})
	if err != nil {
		return eventlog.AggregateState{}, investigationClosure{}, err
	}
	state, found, err := j.store.LoadState(ctx, key)
	if err != nil || !found {
		return state, investigationClosure{}, err
	}
	var closure investigationClosure
	if err := json.Unmarshal(state.Data, &closure); err != nil || closure.Version != investigationClosureVersion ||
		closure.TaskID != taskID || closure.Trigger != trigger || closure.EventID == "" || closure.OccurredAt.IsZero() {
		return state, closure, errors.New("invalid investigation closure checkpoint")
	}
	return state, closure, nil
}

func (j *recoveryInvestigationJournal) closed(ctx context.Context, event agentcontract.Event) error {
	if j.store == nil {
		return errors.New("durable automatic investigation journal unavailable")
	}
	code, _ := event.Payload["code"].(string)
	component, _ := event.Payload["component"].(string)
	if event.Topic != agentcontract.TopicOpsAnomalyCleared || event.Agent != agentruntime.OpsAgentName || event.AgentVersion != agentruntime.OpsAgentVersion ||
		event.ProtocolVersion != agentcontract.EventProtocolV1 || event.ID == "" || code == "" || event.OccurredAt.IsZero() {
		return errors.New("invalid automatic investigation closing event")
	}
	trigger := agentcontract.AnomalyIdentity(code, component)
	key, err := investigationKey("automatic-investigation-closure", []string{event.TaskID, trigger})
	if err != nil {
		return err
	}
	for attempt := 0; attempt < 3; attempt++ {
		state, prior, err := j.loadClosure(ctx, event.TaskID, trigger)
		if err != nil {
			return err
		}
		if prior.EventID == event.ID || prior.OccurredAt.After(event.OccurredAt) ||
			(prior.OccurredAt.Equal(event.OccurredAt) && prior.EventID > event.ID) {
			return nil
		}
		closure := investigationClosure{Version: investigationClosureVersion, TaskID: event.TaskID, Trigger: trigger, EventID: event.ID, OccurredAt: event.OccurredAt}
		wire, _ := json.Marshal(closure)
		id := fmt.Sprintf("%s/%d", key, state.Version+1)
		err = j.store.Commit(ctx, eventlog.CommitRequest{ExpectedVersion: state.Version,
			State: eventlog.AggregateState{AggregateID: key, Version: state.Version + 1, Data: wire, UpdatedAt: event.OccurredAt},
			Events: []eventlog.DomainEvent{{EventID: id, AggregateID: key, AggregateType: "automatic_investigation", AggregateVersion: state.Version + 1,
				EventType: "INVESTIGATION_CONDITION_CLEARED", IdempotencyKey: id, CorrelationID: event.TaskID, CausationID: event.ID,
				Actor: "local-agent", OccurredAt: event.OccurredAt, Payload: map[string]any{"closureJSON": string(wire)}}}})
		if !errors.Is(err, eventlog.ErrVersionConflict) {
			return err
		}
	}
	return eventlog.ErrVersionConflict
}
