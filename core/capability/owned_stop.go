package capability

import (
	"context"
	"errors"
	"strings"
	"time"
)

// OwnedStop describes an already dispatched operation, frozen from the executor's
// receipt and operation contract. It is process-local authority for cancellation
// supervision only: it must never be constructed from an HTTP request or model.
type OwnedStop struct {
	RobotID       string
	OperationID   string
	OwnerID       string
	StatusService string
	CancelService string
	deadline      time.Time
}

type ownedStopKey struct{}

// WithOwnedStop is used by the executor's stop path, after it has a receipt and
// an immutable operation descriptor. A fresh bounded context allows shutdown
// even when the execution context was cancelled; it never renews motion leases.
func WithOwnedStop(ctx context.Context, scope OwnedStop) (context.Context, error) {
	for _, value := range []string{scope.RobotID, scope.OperationID, scope.OwnerID, scope.StatusService, scope.CancelService} {
		if strings.TrimSpace(value) == "" || len(value) > 512 || strings.ContainsAny(value, "\r\n\t") {
			return nil, errors.New("owned stop requires bounded operation and service identities")
		}
	}
	if scope.StatusService == scope.CancelService {
		return nil, errors.New("owned stop status and cancel services must differ")
	}
	deadline, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || time.Until(deadline) > 15*time.Second {
		return nil, errors.New("owned stop requires a fresh deadline of at most 15 seconds")
	}
	scope.deadline = deadline
	return context.WithValue(ctx, ownedStopKey{}, scope), nil
}

// OwnedStopFromContext returns a value copy; the context key is private and the
// original deadline remains part of the scope even if a caller strips cancellation.
func OwnedStopFromContext(ctx context.Context) (OwnedStop, bool) {
	scope, ok := ctx.Value(ownedStopKey{}).(OwnedStop)
	return scope, ok
}

func (scope OwnedStop) Allows(ctx context.Context, robot, service, requestID, operationID, ownerID string, leaseMS uint32, parameterCount int) bool {
	deadline, ok := ctx.Deadline()
	if !ok || ctx.Err() != nil || scope.deadline.IsZero() || !time.Now().Before(scope.deadline) || deadline.After(scope.deadline) {
		return false
	}
	if robot != scope.RobotID || operationID != scope.OperationID || ownerID != scope.OwnerID || leaseMS != 0 || parameterCount != 0 {
		return false
	}
	return (service == scope.StatusService && requestID == "") || (service == scope.CancelService && requestID == scope.OwnerID+"/cancel")
}
