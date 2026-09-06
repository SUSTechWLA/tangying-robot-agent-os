// Package worldhub serializes accepted observations into one realtime world.
package worldhub

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

type Hub struct {
	mu             sync.Mutex
	projector      *worldmodel.Projector
	retention      int
	deltas         []worldmodel.Delta
	subscribers    map[uint64]chan worldmodel.Delta
	nextID         uint64
	store          SnapshotStore
	persistenceErr error
}

func New(worldID string, freshness time.Duration, retention int) *Hub {
	if retention <= 0 {
		retention = 256
	}
	return &Hub{
		projector: worldmodel.NewProjector(worldID, freshness), retention: retention,
		subscribers: map[uint64]chan worldmodel.Delta{},
	}
}

// NewPersistent opens a durable world and rejects unreadable or inconsistent
// checkpoints. Missing snapshots initialize an empty world durably before use.
func NewPersistent(ctx context.Context, worldID string, freshness time.Duration, retention int, store SnapshotStore) (*Hub, error) {
	if store == nil || strings.TrimSpace(worldID) == "" {
		return nil, errors.New("persistent world requires a store and world ID")
	}
	h := New(worldID, freshness, retention)
	checkpoint, err := store.Load(ctx)
	if errors.Is(err, ErrSnapshotNotFound) {
		if err := store.Save(ctx, h.projector.Checkpoint()); err != nil {
			return nil, fmt.Errorf("initialize world checkpoint: %w", err)
		}
	} else if err != nil {
		return nil, fmt.Errorf("load world checkpoint: %w", err)
	} else {
		h.projector, err = worldmodel.RestoreProjector(worldID, freshness, checkpoint)
		if err != nil {
			return nil, fmt.Errorf("restore world checkpoint: %w", err)
		}
	}
	h.store = store
	return h, nil
}

func (h *Hub) Ingest(ctx context.Context, event observation.Envelope) (worldmodel.Delta, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.persistenceErr != nil {
		return worldmodel.Delta{}, h.persistenceErr
	}
	before := h.projector.Snapshot().Revision
	candidate := h.projector
	var prior worldmodel.Checkpoint
	if h.store != nil {
		prior = h.projector.Checkpoint()
		candidate = h.projector.Clone()
	}
	snapshot, accepted, err := candidate.Apply(event)
	if err != nil {
		return worldmodel.Delta{}, err
	}
	if h.store != nil {
		checkpoint := candidate.Checkpoint()
		// An unchanged static fact still advances private source deduplication state.
		if accepted || len(checkpoint.ObservationIDs) != len(prior.ObservationIDs) {
			if err := h.store.Save(ctx, checkpoint); err != nil {
				h.persistenceErr = fmt.Errorf("world persistence failed; restart required: %w", err)
				for id, subscriber := range h.subscribers {
					close(subscriber)
					delete(h.subscribers, id)
				}
				return worldmodel.Delta{}, h.persistenceErr
			}
			h.projector = candidate
			// Durable I/O may outlast the freshness budget.
			snapshot = h.projector.Snapshot()
		}
	}
	if !accepted {
		return worldmodel.Delta{}, nil
	}
	delta := worldmodel.Delta{
		SchemaVersion: "world.delta.v1", WorldID: snapshot.WorldID,
		Revision: snapshot.Revision, Previous: before, EventCursor: snapshot.EventCursor,
		ObservationID: event.ObservationID, Snapshot: snapshot,
	}
	h.deltas = append(h.deltas, delta)
	if len(h.deltas) > h.retention {
		h.deltas = append([]worldmodel.Delta(nil), h.deltas[len(h.deltas)-h.retention:]...)
	}
	for id, subscriber := range h.subscribers {
		select {
		case subscriber <- delta:
		default:
			close(subscriber)
			delete(h.subscribers, id)
		}
	}
	return delta, nil
}

func (h *Hub) Snapshot(_ context.Context) (worldmodel.Snapshot, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.persistenceErr != nil {
		return worldmodel.Snapshot{}, h.persistenceErr
	}
	return h.projector.Snapshot(), nil
}

func (h *Hub) Subscribe(ctx context.Context, afterRevision uint64) (<-chan worldmodel.Delta, error) {
	h.mu.Lock()
	if h.persistenceErr != nil {
		h.mu.Unlock()
		return nil, h.persistenceErr
	}
	current := h.projector.Snapshot().Revision
	if afterRevision > current || (len(h.deltas) == 0 && afterRevision < current) || (len(h.deltas) > 0 && afterRevision+1 < h.deltas[0].Revision) {
		h.mu.Unlock()
		return nil, worldmodel.ErrResyncRequired
	}
	replay := make([]worldmodel.Delta, 0, len(h.deltas))
	for _, delta := range h.deltas {
		if delta.Revision > afterRevision {
			replay = append(replay, delta)
		}
	}
	buffer := h.retention + 16
	if buffer < len(replay)+16 {
		buffer = len(replay) + 16
	}
	channel := make(chan worldmodel.Delta, buffer)
	for _, delta := range replay {
		channel <- delta
	}
	h.nextID++
	id := h.nextID
	h.subscribers[id] = channel
	h.mu.Unlock()

	go func() {
		<-ctx.Done()
		h.mu.Lock()
		if active, ok := h.subscribers[id]; ok {
			delete(h.subscribers, id)
			close(active)
		}
		h.mu.Unlock()
	}()
	return channel, nil
}

var _ worldmodel.Reader = (*Hub)(nil)
