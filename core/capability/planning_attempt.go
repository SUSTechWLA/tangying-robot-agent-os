package capability

import (
	"errors"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

const PlanningAttemptVersion = "planning.attempt.v1"

var ErrPlanningAttemptNotFound = errors.New("planning attempt not found")
var ErrPlanningAttemptCorrupt = errors.New("planning attempt source checksum failed")

// PlanningAttempt is rejected planning evidence, never a plan or task. The
// exact trace is a string so generic JSON storage cannot reorder or round its
// model inputs and archived source. Missing trace remains explicitly missing.
type PlanningAttempt struct {
	SchemaVersion       string             `json:"schemaVersion"`
	ID                  string             `json:"id"`
	CreatedAt           time.Time          `json:"createdAt"`
	Request             string             `json:"request"`
	Error               string             `json:"error"`
	Source              string             `json:"source"`
	Scope               agentcontext.Scope `json:"scope"`
	RobotID             string             `json:"robotId"`
	CatalogRevision     string             `json:"catalogRevision"`
	OriginalSourceTrace string             `json:"originalSourceTrace"`
	SourceTraceSHA256   string             `json:"sourceTraceSHA256"`
	TraceAvailable      bool               `json:"traceAvailable"`
}

// FailedPlanningError preserves the original classification while exposing an
// audit identity only after durable commit. Audit failures grant no fake ID.
type FailedPlanningError struct {
	Cause             error
	PlanningAttemptID string
	AuditUnavailable  bool
}

func (e *FailedPlanningError) Error() string { return e.Cause.Error() }
func (e *FailedPlanningError) Unwrap() error { return e.Cause }
