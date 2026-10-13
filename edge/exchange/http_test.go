package exchange

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

func TestTLSPeerDeliveryAndRedirectRefusal(t *testing.T) {
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 20)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b.db"), identityB, identityA, 20)
	defer bs.Close()
	token := strings.Repeat("t", 32)
	handler, err := b.ReceiveHandler(map[string]string{identityA.AgentID: token})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewTLSServer(handler)
	defer server.Close()
	client := server.Client()
	client.Timeout = time.Second
	transport, err := NewHTTPTransport(client, map[string]Destination{identityB.AgentID: {URL: server.URL, Token: token}}, false)
	if err != nil {
		t.Fatal(err)
	}
	e := publish(t, a, "tls-one")
	receipt, err := transport.Send(context.Background(), e)
	if err != nil || receipt.MessageID != e.MessageID {
		t.Fatalf("TLS=%+v %v", receipt, err)
	}
	unauthenticated, err := NewHTTPTransport(client, map[string]Destination{identityB.AgentID: {URL: server.URL, Token: strings.Repeat("z", 32)}}, false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := unauthenticated.Send(context.Background(), e); err == nil {
		t.Fatal("invalid peer token accepted")
	}
	redirect := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, server.URL+"/v1/exchange/receive", http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	redirectClient := redirect.Client()
	redirectClient.Timeout = time.Second
	redirectTransport, err := NewHTTPTransport(redirectClient, map[string]Destination{identityB.AgentID: {URL: redirect.URL, Token: token}}, false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := redirectTransport.Send(context.Background(), e); err == nil {
		t.Fatal("redirect followed with peer credential")
	}
}

func TestSpatialBasisRoundTripAndNoCommandSurface(t *testing.T) {
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 20)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b.db"), identityB, identityA, 20)
	defer bs.Close()
	r := request("map-1")
	r.Kind = "map_reference"
	r.Basis = &x.Basis{WorldID: "site-a", FrameID: "map", TransformRevision: "tf-4", MapID: "floor-1", MapRevision: "revision-9"}
	r.Payload = json.RawMessage(`{"sha256":"abc","note":"advisory reference only"}`)
	e, err := a.Publish(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.Receive(context.Background(), identityA.AgentID, e); err != nil {
		t.Fatal(err)
	}
	inbox, err := b.Inbox(context.Background(), 10)
	if err != nil || len(inbox) != 1 || *inbox[0].Basis != *r.Basis {
		t.Fatalf("basis=%+v %v", inbox, err)
	}
	token := strings.Repeat("t", 32)
	handler, err := b.ReceiveHandler(map[string]string{identityA.AgentID: token})
	if err != nil {
		t.Fatal(err)
	}
	for _, route := range []string{"/v1/tasks", "/v1/robot/execute", "/v1/exchange/publish"} {
		req := httptest.NewRequest("POST", route, strings.NewReader(`{}`))
		req.Header.Set("Authorization", "Bearer "+token)
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, req)
		if recorder.Code != 404 {
			t.Fatalf("peer exposed %s", route)
		}
	}
}
