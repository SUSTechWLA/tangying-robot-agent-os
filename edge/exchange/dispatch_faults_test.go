package exchange

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
)

// These fixtures exercise real HTTP and durable SQLite, without ROS, Gazebo,
// physical robots, external services, or model calls.
func TestDispatchSmallBatchRotatesOfflinePeersAndRestarts(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	peers := []x.Identity{identityB,
		{AgentID: "edge-c", RobotID: "legged-c", Domain: "warehouse"},
		{AgentID: "edge-d", RobotID: "uav-d", Domain: "warehouse"}}
	token := strings.Repeat("f", 32)
	var sender *Service
	var senderStore *sqlite.ExchangeStore
	var receivers []*Service
	var stores []*sqlite.ExchangeStore
	var servers []*httptest.Server
	var attempts [3]atomic.Int64
	var offline atomic.Bool
	offline.Store(true)
	var transport *HTTPTransport
	open := func() {
		var err error
		senderStore, err = sqlite.OpenExchange(filepath.Join(dir, "sender.db"), 50)
		if err != nil {
			t.Fatal(err)
		}
		sender, err = New(ctx, identityA, peers, senderStore)
		if err != nil {
			t.Fatal(err)
		}
		sender.now = func() time.Time { return testNow }
		receivers, stores, servers = nil, nil, nil
		destinations := make(map[string]Destination)
		for i, peer := range peers {
			receiver, store := openService(t, filepath.Join(dir, peer.AgentID+".db"), peer, identityA, 50)
			receivers, stores = append(receivers, receiver), append(stores, store)
			handler, err := receiver.ReceiveHandler(map[string]string{identityA.AgentID: token})
			if err != nil {
				t.Fatal(err)
			}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				attempts[i].Add(1)
				if i < 2 && offline.Load() {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				handler.ServeHTTP(w, r)
			}))
			servers = append(servers, server)
			destinations[peer.AgentID] = Destination{URL: server.URL, Token: token}
		}
		transport, err = NewHTTPTransport(&http.Client{Timeout: time.Second}, destinations, true)
		if err != nil {
			t.Fatal(err)
		}
	}
	closeAll := func() {
		for _, server := range servers {
			server.Close()
		}
		for _, store := range stores {
			if err := store.Close(); err != nil {
				t.Error(err)
			}
		}
		if err := senderStore.Close(); err != nil {
			t.Error(err)
		}
	}
	open()
	defer func() { closeAll() }()
	expected := make(map[string][]x.Envelope)
	for _, peer := range peers {
		for i := 0; i < 2; i++ {
			r := request(fmt.Sprintf("%s-%d", peer.AgentID, i))
			r.PeerID = peer.AgentID
			e, err := sender.Publish(ctx, r)
			if err != nil {
				t.Fatal(err)
			}
			expected[peer.AgentID] = append(expected[peer.AgentID], e)
		}
	}
	for i := 0; i < 6; i++ {
		deliveries, err := sender.Dispatch(ctx, transport, 1)
		if err != nil || len(deliveries) != 1 {
			t.Fatalf("dispatch %d: %+v %v", i, deliveries, err)
		}
	}
	counts, err := sender.Counts(ctx)
	if err != nil || counts.Delivered != 2 || counts.Pending != 4 {
		t.Fatalf("healthy peer starved behind offline heads with limit=1: %+v err=%v attempts=[%d,%d,%d]", counts, err, attempts[0].Load(), attempts[1].Load(), attempts[2].Load())
	}
	for i := range attempts {
		if attempts[i].Load() != 2 {
			t.Fatalf("peer %d attempts=%d", i, attempts[i].Load())
		}
	}
	// Scheduling fairness starts afresh after restart; durable envelopes and
	// per-stream sequence/receipt state must survive independently of it.
	closeAll()
	offline.Store(false)
	open()
	for i := 0; i < 4; i++ {
		deliveries, err := sender.Dispatch(ctx, transport, 1)
		if err != nil || len(deliveries) != 1 || deliveries[0].Error != "" {
			t.Fatalf("recovery %d: %+v %v", i, deliveries, err)
		}
	}
	for i, receiver := range receivers {
		feed, err := receiver.Feed(ctx, 0, 10)
		if err != nil || len(feed.Items) != 2 {
			t.Fatalf("peer %d feed=%+v err=%v", i, feed, err)
		}
		for n, item := range feed.Items {
			want := expected[peers[i].AgentID][n]
			if item.Envelope.MessageID != want.MessageID || item.Envelope.Sequence != uint64(n+1) {
				t.Fatalf("peer %d FIFO changed: %+v", i, feed.Items)
			}
		}
	}
	counts, err = sender.Counts(ctx)
	if err != nil || counts.Delivered != 6 || counts.Pending != 0 {
		t.Fatalf("final counts=%+v err=%v", counts, err)
	}
	t.Log(`PEER_EXPERIMENT {"scenario":"small_batch_offline_peers_restart","peerCount":3,"dispatchLimit":1,"initialDispatches":6,"healthyDeliveredWhilePeersOffline":2,"offlineAttempts":4,"recoveryDispatches":4,"delivered":6,"duplicateFeedItems":0,"senderRestarts":1,"receiverRestarts":3,"fairnessScope":"service_lifetime","evidenceType":"sqlite_loopback_http_fixture"}`)
}

func TestMalformedAcknowledgementRetainsOutboxAcrossHTTPRestart(t *testing.T) {
	for _, tc := range []struct {
		name string
		body func(string) string
	}{
		{"duplicate-disposition", func(valid string) string { return `{"disposition":"rejected",` + strings.TrimPrefix(valid, "{") }},
		{"case-alias-disposition", func(valid string) string { return `{"Disposition":"rejected",` + strings.TrimPrefix(valid, "{") }},
		{"oversized-whitespace", func(valid string) string { return valid + strings.Repeat(" ", 5000) }},
		{"hidden-trailing-json", func(valid string) string { return valid + strings.Repeat(" ", 5000) + `{"secret":"must-not-appear"}` }},
		{"unknown-field", func(valid string) string { return `{"secret-must-not-appear":true,` + strings.TrimPrefix(valid, "{") }},
		{"malformed-field", func(valid string) string {
			return strings.Replace(valid, `"disposition":"accepted"`, `"disposition":{"secret-must-not-appear":true}`, 1)
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			dir := t.TempDir()
			sender, as := openService(t, filepath.Join(dir, "sender.db"), identityA, identityB, 20)
			receiver, bs := openService(t, filepath.Join(dir, "receiver.db"), identityB, identityA, 20)
			defer func() { as.Close(); bs.Close() }()
			first := publish(t, sender, "first")
			second := publish(t, sender, "second")
			token := strings.Repeat("q", 32)
			handler, err := receiver.ReceiveHandler(map[string]string{identityA.AgentID: token})
			if err != nil {
				t.Fatal(err)
			}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				recorder := httptest.NewRecorder()
				handler.ServeHTTP(recorder, r)
				if recorder.Code != http.StatusOK {
					t.Errorf("receiver status=%d", recorder.Code)
					w.WriteHeader(recorder.Code)
					return
				}
				_, _ = w.Write([]byte(tc.body(recorder.Body.String())))
			}))
			defer func() { server.Close() }()
			newTransport := func() *HTTPTransport {
				transport, err := NewHTTPTransport(&http.Client{Timeout: time.Second}, map[string]Destination{identityB.AgentID: {URL: server.URL, Token: token}}, true)
				if err != nil {
					t.Fatal(err)
				}
				return transport
			}
			deliveries, err := sender.Dispatch(ctx, newTransport(), 1)
			if err != nil || len(deliveries) != 1 || deliveries[0].Error == "" {
				t.Fatalf("malformed ACK retired envelope: %+v %v", deliveries, err)
			}
			if strings.Contains(deliveries[0].Error, "must-not-appear") || strings.Contains(deliveries[0].Error, token) {
				t.Fatalf("peer data leaked in error: %q", deliveries[0].Error)
			}
			counts, err := sender.Counts(ctx)
			if err != nil || counts.Pending != 2 || counts.Delivered != 0 {
				t.Fatalf("counts=%+v %v", counts, err)
			}
			feed, err := receiver.Feed(ctx, 0, 10)
			if err != nil || len(feed.Items) != 1 || feed.Items[0].Envelope.MessageID != first.MessageID {
				t.Fatalf("accept was not durable: %+v %v", feed, err)
			}
			server.Close()
			if err := as.Close(); err != nil {
				t.Fatal(err)
			}
			if err := bs.Close(); err != nil {
				t.Fatal(err)
			}
			sender, as = openService(t, filepath.Join(dir, "sender.db"), identityA, identityB, 20)
			receiver, bs = openService(t, filepath.Join(dir, "receiver.db"), identityB, identityA, 20)
			handler, err = receiver.ReceiveHandler(map[string]string{identityA.AgentID: token})
			if err != nil {
				t.Fatal(err)
			}
			var duplicateReceipts atomic.Int64
			server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				recorder := httptest.NewRecorder()
				handler.ServeHTTP(recorder, r)
				var receipt x.Receipt
				if err := json.Unmarshal(recorder.Body.Bytes(), &receipt); err != nil {
					t.Error(err)
				}
				if receipt.Disposition == "duplicate" {
					duplicateReceipts.Add(1)
				}
				w.WriteHeader(recorder.Code)
				_, _ = w.Write(recorder.Body.Bytes())
			}))
			transport := newTransport()
			for _, want := range []x.Envelope{first, second} {
				deliveries, err := sender.Dispatch(ctx, transport, 1)
				if err != nil || len(deliveries) != 1 || deliveries[0].Error != "" || deliveries[0].MessageID != want.MessageID {
					t.Fatalf("restart replay=%+v %v", deliveries, err)
				}
			}
			feed, err = receiver.Feed(ctx, 0, 10)
			if err != nil || len(feed.Items) != 2 || feed.Items[0].Envelope.MessageID != first.MessageID || feed.Items[1].Envelope.MessageID != second.MessageID || duplicateReceipts.Load() != 1 {
				t.Fatalf("duplicate/FIFO evidence=%+v duplicates=%d err=%v", feed, duplicateReceipts.Load(), err)
			}
			counts, err = sender.Counts(ctx)
			if err != nil || counts.Pending != 0 || counts.Delivered != 2 {
				t.Fatalf("final counts=%+v %v", counts, err)
			}
			t.Logf(`PEER_EXPERIMENT {"scenario":"malformed_ack_restart","fault":%q,"rejectedACKs":1,"acceptedBeforeRestart":1,"senderRestarts":1,"receiverRestarts":1,"duplicateReceipts":1,"delivered":2,"duplicateFeedItems":0,"evidenceType":"sqlite_loopback_http_fixture"}`, tc.name)
		})
	}
}

type dispatchFaultTransport func(context.Context, x.Envelope) (x.Receipt, error)

func (f dispatchFaultTransport) Send(ctx context.Context, e x.Envelope) (x.Receipt, error) {
	return f(ctx, e)
}

type legacyDispatchJournal struct{ x.Journal }

func TestDispatchConcurrentFairnessAndLegacyJournal(t *testing.T) {
	ctx := context.Background()
	store, err := sqlite.OpenExchange(filepath.Join(t.TempDir(), "sender.db"), 20)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	peers := []x.Identity{identityB,
		{AgentID: "edge-c", RobotID: "legged-c", Domain: "warehouse"},
		{AgentID: "edge-d", RobotID: "uav-d", Domain: "warehouse"}}
	sender, err := New(ctx, identityA, peers, store)
	if err != nil {
		t.Fatal(err)
	}
	sender.now = func() time.Time { return testNow }
	attempts := make(map[string]*atomic.Int64)
	for _, peer := range peers {
		attempts[peer.AgentID] = new(atomic.Int64)
		r := request(peer.AgentID)
		r.PeerID = peer.AgentID
		if _, err := sender.Publish(ctx, r); err != nil {
			t.Fatal(err)
		}
	}
	transport := dispatchFaultTransport(func(ctx context.Context, e x.Envelope) (x.Receipt, error) {
		attempts[e.Recipient.AgentID].Add(1)
		return wrongReceipt{}.Send(ctx, e)
	})
	for _, limit := range []int{0, 1001} {
		if _, err := sender.Dispatch(ctx, transport, limit); err == nil {
			t.Fatalf("accepted invalid limit %d", limit)
		}
	}
	var group sync.WaitGroup
	for i := 0; i < 9; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			deliveries, err := sender.Dispatch(ctx, transport, 1)
			if err != nil || len(deliveries) != 1 || deliveries[0].Error == "" {
				t.Errorf("dispatch=%+v err=%v", deliveries, err)
			}
		}()
	}
	group.Wait()
	for peer, count := range attempts {
		if count.Load() != 3 {
			t.Fatalf("peer %s attempts=%d", peer, count.Load())
		}
	}
	// Existing adapters without PendingFor retain their old bounded Pending
	// contract; no new interface implementation is required.
	legacy, err := New(ctx, identityA, peers, legacyDispatchJournal{store})
	if err != nil {
		t.Fatal(err)
	}
	legacy.now = sender.now
	deliveries, err := legacy.Dispatch(ctx, transport, 1)
	if err != nil || len(deliveries) != 1 || deliveries[0].Error == "" {
		t.Fatalf("legacy=%+v err=%v", deliveries, err)
	}
	counts, err := sender.Counts(ctx)
	if err != nil || counts.Pending != 3 || counts.Delivered != 0 {
		t.Fatalf("wrong ACK changed durable state: %+v %v", counts, err)
	}
}

func TestAcknowledgementByteLimitIncludesWhitespace(t *testing.T) {
	e := x.Envelope{Recipient: identityB, MessageID: "expected", Sequence: 1}
	valid, err := json.Marshal(x.Receipt{SchemaVersion: x.Schema, MessageID: e.MessageID, Sequence: e.Sequence, Disposition: "accepted"})
	if err != nil {
		t.Fatal(err)
	}
	for _, size := range []int{4096, 4097} {
		t.Run(fmt.Sprint(size), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				_, _ = w.Write([]byte(string(valid) + strings.Repeat(" ", size-len(valid))))
			}))
			defer server.Close()
			transport, err := NewHTTPTransport(&http.Client{Timeout: time.Second}, map[string]Destination{identityB.AgentID: {URL: server.URL, Token: strings.Repeat("l", 32)}}, true)
			if err != nil {
				t.Fatal(err)
			}
			receipt, err := transport.Send(context.Background(), e)
			if size == 4096 && (err != nil || receipt.MessageID != e.MessageID) {
				t.Fatalf("boundary receipt=%+v err=%v", receipt, err)
			}
			if size == 4097 && err == nil {
				t.Fatal("over-budget ACK accepted")
			}
		})
	}
}
