package tasks

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

type RevisionStatus string

const (
	RevisionProposed         RevisionStatus = "PROPOSED"
	RevisionWaitingApproval  RevisionStatus = "WAITING_APPROVAL"
	RevisionWaitingSafePoint RevisionStatus = "WAITING_SAFE_POINT"
	RevisionActive           RevisionStatus = "ACTIVE"
	RevisionSuperseded       RevisionStatus = "SUPERSEDED"
	RevisionRejected         RevisionStatus = "REJECTED"
)

type StepStatus string

const (
	StepPending             StepStatus = "PENDING"
	StepReady               StepStatus = "READY"
	StepRunning             StepStatus = "RUNNING"
	StepAwaitingEvidence    StepStatus = "AWAITING_EVIDENCE"
	StepSatisfied           StepStatus = "SATISFIED"
	StepFailed              StepStatus = "FAILED"
	StepCancelledByRevision StepStatus = "CANCELLED_BY_REVISION"
)

type RevisionStep struct {
	StepID                string     `json:"stepId"`
	IntroducedRevision    uint64     `json:"introducedRevision"`
	SemanticFingerprint   string     `json:"semanticFingerprint"`
	IntentIndex           int        `json:"intentIndex"`
	Action                string     `json:"action"`
	RobotID               string     `json:"robotId,omitempty"`
	ResourceID            string     `json:"resourceId,omitempty"`
	RequiredPostcondition string     `json:"requiredPostcondition"`
	Status                StepStatus `json:"status"`
	HarnessEvidenceIDs    []string   `json:"harnessEvidenceIds,omitempty"`
}

type ChangeSet struct {
	Retained []string `json:"retained"`
	Changed  []string `json:"changed"`
	Added    []string `json:"added"`
	Paused   []string `json:"paused"`
}

type TaskRevision struct {
	TaskID                   string                `json:"taskId"`
	Revision                 uint64                `json:"revision"`
	BaseRevision             uint64                `json:"baseRevision"`
	ExpectedAggregateVersion uint64                `json:"expectedAggregateVersion"`
	Request                  string                `json:"request"`
	Understanding            string                `json:"understanding"`
	ChangeSet                ChangeSet             `json:"changeSet"`
	Intent                   manipulation.Intent   `json:"intent"`
	Plan                     *orchestration.Bundle `json:"plan,omitempty"`
	Steps                    []RevisionStep        `json:"steps"`
	RiskClass                string                `json:"riskClass"`
	ApprovalRequired         bool                  `json:"approvalRequired"`
	Creator                  string                `json:"creator"`
	IdempotencyKey           string                `json:"idempotencyKey"`
	CreatedAt                time.Time             `json:"createdAt"`
}

type RevisionLifecycleEvent struct {
	Sequence   uint64         `json:"sequence"`
	Revision   uint64         `json:"revision"`
	Status     RevisionStatus `json:"status"`
	Reason     string         `json:"reason,omitempty"`
	Payload    map[string]any `json:"payload,omitempty"`
	OccurredAt time.Time      `json:"occurredAt"`
}

type RevisionRecord struct {
	Revision TaskRevision             `json:"revision"`
	Status   RevisionStatus           `json:"status"`
	Events   []RevisionLifecycleEvent `json:"events"`
}

type RevisionBasis struct {
	RunningStepIDs   []string        `json:"runningStepIds,omitempty"`
	EvidenceValidity map[string]bool `json:"evidenceValidity,omitempty"`
}

type ProposeRevisionCommand struct {
	TaskID           string `json:"taskId"`
	ExpectedRevision uint64 `json:"expectedRevision"`
	Request          string `json:"request"`
	IdempotencyKey   string `json:"idempotencyKey"`
	Creator          string `json:"creator"`
}

type ConfirmRevisionCommand struct {
	TaskID                  string `json:"taskId"`
	Revision                uint64 `json:"revision"`
	ExpectedCurrentRevision uint64 `json:"expectedCurrentRevision"`
	IdempotencyKey          string `json:"idempotencyKey"`
	Actor                   string `json:"actor"`
	WaitForSafePoint        bool   `json:"waitForSafePoint"`
	ApprovalRequired        bool   `json:"approvalRequired"`
}

type RevisionConflictError struct {
	Current *Task
}

func (e *RevisionConflictError) Error() string {
	if e == nil || e.Current == nil {
		return ErrRevisionConflict.Error()
	}
	return fmt.Sprintf("%s: current revision is %d", ErrRevisionConflict, e.Current.CurrentRevision)
}

func (e *RevisionConflictError) Unwrap() error { return ErrRevisionConflict }

type RevisionCommit struct {
	TaskID                   string                   `json:"taskId"`
	ExpectedAggregateVersion uint64                   `json:"expectedAggregateVersion"`
	Task                     *Task                    `json:"task"`
	NewRevision              *TaskRevision            `json:"newRevision,omitempty"`
	LifecycleEvent           RevisionLifecycleEvent   `json:"lifecycleEvent"`
	LifecycleEvents          []RevisionLifecycleEvent `json:"lifecycleEvents,omitempty"`
	TaskEvent                *TaskEvent               `json:"taskEvent,omitempty"`
}

func RevisionContentEqual(left, right *TaskRevision) bool {
	leftWire, leftErr := json.Marshal(left)
	rightWire, rightErr := json.Marshal(right)
	return leftErr == nil && rightErr == nil && bytes.Equal(leftWire, rightWire)
}

func SemanticFingerprint(step RevisionStep) string {
	canonical := struct {
		Action                string `json:"action"`
		RobotID               string `json:"robotId"`
		ResourceID            string `json:"resourceId"`
		RequiredPostcondition string `json:"requiredPostcondition"`
	}{
		Action:                strings.TrimSpace(step.Action),
		RobotID:               strings.TrimSpace(step.RobotID),
		ResourceID:            strings.TrimSpace(step.ResourceID),
		RequiredPostcondition: strings.TrimSpace(step.RequiredPostcondition),
	}
	wire, _ := json.Marshal(canonical)
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:])
}

func StableStepID(intentIndex int, fingerprint string) string {
	fingerprint = strings.TrimSpace(fingerprint)
	if len(fingerprint) > 12 {
		fingerprint = fingerprint[:12]
	}
	return fmt.Sprintf("intent-%03d/%s", intentIndex, fingerprint)
}

func NormalizeLegacyTask(task *Task) *Task {
	if task == nil {
		return nil
	}
	if task.CurrentRevision == 0 {
		task.CurrentRevision = 1
	}
	if task.AggregateVersion == 0 {
		task.AggregateVersion = 1
	}
	if task.RevisionState == "" {
		task.RevisionState = RevisionActive
	}
	return task
}
