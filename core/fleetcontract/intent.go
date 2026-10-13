// Package fleetcontract defines transport-neutral values shared by cloud and edge.
// It contains no coordinator, storage implementation or network client.
package fleetcontract

import (
	"errors"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
)

var ErrStaleTaskRevision = errors.New("stale task revision")

// IntentStatus mirrors taskgraph.NodeStatus for one subtask node.
type IntentStatus string

const (
	StatusPending   IntentStatus = "PENDING"
	StatusReady     IntentStatus = "READY"
	StatusRunning   IntentStatus = "RUNNING"
	StatusSucceeded IntentStatus = "SUCCEEDED"
	StatusFailed    IntentStatus = "FAILED"
	StatusCancelled IntentStatus = "CANCELLED"

	// StatusUnknownOutcome means the claim lease lapsed while the intent was
	// RUNNING, so whether the robot acted is not established.
	//
	// It is deliberately not StatusReady. Returning the intent to the claimable
	// pool would let another worker repeat a physical action whose first attempt
	// may already have happened — the one thing core/closedloop forbids for an
	// unknown outcome, and the reason its UnknownOutcome class carries a
	// permanent retry prohibition. A claim lease lapsing tells us the worker
	// stopped reporting; it does not tell us the robot stood still.
	//
	// Only the coordinator's explicit reconciliation moves an intent out of
	// this state, and it records who decided and why.
	StatusUnknownOutcome IntentStatus = "UNKNOWN_OUTCOME"
)

// IntentNode is one subtask node of the distributed task graph.
type IntentNode struct {
	ContextBasis        *contextcontract.Basis `json:"contextBasis,omitempty"`
	Index               int                    `json:"index"`
	StepID              string                 `json:"stepId,omitempty"`
	TaskRevision        uint64                 `json:"taskRevision,omitempty"`
	AggregateVersion    uint64                 `json:"aggregateVersion,omitempty"`
	SemanticFingerprint string                 `json:"semanticFingerprint,omitempty"`
	CommandID           string                 `json:"commandId,omitempty"`
	SafeCheckpoint      bool                   `json:"safeCheckpoint,omitempty"`
	Action              string                 `json:"action"`
	RobotID             string                 `json:"robotId,omitempty"` // bound robot, "" = any worker
	Claimed             string                 `json:"claimed,omitempty"` // worker that claimed it
	Status              IntentStatus           `json:"status"`
	ResourceID          string                 `json:"resourceId,omitempty"`
	FencingToken        uint64                 `json:"fencingToken,omitempty"`
	CatalogRevision     string                 `json:"catalogRevision,omitempty"`
	WorldRevision       uint64                 `json:"worldRevision,omitempty"`
	EntitySourceID      string                 `json:"entitySourceId,omitempty"`
	EntitySequence      uint64                 `json:"entitySequenceBasis,omitempty"`
	EntityCount         uint64                 `json:"entityObservationCountBasis,omitempty"`
	RobotSourceID       string                 `json:"robotSourceId,omitempty"`
	RobotSequence       uint64                 `json:"robotSequenceBasis,omitempty"`
	// VerificationBasis records what confirmed this intent's completion. Without
	// it, "a harness confirmed the block is in the zone" and "the worker said so"
	// are the same record.
	VerificationBasis string    `json:"verificationBasis,omitempty"`
	PredicateState    string    `json:"predicateState,omitempty"`
	HarnessStatus     string    `json:"harnessStatus,omitempty"`
	HarnessReason     string    `json:"harnessReason,omitempty"`
	HarnessEvidence   []string  `json:"harnessEvidenceIds,omitempty"`
	LeaseRenewed      time.Time `json:"leaseRenewedAt,omitempty"`
	LeaseOwner        string    `json:"leaseOwner,omitempty"`
	Started           time.Time `json:"startedAt,omitempty"`
	Finished          time.Time `json:"finishedAt,omitempty"`
	Error             string    `json:"error,omitempty"`
	// Reconciled* record the person who resolved an UNKNOWN_OUTCOME intent.
	//
	// They exist because the alternative — a bare status flip back to READY —
	// leaves no answer to "who decided it was safe to try again, and on what
	// grounds". That question is the whole reason the intent needed a person.
	ReconciledBy      string    `json:"reconciledBy,omitempty"`
	ReconciledAt      time.Time `json:"reconciledAt,omitempty"`
	ReconcileDecision string    `json:"reconcileDecision,omitempty"`
	ReconcileNote     string    `json:"reconcileNote,omitempty"`
}
