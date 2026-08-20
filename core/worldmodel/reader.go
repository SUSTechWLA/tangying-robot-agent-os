package worldmodel

import (
	"context"
	"errors"
)

var ErrResyncRequired = errors.New("world delta retention gap requires snapshot resync")

// Reader is the stable environment-state boundary shared by the Console and
// future Harness Agents. Implementations must emit accepted revisions in order.
type Reader interface {
	Snapshot(context.Context) (Snapshot, error)
	Subscribe(context.Context, uint64) (<-chan Delta, error)
}
