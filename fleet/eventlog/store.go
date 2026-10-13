// Package eventlog provides Fleet event-store implementations. Shared atomic
// commit values and the Store port live in core/eventstore.
package eventlog

import "github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"

var ErrVersionConflict = eventstore.ErrVersionConflict

const OutboxClaimTTL = eventstore.OutboxClaimTTL

type AggregateState = eventstore.AggregateState
type DomainEvent = eventstore.DomainEvent
type OutboxEntry = eventstore.OutboxEntry
type Checkpoint = eventstore.Checkpoint
type CommitRequest = eventstore.CommitRequest
type Store = eventstore.Store
