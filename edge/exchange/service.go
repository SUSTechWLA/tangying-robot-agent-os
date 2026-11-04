// Package exchange implements authenticated advisory data interchange. It has no
// dependency on a robot Runtime, task executor, cloud coordinator or database.
package exchange

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

type Service struct {
	identity x.Identity
	peers    map[string]x.Identity
	journal  x.Journal
	now      func() time.Time

	// A scheduling hint only: durable delivery identity, FIFO and receipts stay
	// in the journal. A new Service starts a new fairness cycle after restart.
	dispatchMu sync.Mutex
	lastPeer   string
}

func New(ctx context.Context, identity x.Identity, peers []x.Identity, journal x.Journal) (*Service, error) {
	if journal == nil {
		return nil, errors.New("exchange journal is required")
	}
	if err := identity.Validate(); err != nil {
		return nil, err
	}
	s := &Service{identity: identity, peers: make(map[string]x.Identity), journal: journal, now: time.Now}
	if len(peers) == 0 || len(peers) > 256 {
		return nil, errors.New("exchange requires 1..256 peers")
	}
	for _, peer := range peers {
		if err := peer.Validate(); err != nil {
			return nil, err
		}
		if _, exists := s.peers[peer.AgentID]; exists || peer.AgentID == identity.AgentID {
			return nil, errors.New("peer agent identity must be unique")
		}
		s.peers[peer.AgentID] = peer
	}
	if err := journal.Bind(ctx, identity); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *Service) Identity() x.Identity { return s.identity }

func (s *Service) Publish(ctx context.Context, request x.Request) (x.Envelope, error) {
	peer, ok := s.peers[request.PeerID]
	if !ok {
		return x.Envelope{}, errors.New("unknown peer")
	}
	if !x.ValidIdentifier(request.RequestID) {
		return x.Envelope{}, errors.New("requestId is required for durable publication idempotency")
	}
	if request.TTLMS <= 0 || request.TTLMS > x.MaxTTLMS {
		return x.Envelope{}, errors.New("ttlMs must be 1..86400000")
	}
	payload, err := x.CanonicalPayload(request.Payload)
	if err != nil {
		return x.Envelope{}, err
	}
	request.Payload = payload
	raw, err := json.Marshal(struct {
		Request   x.Request  `json:"request"`
		Recipient x.Identity `json:"recipient"`
	}{request, peer})
	if err != nil {
		return x.Envelope{}, err
	}
	hash := sha256.Sum256(raw)
	now := s.now().UnixMilli()
	e := x.Envelope{SchemaVersion: x.Schema, Sender: s.identity, Recipient: peer, Stream: request.Stream, Kind: request.Kind, Sequence: 1, CreatedAtMS: now, ExpiresAtMS: now + request.TTLMS, Basis: request.Basis, Payload: payload}
	e.MessageID, err = e.Digest()
	if err != nil {
		return x.Envelope{}, err
	}
	if err := e.Validate(); err != nil {
		return x.Envelope{}, err
	}
	return s.journal.Publish(ctx, request.RequestID, hex.EncodeToString(hash[:]), e, now)
}

func (s *Service) Receive(ctx context.Context, authenticatedPeer string, e x.Envelope) (x.Receipt, error) {
	peer, ok := s.peers[authenticatedPeer]
	if !ok || e.Sender != peer || e.Recipient != s.identity {
		return x.Receipt{}, fmt.Errorf("%w: authenticated peer, domain, robot or recipient mismatch", x.ErrConflict)
	}
	if err := e.Validate(); err != nil {
		return x.Receipt{}, err
	}
	return s.journal.Accept(ctx, e, s.now().UnixMilli())
}

func (s *Service) Inbox(ctx context.Context, limit int) ([]x.Envelope, error) {
	return s.journal.Inbox(ctx, limit)
}
func (s *Service) Counts(ctx context.Context) (x.Counts, error) {
	return s.journal.Counts(ctx, s.now().UnixMilli())
}

func (s *Service) Prune(ctx context.Context) error { return s.journal.Prune(ctx, s.now().UnixMilli()) }

type Transport interface {
	Send(context.Context, x.Envelope) (x.Receipt, error)
}

// identityPendingJournal filters the configured identities before selecting FIFO
// heads. Otherwise a retained envelope for an old peer identity can hide every
// valid successor until its TTL expires. The base Journal remains compatible
// with existing adapters; Dispatch still rejects mismatched identities below.
type identityPendingJournal interface {
	PendingFor(context.Context, x.Identity, []x.Identity, int64, int) ([]x.Envelope, error)
}

type Delivery struct {
	MessageID string `json:"messageId"`
	PeerID    string `json:"peerId"`
	Error     string `json:"error,omitempty"`
}

// Dispatch keeps the original envelope until an exact durable receipt arrives.
// A lost response causes replay, never a newly generated data identity.
func (s *Service) Dispatch(ctx context.Context, transport Transport, limit int) ([]Delivery, error) {
	if transport == nil {
		return nil, errors.New("exchange transport is required")
	}
	if limit < 1 || limit > 1000 {
		return nil, errors.New("invalid pending limit")
	}
	var pending []x.Envelope
	var err error
	if journal, ok := s.journal.(identityPendingJournal); ok {
		peers := make([]x.Identity, 0, len(s.peers))
		for _, peer := range s.peers {
			peers = append(peers, peer)
		}
		// Obtain every configured peer's head before applying the delivery
		// budget. Limiting the oldest heads in SQL would let offline peers
		// monopolize a small batch forever. This read is bounded by 256 peers.
		pending, err = journal.PendingFor(ctx, s.identity, peers, s.now().UnixMilli(), len(peers))
		if err == nil {
			pending = s.rotatePeerHeads(pending, limit)
		}
	} else {
		pending, err = s.journal.Pending(ctx, s.now().UnixMilli(), limit)
	}
	if err != nil {
		return nil, err
	}
	result := make([]Delivery, len(pending))
	var group sync.WaitGroup
	for index, e := range pending {
		// One pending head per peer is selected by the journal. The configured
		// allowlist bounds concurrent network requests to at most 256. Retain
		// this check for legacy adapters and as a second admission boundary.
		if peer, ok := s.peers[e.Recipient.AgentID]; !ok || peer != e.Recipient || e.Sender != s.identity {
			result[index] = Delivery{MessageID: e.MessageID, PeerID: e.Recipient.AgentID, Error: "queued envelope identity no longer matches configuration"}
			continue
		}
		group.Add(1)
		go func(index int, e x.Envelope) {
			defer group.Done()
			delivery := Delivery{MessageID: e.MessageID, PeerID: e.Recipient.AgentID}
			receipt, err := transport.Send(ctx, e)
			if err == nil && (receipt.SchemaVersion != x.Schema || receipt.MessageID != e.MessageID || receipt.Sequence != e.Sequence || (receipt.Disposition != "accepted" && receipt.Disposition != "duplicate")) {
				err = errors.New("remote acknowledgement does not match queued envelope")
			}
			if err == nil {
				err = s.journal.Acknowledge(ctx, e, s.now().UnixMilli())
			}
			if err != nil {
				delivery.Error = err.Error()
			}
			result[index] = delivery
		}(index, e)
	}
	group.Wait()
	return result, ctx.Err()
}

// rotatePeerHeads fairly shares successive Dispatch calls within this Service.
// PendingFor returns one FIFO head per peer; it remains responsible for current
// identity filtering and ordering. Legacy Journals keep their existing bounded
// Pending behavior, without acquiring a stronger fairness guarantee.
func (s *Service) rotatePeerHeads(pending []x.Envelope, limit int) []x.Envelope {
	if len(pending) == 0 {
		return pending
	}
	sort.Slice(pending, func(i, j int) bool { return pending[i].Recipient.AgentID < pending[j].Recipient.AgentID })
	s.dispatchMu.Lock()
	defer s.dispatchMu.Unlock()
	start := sort.Search(len(pending), func(i int) bool { return pending[i].Recipient.AgentID > s.lastPeer })
	if limit > len(pending) {
		limit = len(pending)
	}
	selected := make([]x.Envelope, limit)
	for i := range selected {
		selected[i] = pending[(start+i)%len(pending)]
	}
	s.lastPeer = selected[len(selected)-1].Recipient.AgentID
	return selected
}
