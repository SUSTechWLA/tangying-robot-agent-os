package exchange

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

const MaxEnvelopeBytes = x.MaxPayload + 8192

const maxReceiptBytes = 4096

func tokenMatches(r *http.Request, token string) bool {
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		return false
	}
	provided := strings.TrimPrefix(header, "Bearer ")
	return len(token) >= 32 && subtle.ConstantTimeCompare([]byte(provided), []byte(token)) == 1
}

func decode(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, MaxEnvelopeBytes)
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		return err
	}
	if err := x.ValidateJSON(raw); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}

func respond(w http.ResponseWriter, value any, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, x.ErrConflict) || errors.Is(err, x.ErrStale) || errors.Is(err, x.ErrResyncRequired) {
			status = http.StatusConflict
		}
		if errors.Is(err, x.ErrExpired) {
			status = http.StatusGone
		}
		if errors.Is(err, x.ErrCapacity) {
			status = http.StatusInsufficientStorage
		}
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	_ = json.NewEncoder(w).Encode(value)
}

// ReceiveHandler authenticates each peer independently. TLS is enforced by the
// executable; a peer token never authorizes local publishing or robot control.
func (s *Service) ReceiveHandler(tokens map[string]string) (http.Handler, error) {
	copyTokens := make(map[string]string, len(tokens))
	seen := map[string]bool{}
	for peer, token := range tokens {
		if _, ok := s.peers[peer]; !ok || len(token) < 32 || seen[token] {
			return nil, errors.New("peer tokens must be unique, at least 32 bytes, and belong to configured peers")
		}
		copyTokens[peer] = token
		seen[token] = true
	}
	if len(copyTokens) != len(s.peers) {
		return nil, errors.New("every peer requires an incoming token")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/exchange/receive", func(w http.ResponseWriter, r *http.Request) {
		principal := ""
		for peer, token := range copyTokens {
			if tokenMatches(r, token) {
				principal = peer
			}
		}
		if principal == "" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		var e x.Envelope
		if err := decode(w, r, &e); err != nil {
			respond(w, nil, err)
			return
		}
		receipt, err := s.Receive(r.Context(), principal, e)
		respond(w, receipt, err)
	})
	return mux, nil
}

// AdminHandler must be bound to literal loopback only. Consumers read advisory
// envelopes here and must independently validate freshness/frame/task context.
func (s *Service) AdminHandler(token string) (http.Handler, error) {
	if len(token) < 32 {
		return nil, errors.New("admin token requires at least 32 bytes")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/exchange/publish", func(w http.ResponseWriter, r *http.Request) {
		var request x.Request
		if err := decode(w, r, &request); err != nil {
			respond(w, nil, err)
			return
		}
		e, err := s.Publish(r.Context(), request)
		respond(w, e, err)
	})
	mux.HandleFunc("GET /v1/exchange/inbox", func(w http.ResponseWriter, r *http.Request) {
		items, err := s.Inbox(r.Context(), 100)
		respond(w, items, err)
	})
	mux.HandleFunc("GET /v1/exchange/feed", func(w http.ResponseWriter, r *http.Request) {
		values := r.URL.Query()
		if len(values) > 2 || len(values["after"]) != 1 || len(values["limit"]) != 1 {
			respond(w, nil, errors.New("feed requires exactly after and limit"))
			return
		}
		after, err := strconv.ParseUint(values.Get("after"), 10, 63)
		if err != nil {
			respond(w, nil, err)
			return
		}
		limit, err := strconv.Atoi(values.Get("limit"))
		if err != nil {
			respond(w, nil, err)
			return
		}
		feed, err := s.Feed(r.Context(), after, limit)
		if errors.Is(err, x.ErrResyncRequired) {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Cache-Control", "no-store")
			w.WriteHeader(http.StatusConflict)
			_ = json.NewEncoder(w).Encode(struct {
				Error string `json:"error"`
				Feed  x.Feed `json:"feed"`
			}{err.Error(), feed})
			return
		}
		respond(w, feed, err)
	})
	mux.HandleFunc("POST /v1/exchange/coverage-view", func(w http.ResponseWriter, r *http.Request) {
		var query x.CoverageQuery
		if err := decode(w, r, &query); err != nil {
			respond(w, nil, err)
			return
		}
		view, err := s.CoverageView(r.Context(), query)
		respond(w, view, err)
	})
	mux.HandleFunc("GET /v1/exchange/status", func(w http.ResponseWriter, r *http.Request) {
		counts, err := s.Counts(r.Context())
		respond(w, struct {
			Schema   string     `json:"schemaVersion"`
			Identity x.Identity `json:"identity"`
			Counts   x.Counts   `json:"counts"`
		}{x.Schema, s.identity, counts}, err)
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !tokenMatches(r, token) {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		mux.ServeHTTP(w, r)
	}), nil
}

type Destination struct {
	URL   string
	Token string
}
type HTTPTransport struct {
	client       *http.Client
	destinations map[string]Destination
}

func NewHTTPTransport(client *http.Client, destinations map[string]Destination, allowLoopbackHTTP bool) (*HTTPTransport, error) {
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	copyClient := *client
	copyClient.CheckRedirect = func(*http.Request, []*http.Request) error { return errors.New("exchange redirects are forbidden") }
	if copyClient.Timeout <= 0 || copyClient.Timeout > 30*time.Second {
		return nil, errors.New("exchange HTTP timeout must be positive and at most 30 seconds")
	}
	copyDestinations := make(map[string]Destination, len(destinations))
	for peer, destination := range destinations {
		u, err := url.Parse(destination.URL)
		if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || len(destination.Token) < 32 {
			return nil, errors.New("invalid exchange destination or token")
		}
		loopback := u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"
		if u.Scheme != "https" && !(allowLoopbackHTTP && loopback && u.Scheme == "http") {
			return nil, errors.New("exchange destinations require HTTPS; explicit development HTTP is literal loopback only")
		}
		destination.URL = strings.TrimSuffix(destination.URL, "/") + "/v1/exchange/receive"
		copyDestinations[peer] = destination
	}
	return &HTTPTransport{client: &copyClient, destinations: copyDestinations}, nil
}

func (t *HTTPTransport) Send(ctx context.Context, e x.Envelope) (x.Receipt, error) {
	destination, ok := t.destinations[e.Recipient.AgentID]
	if !ok {
		return x.Receipt{}, errors.New("unknown exchange destination")
	}
	raw, err := json.Marshal(e)
	if err != nil {
		return x.Receipt{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, destination.URL, bytes.NewReader(raw))
	if err != nil {
		return x.Receipt{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+destination.Token)
	response, err := t.client.Do(request)
	if err != nil {
		return x.Receipt{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return x.Receipt{}, fmt.Errorf("peer rejected exchange data: HTTP %d", response.StatusCode)
	}
	// Read one byte beyond the budget so a truncated reader cannot manufacture
	// EOF and hide oversized whitespace or a second JSON document. Reject
	// duplicate keys before Go's decoder can silently choose the last value.
	raw, err = io.ReadAll(io.LimitReader(response.Body, maxReceiptBytes+1))
	if err != nil || len(raw) > maxReceiptBytes || x.ValidateJSON(raw) != nil {
		return x.Receipt{}, errors.New("invalid acknowledgement body")
	}
	// Struct decoding also accepts case-insensitive aliases (Disposition and
	// disposition), which can otherwise override one another despite distinct
	// JSON keys. Require the exact four wire names before decoding their types.
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) != 4 {
		return x.Receipt{}, errors.New("invalid acknowledgement body")
	}
	for _, field := range []string{"schemaVersion", "messageId", "sequence", "disposition"} {
		if _, ok := fields[field]; !ok {
			return x.Receipt{}, errors.New("invalid acknowledgement body")
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var receipt x.Receipt
	if err := decoder.Decode(&receipt); err != nil {
		// Remote field names and values are untrusted and may contain secrets.
		return x.Receipt{}, errors.New("invalid acknowledgement body")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return x.Receipt{}, errors.New("invalid acknowledgement body")
	}
	return receipt, nil
}
