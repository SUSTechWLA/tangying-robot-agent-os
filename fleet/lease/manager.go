package lease

import (
	"context"
	"errors"
	"time"
)

var (
	ErrLeaseHeld         = errors.New("resource lease is held by another owner")
	ErrLeaseNotFound     = errors.New("resource lease not found")
	ErrLeaseExpired      = errors.New("resource lease expired")
	ErrStaleFencingToken = errors.New("stale fencing token")
)

type Grant struct {
	ResourceID string    `json:"resourceId"`
	Owner      string    `json:"owner"`
	Token      uint64    `json:"token"`
	ExpiresAt  time.Time `json:"expiresAt"`
}

type Manager interface {
	Acquire(context.Context, string, string, time.Duration) (Grant, error)
	Renew(context.Context, string, string, uint64, time.Duration) (Grant, error)
	Transfer(context.Context, string, string, string, uint64, time.Duration) (Grant, error)
	Validate(context.Context, string, string, uint64) error
	Release(context.Context, string, string, uint64) error
}
