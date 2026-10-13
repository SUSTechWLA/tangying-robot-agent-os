package exchange

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
)

var identityA = x.Identity{AgentID: "edge-a", RobotID: "ugv-a", Domain: "warehouse"}
var identityB = x.Identity{AgentID: "cloud-b", RobotID: "fleet", Domain: "cloud"}
var testNow = time.UnixMilli(1800000000000)

func openService(t *testing.T, path string, identity, peer x.Identity, capacity int) (*Service, *sqlite.ExchangeStore) {
	t.Helper()
	store, err := sqlite.OpenExchange(path, capacity)
	if err != nil {
		t.Fatal(err)
	}
	s, err := New(context.Background(), identity, []x.Identity{peer}, store)
	if err != nil {
		store.Close()
		t.Fatal(err)
	}
	s.now = func() time.Time { return testNow }
	return s, store
}

func request(id string) x.Request {
	return x.Request{RequestID: id, PeerID: identityB.AgentID, Stream: "mission-status", Kind: "mission_status", TTLMS: 60000, Payload: json.RawMessage(`{"status":"awaiting-evidence"}`)}
}
func publish(t *testing.T, s *Service, id string) x.Envelope {
	t.Helper()
	e, err := s.Publish(context.Background(), request(id))
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestRestartLostAcknowledgementAndFIFO(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 50)
	b, bs := openService(t, filepath.Join(dir, "b.db"), identityB, identityA, 50)
	first := publish(t, a, "first")
	second := publish(t, a, "second")
	token := strings.Repeat("x", 32)
	handler, err := b.ReceiveHandler(map[string]string{identityA.AgentID: token})
	if err != nil {
		t.Fatal(err)
	}
	lost := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if lost {
			lost = false
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, r)
			if recorder.Code != 200 {
				t.Errorf("accept=%d %s", recorder.Code, recorder.Body)
			}
			connection, _, _ := w.(http.Hijacker).Hijack()
			connection.Close()
			return
		}
		handler.ServeHTTP(w, r)
	}))
	defer server.Close()
	// httptest clients have no default timeout; bound every data delivery.
	client := server.Client()
	client.Timeout = time.Second
	transport, err := NewHTTPTransport(client, map[string]Destination{identityB.AgentID: {URL: server.URL, Token: token}}, true)
	if err != nil {
		t.Fatal(err)
	}
	deliveries, err := a.Dispatch(ctx, transport, 100)
	if err != nil || len(deliveries) != 1 || deliveries[0].Error == "" {
		t.Fatalf("lost response=%+v %v", deliveries, err)
	}
	items, _ := b.Inbox(ctx, 100)
	if len(items) != 1 || items[0].MessageID != first.MessageID {
		t.Fatal("first envelope not durable")
	}
	if err := as.Close(); err != nil {
		t.Fatal(err)
	}
	if err := bs.Close(); err != nil {
		t.Fatal(err)
	}
	a, as = openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 50)
	defer as.Close()
	b, bs = openService(t, filepath.Join(dir, "b.db"), identityB, identityA, 50)
	defer bs.Close()
	handler, err = b.ReceiveHandler(map[string]string{identityA.AgentID: token})
	if err != nil {
		t.Fatal(err)
	}
	deliveries, err = a.Dispatch(ctx, transport, 100)
	if err != nil || len(deliveries) != 1 || deliveries[0].Error != "" {
		t.Fatalf("replay=%+v %v", deliveries, err)
	}
	deliveries, err = a.Dispatch(ctx, transport, 100)
	if err != nil || len(deliveries) != 1 || deliveries[0].MessageID != second.MessageID || deliveries[0].Error != "" {
		t.Fatalf("second=%+v %v", deliveries, err)
	}
	items, _ = b.Inbox(ctx, 100)
	if len(items) != 2 {
		t.Fatalf("inbox has %d", len(items))
	}
	counts, _ := a.Counts(ctx)
	if counts.Pending != 0 || counts.Delivered != 2 {
		t.Fatalf("counts=%+v", counts)
	}
	repeated := publish(t, a, "first")
	if repeated.MessageID != first.MessageID || repeated.Sequence != first.Sequence {
		t.Fatal("publish retry changed durable identity")
	}
	third := publish(t, a, "third")
	if third.Sequence != 3 {
		t.Fatalf("sequence after restart=%d", third.Sequence)
	}
}

func TestIdentityConflictExpiryAndOrdering(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 50)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b.db"), identityB, identityA, 50)
	defer bs.Close()
	one := publish(t, a, "one")
	two := publish(t, a, "two")
	if _, err := b.Receive(ctx, identityA.AgentID, two); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Receive(ctx, identityA.AgentID, one); !errors.Is(err, x.ErrStale) {
		t.Fatalf("old unseen=%v", err)
	}
	conflict := two
	conflict.Payload = json.RawMessage(`{"status":"different"}`)
	conflict.MessageID, _ = conflict.Digest()
	if _, err := b.Receive(ctx, identityA.AgentID, conflict); !errors.Is(err, x.ErrConflict) {
		t.Fatalf("conflict=%v", err)
	}
	for _, mutate := range []func(*x.Envelope){func(e *x.Envelope) { e.Sender.RobotID = "other" }, func(e *x.Envelope) { e.Sender.Domain = "other" }, func(e *x.Envelope) { e.Recipient.AgentID = "other" }} {
		e := one
		mutate(&e)
		e.MessageID, _ = e.Digest()
		if _, err := b.Receive(ctx, identityA.AgentID, e); !errors.Is(err, x.ErrConflict) {
			t.Fatalf("identity=%v", err)
		}
	}
	if _, err := b.Receive(ctx, "unauthenticated", one); err == nil {
		t.Fatal("accepted unauthenticated peer")
	}
	b.now = func() time.Time { return testNow.Add(time.Minute) }
	if _, err := b.Receive(ctx, identityA.AgentID, one); !errors.Is(err, x.ErrExpired) {
		t.Fatalf("expired=%v", err)
	}
	if receipt, err := b.Receive(ctx, identityA.AgentID, two); err != nil || receipt.Disposition != "duplicate" {
		t.Fatalf("durable duplicate after expiry=%+v %v", receipt, err)
	}
	changed := request("one")
	changed.Payload = json.RawMessage(`{"new":true}`)
	if _, err := a.Publish(ctx, changed); !errors.Is(err, x.ErrConflict) {
		t.Fatalf("publication conflict=%v", err)
	}
	if _, err := New(ctx, x.Identity{AgentID: "replacement", RobotID: identityA.RobotID, Domain: identityA.Domain}, []x.Identity{identityB}, as); !errors.Is(err, x.ErrConflict) {
		t.Fatalf("identity rebinding=%v", err)
	}
}

func TestBoundedRetentionPreservesSequenceAndRejection(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 1)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b.db"), identityB, identityA, 1)
	defer bs.Close()
	e := publish(t, a, "one")
	if _, err := b.Receive(ctx, identityA.AgentID, e); err != nil {
		t.Fatal(err)
	}
	if _, err := a.Publish(ctx, request("two")); !errors.Is(err, x.ErrCapacity) {
		t.Fatalf("capacity=%v", err)
	}
	later := testNow.Add(25 * time.Hour)
	a.now = func() time.Time { return later }
	b.now = a.now
	if err := a.Prune(ctx); err != nil {
		t.Fatal(err)
	}
	if err := b.Prune(ctx); err != nil {
		t.Fatal(err)
	}
	newEnvelope := publish(t, a, "two")
	if newEnvelope.Sequence != 2 {
		t.Fatal("GC reset sequence")
	}
	if _, err := b.Receive(ctx, identityA.AgentID, newEnvelope); err != nil {
		t.Fatal(err)
	}
	counts, _ := a.Counts(ctx)
	if counts.PrunedOutbox != 1 {
		t.Fatalf("prune counter=%+v", counts)
	}
	// Even if an old producer lies about freshness and rehashes, the retained
	// stream cursor rejects its old sequence after envelope bodies were pruned.
	e.CreatedAtMS = later.UnixMilli()
	e.ExpiresAtMS = e.CreatedAtMS + 60000
	e.MessageID, _ = e.Digest()
	if _, err := b.Receive(ctx, identityA.AgentID, e); !errors.Is(err, x.ErrStale) {
		t.Fatalf("replayed pruned sequence=%v", err)
	}
}

func TestConcurrentPublishAcrossJournalConnections(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "shared.db")
	a, as := openService(t, path, identityA, identityB, 50)
	defer as.Close()
	b, bs := openService(t, path, identityA, identityB, 50)
	defer bs.Close()
	var group sync.WaitGroup
	results := make(chan x.Envelope, 20)
	failures := make(chan error, 20)
	for i := 0; i < 20; i++ {
		group.Add(1)
		go func(i int) {
			defer group.Done()
			service := a
			if i%2 == 1 {
				service = b
			}
			e, err := service.Publish(ctx, request("same-request"))
			if err != nil {
				failures <- err
				return
			}
			results <- e
		}(i)
	}
	group.Wait()
	close(results)
	close(failures)
	for err := range failures {
		t.Error(err)
	}
	var id string
	for e := range results {
		if id != "" && id != e.MessageID {
			t.Fatal("concurrent idempotency split")
		}
		id = e.MessageID
		if e.Sequence != 1 {
			t.Fatal("duplicate allocated a sequence")
		}
	}
}

type wrongReceipt struct{}

func (wrongReceipt) Send(_ context.Context, e x.Envelope) (x.Receipt, error) {
	return x.Receipt{SchemaVersion: x.Schema, MessageID: e.MessageID, Sequence: e.Sequence + 1, Disposition: "accepted"}, nil
}
func TestWrongAcknowledgementKeepsOutbox(t *testing.T) {
	a, as := openService(t, filepath.Join(t.TempDir(), "a.db"), identityA, identityB, 10)
	defer as.Close()
	publish(t, a, "one")
	results, err := a.Dispatch(context.Background(), wrongReceipt{}, 10)
	if err != nil || results[0].Error == "" {
		t.Fatalf("wrong receipt=%+v %v", results, err)
	}
	counts, _ := a.Counts(context.Background())
	if counts.Pending != 1 || counts.Delivered != 0 {
		t.Fatal("wrong ACK retired data")
	}
}

func TestDataAdmissionAndHTTPAuthentication(t *testing.T) {
	a, as := openService(t, filepath.Join(t.TempDir(), "a.db"), identityA, identityB, 20)
	defer as.Close()
	for _, mutate := range []func(*x.Request){func(r *x.Request) { r.Kind = "command" }, func(r *x.Request) { r.Kind = "observation" }, func(r *x.Request) { r.TTLMS = x.MaxTTLMS + 1 }, func(r *x.Request) { r.Payload = json.RawMessage(`{"x":1,"x":2}`) }, func(r *x.Request) { r.Payload = json.RawMessage(`{"x":"` + strings.Repeat("a", x.MaxPayload) + `"}`) }} {
		r := request("invalid")
		mutate(&r)
		if _, err := a.Publish(context.Background(), r); err == nil {
			t.Fatal("accepted invalid request")
		}
	}
	token := strings.Repeat("a", 32)
	handler, err := a.AdminHandler(token)
	if err != nil {
		t.Fatal(err)
	}
	for _, header := range []string{token, "Basic " + token, "Bearer wrong", ""} {
		req := httptest.NewRequest("GET", "/v1/exchange/status", nil)
		req.Header.Set("Authorization", header)
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, req)
		if recorder.Code != 401 {
			t.Fatalf("auth accepted %q", header)
		}
	}
	req := httptest.NewRequest("POST", "/v1/exchange/publish", strings.NewReader(`{"requestId":"one","requestId":"two"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	if recorder.Code != 400 {
		t.Fatalf("duplicate HTTP key=%d", recorder.Code)
	}
	if _, err := NewHTTPTransport(&http.Client{Timeout: time.Second}, map[string]Destination{"peer": {URL: "http://192.0.2.2", Token: token}}, true); err == nil {
		t.Fatal("remote plaintext allowed")
	}
}

type blockedPeerTransport struct {
	healthy chan struct{}
	release chan struct{}
	blocked string
}

func (b blockedPeerTransport) Send(ctx context.Context, e x.Envelope) (x.Receipt, error) {
	if e.Recipient.AgentID == b.blocked {
		select {
		case <-ctx.Done():
			return x.Receipt{}, ctx.Err()
		case <-b.release:
			return x.Receipt{}, errors.New("offline peer")
		}
	}
	close(b.healthy)
	return x.Receipt{SchemaVersion: x.Schema, MessageID: e.MessageID, Sequence: e.Sequence, Disposition: "accepted"}, nil
}

func TestOfflineBacklogDoesNotBlockHealthyPeer(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	store, err := sqlite.OpenExchange(filepath.Join(t.TempDir(), "exchange.db"), 100)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	identityC := x.Identity{AgentID: "edge-c", RobotID: "legged-c", Domain: "warehouse"}
	s, err := New(ctx, identityA, []x.Identity{identityB, identityC}, store)
	if err != nil {
		t.Fatal(err)
	}
	s.now = func() time.Time { return testNow }
	for i := 0; i < 20; i++ {
		r := request(fmt.Sprintf("offline-%d", i))
		r.Stream = fmt.Sprintf("stream-%d", i)
		if _, err := s.Publish(ctx, r); err != nil {
			t.Fatal(err)
		}
	}
	r := request("healthy")
	r.PeerID = identityC.AgentID
	if _, err := s.Publish(ctx, r); err != nil {
		t.Fatal(err)
	}
	transport := blockedPeerTransport{healthy: make(chan struct{}), release: make(chan struct{}), blocked: identityB.AgentID}
	done := make(chan error, 1)
	go func() { _, err := s.Dispatch(ctx, transport, 10); done <- err }()
	select {
	case <-transport.healthy:
	case <-ctx.Done():
		t.Fatal("healthy peer starved behind offline backlog")
	}
	close(transport.release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	counts, err := s.Counts(ctx)
	if err != nil || counts.Delivered != 1 || counts.Pending != 20 {
		t.Fatalf("counts=%+v %v", counts, err)
	}
}

func TestChangedPeerIdentityCannotReceiveOldQueuedData(t *testing.T) {
	ctx := context.Background()
	a, store := openService(t, filepath.Join(t.TempDir(), "exchange.db"), identityA, identityB, 10)
	defer store.Close()
	publish(t, a, "old")
	changed := identityB
	changed.Domain = "different-domain"
	reconfigured, err := New(ctx, identityA, []x.Identity{changed}, store)
	if err != nil {
		t.Fatal(err)
	}
	reconfigured.now = a.now
	deliveries, err := reconfigured.Dispatch(ctx, wrongReceipt{}, 10)
	if err != nil || len(deliveries) != 1 || !strings.Contains(deliveries[0].Error, "identity no longer matches") {
		t.Fatalf("reconfigured=%+v %v", deliveries, err)
	}
}
