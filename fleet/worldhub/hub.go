// Package worldhub serializes accepted observations into one realtime world.
package worldhub

import (
	"context"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

type Hub struct {
	mu          sync.Mutex
	projector   *worldmodel.Projector
	retention   int
	deltas      []worldmodel.Delta
	subscribers map[uint64]chan worldmodel.Delta
	nextID      uint64
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

func (h *Hub) Ingest(_ context.Context, event observation.Envelope) (worldmodel.Delta, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	before := h.projector.Snapshot().Revision
	snapshot, accepted, err := h.projector.Apply(event)
	if err != nil || !accepted {
		return worldmodel.Delta{}, err
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
	return h.projector.Snapshot(), nil
}

func (h *Hub) Subscribe(ctx context.Context, afterRevision uint64) (<-chan worldmodel.Delta, error) {
	h.mu.Lock()
	current := h.projector.Snapshot().Revision
	if afterRevision > current || (len(h.deltas) > 0 && afterRevision+1 < h.deltas[0].Revision) {
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
